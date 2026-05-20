"""
Smoke tests for batches domain (pre-refactor safety net).

Covers:
- GET /api/batches/by-no (happy path + not_found returning 200 + success=false)
- POST /api/materials/batches/move-location (full move, partial split, invalid quantity)
"""
import pytest


def _get_batch_no(material_id):
    """Fetch the seeded LEGACY-* batch_no for a material from sample_material fixture."""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT batch_no FROM batches WHERE material_id = ? "
        "AND is_exhausted = 0 ORDER BY id ASC LIMIT 1",
        (material_id,),
    )
    row = cur.fetchone()
    conn.close()
    assert row is not None, f"no batch found for material_id={material_id}"
    return row['batch_no']


class TestGetBatchByNo:
    def test_get_batch_by_no_happy_path(self, admin_client, sample_material):
        bn = _get_batch_no(sample_material['id'])
        resp = admin_client.get(
            "/api/batches/by-no",
            params={"batch_no": bn, "warehouse_id": sample_material['warehouse_id']},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['batch']['batch_no'] == bn
        assert data['batch']['material_name'] == sample_material['name']
        assert data['batch']['quantity'] == 100

    def test_get_batch_by_no_not_found(self, admin_client):
        # Note: design returns 200 + success=false (NOT 404) so MCP wraps as
        # speak_failed instead of transport error.
        resp = admin_client.get(
            "/api/batches/by-no", params={"batch_no": "NOEXIST-999"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == "batch_not_found"


class TestMoveBatchLocation:
    def test_move_batch_location_full(self, admin_client, sample_material):
        bn = _get_batch_no(sample_material['id'])
        resp = admin_client.post(
            "/api/materials/batches/move-location",
            json={
                "batch_no": bn,
                "new_location": "B-99",
                "warehouse_id": sample_material['warehouse_id'],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data['success'] is True
        assert data['full_move'] is True
        assert data['to_location'] == "B-99"
        # Full move: source and target are the same batch_no
        assert data['target_batch']['batch_no'] == bn

    def test_move_batch_location_partial(self, admin_client, sample_material):
        bn = _get_batch_no(sample_material['id'])
        # sample_material's seeded batch quantity is 100; move 30
        resp = admin_client.post(
            "/api/materials/batches/move-location",
            json={
                "batch_no": bn,
                "new_location": "C-77",
                "quantity": 30,
                "warehouse_id": sample_material['warehouse_id'],
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data['success'] is True
        assert data['full_move'] is False
        assert data['moved_quantity'] == 30
        # Source retains 70, target is a brand-new batch_no
        assert data['source_batch']['batch_no'] == bn
        assert data['source_batch']['quantity'] == 70
        assert data['target_batch']['batch_no'] != bn
        assert data['target_batch']['quantity'] == 30

    def test_move_batch_location_invalid_quantity(self, admin_client, sample_material):
        bn = _get_batch_no(sample_material['id'])
        resp = admin_client.post(
            "/api/materials/batches/move-location",
            json={
                "batch_no": bn,
                "new_location": "D-01",
                "quantity": 999,  # exceeds the 100 available
                "warehouse_id": sample_material['warehouse_id'],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == "insufficient_quantity"
