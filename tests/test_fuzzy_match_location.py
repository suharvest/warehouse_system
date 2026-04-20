"""Scoped location fuzzy match tests."""
import pytest


@pytest.fixture()
def scoped_material(admin_client, default_warehouse_id):
    """Material with three batches in distinct locations."""
    import uuid
    from database import get_db_connection

    sku = f"LOC-{uuid.uuid4().hex[:8].upper()}"
    name = f"Location Test Material {sku}"

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (name, sku, 'Test', 0, 'pcs', 10, '', default_warehouse_id))
    material_id = cursor.lastrowid
    conn.commit()
    conn.close()

    for loc in ['A-01', 'A-02', 'B-10']:
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": name, "quantity": 10,
            "reason_category": "purchase",
            "warehouse_id": default_warehouse_id,
            "location": loc,
        })
        assert resp.json()['success'] is True

    return {
        'material_id': material_id,
        'warehouse_id': default_warehouse_id,
        'locations': ['A-01', 'A-02', 'B-10'],
    }


class TestResolveLocationInScope:
    def test_exact_match_confident(self, scoped_material):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'A-01',
        )
        assert result['confident'] is True
        assert result['best_match']['name'] == 'A-01'

    def test_partial_match_confident(self, scoped_material):
        """'A01' (no dash) should still confidently resolve to A-01."""
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'A01',
        )
        assert result['confident'] is True
        assert result['best_match']['name'] == 'A-01'

    def test_ambiguous_returns_candidates(self, scoped_material):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'A',
        )
        # 'A' matches both A-01 and A-02 with equal footing
        assert result['confident'] is False
        names = [c['name'] for c in result['candidates']]
        assert 'A-01' in names and 'A-02' in names

    def test_no_match(self, scoped_material):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(
            scoped_material['material_id'],
            scoped_material['warehouse_id'],
            'ZZZZ-999',
        )
        assert result['confident'] is False
        assert result['best_match'] is None

    def test_empty_scope_returns_empty(self, admin_client, default_warehouse_id):
        """Material with no batches → no candidates."""
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        import uuid
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (f"Empty-{uuid.uuid4().hex[:8]}", f"E-{uuid.uuid4().hex[:8]}",
              'Test', 0, 'pcs', default_warehouse_id))
        mid = cursor.lastrowid
        conn.commit()
        conn.close()

        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_location_in_scope(mid, default_warehouse_id, 'A-01')
        assert result['confident'] is False
        assert result['best_match'] is None
        assert result['candidates'] == []
