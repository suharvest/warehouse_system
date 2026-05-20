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
