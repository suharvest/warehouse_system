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
    """Read material id + current quantity. quantity 来自 active batches 聚合
    (单一真相源)，不再读取 materials.quantity 字段。
    """
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id FROM materials WHERE name = ? AND warehouse_id = ?",
        (name, warehouse_id),
    )
    row = cur.fetchone()
    if not row:
        conn.close()
        return (None, None)
    mid = row[0]
    cur.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM batches "
        "WHERE material_id = ? AND is_exhausted = 0",
        (mid,),
    )
    qty = cur.fetchone()[0]
    conn.close()
    return (mid, qty)


def _sum_batches(material_id):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT COALESCE(SUM(quantity), 0) FROM batches "
        "WHERE material_id = ? AND is_exhausted = 0",
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
        assert qty_after == qty_before, "active batch sum must not change on failed stock-out"
        assert _count_records(material_id) == rec_count_before, "no OUT record may be committed"

    def test_stock_out_per_batch_matches_aggregate(self, admin_client, default_warehouse_id):
        """Bug B regression: after a partial stock-out, active batch SUM stays
        consistent (5 - 2 = 3) — 现在的真相源就是 active batches.quantity 之和。
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
            "active batch sum must equal 5 - 2 = 3"
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


class TestEndToEndUpgrade:
    @pytest.mark.skip(
        reason="Obsolete: simulates a pre-refactor divergence between "
        "materials.quantity (legacy auth source) and batches.quantity. "
        "After single-source-of-truth refactor (batches.quantity is authoritative), "
        "stock-in/out no longer write materials.quantity, so the divergent state "
        "this test reproduces can no longer occur. The 6fec76bb57d9 repair migration "
        "remains in place for users upgrading from old DBs."
    )
    def test_simulated_customer_upgrade_repairs_then_new_stock_out_works(
        self, admin_client, default_warehouse_id
    ):
        """End-to-end: reproduce a customer's pre-fix divergent state,
        run the data-repair migration body, then exercise the (now-fixed)
        stock-out API and confirm everything stays consistent.

        Mirrors the production scenario:
          - 7 inbound, 5 outbound recorded historically
          - materials.quantity = 2 (correct), but SUM(batches.quantity) = 5
            (3 OUT records left batches untouched due to bug B)
          - 3 of those 5 OUT records lack batch_consumptions (orphans)
        After migration:
          - SUM(batches.quantity) must equal materials.quantity (2)
          - oldest batches must be exhausted first (FIFO)
          - orphan OUT records must have backfilled batch_consumptions
        After a *new* stock-out via the fixed API:
          - SUM(batches.quantity) must still equal materials.quantity
          - new OUT record must have a paired batch_consumptions row
        """
        import importlib.util
        import sqlalchemy as sa
        from datetime import datetime, timedelta
        from pathlib import Path
        from database import get_db_connection

        # Step 1: bootstrap a material via the API so all FKs / scope columns
        # are populated correctly (mirrors how customer data was created).
        sku = f"E2E-{uuid.uuid4().hex[:8].upper()}"
        name = f"E2E Monitor {sku}"
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO materials (name, sku, category, quantity, unit, "
            "safe_stock, location, warehouse_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (name, sku, 'Test', 0, 'pcs', 0, 'A-01', default_warehouse_id),
        )
        conn.commit()
        conn.close()

        # 4 inbounds → 4 batches summing to 7 (mirrors customer: 4+1+1+1)
        for q in (4, 1, 1, 1):
            r = admin_client.post("/api/materials/stock-in", json={
                "product_name": name,
                "quantity": q,
                "reason_category": "purchase",
                "warehouse_id": default_warehouse_id,
            })
            assert r.json()['success'] is True

        material_id, qty_after_in = _read_material_qty(name, default_warehouse_id)
        assert qty_after_in == 7
        assert _sum_batches(material_id) == 7

        # Step 2: inject the divergent state directly — simulate the buggy
        # behavior of pre-fix code: materials.quantity decremented by 5,
        # 5 OUT records inserted, but batches untouched for 3 of them, and
        # those 3 have no batch_consumptions rows.
        # We'll do 2 "good" outbounds via the fixed API (these create
        # consumptions correctly), then directly forge 3 orphan OUTs.
        for _ in range(2):
            r = admin_client.post("/api/materials/stock-out", json={
                "product_name": name,
                "quantity": 1,
                "reason_category": "sell",
                "warehouse_id": default_warehouse_id,
            })
            assert r.json()['success'] is True

        # After 2 clean outs: aggregate=5, batches sum=5, in sync.
        _, q5 = _read_material_qty(name, default_warehouse_id)
        assert q5 == 5
        assert _sum_batches(material_id) == 5

        # Now forge 3 orphan OUT records (decrement materials.quantity but
        # NOT batches, NO batch_consumptions). This is exactly what the
        # buggy pre-fix code did.
        conn = get_db_connection()
        cur = conn.cursor()
        base_ts = datetime(2026, 5, 8, 10, 17, 56)
        for i in range(3):
            cur.execute(
                "INSERT INTO inventory_records "
                "(material_id, type, quantity, operator, reason_category, "
                " warehouse_id, tenant_id, created_at) "
                "VALUES (?, 'out', 1, 'admin', 'sell', ?, 1, ?)",
                (material_id, default_warehouse_id,
                 (base_ts + timedelta(minutes=i*2)).isoformat()),
            )
        cur.execute(
            "UPDATE materials SET quantity = quantity - 3 WHERE id = ?",
            (material_id,),
        )
        conn.commit()
        conn.close()

        # Confirm divergent state matches the customer report.
        # 注：单一真相源切换后 _read_material_qty 已读 batch 聚合；这里需要
        # 直接读 materials.quantity 才能再现"aggregate（旧 cache）vs batches 不一致"的历史 bug 场景。
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT quantity FROM materials WHERE id = ?", (material_id,))
        qty_diverged = cur.fetchone()[0]
        conn.close()
        sum_diverged = _sum_batches(material_id)
        assert qty_diverged == 2, "materials.quantity (legacy cache) should be 7 - 5 = 2"
        assert sum_diverged == 5, "batches sum should be inflated to 5 (bug)"

        # Step 3: run the data-repair migration via Alembic against the live
        # test DB. test_db is already at head, so stamp back one rev and
        # re-upgrade to force the repair body to run on the divergent state.
        REPO_ROOT = Path(__file__).resolve().parents[1]
        BACKEND_DIR = REPO_ROOT / "backend"
        from alembic import command as _alembic_command
        from alembic.config import Config as _AlembicConfig
        import os as _os

        cfg = _AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
        cfg.set_main_option(
            "sqlalchemy.url", f"sqlite:///{_os.environ['DATABASE_PATH']}"
        )
        _alembic_command.stamp(cfg, "1826e23835b6")
        _alembic_command.upgrade(cfg, "6fec76bb57d9")

        # Step 4: assert post-repair state.
        _, qty_after_repair = _read_material_qty(name, default_warehouse_id)
        sum_after_repair = _sum_batches(material_id)
        assert qty_after_repair == 2, "aggregate untouched"
        assert sum_after_repair == 2, (
            "batches sum must now match aggregate after FIFO excess-consume"
        )

        # Verify FIFO order: oldest batch (qty 4) should have been touched
        # most. Original batches were 4, 1, 1, 1 in created_at order.
        # 2 clean outs already consumed 2 from the oldest (4→2).
        # Repair needs to consume 3 more (excess=3): from oldest still:
        # batch1: 2→0 (consume 2), batch2: 1→0 (consume 1). batches 3,4 untouched (1 each).
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT quantity, is_exhausted FROM batches "
            "WHERE material_id = ? ORDER BY created_at ASC",
            (material_id,),
        )
        batch_rows = cur.fetchall()
        # The 3 orphan OUTs should have backfilled consumptions now.
        cur.execute(
            "SELECT COUNT(*) FROM batch_consumptions bc "
            "JOIN inventory_records ir ON ir.id = bc.record_id "
            "WHERE ir.material_id = ? AND ir.type = 'out'",
            (material_id,),
        )
        consumption_count = cur.fetchone()[0]
        conn.close()

        quantities = [int(r[0]) for r in batch_rows]
        assert quantities == [0, 0, 1, 1], (
            f"FIFO repair should drain oldest first; got {quantities}"
        )
        # 5 OUT records total, each should have at least one consumption row
        # (2 from clean outs + 3 backfilled).
        assert consumption_count >= 5, (
            f"expected >=5 consumption rows after backfill, got {consumption_count}"
        )

        # Step 5: exercise the *fixed* stock-out API on the repaired DB.
        # Stock out 1 more — should drain one of the remaining batches
        # cleanly with no divergence.
        r = admin_client.post("/api/materials/stock-out", json={
            "product_name": name,
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": default_warehouse_id,
        })
        assert r.status_code == 200
        assert r.json()['success'] is True

        _, qty_final = _read_material_qty(name, default_warehouse_id)
        sum_final = _sum_batches(material_id)
        assert qty_final == 1
        assert sum_final == 1, (
            "post-repair stock-out must keep batches sum == aggregate"
        )
