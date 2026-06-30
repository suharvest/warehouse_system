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
