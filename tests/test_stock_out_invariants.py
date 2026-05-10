"""
Stock-out invariants — regression tests for bugs B, D and FIFO/aggregate parity.

These guard against the classic "phantom OUT" pattern where the OUT
inventory_records row + materials.quantity decrement get committed but the
underlying batches don't add up.
"""
import uuid
import pytest


def _create_material_with_batches(admin_client, warehouse_id):
    """Create a material with two FIFO batches (4 + 1 = 5)."""
    from database import get_db_connection

    sku = f"INV-{uuid.uuid4().hex[:8].upper()}"
    name = f"Inv Material {sku}"

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (name, sku, 'Test', 0, 'pcs', 0, 'B-01', warehouse_id),
    )
    conn.commit()
    conn.close()

    r1 = admin_client.post("/api/materials/stock-in", json={
        "product_name": name,
        "quantity": 4,
        "reason_category": "purchase",
        "warehouse_id": warehouse_id,
    })
    assert r1.json()['success'] is True

    r2 = admin_client.post("/api/materials/stock-in", json={
        "product_name": name,
        "quantity": 1,
        "reason_category": "purchase",
        "warehouse_id": warehouse_id,
    })
    assert r2.json()['success'] is True

    return {'name': name, 'sku': sku, 'total_quantity': 5}


def _read_material_qty(name, warehouse_id):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, quantity FROM materials WHERE name = ? AND warehouse_id = ?",
        (name, warehouse_id),
    )
    row = cur.fetchone()
    conn.close()
    return (row[0], row[1]) if row else (None, None)


def _sum_batches(material_id):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM batches WHERE material_id = ?",
        (material_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row[0]


def _count_records(material_id):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM inventory_records WHERE material_id = ?",
        (material_id,),
    )
    n = cur.fetchone()[0]
    conn.close()
    return n


class TestStockOutInvariants:
    def test_stock_out_with_unmatched_location_raises(self, admin_client, default_warehouse_id):
        """Bug B regression: requesting a location with no matching batches
        must NOT silently commit a phantom OUT. The aggregate quantity must
        stay unchanged and no inventory_records row created.
        """
        m = _create_material_with_batches(admin_client, default_warehouse_id)
        material_id, qty_before = _read_material_qty(m['name'], default_warehouse_id)
        assert qty_before == 5
        rec_count_before = _count_records(material_id)

        # Branch B precheck rejects this with a non-200-ish response (success=False
        # via 200 wrapper) because availability under the bogus location is 0 < 1.
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['name'],
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": default_warehouse_id,
            "location": "DOES-NOT-EXIST-XYZ",
        })
        # Either 200 with success=False, or 409 — both are acceptable; what
        # matters is that nothing committed.
        if resp.status_code == 200:
            assert resp.json().get('success') is False
        else:
            assert resp.status_code in (409,)

        material_id_after, qty_after = _read_material_qty(m['name'], default_warehouse_id)
        assert material_id_after == material_id
        assert qty_after == qty_before, "materials.quantity must not change on failed stock-out"
        assert _count_records(material_id) == rec_count_before, "no OUT record may be committed"

    def test_stock_out_per_batch_matches_aggregate(self, admin_client, default_warehouse_id):
        """Bug B regression: after a partial stock-out, SUM(batches.quantity)
        must equal materials.quantity.
        """
        m = _create_material_with_batches(admin_client, default_warehouse_id)
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['name'],
            "quantity": 2,
            "reason_category": "sell",
            "warehouse_id": default_warehouse_id,
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True

        material_id, qty = _read_material_qty(m['name'], default_warehouse_id)
        assert qty == 3
        assert _sum_batches(material_id) == 3, (
            "SUM(batches.quantity) must equal materials.quantity (5 - 2 = 3)"
        )


class TestExcelImportInvariants:
    def test_excel_import_negative_diff_creates_consumption(self, admin_client, default_warehouse_id):
        """Bug D regression: an Excel batch-mode update that decreases batch
        quantity (diff < 0) must insert a batch_consumptions row paired with
        the new OUT inventory_records row.
        """
        from database import get_db_connection

        # Create material + a single batch via stock-in.
        sku = f"EXLD-{uuid.uuid4().hex[:8].upper()}"
        name = f"Excel Diff {sku}"
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, sku, 'Test', 0, 'pcs', 0, 'A-01', default_warehouse_id),
        )
        conn.commit()
        conn.close()

        r = admin_client.post("/api/materials/stock-in", json={
            "product_name": name,
            "quantity": 10,
            "reason_category": "purchase",
            "warehouse_id": default_warehouse_id,
        })
        assert r.json()['success'] is True
        batch_no = r.json()['batch']['batch_no']

        material_id, _ = _read_material_qty(name, default_warehouse_id)

        # Direct Excel confirm with a negative diff for the existing batch.
        payload = {
            "warehouse_id": default_warehouse_id,
            "is_batch_mode": True,
            "confirm_disable_missing_skus": False,
            "confirm_new_skus": False,
            "reason_note": "diff-test",
            "changes": [
                {
                    "sku": sku,
                    "name": name,
                    "category": "Test",
                    "unit": "pcs",
                    "safe_stock": 0,
                    "location": "A-01",
                    "batch_no": batch_no,
                    "import_quantity": 7,    # was 10 → diff = -3
                    "current_quantity": 10,
                    "difference": -3,
                    "operation": "out",
                    "is_new": False,
                    "is_batch_new": False,
                    "reason_category": "sell",
                }
            ],
        }
        resp = admin_client.post("/api/materials/import-excel/confirm", json=payload)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get('success') is True, f"import did not succeed: {body}"

        from database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT bc.quantity FROM batch_consumptions bc "
            "JOIN inventory_records ir ON ir.id = bc.record_id "
            "WHERE ir.material_id = ? AND ir.type = 'out' "
            "ORDER BY bc.id DESC LIMIT 1",
            (material_id,),
        )
        row = cur.fetchone()
        conn.close()
        assert row is not None, (
            "Bug D: an OUT record from Excel batch-mode diff<0 must have a "
            "paired batch_consumptions row"
        )
        assert row[0] == 3
