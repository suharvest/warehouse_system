"""
Stock-out tests: normal stock-out, insufficient inventory rejection, FIFO batch consumption.
"""
import pytest
import uuid


@pytest.fixture()
def stocked_material(admin_client, default_warehouse_id):
    """Create a material with known batch structure for FIFO testing."""
    from database import get_db_connection
    import uuid

    sku = f"FIFO-{uuid.uuid4().hex[:8].upper()}"
    name = f"FIFO Material {sku}"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (name, sku, 'Test', 0, 'pcs', 10, 'B-01', default_warehouse_id))
    conn.commit()
    conn.close()

    # Stock in two batches
    resp1 = admin_client.post("/api/materials/stock-in", json={
        "product_name": name,
        "quantity": 30,
        "reason_category": "purchase",
        "warehouse_id": default_warehouse_id
    })
    assert resp1.json()['success'] is True
    batch1_no = resp1.json()['batch']['batch_no']

    resp2 = admin_client.post("/api/materials/stock-in", json={
        "product_name": name,
        "quantity": 20,
        "reason_category": "purchase",
        "warehouse_id": default_warehouse_id
    })
    assert resp2.json()['success'] is True
    batch2_no = resp2.json()['batch']['batch_no']

    return {
        'name': name,
        'sku': sku,
        'total_quantity': 50,
        'batch1_no': batch1_no,
        'batch1_qty': 30,
        'batch2_no': batch2_no,
        'batch2_qty': 20,
        'warehouse_id': default_warehouse_id,
    }


class TestStockOut:
    """Stock-out operation tests."""

    def test_stock_out_success(self, admin_client, stocked_material):
        """Normal stock-out should succeed."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 10,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['product']['out_quantity'] == 10
        assert data['product']['new_quantity'] == 40  # 50 - 10

    def test_stock_out_insufficient_inventory(self, admin_client, stocked_material):
        """Stock-out exceeding inventory should be rejected."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 9999,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False
        assert "库存不足" in data.get('error', '') or "库存不足" in data.get('message', '')

    def test_stock_out_zero_quantity_rejected(self, admin_client, stocked_material):
        """Stock-out with zero quantity should be rejected."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 0,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_out_nonexistent_product(self, admin_client, default_warehouse_id):
        """Stock-out for non-existent product should fail."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": "NonexistentProduct_ABC",
            "quantity": 5,
            "reason_category": "sell",
            "warehouse_id": default_warehouse_id
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_out_compound_sku_name_resolves_single_duplicate_name(
            self, admin_client, default_warehouse_id):
        """Voice/MCP input may combine a code and name, with LV heard as LB."""
        from database import get_db_connection
        from app import get_fuzzy_matcher

        name = f"电极帽-{uuid.uuid4().hex[:6]}"
        rows = [
            ("LV-0045", 50),
            ("LV-0046", 100),
        ]
        conn = get_db_connection()
        cursor = conn.cursor()
        material_ids = {}
        for sku, qty in rows:
            cursor.execute('''
                INSERT INTO materials (name, sku, category, quantity, unit, safe_stock,
                                       location, warehouse_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (name, sku, 'Test', qty, 'pcs', 1, '', default_warehouse_id))
            mid = cursor.lastrowid
            material_ids[sku] = mid
            cursor.execute('''
                INSERT INTO batches (batch_no, material_id, quantity, initial_quantity,
                                     is_exhausted, warehouse_id)
                VALUES (?, ?, ?, ?, 0, ?)
            ''', (f"B-{sku}", mid, qty, qty, default_warehouse_id))
        conn.commit()
        conn.close()
        get_fuzzy_matcher().invalidate_cache(entity_type="material")

        phrases = [
            f"LB0045{name}",
            f"{name} LB0045",
            f"SKU为LB0045的{name}",
        ]
        for phrase in phrases:
            resp = admin_client.post("/api/materials/stock-out", json={
                "product_name": phrase,
                "quantity": 5,
                "reason_category": "sell",
                "warehouse_id": default_warehouse_id,
            })
            assert resp.status_code == 200
            data = resp.json()
            assert data['success'] is True, data
            assert data['resolved_from'] == phrase
            assert data['batch_consumptions'][0]['batch_id']

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT quantity FROM batches WHERE material_id = ?",
                       (material_ids["LV-0045"],))
        assert cursor.fetchone()['quantity'] == 35
        cursor.execute("SELECT quantity FROM batches WHERE material_id = ?",
                       (material_ids["LV-0046"],))
        assert cursor.fetchone()['quantity'] == 100
        conn.close()


class TestFIFOConsumption:
    """FIFO batch consumption tests."""

    def test_fifo_consumes_first_batch(self, admin_client, stocked_material):
        """Stock-out should consume from the first batch first (FIFO)."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 25,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True

        # Should have batch consumptions
        consumptions = data.get('batch_consumptions')
        if consumptions:
            # First consumption should be from batch1
            assert consumptions[0]['batch_no'] == stocked_material['batch1_no']
            total_consumed = sum(c['quantity'] for c in consumptions)
            assert total_consumed == 25

    def test_fifo_spans_multiple_batches(self, admin_client, stocked_material):
        """Stock-out spanning multiple batches should consume in FIFO order."""
        # Consume 40 out of 50: should take all 30 from batch1 + 10 from batch2
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 40,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id']
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True

        consumptions = data.get('batch_consumptions')
        if consumptions:
            assert len(consumptions) >= 2
            total_consumed = sum(c['quantity'] for c in consumptions)
            assert total_consumed == 40
            # First batch should be fully consumed
            assert consumptions[0]['batch_no'] == stocked_material['batch1_no']
            assert consumptions[0]['quantity'] == 30
            # Second batch partially consumed
            assert consumptions[1]['batch_no'] == stocked_material['batch2_no']
            assert consumptions[1]['quantity'] == 10

    def test_stock_out_low_stock_warning(self, admin_client, default_warehouse_id):
        """Stock-out that drops below safe_stock should include warning."""
        from database import get_db_connection
        import uuid

        sku = f"WARN-{uuid.uuid4().hex[:8].upper()}"
        name = f"Warning Material {sku}"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, sku, 'Test', 0, 'pcs', 50, 'C-01', default_warehouse_id))
        conn.commit()
        conn.close()

        # Stock in 60
        admin_client.post("/api/materials/stock-in", json={
            "product_name": name,
            "quantity": 60,
            "reason_category": "purchase",
            "warehouse_id": default_warehouse_id
        })

        # Stock out 50 (leaves 10, below safe_stock of 50)
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": name,
            "quantity": 50,
            "reason_category": "sell",
            "warehouse_id": default_warehouse_id
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data.get('warning') is not None

    def test_stock_out_specified_batch_success(self, admin_client, stocked_material):
        """指定 batch_no 出库应仅从该批次扣减，不触发 FIFO。"""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 5,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch2_no'],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert len(data['batch_consumptions']) == 1
        assert data['batch_consumptions'][0]['batch_no'] == stocked_material['batch2_no']
        assert data['batch_consumptions'][0]['quantity'] == 5

    def test_stock_out_specified_batch_insufficient(self, admin_client, stocked_material):
        """指定批次余量不足应报错，不 fallback 到 FIFO。"""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 999,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch1_no'],
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'batch_insufficient_stock'

    def test_stock_out_batch_not_found(self, admin_client, stocked_material):
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": "NONEXISTENT-9999",
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'batch_not_found'

    def test_stock_out_batch_location_mismatch(self, admin_client, stocked_material):
        """指定 batch_no + 不匹配的 location 应报 batch_field_mismatch。"""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch1_no'],
            "location": "WRONG-LOC-ZZ",
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'batch_field_mismatch'

    def test_stock_out_location_fuzzy_confident(self, admin_client, default_warehouse_id):
        """location_fuzzy=True 且匹配明确时应成功出库。"""
        import uuid
        name = f"LocFuzzy-{uuid.uuid4().hex[:8]}"

        from database import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''INSERT INTO materials
            (name, sku, category, quantity, unit, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?)''',
            (name, f"S-{uuid.uuid4().hex[:8]}", 'Test', 0, 'pcs', default_warehouse_id))
        conn.commit()
        conn.close()

        admin_client.post("/api/materials/stock-in", json={
            "product_name": name, "quantity": 20, "reason_category": "purchase",
            "warehouse_id": default_warehouse_id, "location": "A-01",
        })

        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": name, "quantity": 5, "reason_category": "sell",
            "warehouse_id": default_warehouse_id, "location": "A01",
            "location_fuzzy": True,
        })
        assert resp.json()['success'] is True

    def test_stock_out_location_fuzzy_not_found(self, admin_client, stocked_material):
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'], "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "location": "ZZZ-999", "location_fuzzy": True,
        })
        data = resp.json()
        assert data['success'] is False
        assert data['error'] == 'location_not_found'

    def test_atomic_batch_update_remaining_correct(self, admin_client, stocked_material):
        """Verify atomic batch update produces correct remaining value."""
        from database import get_db_connection

        # Initial batch2 quantity
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT quantity FROM batches WHERE batch_no = ?',
            (stocked_material['batch2_no'],)
        )
        initial_qty = cursor.fetchone()['quantity']
        conn.close()

        # Stock out 5 from batch2
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 5,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch2_no'],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert len(data['batch_consumptions']) == 1
        assert data['batch_consumptions'][0]['batch_no'] == stocked_material['batch2_no']
        assert data['batch_consumptions'][0]['quantity'] == 5
        expected_remaining = max(initial_qty - 5, 0)
        assert data['batch_consumptions'][0]['remaining'] == expected_remaining

        # Verify batch_consumptions table has exactly 1 entry for this record
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT COUNT(*) as cnt FROM batch_consumptions WHERE batch_id IN '
            '(SELECT id FROM batches WHERE batch_no = ?)',
            (stocked_material['batch2_no'],)
        )
        count = cursor.fetchone()['cnt']
        conn.close()
        assert count == 1  # Only this consumption, no duplicates


class TestAddRecordBatchNoForwarding:
    """Test that /api/inventory/add-record forwards batch_no for 'out' operations."""

    def test_add_record_out_forwards_batch_no(self, admin_client, stocked_material):
        """add-record with type=out + batch_no should bypass FIFO."""
        resp = admin_client.post("/api/inventory/add-record", json={
            "product_name": stocked_material['name'],
            "type": "out",
            "quantity": 5,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
            "batch_no": stocked_material['batch2_no'],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert len(data['batch_consumptions']) == 1
        assert data['batch_consumptions'][0]['batch_no'] == stocked_material['batch2_no']
        assert data['batch_consumptions'][0]['quantity'] == 5

    def test_add_record_out_without_batch_no_uses_fifo(self, admin_client, stocked_material):
        """add-record with type=out but no batch_no should use FIFO (pick batch1)."""
        resp = admin_client.post("/api/inventory/add-record", json={
            "product_name": stocked_material['name'],
            "type": "out",
            "quantity": 5,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert len(data['batch_consumptions']) == 1
        assert data['batch_consumptions'][0]['batch_no'] == stocked_material['batch1_no']
        assert data['batch_consumptions'][0]['quantity'] == 5


# ===========================================================================
# Stock-out edge cases (added for SQLAlchemy migration safety net).
# Pin down the *current* behavior — do not "fix" anything found here.
# ===========================================================================

import uuid as _uuid


class TestStockOutEdgeCases:
    """Edge cases that lock down current behavior before the SA migration."""

    def test_stock_out_negative_quantity_rejected(self, admin_client,
                                                  stocked_material):
        """quantity < 0 must be rejected with success=False (mirrors zero
        case)."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": -5,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_out_overdraw_rejected(self, admin_client, stocked_material):
        """Stock-out > current stock must be rejected ('库存不足')."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": stocked_material['total_quantity'] + 100,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False
        # Pin down the message
        msg = data.get('error', '') + data.get('message', '')
        assert "库存不足" in msg, f"unexpected error: {data}"

    def test_stock_out_rejects_disabled_material(
            self, admin_client, default_warehouse_id):
        """Disabled materials must not accept stock-out (regression for the
        previous bug where the SELECT did not filter is_disabled).
        """
        from database import get_db_connection
        name = f"DisMat-{_uuid.uuid4().hex[:6]}"
        sku = f"DS-{_uuid.uuid4().hex[:6]}"
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO materials (name, sku, category, quantity, unit, "
            "safe_stock, location, warehouse_id, is_disabled) "
            "VALUES (?, ?, 'T', 50, 'pcs', 1, '', ?, 1)",
            (name, sku, default_warehouse_id))
        mid = cur.lastrowid
        cur.execute(
            "INSERT INTO batches (batch_no, material_id, quantity, "
            "initial_quantity, warehouse_id, created_at) "
            "VALUES (?, ?, 50, 50, ?, CURRENT_TIMESTAMP)",
            (f"B-{sku}", mid, default_warehouse_id))
        conn.commit()
        conn.close()

        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": name,
            "quantity": 1,
            "reason_category": "sell",
            "warehouse_id": default_warehouse_id,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False, (
            f"Stock-out should reject disabled material; got: {data}"
        )
        # The disabled material must not be selected. Acceptable rejections:
        # - 'not found' when no other materials exist
        # - 'ambiguous_name' when fuzzy matcher offers other (enabled) candidates
        # In either case, the disabled material itself must not appear in candidates.
        candidate_names = [c.get('name') for c in (data.get('candidates') or [])]
        assert name not in candidate_names, (
            f"Disabled material leaked into fuzzy candidates: {data}"
        )

    def test_stock_out_cross_tenant_material_via_api_key(
            self, admin_client, app_instance, monkeypatch):
        """An API key for tenant A cannot stock-out a material that lives in
        tenant B. The expected status is 403 (warehouse access) or 400
        (warehouse mismatch)."""
        monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")

        # Promote admin to global, build two tenants
        from database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
        conn.commit()
        conn.close()

        try:
            suffix = _uuid.uuid4().hex[:6]
            t_a = admin_client.post("/api/tenants", json={
                "slug": f"sa-{suffix}", "name": f"SA{suffix}"}).json()['id']
            t_b = admin_client.post("/api/tenants", json={
                "slug": f"sb-{suffix}", "name": f"SB{suffix}"}).json()['id']
            wh_a = admin_client.post("/api/warehouses", json={
                "slug": f"swa-{suffix}", "name": f"SWA{suffix}",
                "tenant_id": t_a}).json()['id']
            wh_b = admin_client.post("/api/warehouses", json={
                "slug": f"swb-{suffix}", "name": f"SWB{suffix}",
                "tenant_id": t_b}).json()['id']

            # Material in tenant B's warehouse
            mat_name = f"BMat-{suffix}"
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO materials (name, sku, category, quantity, "
                "unit, safe_stock, warehouse_id, tenant_id) "
                "VALUES (?, ?, 'T', 50, 'pcs', 1, ?, ?)",
                (mat_name, f"bm-{suffix}", wh_b, t_b))
            mid_b = cur.lastrowid
            cur.execute(
                "INSERT INTO batches (batch_no, material_id, quantity, "
                "initial_quantity, warehouse_id, tenant_id, created_at) "
                "VALUES (?, ?, 50, 50, ?, ?, CURRENT_TIMESTAMP)",
                (f"BB-{suffix}", mid_b, wh_b, t_b))
            conn.commit()
            conn.close()

            # API key bound to tenant A's warehouse
            info = admin_client.post("/api/api-keys", json={
                "name": f"k-{suffix}", "role": "operate",
                "warehouse_id": wh_a,
            }).json()

            from fastapi.testclient import TestClient
            c = TestClient(app_instance)

            # Try to stock-out tenant B's material via tenant A's key,
            # specifying tenant B's warehouse — must be denied.
            resp = c.post("/api/materials/stock-out",
                          headers={"X-API-Key": info['key']},
                          json={
                              "product_name": mat_name,
                              "quantity": 1,
                              "reason_category": "sell",
                              "warehouse_id": wh_b,
                          })
            assert resp.status_code in (400, 403, 404), (
                f"cross-tenant stock-out should be rejected, got "
                f"{resp.status_code}: {resp.text}")
        finally:
            # Always restore admin tenant
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(
                "UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
            conn.commit()
            conn.close()

    def test_stock_out_fifo_decrements_each_batch_correctly(
            self, admin_client, stocked_material):
        """When stock-out spans multiple batches, each batch's `quantity`
        column is decremented by exactly the consumed amount. Lock this down
        because the SA migration will swap the UPDATE into ORM."""
        from database import get_db_connection

        # Capture initial batch quantities
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT batch_no, quantity FROM batches WHERE batch_no IN (?, ?)",
                    (stocked_material['batch1_no'], stocked_material['batch2_no']))
        before = {r['batch_no']: r['quantity'] for r in cur.fetchall()}
        conn.close()

        # Consume 35 → 30 from batch1 (full), 5 from batch2
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 35,
            "reason_category": "sell",
            "warehouse_id": stocked_material['warehouse_id'],
        })
        assert resp.status_code == 200
        assert resp.json()['success'] is True

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT batch_no, quantity FROM batches WHERE batch_no IN (?, ?)",
                    (stocked_material['batch1_no'], stocked_material['batch2_no']))
        after = {r['batch_no']: r['quantity'] for r in cur.fetchall()}
        conn.close()

        # batch1 fully drained (was 30 → now 0)
        assert after[stocked_material['batch1_no']] == \
            before[stocked_material['batch1_no']] - 30
        # batch2 lost 5 (was 20 → now 15)
        assert after[stocked_material['batch2_no']] == \
            before[stocked_material['batch2_no']] - 5
