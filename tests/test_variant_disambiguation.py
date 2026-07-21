"""同名多规格物料歧义消解测试。

覆盖：
- fuzzy 索引 material 候选携带 extra.variants
- FuzzyMatcher.resolve_variant_in_scope 精确/变体/歧义
- stock_out 同名多行 + variant 精确选料 / ambiguous_name / variant 归一
- stock_in 同名多行 + variant 选料
- product-stats / batches 按 material_id 查询
- _wrap_response 歧义分支 say 文案带规格
"""
import importlib
import sys
import uuid
from pathlib import Path

import pytest


def _insert_batch_sql(material_id, warehouse_id, variant, qty):
    """直插批次（绕过 stock-in 的 variant 归一，用于构造相近规格并存的场景）。"""
    from database import get_db_connection
    batch_no = f"TB-{uuid.uuid4().hex[:10].upper()}"
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO batches (batch_no, material_id, quantity, initial_quantity,
                             is_exhausted, warehouse_id, created_at, location,
                             variant, tenant_id)
        VALUES (?, ?, ?, ?, 0, ?, datetime('now'), '', ?, 1)
    ''', (batch_no, material_id, qty, qty, warehouse_id, variant))
    conn.commit()
    conn.close()
    return batch_no


def _insert_material_sql(name, sku, warehouse_id):
    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
        VALUES (?, ?, 'Test', 0, 'pcs', 0, '', ?)
    ''', (name, sku, warehouse_id))
    mid = cursor.lastrowid
    conn.commit()
    conn.close()
    return mid


def _import_warehouse_mcp():
    mcp_dir = Path(__file__).resolve().parents[1] / "mcp"
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    return importlib.import_module("warehouse_mcp")


@pytest.fixture()
def dual_variant_materials(admin_client, default_warehouse_id):
    """两个同名物料（SKU 不同），各自有不同 variant 的批次。"""
    from database import get_db_connection

    tag = uuid.uuid4().hex[:8].upper()
    name = f"M3螺丝{tag}"
    sku_a, sku_b = f"VDA-{tag}", f"VDB-{tag}"

    conn = get_db_connection()
    cursor = conn.cursor()
    ids = []
    for sku in (sku_a, sku_b):
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (name, sku, 'Test', 0, 'pcs', 0, '', default_warehouse_id))
        ids.append(cursor.lastrowid)
    conn.commit()
    conn.close()

    # 用 SKU 精确入库（同名多行时 name 会歧义），带 variant 建批次
    for sku, variant in ((sku_a, '黑色 10mm'), (sku_b, '白色 20mm')):
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": sku, "quantity": 30,
            "reason_category": "purchase",
            "warehouse_id": default_warehouse_id,
            "variant": variant,
        })
        assert resp.json()['success'] is True, resp.json()

    return {
        'name': name,
        'sku_a': sku_a, 'sku_b': sku_b,
        'id_a': ids[0], 'id_b': ids[1],
        'variant_a': '黑色 10mm', 'variant_b': '白色 20mm',
        'warehouse_id': default_warehouse_id,
    }


class TestSearchCandidatesCarryVariants:
    def test_extra_variants_populated(self, dual_variant_materials):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        m = dual_variant_materials
        matcher = FuzzyMatcher(get_db_connection)
        results = matcher.search(m['name'], entity_type="material")
        by_id = {r['entity_id']: r for r in results}
        assert m['id_a'] in by_id and m['id_b'] in by_id
        assert by_id[m['id_a']]['extra'].get('variants') == [m['variant_a']]
        assert by_id[m['id_b']]['extra'].get('variants') == [m['variant_b']]

    def test_combined_entry_extra_has_variants(self, dual_variant_materials):
        """name+variant 组合条目命中时，extra 同时含 variant 单值和 variants 列表。"""
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        m = dual_variant_materials
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve(f"{m['name']} {m['variant_a']}", entity_type="material")
        assert result['confident'] is True
        extra = result['best_match']['extra']
        assert extra.get('variant') == m['variant_a']
        assert extra.get('variants') == [m['variant_a']]


class TestResolveVariantInScope:
    def test_exact_confident(self, dual_variant_materials):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        m = dual_variant_materials
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_variant_in_scope(
            m['id_a'], m['warehouse_id'], m['variant_a'])
        assert result['confident'] is True
        assert result['best_match']['name'] == m['variant_a']
        assert result['best_match']['entity_type'] == 'variant'

    def test_space_difference_confident(self, dual_variant_materials):
        """'黑色10mm'（无空格）应 confident 归一到库内 '黑色 10mm'。"""
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        m = dual_variant_materials
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_variant_in_scope(
            m['id_a'], m['warehouse_id'], '黑色10mm')
        assert result['confident'] is True
        assert result['best_match']['name'] == m['variant_a']

    def test_warehouse_none_no_filter(self, dual_variant_materials):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        m = dual_variant_materials
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_variant_in_scope(m['id_a'], None, m['variant_a'])
        assert result['confident'] is True

    def test_multiple_close_variants_not_confident(
            self, admin_client, default_warehouse_id):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection

        tag = uuid.uuid4().hex[:8].upper()
        sku = f"VDC-{tag}"
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (f"歧义规格料{tag}", sku, 'Test', 0, 'pcs', 0, '', default_warehouse_id))
        mid = cursor.lastrowid
        conn.commit()
        conn.close()
        for variant in ('黑色 10mm', '黑色 12mm'):
            _insert_batch_sql(mid, default_warehouse_id, variant, 5)

        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_variant_in_scope(mid, default_warehouse_id, '黑色')
        assert result['confident'] is False
        assert len(result['candidates']) == 2

    def test_no_match_returns_empty(self, dual_variant_materials):
        from fuzzy_match import FuzzyMatcher
        from database import get_db_connection
        m = dual_variant_materials
        matcher = FuzzyMatcher(get_db_connection)
        result = matcher.resolve_variant_in_scope(
            m['id_a'], m['warehouse_id'], '完全无关规格XYZW')
        assert result['confident'] is False
        assert result['candidates'] == []


class TestStockOutVariantDisambiguation:
    def _stats(self, admin_client, material_id, wh_id):
        r = admin_client.get("/api/materials/product-stats",
                             params={"material_id": material_id, "warehouse_id": wh_id})
        assert r.status_code == 200
        return r.json()

    def test_variant_selects_correct_material(self, admin_client, dual_variant_materials):
        m = dual_variant_materials
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['name'], "quantity": 5,
            "reason_category": "consume",
            "warehouse_id": m['warehouse_id'],
            "variant": m['variant_a'],
        }).json()
        assert resp['success'] is True, resp
        assert self._stats(admin_client, m['id_a'], m['warehouse_id'])['current_stock'] == 25
        assert self._stats(admin_client, m['id_b'], m['warehouse_id'])['current_stock'] == 30

    def test_variant_space_difference_normalized(self, admin_client, dual_variant_materials):
        """语音表述 '白色20mm'（无空格）→ 归一到库内 '白色 20mm' 后成功扣减。"""
        m = dual_variant_materials
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['name'], "quantity": 3,
            "reason_category": "consume",
            "warehouse_id": m['warehouse_id'],
            "variant": "白色20mm",
        }).json()
        assert resp['success'] is True, resp
        assert self._stats(admin_client, m['id_b'], m['warehouse_id'])['current_stock'] == 27
        assert self._stats(admin_client, m['id_a'], m['warehouse_id'])['current_stock'] == 30

    def test_no_variant_ambiguous_with_variants_in_candidates(
            self, admin_client, dual_variant_materials):
        m = dual_variant_materials
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['name'], "quantity": 5,
            "reason_category": "consume",
            "warehouse_id": m['warehouse_id'],
        }).json()
        assert resp['success'] is False
        assert resp['error'] == 'ambiguous_name'
        cands = resp['candidates']
        assert len(cands) == 2
        variants = sorted(v for c in cands for v in c['extra']['variants'])
        assert variants == sorted([m['variant_a'], m['variant_b']])
        by_id = {c['entity_id']: c for c in cands}
        assert by_id[m['id_a']]['extra']['sku'] == m['sku_a']
        # message 里列出 名称+规格
        assert m['variant_a'] in resp['message']
        assert m['variant_b'] in resp['message']
        # 没扣任何库存
        assert self._stats(admin_client, m['id_a'], m['warehouse_id'])['current_stock'] == 30
        assert self._stats(admin_client, m['id_b'], m['warehouse_id'])['current_stock'] == 30

    def test_variant_ambiguous_within_material(self, admin_client, default_warehouse_id):
        """单一物料下多个相近规格 + 模糊 variant → variant_ambiguous。"""
        from database import get_db_connection
        tag = uuid.uuid4().hex[:8].upper()
        sku = f"VDD-{tag}"
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (f"规格歧义出库料{tag}", sku, 'Test', 0, 'pcs', 0, '', default_warehouse_id))
        mid = cursor.lastrowid
        conn.commit()
        conn.close()
        for variant in ('黑色 10mm', '黑色 12mm'):
            _insert_batch_sql(mid, default_warehouse_id, variant, 5)

        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": sku, "quantity": 2,
            "reason_category": "consume",
            "warehouse_id": default_warehouse_id,
            "variant": "黑色",
        }).json()
        assert resp['success'] is False
        assert resp['error'] == 'variant_ambiguous'
        names = [c['name'] for c in resp['candidates']]
        assert sorted(names) == ['黑色 10mm', '黑色 12mm']
        assert '黑色 10mm' in resp['message']


class TestStockInVariantDisambiguation:
    def test_variant_selects_correct_material(self, admin_client, dual_variant_materials):
        m = dual_variant_materials
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": m['name'], "quantity": 7,
            "reason_category": "purchase",
            "warehouse_id": m['warehouse_id'],
            "variant": "黑色10mm",  # 无空格，应归一到 '黑色 10mm' 并选中料 A
        }).json()
        assert resp['success'] is True, resp
        assert resp['batch']['variant'] == m['variant_a']
        r = admin_client.get("/api/materials/product-stats",
                             params={"material_id": m['id_a'],
                                     "warehouse_id": m['warehouse_id']})
        assert r.json()['current_stock'] == 37

    def test_no_variant_ambiguous(self, admin_client, dual_variant_materials):
        m = dual_variant_materials
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": m['name'], "quantity": 7,
            "reason_category": "purchase",
            "warehouse_id": m['warehouse_id'],
        }).json()
        assert resp['success'] is False
        assert resp['error'] == 'ambiguous_name'
        assert len(resp['candidates']) == 2
        assert resp['candidates'][0]['extra']['variants']

    def test_new_variant_not_blocked_when_no_similar(self, admin_client, dual_variant_materials):
        """库内无相近规格时，直接放行创建新规格。"""
        m = dual_variant_materials
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": m['sku_a'], "quantity": 4,
            "reason_category": "purchase",
            "warehouse_id": m['warehouse_id'],
            "variant": "超长加固版",
        }).json()
        assert resp['success'] is True, resp
        assert resp['batch']['variant'] == '超长加固版'


class TestStockInNewVariantConfirm:
    """入库 variant 归一不 confident 且库内有相近规格 → 先追问（variant_ambiguous）。"""

    @pytest.fixture()
    def close_variant_material(self, admin_client, default_warehouse_id):
        tag = uuid.uuid4().hex[:8].upper()
        sku = f"VDE-{tag}"
        mid = _insert_material_sql(f"近似规格入库料{tag}", sku, default_warehouse_id)
        for variant in ('黑色 10mm', '黑色 12mm'):
            _insert_batch_sql(mid, default_warehouse_id, variant, 5)
        return {'sku': sku, 'id': mid, 'warehouse_id': default_warehouse_id}

    def test_similar_new_variant_asks_first(self, admin_client, close_variant_material):
        m = close_variant_material
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": m['sku'], "quantity": 3,
            "reason_category": "purchase",
            "warehouse_id": m['warehouse_id'],
            "variant": "黑色 11mm",
        }).json()
        assert resp['success'] is False
        assert resp['error'] == 'variant_ambiguous'
        names = sorted(c['name'] for c in resp['candidates'])
        assert names == ['黑色 10mm', '黑色 12mm']
        assert "新建规格 '黑色 11mm'" in resp['message']

    def test_allow_new_variant_true_passes(self, admin_client, close_variant_material):
        m = close_variant_material
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": m['sku'], "quantity": 3,
            "reason_category": "purchase",
            "warehouse_id": m['warehouse_id'],
            "variant": "黑色 11mm",
            "allow_new_variant": True,
        }).json()
        assert resp['success'] is True, resp
        assert resp['batch']['variant'] == '黑色 11mm'

    def test_existing_variant_normal_stock_in(self, admin_client, close_variant_material):
        m = close_variant_material
        resp = admin_client.post("/api/materials/stock-in", json={
            "product_name": m['sku'], "quantity": 3,
            "reason_category": "purchase",
            "warehouse_id": m['warehouse_id'],
            "variant": "黑色 10mm",
        }).json()
        assert resp['success'] is True, resp
        assert resp['batch']['variant'] == '黑色 10mm'


class TestStockOutBatchNoDisambiguation:
    def test_batch_no_selects_material_among_same_names(
            self, admin_client, dual_variant_materials):
        """同名多料 + batch_no：批次归属唯一定位物料，直接出对料，不返回 ambiguous。"""
        m = dual_variant_materials
        r = admin_client.get("/api/materials/batches",
                             params={"material_id": m['id_b'],
                                     "warehouse_id": m['warehouse_id']})
        bn = r.json()['batches'][0]['batch_no']
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['name'], "quantity": 5,
            "reason_category": "consume",
            "warehouse_id": m['warehouse_id'],
            "batch_no": bn,
        }).json()
        assert resp['success'] is True, resp
        stats_b = admin_client.get("/api/materials/product-stats",
                                   params={"material_id": m['id_b'],
                                           "warehouse_id": m['warehouse_id']}).json()
        stats_a = admin_client.get("/api/materials/product-stats",
                                   params={"material_id": m['id_a'],
                                           "warehouse_id": m['warehouse_id']}).json()
        assert stats_b['current_stock'] == 25
        assert stats_a['current_stock'] == 30

    def test_unknown_batch_no_still_ambiguous(self, admin_client, dual_variant_materials):
        m = dual_variant_materials
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['name'], "quantity": 5,
            "reason_category": "consume",
            "warehouse_id": m['warehouse_id'],
            "batch_no": "TB-DOES-NOT-EXIST",
        }).json()
        assert resp['success'] is False
        assert resp['error'] == 'ambiguous_name'


class TestFallbackVariantScoped:
    """指定批次不足确认补扣时，补扣只能来自同规格/同库位批次。"""

    @pytest.fixture()
    def fallback_material(self, admin_client, default_warehouse_id):
        tag = uuid.uuid4().hex[:8].upper()
        sku = f"VDF-{tag}"
        mid = _insert_material_sql(f"补扣规格料{tag}", sku, default_warehouse_id)
        b1 = _insert_batch_sql(mid, default_warehouse_id, '黑色 10mm', 2)
        b2 = _insert_batch_sql(mid, default_warehouse_id, '黑色 10mm', 10)
        b3 = _insert_batch_sql(mid, default_warehouse_id, '白色 20mm', 50)
        return {'sku': sku, 'id': mid, 'warehouse_id': default_warehouse_id,
                'b1': b1, 'b2': b2, 'b3': b3}

    def test_fallback_precheck_counts_same_variant_only(
            self, admin_client, fallback_material):
        m = fallback_material
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['sku'], "quantity": 5,
            "reason_category": "consume",
            "warehouse_id": m['warehouse_id'],
            "batch_no": m['b1'],
            "variant": "黑色 10mm",
        }).json()
        assert resp['success'] is False
        assert resp['error'] == 'batch_insufficient_stock'
        # 其他批次可用量只算同规格的 b2（10），不含 b3 的 50
        assert resp['fallback_total_available'] == 10
        assert resp['can_fallback'] is True

    def test_fallback_consumes_same_variant_batches_only(
            self, admin_client, fallback_material):
        m = fallback_material
        resp = admin_client.post("/api/materials/stock-out", json={
            "product_name": m['sku'], "quantity": 5,
            "reason_category": "consume",
            "warehouse_id": m['warehouse_id'],
            "batch_no": m['b1'],
            "variant": "黑色 10mm",
            "allow_partial_fallback": True,
        }).json()
        assert resp['success'] is True, resp
        consumed = {bc['batch_no']: bc['quantity'] for bc in resp['batch_consumptions']}
        assert consumed == {m['b1']: 2, m['b2']: 3}
        # 白色 20mm 的 b3 未被动过
        r = admin_client.get("/api/materials/batches",
                             params={"material_id": m['id'],
                                     "warehouse_id": m['warehouse_id']}).json()
        by_no = {b['batch_no']: b for b in r['batches']}
        assert by_no[m['b3']]['quantity'] == 50


class TestQueryStockVariantFiltered:
    """query_stock 命中 name+variant 组合时，product.current_stock 必须是规格过滤后库存。"""

    def _provider(self):
        _import_warehouse_mcp()
        from providers.default import DefaultProvider

        class P(DefaultProvider):
            def __init__(self):
                pass

            def http_get(self, path, params=None):
                params = params or {}
                if path == "/materials/product-stats":
                    if params.get("material_id") == 42:
                        return {"name": "M3螺丝", "sku": "SKU-A",
                                "current_stock": 50, "unit": "个",
                                "safe_stock": None, "location": ""}
                    return {"error": "not found"}
                if path == "/fuzzy-match":
                    return {
                        "confident": True,
                        "best_match": {
                            "name": "M3螺丝 黑色 10mm", "score": 96.0,
                            "entity_type": "material", "entity_id": 42,
                            "extra": {"sku": "SKU-A", "canonical_name": "M3螺丝",
                                      "variant": "黑色 10mm",
                                      "variants": ["白色 20mm", "黑色 10mm"]},
                        },
                        "candidates": [],
                    }
                if path == "/materials/batches":
                    assert params.get("material_id") == 42
                    return {"batches": [
                        {"batch_no": "B1", "quantity": 30, "location": "",
                         "variant": "黑色 10mm", "contact_name": "", "created_at": ""},
                        {"batch_no": "B2", "quantity": 20, "location": "",
                         "variant": "白色 20mm", "contact_name": "", "created_at": ""},
                    ], "total_quantity": 50}
                return {"error": "unexpected"}

        return P()

    def test_product_current_stock_is_variant_filtered(self):
        resp = self._provider().query_stock("M3螺丝 黑色")
        assert resp["success"] is True
        assert resp["product"]["current_stock"] == 30
        assert resp["product"]["variant"] == "黑色 10mm"

    def test_wrap_response_say_uses_filtered_stock_and_variant(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = self._provider().query_stock("M3螺丝 黑色")
        wrapped = warehouse_mcp._wrap_response("query_stock", resp)
        assert wrapped["say"] == "M3螺丝（黑色 10mm）当前库存30个。"
        assert wrapped["data"]["qty"] == 30
        assert wrapped["data"]["variant"] == "黑色 10mm"


class TestMaterialIdQueries:
    def test_product_stats_by_material_id(self, admin_client, dual_variant_materials):
        m = dual_variant_materials
        r = admin_client.get("/api/materials/product-stats",
                             params={"material_id": m['id_b'],
                                     "warehouse_id": m['warehouse_id']})
        assert r.status_code == 200
        data = r.json()
        assert data['sku'] == m['sku_b']
        assert data['current_stock'] == 30

    def test_batches_by_material_id(self, admin_client, dual_variant_materials):
        m = dual_variant_materials
        r = admin_client.get("/api/materials/batches",
                             params={"material_id": m['id_a'],
                                     "warehouse_id": m['warehouse_id']})
        assert r.status_code == 200
        data = r.json()
        assert data['total_quantity'] == 30
        assert data['batches'][0]['variant'] == m['variant_a']

    def test_missing_both_params_400(self, admin_client):
        assert admin_client.get("/api/materials/product-stats").status_code == 400
        assert admin_client.get("/api/materials/batches").status_code == 400

    def test_unknown_material_id_404(self, admin_client):
        r = admin_client.get("/api/materials/product-stats",
                             params={"material_id": 99999999})
        assert r.status_code == 404


class TestWrapResponseSpec:
    def test_stock_out_ambiguous_say_contains_spec(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("stock_out", {
            "success": False,
            "error": "ambiguous_name",
            "message": "无法确定产品",
            "candidates": [
                {"name": "M3螺丝", "score": None, "entity_type": "material",
                 "entity_id": 1, "extra": {"sku": "SKU-A", "variants": ["黑色 10mm"]}},
                {"name": "M3螺丝", "score": None, "entity_type": "material",
                 "entity_id": 2, "extra": {"sku": "SKU-B", "variants": ["白色 20mm"]}},
            ],
        })
        assert resp["ok"] is False
        assert resp["say_kind"] == "ask"
        assert "黑色 10mm" in resp["say"]
        assert "白色 20mm" in resp["say"]
        # 有规格时优先播报规格，不再播报 SKU
        assert "SKU-A" not in resp["say"]
        assert resp["data"]["candidates"][0]["spec"] == "黑色 10mm"

    def test_candidate_without_spec_falls_back_to_sku(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("stock_in", {
            "success": False,
            "error": "ambiguous_name",
            "candidates": [
                {"name": "扎带", "score": 88.0, "entity_type": "material",
                 "entity_id": 3, "extra": {"sku": "ZD-01", "variants": []}},
            ],
        })
        assert "ZD-01" in resp["say"]
        assert resp["say_kind"] == "ask"

    def test_variant_ambiguous_is_ask(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("stock_out", {
            "success": False,
            "error": "variant_ambiguous",
            "message": "规格不明确",
            "candidates": [
                {"name": "黑色 10mm", "score": 95.0, "entity_type": "variant",
                 "entity_id": None, "extra": {}},
                {"name": "黑色 12mm", "score": 95.0, "entity_type": "variant",
                 "entity_id": None, "extra": {}},
            ],
        })
        assert resp["say_kind"] == "ask"
        assert "黑色 10mm" in resp["say"]

    def test_stock_in_variant_ambiguous_asks_with_patch(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("stock_in", {
            "success": False,
            "error": "variant_ambiguous",
            "message": "库里已有相近规格",
            "candidates": [
                {"name": "黑色 10mm", "score": 83.0, "entity_type": "variant",
                 "entity_id": None, "extra": {}},
                {"name": "黑色 12mm", "score": 83.0, "entity_type": "variant",
                 "entity_id": None, "extra": {}},
            ],
        })
        assert resp["ok"] is False
        assert resp["executed"] is False
        assert resp["say_kind"] == "ask"
        assert "黑色 10mm" in resp["say"] and "黑色 12mm" in resp["say"]
        assert "新建" in resp["say"]
        assert resp["awaiting_confirm"] == {"patch": {"allow_new_variant": True}}

    def test_stock_in_variant_not_excluded_from_schema(self):
        import asyncio
        warehouse_mcp = _import_warehouse_mcp()
        tools = asyncio.run(warehouse_mcp.mcp.get_tools())
        schema = tools["stock_in"].to_mcp_tool().inputSchema
        props = schema.get("properties") or {}
        assert "variant" in props
        # allow_new_variant 不暴露给 LLM，仅供 runtime 带 patch 重发
        assert "allow_new_variant" not in props


class TestQueryStockLocations:
    """query_stock 播报库位："X放在哪" 场景。"""

    def _resp(self, batches):
        return {
            "success": True,
            "product": {"name": "M3螺丝", "current_stock": 500, "unit": "个"},
            "batches": batches,
        }

    def test_single_location_say(self):
        warehouse_mcp = _import_warehouse_mcp()
        wrapped = warehouse_mcp._wrap_response("query_stock", self._resp([
            {"batch_no": "B1", "quantity": 500, "location": "A-01-03"},
        ]))
        assert "位于A-01-03" in wrapped["say"]
        assert wrapped["data"]["locations"] == [{"location": "A-01-03", "qty": 500}]

    def test_multi_location_say_sorted_by_qty(self):
        warehouse_mcp = _import_warehouse_mcp()
        wrapped = warehouse_mcp._wrap_response("query_stock", self._resp([
            {"batch_no": "B1", "quantity": 200, "location": "B-02-01"},
            {"batch_no": "B2", "quantity": 300, "location": "A-01-03"},
            {"batch_no": "B3", "quantity": 100, "location": "A-01-03"},
        ]))
        assert "分布在A-01-03(400个)、B-02-01(200个)" in wrapped["say"]
        assert wrapped["data"]["locations"][0] == {"location": "A-01-03", "qty": 400}

    def test_no_batch_location_falls_back_to_material_location(self):
        warehouse_mcp = _import_warehouse_mcp()
        wrapped = warehouse_mcp._wrap_response("query_stock", {
            "success": True,
            "product": {"name": "M3螺丝", "current_stock": 500, "unit": "个",
                        "location": "老仓货架3"},
            "batches": [],
        })
        assert "位于老仓货架3" in wrapped["say"]
        assert wrapped["data"]["locations"] == [{"location": "老仓货架3", "qty": None}]

    def test_no_location_anywhere_say_unchanged(self):
        warehouse_mcp = _import_warehouse_mcp()
        wrapped = warehouse_mcp._wrap_response("query_stock", self._resp([
            {"batch_no": "B1", "quantity": 500, "location": ""},
        ]))
        assert "位于" not in wrapped["say"] and "分布在" not in wrapped["say"]
        assert "locations" not in wrapped["data"]

    def test_variant_filter_keeps_location_scope(self):
        """规格过滤后 batches 已是过滤后的批次，库位聚合口径一致。"""
        warehouse_mcp = _import_warehouse_mcp()
        wrapped = warehouse_mcp._wrap_response("query_stock", {
            "success": True,
            "product": {"name": "M3螺丝", "current_stock": 300, "unit": "个",
                        "variant": "银色 8mm"},
            "batches": [
                {"batch_no": "B2", "quantity": 300, "location": "A-01-04",
                 "variant": "银色 8mm"},
            ],
        })
        assert "M3螺丝（银色 8mm）" in wrapped["say"]
        assert "位于A-01-04" in wrapped["say"]

    def test_query_stock_tool_requests_batches(self):
        """MCP 工具层必须带 show_batches=True，否则库位信息在 provider 层就丢了。"""
        import inspect
        warehouse_mcp = _import_warehouse_mcp()
        src = inspect.getsource(warehouse_mcp)
        assert "query_stock(product_name, show_batches=True)" in src
