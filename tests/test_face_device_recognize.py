"""设备识别代理端点测试：POST /api/face/device/recognize。

lan 模式下设备现场抓拍后按 face_rec_api /recognize 契约 POST 到 warehouse，
warehouse 转发租户端点 /infer（mock 掉）取 embedding，再对本库 face_enrollments
比对。鉴权是租户级 tenant_face_config.auth_token（Bearer，无登录态）。
响应恒 200，spoof / no_match 以 matched=false + reason 表达；每次调用写
face_auth_logs（operation='device_recognize'）。
"""
import base64 as _b64
import struct as _struct
import uuid

import pytest


def _emb_bytes(vec):
    return b"".join(_struct.pack("<f", x) for x in vec)


def _emb_b64(vec):
    return _b64.b64encode(_emb_bytes(vec)).decode()


def _set_cfg(admin_client, token, *, min_confidence=0.5,
             endpoint="http://device-recog-fake.invalid:8001"):
    r = admin_client.put("/api/face/config", json={
        "enabled": True, "mode": "lan", "endpoint": endpoint,
        "auth_token": token, "min_confidence": min_confidence,
    })
    assert r.status_code == 200, r.text


def _patch_infer(monkeypatch, *, embedding=None, model_tag=None, error=None,
                 calls=None):
    """Mock face.endpoint_client.infer（orchestrator 与 device 端点共用同一模块属性）。"""
    import face.endpoint_client as ec

    async def fake_infer(cfg, image_b64):
        if calls is not None:
            calls.append(image_b64)
        if error is not None:
            raise ec.FaceEndpointError(error)
        return {"embedding": embedding, "model_tag": model_tag}

    monkeypatch.setattr(ec, "infer", fake_infer)


def _recognize(admin_client, token, image="c25hcA=="):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return admin_client.post(
        "/api/face/device/recognize",
        json={"image_base64": image}, headers=headers,
    )


def _latest_device_log(admin_client):
    items = admin_client.get(
        "/api/face/logs?operation=device_recognize").json()["items"]
    return items[0] if items else None


@pytest.fixture(autouse=True)
def _clean_reembed_state():
    from face import orchestrator
    orchestrator._reembed_failed.clear()
    orchestrator._reembed_inflight.clear()
    yield


@pytest.fixture()
def _track_subjects(admin_client):
    """测试内创建的 subject 结束时删除（级联清 enrollment），避免带照片的
    enrollment 污染 push-faces 懒重算测试（共享 DB，按 tenant 全量扫描）。"""
    created = []
    yield created
    for sid in created:
        admin_client.delete(f"/api/face/subjects/{sid}")


class TestDeviceRecognizeAuth:
    def test_missing_token_401(self, admin_client):
        r = _recognize(admin_client, None)
        assert r.status_code == 401, r.text

    def test_wrong_token_401(self, admin_client):
        _set_cfg(admin_client, "tok-" + uuid.uuid4().hex)
        r = _recognize(admin_client, "definitely-wrong-token")
        assert r.status_code == 401, r.text

    def test_empty_bearer_401(self, admin_client):
        # auth_token 为空的租户不可用此端点：空 Bearer 不得命中空列。
        _set_cfg(admin_client, "")
        r = _recognize(admin_client, "")
        assert r.status_code == 401, r.text


class TestDeviceRecognizeFlow:
    def _enroll(self, admin_client, name, model_tag, vec):
        sid = admin_client.post(
            "/api/face/subjects", json={"name": name}).json()["id"]
        r = admin_client.post("/api/face/enrollments", json={
            "subject_id": sid,
            "embeddings": [{"embedding_b64": _emb_b64(vec), "model_tag": model_tag}],
        })
        assert r.status_code == 200, r.text
        return sid

    def test_hit_returns_matched_name(self, admin_client, monkeypatch, _track_subjects):
        tok = "tok-" + uuid.uuid4().hex
        mt = "dev-mt-" + uuid.uuid4().hex[:8]
        _set_cfg(admin_client, tok)
        vec = [1.0, 0.0, 0.0]
        sid = self._enroll(admin_client, "Dev Hit", mt, vec)
        _track_subjects.append(sid)
        _patch_infer(monkeypatch, embedding=_emb_bytes(vec), model_tag=mt)

        r = _recognize(admin_client, tok)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["matched"] is True
        assert data["name"] == "Dev Hit"
        assert data["confidence"] == pytest.approx(1.0)
        assert data["reason"] is None
        assert isinstance(data["processing_time_ms"], int)

        log = _latest_device_log(admin_client)
        assert log is not None
        assert log["decision"] == "pass"
        assert log["matched_subject_id"] == sid
        assert log["confidence"] == pytest.approx(1.0)

    def test_spoof_returns_reason_and_audits(self, admin_client, monkeypatch):
        tok = "tok-" + uuid.uuid4().hex
        _set_cfg(admin_client, tok)
        _patch_infer(monkeypatch, error="spoof")

        r = _recognize(admin_client, tok)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["matched"] is False
        assert data["reason"] == "spoof"
        assert data["live"] is False
        assert data["name"] is None

        log = _latest_device_log(admin_client)
        assert log is not None
        assert log["decision"] == "deny"
        assert log["failure_reason"] == "spoof"

    def test_no_face_reason_passthrough(self, admin_client, monkeypatch):
        tok = "tok-" + uuid.uuid4().hex
        _set_cfg(admin_client, tok)
        _patch_infer(monkeypatch, error="no_face_detected")

        data = _recognize(admin_client, tok).json()
        assert data["matched"] is False
        assert data["reason"] == "no_face_detected"
        assert data["live"] is None  # 活体未知，不是 false

    def test_endpoint_unreachable_still_200(self, admin_client, monkeypatch):
        tok = "tok-" + uuid.uuid4().hex
        _set_cfg(admin_client, tok)
        _patch_infer(monkeypatch, error="endpoint_unreachable")

        r = _recognize(admin_client, tok)
        assert r.status_code == 200
        data = r.json()
        assert data["matched"] is False
        assert data["reason"] == "endpoint_unreachable"

    def test_no_match_when_library_empty_for_model(self, admin_client, monkeypatch):
        tok = "tok-" + uuid.uuid4().hex
        _set_cfg(admin_client, tok)
        # 该 model_tag 下没有任何 enrollment → no_match。
        _patch_infer(
            monkeypatch, embedding=_emb_bytes([0.0, 1.0, 0.0]),
            model_tag="dev-mt-empty-" + uuid.uuid4().hex[:8],
        )

        data = _recognize(admin_client, tok).json()
        assert data["matched"] is False
        assert data["reason"] == "no_match"

        log = _latest_device_log(admin_client)
        assert log["decision"] == "deny"
        assert log["failure_reason"] == "no_match"

    def test_low_confidence_denied_as_no_match(self, admin_client, monkeypatch, _track_subjects):
        tok = "tok-" + uuid.uuid4().hex
        mt = "dev-mt-low-" + uuid.uuid4().hex[:8]
        _set_cfg(admin_client, tok, min_confidence=0.9)
        _track_subjects.append(self._enroll(admin_client, "Dev Low", mt, [1.0, 0.0, 0.0]))
        # 正交向量 → cosine 0 < 0.9 阈值。
        _patch_infer(monkeypatch, embedding=_emb_bytes([0.0, 1.0, 0.0]), model_tag=mt)

        data = _recognize(admin_client, tok).json()
        assert data["matched"] is False
        assert data["reason"] == "no_match"
        # 审计里保留更细的 low_confidence 原因。
        log = _latest_device_log(admin_client)
        assert log["failure_reason"] == "low_confidence"

    def test_lazy_reembed_fallback(self, admin_client, monkeypatch, _track_subjects):
        """subject 只有旧 model_tag 的 enrollment + 注册照片 → 识别时用照片
        经端点现算补新模型行再比对（与 verify 路径同一懒重算 helper）。"""
        tok = "tok-" + uuid.uuid4().hex
        old_mt = "dev-mt-old-" + uuid.uuid4().hex[:8]
        new_mt = "dev-mt-new-" + uuid.uuid4().hex[:8]
        _set_cfg(admin_client, tok)

        # 用 images_b64 注册路径落一行带 source_image_b64 的旧模型 enrollment。
        sid = admin_client.post(
            "/api/face/subjects", json={"name": "Dev Lazy"}).json()["id"]
        _track_subjects.append(sid)
        _patch_infer(
            monkeypatch, embedding=_emb_bytes([0.5, 0.5, 0.0]), model_tag=old_mt)
        r = admin_client.post("/api/face/enrollments", json={
            "subject_id": sid, "images_b64": ["photo:dev-lazy"],
        })
        assert r.status_code == 200, r.text

        # 端点换了模型：抓拍与照片重算都返回 new_mt。
        calls = []
        vec_new = [0.0, 0.0, 1.0]
        _patch_infer(
            monkeypatch, embedding=_emb_bytes(vec_new), model_tag=new_mt,
            calls=calls)

        data = _recognize(admin_client, tok, image="c25hcDI=").json()
        assert data["matched"] is True, data
        assert data["name"] == "Dev Lazy"
        # 第一次 infer 是抓拍图，第二次是懒重算用的注册照片。
        assert calls == ["c25hcDI=", "photo:dev-lazy"]

        # DB 长出 new_mt 行。
        from db import get_engine
        from sqlalchemy import text as _text
        with get_engine().connect() as c:
            tags = {row[0] for row in c.execute(_text(
                "SELECT model_tag FROM face_enrollments WHERE subject_id = :sid"),
                {"sid": sid}).fetchall()}
        assert {old_mt, new_mt} <= tags
