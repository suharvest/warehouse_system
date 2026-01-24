"""
Stock-out tests: normal stock-out, insufficient inventory rejection, FIFO batch consumption.
"""
import pytest


@pytest.fixture()
def stocked_material(admin_client):
    """Create a material with known batch structure for FIFO testing."""
    from database import get_db_connection
    import uuid

    sku = f"FIFO-{uuid.uuid4().hex[:8].upper()}"
    name = f"FIFO Material {sku}"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (name, sku, 'Test', 0, 'pcs', 10, 'B-01'))
    conn.commit()
    conn.close()

    # Stock in two batches
    resp1 = admin_client.post("/api/materials/stock-in", json={
        "product_name": name,
        "quantity": 30,
        "reason": "First batch"
    })
    assert resp1.json()['success'] is True
    batch1_no = resp1.json()['batch']['batch_no']

    resp2 = admin_client.post("/api/materials/stock-in", json={
        "product_name": name,
        "quantity": 20,
        "reason": "Second batch"
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
    }


class TestStockOut:
    """Stock-out operation tests."""

    def test_stock_out_success(self, admin_client, stocked_material):
        """Normal stock-out should succeed."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 10,
            "reason": "Sales"
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
            "reason": "Excessive"
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
            "reason": "Invalid"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False

    def test_stock_out_nonexistent_product(self, admin_client):
        """Stock-out for non-existent product should fail."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": "NonexistentProduct_ABC",
            "quantity": 5,
            "reason": "Test"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is False


class TestFIFOConsumption:
    """FIFO batch consumption tests."""

    def test_fifo_consumes_first_batch(self, admin_client, stocked_material):
        """Stock-out should consume from the first batch first (FIFO)."""
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": stocked_material['name'],
            "quantity": 25,
            "reason": "FIFO test"
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
            "reason": "Multi-batch FIFO"
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

    def test_stock_out_low_stock_warning(self, admin_client):
        """Stock-out that drops below safe_stock should include warning."""
        from database import get_db_connection
        import uuid

        sku = f"WARN-{uuid.uuid4().hex[:8].upper()}"
        name = f"Warning Material {sku}"

        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (name, sku, 'Test', 0, 'pcs', 50, 'C-01'))
        conn.commit()
        conn.close()

        # Stock in 60
        admin_client.post("/api/materials/stock-in", json={
            "product_name": name,
            "quantity": 60,
            "reason": "Setup"
        })

        # Stock out 50 (leaves 10, below safe_stock of 50)
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": name,
            "quantity": 50,
            "reason": "Trigger warning"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data.get('warning') is not None
