"""
Smoke tests for face domain HTTP routes (admin list endpoints).

These complement test_face.py (orchestrator-level, sqlite-only) by exercising
the HTTP layer through admin_client. They only assert "auth + 200 + list shape"
so they survive the upcoming routers/face.py extraction.
"""


class TestFaceSmokeRoutes:
    def test_face_subjects_list(self, admin_client):
        resp = admin_client.get("/api/face/subjects")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # endpoint returns a list
        assert isinstance(data, list)

    def test_face_logs_list(self, admin_client):
        resp = admin_client.get("/api/face/logs")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        # paginated shape: {items, total, page, page_size}
        assert isinstance(data, dict)
        assert 'items' in data
        assert 'total' in data
        assert isinstance(data['items'], list)

    def test_face_rules_list(self, admin_client):
        resp = admin_client.get("/api/face/rules")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert isinstance(data, list)


import base64 as _b64
import struct as _struct


def _emb_b64(vec):
    return _b64.b64encode(b"".join(_struct.pack("<f", x) for x in vec)).decode()


class TestFaceLibraryModelTagFilter:
    """/api/face/library?model_tag=… returns only that model's enrollments."""

    def _enroll(self, admin_client, name, model_tag, vec):
        sid = admin_client.post("/api/face/subjects", json={"name": name}).json()["id"]
        r = admin_client.post("/api/face/enrollments", json={
            "subject_id": sid,
            "embeddings": [{"embedding_b64": _emb_b64(vec), "model_tag": model_tag}],
        })
        assert r.status_code == 200, r.text
        return sid

    def test_library_unfiltered_returns_all_models(self, admin_client):
        self._enroll(admin_client, "LibA", "lib-mt-A", [1.0, 0.0, 0.0])
        self._enroll(admin_client, "LibB", "lib-mt-B", [0.0, 1.0, 0.0])
        resp = admin_client.get("/api/face/library")
        assert resp.status_code == 200, resp.text
        tags = {e["model_tag"] for e in resp.json()}
        assert "lib-mt-A" in tags and "lib-mt-B" in tags

    def test_library_filtered_returns_one_model(self, admin_client):
        self._enroll(admin_client, "LibA", "lib-mt-A", [1.0, 0.0, 0.0])
        self._enroll(admin_client, "LibB", "lib-mt-B", [0.0, 1.0, 0.0])
        resp = admin_client.get("/api/face/library?model_tag=lib-mt-A")
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data, "expected at least one mt-A enrollment"
        assert all(e["model_tag"] == "lib-mt-A" for e in data)
        names = {e["name"] for e in data}
        assert "LibA" in names and "LibB" not in names


class TestFaceConfigModeValidation:
    """PUT /api/face/config mode 校验须与 tenant_face_config 的
    CHECK(mode IN('local','lan')) 约束一致：UI 发送的 lan 必须能存；
    历史值 hello/jetson/custom/face_rec_api/we2 归一化为 lan 而非撞约束 500。"""

    _BASE = {
        "enabled": True,
        "endpoint": "http://example.local/face",
        "auth_token": "",
        "embedding_model_tag": "",
        "min_confidence": 0.7,
        "verify_mode": "interface",
    }

    def _put(self, admin_client, mode):
        return admin_client.put("/api/face/config", json={**self._BASE, "mode": mode})

    def test_mode_lan_accepted(self, admin_client):
        resp = self._put(admin_client, "lan")
        assert resp.status_code == 200, resp.text
        assert admin_client.get("/api/face/config").json()["mode"] == "lan"

    def test_mode_local_accepted(self, admin_client):
        resp = self._put(admin_client, "local")
        assert resp.status_code == 200, resp.text
        assert admin_client.get("/api/face/config").json()["mode"] == "local"

    def test_legacy_modes_normalized_to_lan(self, admin_client):
        for legacy in ("hello", "jetson", "custom", "face_rec_api", "we2"):
            resp = self._put(admin_client, legacy)
            assert resp.status_code == 200, f"{legacy}: {resp.text}"
            assert admin_client.get("/api/face/config").json()["mode"] == "lan", legacy

    def test_unknown_mode_rejected_400(self, admin_client):
        resp = self._put(admin_client, "bogus")
        assert resp.status_code == 400, resp.text


class TestFaceConfigVerifyFrequency:
    """verify_frequency（新）与 verify_mode（deprecated 兼容入参）的映射契约。"""

    _BASE = {
        "enabled": True,
        "mode": "lan",
        "endpoint": "http://example.local/face",
        "min_confidence": 0.7,
    }

    def test_verify_frequency_roundtrip(self, admin_client):
        resp = admin_client.put(
            "/api/face/config", json={**self._BASE, "verify_frequency": "session"})
        assert resp.status_code == 200, resp.text
        got = admin_client.get("/api/face/config").json()
        assert got["verify_frequency"] == "session"
        # deprecated 列反向同步，旧版本回滚后语义一致
        assert got["verify_mode"] == "session"

    def test_legacy_verify_mode_interface_maps_to_always(self, admin_client):
        resp = admin_client.put(
            "/api/face/config", json={**self._BASE, "verify_mode": "interface"})
        assert resp.status_code == 200, resp.text
        assert admin_client.get("/api/face/config").json()["verify_frequency"] == "always"

    def test_legacy_verify_mode_session_maps_to_session(self, admin_client):
        resp = admin_client.put(
            "/api/face/config", json={**self._BASE, "verify_mode": "session"})
        assert resp.status_code == 200, resp.text
        assert admin_client.get("/api/face/config").json()["verify_frequency"] == "session"

    def test_neither_field_defaults_always(self, admin_client):
        resp = admin_client.put("/api/face/config", json=self._BASE)
        assert resp.status_code == 200, resp.text
        assert admin_client.get("/api/face/config").json()["verify_frequency"] == "always"

    def test_bad_verify_frequency_rejected_400(self, admin_client):
        resp = admin_client.put(
            "/api/face/config", json={**self._BASE, "verify_frequency": "bogus"})
        assert resp.status_code == 400, resp.text

    def test_bad_legacy_verify_mode_rejected_400(self, admin_client):
        resp = admin_client.put(
            "/api/face/config", json={**self._BASE, "verify_mode": "bogus"})
        assert resp.status_code == 400, resp.text


class TestFaceVerifyMcpWarehouseScope:
    """MCP API keys bind to one warehouse; face rules must use that scope too."""

    def test_api_key_bound_warehouse_rule_applies_without_payload_warehouse(
        self, admin_client, client, default_warehouse_id
    ):
        # 新语义：设备拉身份链路由 mode='local' 触发（verify_mode 已 deprecated，
        # 这里仍传旧字段以覆盖 legacy 兼容映射 session→verify_frequency='session'）。
        cfg = {
            "enabled": True,
            "mode": "local",
            "endpoint": "http://example.local/face",
            "auth_token": "",
            "embedding_model_tag": "fake-v1",
            "min_confidence": 0.7,
            "verify_mode": "session",
        }
        resp = admin_client.put("/api/face/config", json=cfg)
        assert resp.status_code == 200, resp.text

        sid_resp = admin_client.post("/api/face/subjects", json={"name": "Scoped Speaker"})
        assert sid_resp.status_code == 200, sid_resp.text
        sid = sid_resp.json()["id"]

        rule_resp = admin_client.post("/api/face/rules", json={
            "warehouse_id": default_warehouse_id,
            "operation": "stock_out",
            "require_face": True,
            "allowed_subject_ids": [sid],
        })
        assert rule_resp.status_code == 200, rule_resp.text

        key_resp = admin_client.post("/api/api-keys", json={
            "name": "face-mcp-scope",
            "role": "operate",
            "warehouse_id": default_warehouse_id,
        })
        assert key_resp.status_code == 200, key_resp.text

        verify = client.post(
            "/api/face/verify-mcp",
            headers={"X-API-Key": key_resp.json()["key"]},
            json={"operation": "stock_out"},
        )
        assert verify.status_code == 200, verify.text
        body = verify.json()
        # 规则被 API-key 绑定的仓库正确命中（require_face）→ deny。session 模式(B)下
        # 该测试 key 没有绑定物理设备，故 fail-closed 原因是 device_unresolved；重点是
        # 规则在无 payload warehouse_id 时仍生效并拒绝，而非具体原因。
        assert body["status"] == "deny", body
        assert body["failure_reason"] == "device_unresolved", body
