"""
Smoke test for /api/operators (filter dropdown source).
"""


class TestOperators:
    def test_list_operators_smoke(self, admin_client):
        resp = admin_client.get("/api/operators")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        # admin itself has role=admin which qualifies; expect at least one entry
        assert len(data) >= 1
        item = data[0]
        assert 'user_id' in item
        assert 'username' in item
