"""
Smoke test for /api/reason-categories.
"""


class TestReasonCategories:
    def test_list_reason_categories_smoke(self, admin_client):
        resp = admin_client.get("/api/reason-categories")
        assert resp.status_code == 200
        data = resp.json()
        assert 'in' in data
        assert 'out' in data
        assert isinstance(data['in'], list)
        assert isinstance(data['out'], list)
        assert len(data['in']) >= 1
        assert len(data['out']) >= 1
        # each entry has key + label
        sample = data['in'][0]
        assert 'key' in sample
        assert 'label' in sample
