"""
Single-source-of-truth invariant tests.

After the materials.quantity → batches.quantity (is_exhausted=0) refactor,
three read paths must return the same number for the same material:

  - /api/materials/list                items[0].total_quantity
  - /api/materials/product-stats       current_stock
  - /api/materials/batches             sum(b.quantity for b in batches)

This test seeds a material then exercises the invariant after:
  1. initial seed (only fixture batch)
  2. additional stock-in (FIFO branch creates a new batch)
  3. FIFO stock-out (consumes oldest batch first)
  4. targeted batch stock-out (specified batch_no)
"""
import uuid

import pytest


def _three_way_qty(client, name):
    """Return (list_qty, stats_qty, batches_sum) for a given material name."""
    r_list = client.get("/api/materials/list", params={"name": name, "fuzzy": False})
    assert r_list.status_code == 200, r_list.text
    items = r_list.json()["items"]
    matched = [it for it in items if it["name"] == name]
    assert matched, f"/materials/list returned no item for {name!r}"
    list_qty = matched[0]["total_quantity"]

    r_stats = client.get("/api/materials/product-stats", params={"name": name})
    assert r_stats.status_code == 200, r_stats.text
    stats_qty = r_stats.json()["current_stock"]

    r_batches = client.get("/api/materials/batches", params={"name": name})
    assert r_batches.status_code == 200, r_batches.text
    batches_sum = sum(b["quantity"] for b in r_batches.json()["batches"])

    return list_qty, stats_qty, batches_sum


def _assert_consistent(client, name, expected):
    list_qty, stats_qty, batches_sum = _three_way_qty(client, name)
    assert list_qty == stats_qty == batches_sum == expected, (
        f"inconsistent qty for {name!r}: list={list_qty} "
        f"stats={stats_qty} batches_sum={batches_sum} expected={expected}"
    )


class TestThreeApiInventoryConsistency:
    """All three read APIs must agree on current quantity at every step."""

    def test_after_seed_consistent(self, admin_client, sample_material):
        # fixture inserts 1 batch with quantity=100
        _assert_consistent(admin_client, sample_material["name"], 100)

    def test_after_stock_in_consistent(self, admin_client, sample_material):
        resp = admin_client.post(
            "/api/materials/stock-in",
            json={
                "product_name": sample_material["name"],
                "quantity": 25,
                "reason_category": "purchase",
                "warehouse_id": sample_material["warehouse_id"],
            },
        )
        assert resp.json()["success"] is True
        _assert_consistent(admin_client, sample_material["name"], 125)

    def test_after_fifo_stock_out_consistent(self, admin_client, sample_material):
        # add a second batch so FIFO has more than one to choose from
        resp = admin_client.post(
            "/api/materials/stock-in",
            json={
                "product_name": sample_material["name"],
                "quantity": 30,
                "reason_category": "purchase",
                "warehouse_id": sample_material["warehouse_id"],
            },
        )
        assert resp.json()["success"] is True

        # FIFO stock-out: 40 — fully drains the oldest 100-batch? No: drains 40 of it.
        resp = admin_client.post(
            "/api/materials/stock-out",
            json={
                "product_name": sample_material["name"],
                "quantity": 40,
                "reason_category": "sell",
                "warehouse_id": sample_material["warehouse_id"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        _assert_consistent(admin_client, sample_material["name"], 100 + 30 - 40)

    def test_after_targeted_batch_stock_out_consistent(self, admin_client, sample_material):
        # Add a second batch then drain that specific batch's qty.
        r_in = admin_client.post(
            "/api/materials/stock-in",
            json={
                "product_name": sample_material["name"],
                "quantity": 17,
                "reason_category": "purchase",
                "warehouse_id": sample_material["warehouse_id"],
            },
        )
        new_batch_no = r_in.json()["batch"]["batch_no"]
        assert new_batch_no

        resp = admin_client.post(
            "/api/materials/stock-out",
            json={
                "product_name": sample_material["name"],
                "quantity": 5,
                "reason_category": "sell",
                "batch_no": new_batch_no,
                "warehouse_id": sample_material["warehouse_id"],
            },
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        _assert_consistent(admin_client, sample_material["name"], 100 + 17 - 5)

    def test_dirty_materials_quantity_is_ignored(self, admin_client, sample_material):
        """Tampering materials.quantity must not affect any of the three APIs.

        This guards against any future code path accidentally re-reading
        materials.quantity for display/stock figures.
        """
        from database import get_db_connection

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE materials SET quantity = 9999 WHERE id = ?",
            (sample_material["id"],),
        )
        conn.commit()
        conn.close()

        # Active batch sum is still 100; all three APIs must agree on 100, not 9999.
        _assert_consistent(admin_client, sample_material["name"], 100)
