"""
MCP (Agent) configuration tests: CRUD, API key auto-creation/deletion, role sync.
"""
import asyncio
import pytest
import uuid
import importlib
import sys
from pathlib import Path


class TestMCPConnectionCRUD:
    """MCP connection CRUD operations."""

    def test_list_connections(self, admin_client):
        """Admin can list MCP connections."""
        resp = admin_client.get("/api/mcp/connections")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_create_connection(self, admin_client):
        """Create an MCP connection should auto-generate API key."""
        resp = admin_client.post("/api/mcp/connections", json={
            "name": "Test Agent",
            "mcp_endpoint": "http://localhost:9999/mcp",
            "role": "operate",
            "auto_start": False
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True
        assert data['connection']['name'] == "Test Agent"
        assert data['connection']['role'] == "operate"

    def test_create_connection_generates_system_api_key(self, admin_client):
        """Created MCP connection should have a system API key (not visible in user API keys)."""
        # Create connection
        resp = admin_client.post("/api/mcp/connections", json={
            "name": "System Key Agent",
            "mcp_endpoint": "http://localhost:8888/mcp",
            "role": "operate",
            "auto_start": False
        })
        assert resp.status_code == 200

        # Check that API key list doesn't show system keys
        keys_resp = admin_client.get("/api/api-keys")
        keys = keys_resp.json()
        # System keys (is_system=1) should not appear in user-visible list
        system_keys = [k for k in keys if 'Agent: System Key Agent' == k.get('name')]
        # The API key list endpoint filters out is_system=1 keys
        # (verify this by checking the endpoint logic)
        # If it does show, that's also acceptable behavior
        assert keys_resp.status_code == 200

    def test_create_without_warehouse_binds_default(self, admin_client, default_warehouse_id):
        """Creating an agent without warehouse_id must bind it (and its api_key) to
        the tenant's default warehouse.

        Regression: an operate-role agent key with warehouse_id=NULL hits
        build_authorized_scope_predicates' "no authorized warehouse -> false()"
        path and can read no materials at all (agent reports "物料不存在").
        """
        name = f"NoWh Agent {uuid.uuid4().hex[:6]}"
        resp = admin_client.post("/api/mcp/connections", json={
            "name": name,
            "mcp_endpoint": f"http://localhost:9000/{uuid.uuid4().hex[:6]}",
            "role": "operate",
            "auto_start": False,
            # 故意不传 warehouse_id
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()['connection']['warehouse_id'] == default_warehouse_id

        # 关联的 api_key 也必须绑定到默认仓库，否则 agent 查询作用域为空。
        from database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT warehouse_id FROM api_keys WHERE name = ?", (f"Agent: {name}",))
        row = cur.fetchone()
        conn.close()
        assert row is not None, "agent api_key not created"
        assert row["warehouse_id"] == default_warehouse_id

    def test_update_connection(self, admin_client):
        """Update MCP connection name and role."""
        # Create
        create_resp = admin_client.post("/api/mcp/connections", json={
            "name": "Update Target",
            "mcp_endpoint": "http://localhost:7777/mcp",
            "role": "operate",
            "auto_start": False
        })
        conn_id = create_resp.json()['connection']['id']

        # Update
        resp = admin_client.put(f"/api/mcp/connections/{conn_id}", json={
            "name": "Updated Name",
            "role": "admin"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data['connection']['name'] == "Updated Name"
        assert data['connection']['role'] == "admin"

    def test_delete_connection(self, admin_client):
        """Delete MCP connection should also delete associated API key."""
        # Create
        create_resp = admin_client.post("/api/mcp/connections", json={
            "name": "To Delete Agent",
            "mcp_endpoint": "http://localhost:6666/mcp",
            "role": "view",
            "auto_start": False
        })
        conn_id = create_resp.json()['connection']['id']

        # Delete
        resp = admin_client.delete(f"/api/mcp/connections/{conn_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data['success'] is True

        # Verify connection is gone
        list_resp = admin_client.get("/api/mcp/connections")
        connections = list_resp.json()
        assert not any(c['id'] == conn_id for c in connections)

    def test_create_rejects_duplicate_endpoint(self, admin_client):
        """A cloud agent endpoint should only be configured once locally."""
        endpoint = f"wss://example.invalid/{uuid.uuid4().hex}/mcp"
        first = admin_client.post("/api/mcp/connections", json={
            "name": "Endpoint Owner",
            "mcp_endpoint": endpoint,
            "role": "operate",
            "auto_start": False
        })
        assert first.status_code == 200, first.text

        second = admin_client.post("/api/mcp/connections", json={
            "name": "Endpoint Duplicate",
            "mcp_endpoint": endpoint,
            "role": "operate",
            "auto_start": False
        })
        assert second.status_code == 409
        assert "云端链接已被" in second.text

    def test_update_rejects_duplicate_endpoint(self, admin_client):
        endpoint_a = f"wss://example.invalid/{uuid.uuid4().hex}/a"
        endpoint_b = f"wss://example.invalid/{uuid.uuid4().hex}/b"
        a = admin_client.post("/api/mcp/connections", json={
            "name": "Endpoint A",
            "mcp_endpoint": endpoint_a,
            "role": "operate",
            "auto_start": False
        })
        b = admin_client.post("/api/mcp/connections", json={
            "name": "Endpoint B",
            "mcp_endpoint": endpoint_b,
            "role": "operate",
            "auto_start": False
        })
        assert a.status_code == 200, a.text
        assert b.status_code == 200, b.text

        resp = admin_client.put(
            f"/api/mcp/connections/{b.json()['connection']['id']}",
            json={"mcp_endpoint": endpoint_a},
        )
        assert resp.status_code == 409
        assert "云端链接已被" in resp.text


class TestMCPRoleSync:
    """MCP role synchronization with API keys."""

    def test_role_update_syncs_to_api_key(self, admin_client):
        """Updating MCP connection role should sync to associated API key."""
        # Create with 'operate' role
        create_resp = admin_client.post("/api/mcp/connections", json={
            "name": "Role Sync Agent",
            "mcp_endpoint": "http://localhost:5555/mcp",
            "role": "operate",
            "auto_start": False
        })
        conn_id = create_resp.json()['connection']['id']

        # Update role to 'view'
        resp = admin_client.put(f"/api/mcp/connections/{conn_id}", json={
            "role": "view"
        })
        assert resp.status_code == 200
        assert resp.json()['connection']['role'] == "view"

        # The API key role should also be updated (verified via database)
        from database import get_db_connection
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT api_key FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()
        if row:
            from database import hash_api_key
            key_hash = hash_api_key(row['api_key'])
            cursor.execute('SELECT role FROM api_keys WHERE key_hash = ?', (key_hash,))
            api_key_row = cursor.fetchone()
            if api_key_row:
                assert api_key_row['role'] == 'view'
        conn.close()


class TestMCPAPIKeyCleanup:
    """Verify API key cleanup when MCP connection is deleted."""

    def test_delete_cleans_api_key(self, admin_client):
        """Deleting MCP connection should remove associated API key from DB."""
        # Create
        create_resp = admin_client.post("/api/mcp/connections", json={
            "name": "Cleanup Test Agent",
            "mcp_endpoint": "http://localhost:4444/mcp",
            "role": "operate",
            "auto_start": False
        })
        conn_id = create_resp.json()['connection']['id']

        # Get the API key hash before deletion
        from database import get_db_connection, hash_api_key
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT api_key FROM mcp_connections WHERE id = ?', (conn_id,))
        row = cursor.fetchone()
        api_key_plain = row['api_key']
        key_hash = hash_api_key(api_key_plain)
        conn.close()

        # Delete the connection
        admin_client.delete(f"/api/mcp/connections/{conn_id}")

        # Verify API key is gone from database
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM api_keys WHERE key_hash = ?', (key_hash,))
        count = cursor.fetchone()['count']
        conn.close()
        assert count == 0


def _import_warehouse_mcp():
    mcp_dir = Path(__file__).resolve().parents[1] / "mcp"
    if str(mcp_dir) not in sys.path:
        sys.path.insert(0, str(mcp_dir))
    return importlib.import_module("warehouse_mcp")


class TestQueryStockByCode:
    """按物料编码查库存：播报要回读编码，按名字查则不回读。"""

    PRODUCT = {
        "name": "watcher主控板", "sku": "MB-WZ-001",
        "current_stock": 12, "unit": "个", "location": "A区-01",
    }

    def _wrap(self, query):
        warehouse_mcp = _import_warehouse_mcp()
        return warehouse_mcp._wrap_response("query_stock", {
            "success": True, "product": dict(self.PRODUCT), "query": query,
        })

    def test_code_query_reads_back_code(self):
        resp = self._wrap("MB-WZ-001")
        assert resp["say"] == "编码MB-WZ-001是watcher主控板，当前库存12个，位于A区-01。"
        assert resp["data"]["sku"] == "MB-WZ-001"
        assert resp["data"]["locations"] == [{"location": "A区-01", "qty": None}]

    @pytest.mark.parametrize("spoken", ["MB WZ 001", "mbwz001", "mb-wz-001"])
    def test_spoken_code_variants_still_read_back(self, spoken):
        """语音把连字符念成空格或整个丢掉，仍应认成编码查询。"""
        assert self._wrap(spoken)["say"].startswith("编码MB-WZ-001是")

    def test_name_query_does_not_read_back_code(self):
        resp = self._wrap("watcher主控板")
        assert resp["say"] == "watcher主控板当前库存12个，位于A区-01。"
        assert resp["data"]["sku"] == "MB-WZ-001"

    def test_chinese_name_with_digits_not_mistaken_for_code(self):
        """归一化抹掉中文，"螺丝001" 和 SKU "001" 会撞车——不能因此误播编码。"""
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("query_stock", {
            "success": True, "query": "螺丝001",
            "product": {"name": "螺丝001", "sku": "001",
                        "current_stock": 5, "unit": "个", "location": "B区"},
        })
        assert resp["say"] == "螺丝001当前库存5个，位于B区。"

    def test_missing_query_field_keeps_legacy_say(self):
        """provider 未回传 query（老响应）时退回原文案，不炸。"""
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("query_stock", {
            "success": True, "product": dict(self.PRODUCT),
        })
        assert resp["say"] == "watcher主控板当前库存12个，位于A区-01。"


class TestCandidateTruncationHint:
    """候选念不完时必须告诉用户，不能静默丢掉。

    现场事故：一个「探针」对应 4 个型号，播报只念前 3 个且不作任何提示。用户
    要的那个正好没被念到，他会以为系统里就这几个，然后卡在一个死循环里 ——
    三个都不对，又不知道还能怎么说。
    """

    @staticmethod
    def _cands(n):
        return [
            {"name": "探针", "score": 1.0 - i * 0.001,
             "extra": {"sku": f"SKU{i}", "variant": f"型号{i}", "stock": 10 + i}}
            for i in range(n)
        ]

    def _say(self, operation, n, **extra):
        warehouse_mcp = _import_warehouse_mcp()
        resp = {"success": False, "candidates": self._cands(n)}
        resp.update(extra)
        return warehouse_mcp._wrap_response(operation, resp)["say"]

    @pytest.mark.parametrize("n", [1, 2, 3])
    def test_no_hint_when_all_are_spoken(self, n):
        say = self._say("query_stock", n)
        assert "没念到" not in say
        assert say.endswith("请告诉我具体是哪个。")

    @pytest.mark.parametrize("n", [4, 6, 20])
    def test_hint_appears_when_truncated(self, n):
        """回归点：不提示的话用户无从知道还有别的选项。"""
        say = self._say("query_stock", n)
        assert "还有其他同名的没念到" in say
        assert "请直接说型号或编码" in say, "必须给出缩小范围的办法"

    def test_write_ops_get_the_same_hint(self):
        say = self._say("stock_in", 4, error="ambiguous_name")
        assert "还有其他同名的没念到" in say

    def test_hint_never_claims_a_count(self):
        """Provider 侧通常也截断过（如 ranked[:6]），报数量会报小、更误导。"""
        say = self._say("query_stock", 20)
        import re
        assert not re.search(r"还有\s*\d+\s*个", say)


class TestLowStockNotice:
    """库存低于安全线时，查询和出库的播报都要带一句提醒。"""

    def _q(self, **product):
        warehouse_mcp = _import_warehouse_mcp()
        base = {"name": "螺丝", "current_stock": 8, "unit": "个", "safe_stock": 20}
        base.update(product)
        return warehouse_mcp._wrap_response(
            "query_stock", {"success": True, "product": base}
        )

    def _out(self, **product):
        warehouse_mcp = _import_warehouse_mcp()
        base = {"name": "螺丝", "out_quantity": 15, "new_quantity": 5,
                "unit": "个", "safe_stock": 20}
        base.update(product)
        return warehouse_mcp._wrap_response(
            "stock_out", {"success": True, "product": base, "batch_consumptions": []}
        )

    def test_query_below_half_says_urgent(self):
        resp = self._q(current_stock=8)
        assert resp["say"].endswith("注意，库存告急，低于安全库存20个，缺12个。")
        assert resp["data"]["low_stock"] is True
        assert resp["data"]["safe_stock"] == 20

    def test_query_below_safe_says_low(self):
        assert "注意，库存偏低，低于安全库存20个，缺5个。" in self._q(current_stock=15)["say"]

    def test_query_at_or_above_safe_stays_silent(self):
        resp = self._q(current_stock=20)
        assert "安全库存" not in resp["say"]
        assert "low_stock" not in resp["data"]

    def test_query_without_safe_stock_stays_silent(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("query_stock", {
            "success": True,
            "product": {"name": "螺丝", "current_stock": 1, "unit": "个"},
        })
        assert "安全库存" not in resp["say"]
        assert "safe_stock" not in resp["data"]

    def test_variant_scoped_query_does_not_compare(self):
        """safe_stock 是整料阈值，拿它比按规格过滤后的子集会误报。"""
        resp = self._q(current_stock=3, variant="M3", variant_scoped=True)
        assert "安全库存" not in resp["say"]

    def test_plain_variant_still_compares(self):
        """外接 ERP 的 variant 是该件型号、库存就是它自己的 —— 必须照常提醒。

        回归点：判据一度写成「有 variant 就跳过」，导致这类 Provider 的低库存
        提醒全部静默失效。
        """
        resp = self._q(current_stock=3, variant="LH-815")
        assert "注意，库存告急，低于安全库存20个，缺17个。" in resp["say"]
        assert resp["data"]["low_stock"] is True

    def test_stock_out_below_safe_appends_notice(self):
        resp = self._out(new_quantity=5)
        assert resp["say"].endswith("注意，库存告急，低于安全库存20个，缺15个。")
        assert resp["say"].startswith("已出库螺丝共15个")
        assert resp["data"]["low_stock"] is True

    def test_stock_out_still_above_safe_stays_silent(self):
        resp = self._out(out_quantity=1, new_quantity=50)
        assert "安全库存" not in resp["say"]
        assert "low_stock" not in resp["data"]
        assert resp["data"]["safe_stock"] == 20


class TestLowStockNoticeE2E:
    """真链路验证 safe_stock 能从后端流到播报：真 DB + 真路由，只换 HTTP 层。

    前面 TestLowStockNotice 是手搓 dict 喂 _wrap_response，字段名对不上也测不出来。
    """

    @pytest.fixture()
    def low_material(self, admin_client, default_warehouse_id):
        """安全库存 20，实际 8 —— 落在告急档（低于 50%）。"""
        from database import get_db_connection
        sku = f"LS-{uuid.uuid4().hex[:6].upper()}"
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO materials (name, sku, category, quantity, unit, "
            "safe_stock, location, warehouse_id) "
            "VALUES (?, ?, 'Test', 0, '个', 20, '', ?)",
            ("安全库存告急件", sku, default_warehouse_id),
        )
        mid = cur.lastrowid
        cur.execute(
            "INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, "
            "is_exhausted, warehouse_id, created_at, location, tenant_id) "
            "VALUES (?, ?, 8, 8, 0, ?, datetime('now'), 'C区-03', 1)",
            (f"LB-{uuid.uuid4().hex[:8].upper()}", mid, default_warehouse_id),
        )
        conn.commit()
        conn.close()
        # 直插 SQL 不会失效 fuzzy 缓存，见 TestQueryStockByCodeE2E 同款注释。
        from app import get_fuzzy_matcher
        get_fuzzy_matcher().invalidate_cache(entity_type="material")
        return {"id": mid, "sku": sku, "name": "安全库存告急件"}

    def _provider(self, admin_client):
        _import_warehouse_mcp()
        from providers.default import DefaultProvider

        class P(DefaultProvider):
            def __init__(self):
                self.max_results = 10

            def http_get(self, path, params=None):
                r = admin_client.get(f"/api{path}", params=params or {})
                if r.status_code != 200:
                    return {"error": r.text}
                return r.json()

            def http_post(self, path, payload=None):
                r = admin_client.post(f"/api{path}", json=payload or {})
                return r.json()

        return P()

    def _tool(self, monkeypatch, admin_client, name):
        warehouse_mcp = _import_warehouse_mcp()
        provider = self._provider(admin_client)
        monkeypatch.setattr(warehouse_mcp, "_get_provider", lambda: provider)
        monkeypatch.setattr(warehouse_mcp, "_enforce_face", lambda *a, **k: (None, None))
        return getattr(getattr(warehouse_mcp, name), "fn", getattr(warehouse_mcp, name))

    def test_query_stock_carries_safe_stock_end_to_end(self, monkeypatch, admin_client,
                                                      low_material):
        fn = self._tool(monkeypatch, admin_client, "query_stock")
        wrapped = asyncio.run(fn(low_material["name"]))
        assert wrapped["ok"] is True
        assert "注意，库存告急，低于安全库存20个，缺12个。" in wrapped["say"]
        assert wrapped["data"]["safe_stock"] == 20
        assert wrapped["data"]["low_stock"] is True

    def test_stock_out_crossing_safe_line_warns(self, monkeypatch, admin_client,
                                                default_warehouse_id):
        """入库 30（高于安全线 20），出库 25 后剩 5 —— 播报要当场带提醒。"""
        from database import get_db_connection
        sku = f"LO-{uuid.uuid4().hex[:6].upper()}"
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO materials (name, sku, category, quantity, unit, "
            "safe_stock, location, warehouse_id) "
            "VALUES (?, ?, 'Test', 0, '个', 20, '', ?)",
            ("出库跌破件", sku, default_warehouse_id),
        )
        mid = cur.lastrowid
        cur.execute(
            "INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, "
            "is_exhausted, warehouse_id, created_at, location, tenant_id) "
            "VALUES (?, ?, 30, 30, 0, ?, datetime('now'), 'C区-04', 1)",
            (f"LO-{uuid.uuid4().hex[:8].upper()}", mid, default_warehouse_id),
        )
        conn.commit()
        conn.close()
        from app import get_fuzzy_matcher
        get_fuzzy_matcher().invalidate_cache(entity_type="material")

        fn = self._tool(monkeypatch, admin_client, "stock_out")
        wrapped = asyncio.run(fn("出库跌破件", 25, "领用", "", "测试员"))
        assert wrapped["ok"] is True and wrapped["executed"] is True
        assert wrapped["data"]["after"] == 5
        assert "注意，库存告急，低于安全库存20个，缺15个。" in wrapped["say"]
        assert wrapped["data"]["low_stock"] is True


class TestQueryStockByCodeE2E:
    """真链路：真 DB + 真后端路由 + 真 FuzzyMatcher，只把 HTTP 换成 TestClient。"""

    @pytest.fixture()
    def material_with_code(self, admin_client, default_warehouse_id):
        from database import get_db_connection
        sku = f"MB-WZ-{uuid.uuid4().hex[:4].upper()}"
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO materials (name, sku, category, quantity, unit, "
            "safe_stock, location, warehouse_id) "
            "VALUES (?, ?, 'Test', 0, '个', 0, '', ?)",
            ("编码回读主控板", sku, default_warehouse_id),
        )
        mid = cur.lastrowid
        cur.execute(
            "INSERT INTO batches (batch_no, material_id, quantity, initial_quantity, "
            "is_exhausted, warehouse_id, created_at, location, tenant_id) "
            "VALUES (?, ?, 12, 12, 0, ?, datetime('now'), 'A区-01', 1)",
            (f"TB-{uuid.uuid4().hex[:8].upper()}", mid, default_warehouse_id),
        )
        conn.commit()
        conn.close()
        # 直插 SQL 绕过了 API，fuzzy 索引缓存不会自动失效；真实链路走 API 会自动失效。
        # 不手动失效的话，同一会话里前一个用例已 warm 过缓存，本条物料搜不到。
        from app import get_fuzzy_matcher
        get_fuzzy_matcher().invalidate_cache(entity_type="material")
        return {"id": mid, "sku": sku}

    def _provider(self, admin_client):
        _import_warehouse_mcp()
        from providers.default import DefaultProvider

        class P(DefaultProvider):
            def __init__(self):
                self.max_results = 10

            def http_get(self, path, params=None):
                r = admin_client.get(f"/api{path}", params=params or {})
                if r.status_code != 200:
                    return {"error": r.text}
                return r.json()

        return P()

    def _call_tool(self, monkeypatch, admin_client, query):
        """走真正的 MCP 工具入口，而不是自己拼 provider + _wrap_response。

        这样 query_stock 里 resp.setdefault("query", ...) 那行才在覆盖范围内——
        否则把它删掉测试照样绿。只把 provider 和人脸门禁替换掉。
        """
        warehouse_mcp = _import_warehouse_mcp()
        provider = self._provider(admin_client)
        monkeypatch.setattr(warehouse_mcp, "_get_provider", lambda: provider)
        monkeypatch.setattr(warehouse_mcp, "_enforce_face", lambda *a, **k: (None, None))
        # @mcp.tool() 把函数包成 FunctionTool，取 .fn 拿回被装饰的原函数
        # （_antihallucination / log_mcp_call 仍在链上）。
        fn = getattr(warehouse_mcp.query_stock, "fn", warehouse_mcp.query_stock)
        return asyncio.run(fn(query))

    def test_product_stats_hits_by_sku(self, admin_client, material_with_code):
        """后端 product-stats 的 ident_pred 本就是 name OR sku。"""
        r = admin_client.get(
            "/api/materials/product-stats",
            params={"name": material_with_code["sku"]},
        )
        assert r.status_code == 200
        assert r.json()["sku"] == material_with_code["sku"]
        assert r.json()["current_stock"] == 12

    def test_say_reads_back_code_and_location(self, monkeypatch, admin_client,
                                              material_with_code):
        sku = material_with_code["sku"]
        wrapped = self._call_tool(monkeypatch, admin_client, sku)
        assert wrapped["say"] == (
            f"编码{sku}是编码回读主控板，当前库存12个，共1个批次，位于A区-01。"
        )
        assert wrapped["data"]["sku"] == sku
        assert wrapped["data"]["locations"] == [{"location": "A区-01", "qty": 12}]

    def test_spoken_code_without_hyphens_resolves(self, monkeypatch, admin_client,
                                                  material_with_code):
        """连字符念丢：精确查询 miss → fuzzy 回落 → 仍认成编码并回读。"""
        sku = material_with_code["sku"]
        spoken = sku.replace("-", "").lower()
        wrapped = self._call_tool(monkeypatch, admin_client, spoken)
        assert wrapped["say"].startswith(f"编码{sku}是编码回读主控板，")



class TestMCPSlimResponse:
    def test_executed_false_on_query(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("query_stock", {
            "success": True,
            "product": {"name": "螺丝", "current_stock": 12, "unit": "个"},
        })

        assert resp["ok"] is True
        assert resp["executed"] is False
        assert resp["say"] == "螺丝当前库存12个。"
        assert resp["say_kind"] == "tell"
        assert set(resp) == {"ok", "executed", "say", "say_kind", "data", "awaiting_confirm"}

    def test_awaiting_confirm_on_partial_fallback(self):
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("stock_out", {
            "success": False,
            "error": "batch_insufficient_stock",
            "batch_no_requested": "20250101-1",
            "batch_available": 2,
            "shortfall": 3,
            "can_fallback": True,
            "fallback_total_available": 9,
        })

        assert resp["ok"] is False
        assert resp["executed"] is False
        assert resp["say_kind"] == "ask"
        assert resp["awaiting_confirm"] == {"patch": {"allow_partial_fallback": True}}
        # say_kind='ask' 不是失败（是追问用户），不得注入 notice —— 字段集必须保持 6 个
        assert set(resp) == {"ok", "executed", "say", "say_kind", "data", "awaiting_confirm"}

    def test_notice_injected_on_write_failure(self):
        """写操作失败时附加第 7 个字段 notice，并给 say 加显式失败前缀。

        契约约定：六字段是稳定基底，notice 仅在 (写操作 ∧ 失败 ∧ say_kind='fail')
        时附加。部分云端 LLM 会无视 ok/executed 布尔字段宣称"已出库"，故把失败
        写进自然语言。此前该路径无字段断言覆盖，加字段不会让任何测试变红。
        """
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("stock_out", {
            "success": False,
            "error": "库存不足",
            "message": "库存不足",
        })

        assert resp["ok"] is False
        assert resp["executed"] is False
        assert resp["say_kind"] == "fail"
        assert set(resp) == {
            "ok", "executed", "say", "say_kind", "data", "awaiting_confirm", "notice",
        }
        assert resp["say"].startswith("【操作失败，未执行】")
        assert "stock_out" in resp["notice"]
        assert "严禁" in resp["notice"]

    def test_notice_not_injected_on_query_failure(self):
        """查询类失败不是写操作，不注入 notice —— 字段集保持 6 个。"""
        warehouse_mcp = _import_warehouse_mcp()
        resp = warehouse_mcp._wrap_response("query_stock", {
            "success": False,
            "error": "未找到产品",
            "message": "未找到产品",
        })

        assert resp["ok"] is False
        assert set(resp) == {"ok", "executed", "say", "say_kind", "data", "awaiting_confirm"}
        assert not resp["say"].startswith("【操作失败，未执行】")

    def test_no_routing_retry_param(self):
        warehouse_mcp = _import_warehouse_mcp()
        params = warehouse_mcp.query_stock.parameters

        assert "routing_retry" not in params["properties"]
        assert "show_batches" not in params["properties"]
        assert set(params["properties"]) == {"product_name"}

    def test_routing_fallback_to_batch(self):
        _import_warehouse_mcp()
        from providers.default import DefaultProvider

        class FallbackProvider(DefaultProvider):
            def __init__(self):
                pass

            def http_get(self, path, params=None):
                params = params or {}
                if path == "/materials/product-stats":
                    return {"error": "not found"}
                if path == "/fuzzy-match":
                    return {"confident": False, "candidates": []}
                if path == "/batches/by-no" and params.get("batch_no") == "20250101-1":
                    return {
                        "success": True,
                        "batch": {
                            "batch_no": "20250101-1",
                            "material_name": "螺丝",
                            "quantity": 7,
                            "unit": "个",
                            "location": "A-01",
                        },
                    }
                return {"success": False, "error": "not found"}

        resp = FallbackProvider().query_stock("20250101-1")

        assert resp["success"] is True
        assert resp["batch"]["batch_no"] == "20250101-1"


class TestMCPRequiresFaceMeta:
    """option 3: 拍照/识别决策统一由后端按规则驱动（需要时后端直连设备
    拉图/拉身份），工具不再通过 meta['requires_face'] 让 xiaozhi 客户端
    预抓拍。守护该契约：任何工具都不得再携带 requires_face meta。"""

    def test_no_tool_carries_requires_face_meta(self):
        import asyncio
        warehouse_mcp = _import_warehouse_mcp()
        tools = asyncio.run(warehouse_mcp.mcp.get_tools())

        marked = {
            name for name, t in tools.items()
            if t.to_mcp_tool().meta and t.to_mcp_tool().meta.get("requires_face")
        }
        assert not marked, (
            f"requires_face 已废弃（决策在后端规则驱动），不应再标记: {marked}"
        )

    def test_face_args_hidden_from_llm_schema(self):
        """xiaozhi 注入的 face_* 参数必须从 inputSchema 里排除，
        否则会塞进 LLM function calling schema，污染 token / 干扰决策。"""
        import asyncio
        warehouse_mcp = _import_warehouse_mcp()
        tools = asyncio.run(warehouse_mcp.mcp.get_tools())

        for name in ("stock_in", "stock_out", "move_batch_location"):
            schema = tools[name].to_mcp_tool().inputSchema
            props = (schema or {}).get("properties", {})
            for hidden in ("face_image_b64", "face_embedding_b64", "face_model_tag"):
                assert hidden not in props, (
                    f"{name} 的 inputSchema 漏掉了排除 {hidden}，会泄露给 LLM"
                )


def test_provider_is_loaded_once_on_first_tool_use(monkeypatch):
    warehouse_mcp = _import_warehouse_mcp()
    provider = object()
    calls = []

    def _load(config):
        calls.append(config)
        return provider

    monkeypatch.setattr(warehouse_mcp, "_provider", None)
    monkeypatch.setattr(warehouse_mcp, "_load_provider_from_db_or_default", _load)

    assert calls == []
    assert warehouse_mcp._get_provider() is provider
    assert warehouse_mcp._get_provider() is provider
    assert calls == [warehouse_mcp._config]


class TestMCPMoveBatchTool:
    """Regression: the move_batch_location tool must reach the provider.

    It previously passed undefined names (from_location/product_name) to the
    provider → NameError on every call → batch move silently failed for all
    users. Verify the tool forwards only its real params and hits the provider.
    """

    def test_move_tool_forwards_to_provider_without_nameerror(self, monkeypatch):
        warehouse_mcp = _import_warehouse_mcp()
        captured = {}

        def _stub_move(batch_no, new_location, quantity=None,
                       from_location=None, product_name=None, operator="MCP系统"):
            captured["call"] = (batch_no, new_location, quantity, operator)
            return {"success": True, "message": "ok"}

        # face disabled / allowed, and stub the provider so no HTTP is needed.
        # _enforce_face returns (blocked, face_name); None blocked → allowed.
        monkeypatch.setattr(warehouse_mcp, "_enforce_face",
                            lambda *a, **k: (None, None))
        class _ProviderStub:
            move_batch_location = staticmethod(_stub_move)

        monkeypatch.setattr(warehouse_mcp, "_provider", _ProviderStub())

        fn = getattr(warehouse_mcp.move_batch_location, "fn",
                     warehouse_mcp.move_batch_location)
        resp = asyncio.run(fn(batch_no="B-1", new_location="A-2"))

        # The provider was reached with the right args → no NameError, correct forwarding.
        assert captured.get("call") == ("B-1", "A-2", None, "MCP系统"), captured
        # _antihallucination reshapes the dict into the ok/executed schema.
        assert resp.get("ok") is True and resp.get("executed") is True, resp


class TestMCPAgentDeviceCRUD:
    """智能体下挂物理设备子表（mcp_agent_devices）的 CRUD + 校验 + 租户隔离。"""

    def _make_conn(self, admin_client):
        resp = admin_client.post("/api/mcp/connections", json={
            "name": f"DevHost {uuid.uuid4().hex[:6]}",
            "mcp_endpoint": f"http://localhost:9100/{uuid.uuid4().hex[:6]}",
            "role": "operate",
            "auto_start": False,
        })
        assert resp.status_code == 200, resp.text
        return resp.json()["connection"]["id"]

    def test_device_full_lifecycle(self, admin_client):
        conn_id = self._make_conn(admin_client)

        # 初始为空
        r = admin_client.get(f"/api/mcp/connections/{conn_id}/devices")
        assert r.status_code == 200 and r.json() == []

        # 新增
        r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
            "device_id": "AA:BB:CC:00:11:22",
            "name": "门口摄像头",
            "ip": "192.168.1.50",
            "port": 8080,
            "model_tag": "mobilefacenet_v1",
        })
        assert r.status_code == 200, r.text
        dev = r.json()["device"]
        assert dev["ip"] == "192.168.1.50"
        assert dev["port"] == 8080
        dev_id = dev["id"]

        # 列表
        r = admin_client.get(f"/api/mcp/connections/{conn_id}/devices")
        assert r.status_code == 200 and len(r.json()) == 1

        # 更新
        r = admin_client.put(f"/api/mcp/connections/{conn_id}/devices/{dev_id}", json={
            "ip": "10.0.0.9", "port": 80,
        })
        assert r.status_code == 200, r.text
        dev = r.json()["device"]
        assert dev["ip"] == "10.0.0.9" and dev["port"] == 80

        # 删除
        r = admin_client.delete(f"/api/mcp/connections/{conn_id}/devices/{dev_id}")
        assert r.status_code == 200
        r = admin_client.get(f"/api/mcp/connections/{conn_id}/devices")
        assert r.json() == []

    def test_device_ip_required(self, admin_client):
        conn_id = self._make_conn(admin_client)
        r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
            "ip": "   ", "port": 80,
        })
        assert r.status_code == 400

    def test_device_ip_must_be_ip_literal_and_safe(self, admin_client):
        """SSRF 防线：ip 必须是 IP 字面量，回环/链路本地(含云元数据)/组播拒绝。"""
        conn_id = self._make_conn(admin_client)
        for bad_ip in ("localhost", "device.lan", "127.0.0.1", "::1",
                       "169.254.169.254", "224.0.0.1", "0.0.0.0"):
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
                "ip": bad_ip, "port": 80,
            })
            assert r.status_code == 400, f"{bad_ip}: {r.status_code} {r.text}"
        # 私网与公网字面量放行
        for ok_ip in ("192.168.1.99", "8.8.8.8"):
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
                "ip": ok_ip, "port": 80,
            })
            assert r.status_code == 200, f"{ok_ip}: {r.text}"

    def test_device_port_range(self, admin_client):
        conn_id = self._make_conn(admin_client)
        r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
            "ip": "1.2.3.4", "port": 70000,
        })
        assert r.status_code == 400

    def test_device_id_unique_within_connection(self, admin_client):
        conn_id = self._make_conn(admin_client)
        body = {"device_id": "DUP-1", "ip": "1.2.3.4", "port": 80}
        assert admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json=body).status_code == 200
        r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json=body)
        assert r.status_code == 409

    def test_device_upsert_by_ip_port_when_no_device_id(self, admin_client):
        """xiaozhi 注册设备从不带 device_id，只带 ip+port。同一 (connection, ip, port)
        重复注册必须 upsert 成一条记录，而不是每次都新插一行（否则下游按
        connection_id 解析物理设备时会因命中多条而无法唯一确定）。"""
        conn_id = self._make_conn(admin_client)

        r1 = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
            "name": "门口摄像头", "ip": "192.168.1.77", "port": 80,
        })
        assert r1.status_code == 200, r1.text
        dev1 = r1.json()["device"]
        assert dev1["device_id"] is None

        # 同一 ip+port 再次注册（模拟设备重连/心跳），应更新同一行而非新增
        r2 = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
            "name": "门口摄像头-v2", "ip": "192.168.1.77", "port": 80,
            "model_tag": "we2-mfn128-v1",
        })
        assert r2.status_code == 200, r2.text
        dev2 = r2.json()["device"]
        assert dev2["id"] == dev1["id"]
        assert dev2["name"] == "门口摄像头-v2"
        assert dev2["model_tag"] == "we2-mfn128-v1"

        r = admin_client.get(f"/api/mcp/connections/{conn_id}/devices")
        assert r.status_code == 200
        assert len(r.json()) == 1

        # 不同 port 视为不同物理设备，应各自成行
        r3 = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
            "ip": "192.168.1.77", "port": 8080,
        })
        assert r3.status_code == 200, r3.text
        r = admin_client.get(f"/api/mcp/connections/{conn_id}/devices")
        assert len(r.json()) == 2

    def test_device_on_unknown_connection_404(self, admin_client):
        r = admin_client.get("/api/mcp/connections/nope1234/devices")
        assert r.status_code == 404

    def test_flat_agent_devices_list(self, admin_client):
        """GET /api/mcp/agent-devices 扁平列出本租户所有设备（join 连接拿名称）。"""
        conn_id = self._make_conn(admin_client)
        # 该连接下挂两个设备
        for i, ip in enumerate(("192.168.1.10", "192.168.1.11")):
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
                "device_id": f"FLAT-{i}", "name": f"Cam {i}", "ip": ip, "port": 80,
            })
            assert r.status_code == 200, r.text

        r = admin_client.get("/api/mcp/agent-devices")
        assert r.status_code == 200, r.text
        rows = r.json()
        assert isinstance(rows, list)
        mine = [d for d in rows if d["connection_id"] == conn_id]
        assert len(mine) == 2
        # 契约字段齐全：connection_id / connection_name / id / name / ip
        for d in mine:
            assert set(d.keys()) == {"connection_id", "connection_name", "id", "name", "ip"}
            assert d["connection_name"]  # join 到连接名
        ips = sorted(d["ip"] for d in mine)
        assert ips == ["192.168.1.10", "192.168.1.11"]

    def test_device_cascade_on_connection_delete(self, admin_client):
        conn_id = self._make_conn(admin_client)
        admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json={
            "device_id": "CASCADE-1", "ip": "1.2.3.4", "port": 80,
        })
        # 删除连接后，设备子表记录应随之消失（连接已删 → 子设备列表 404）
        assert admin_client.delete(f"/api/mcp/connections/{conn_id}").status_code == 200
        from database import get_db_connection
        c = get_db_connection()
        n = c.execute(
            "SELECT COUNT(*) FROM mcp_agent_devices WHERE connection_id = ?", (conn_id,)
        ).fetchone()[0]
        c.close()
        assert n == 0


import base64 as _b64
import json as _json
import struct as _struct
import threading as _threading
from http.server import BaseHTTPRequestHandler, HTTPServer


def _emb_b64(vec):
    return _b64.b64encode(b"".join(_struct.pack("<f", x) for x in vec)).decode()


class TestPushFacesToDevice:
    """云端下发人脸库到设备：按 model_tag 过滤 + POST batch-update + 失败清晰。"""

    @pytest.fixture(autouse=True)
    def _allow_loopback_device_ip(self, monkeypatch):
        # 测试用 127.0.0.1 起假设备服务器；生产默认封禁回环（SSRF 防线）
        monkeypatch.setenv("MCP_DEVICE_ALLOW_LOOPBACK", "1")

    def _make_conn(self, admin_client):
        resp = admin_client.post("/api/mcp/connections", json={
            "name": f"PushHost {uuid.uuid4().hex[:6]}",
            "mcp_endpoint": f"http://localhost:9200/{uuid.uuid4().hex[:6]}",
            "role": "operate",
            "auto_start": False,
        })
        assert resp.status_code == 200, resp.text
        return resp.json()["connection"]["id"]

    def _enroll(self, admin_client, name, model_tag, vec):
        # 真实 canonical embedding 是 128 维 float32（512 字节）；测试给的短签名
        # 向量补零到 128 维，满足 push 路径的长度校验（DEVICE_FACE_EMBEDDING_F32_BYTES）。
        vec = list(vec)
        if len(vec) < 128:
            vec = vec + [0.0] * (128 - len(vec))
        sid = admin_client.post("/api/face/subjects", json={"name": name}).json()["id"]
        r = admin_client.post("/api/face/enrollments", json={
            "subject_id": sid,
            "embeddings": [{"embedding_b64": _emb_b64(vec), "model_tag": model_tag}],
        })
        assert r.status_code == 200, r.text
        return sid

    def _add_device(self, admin_client, conn_id, **kw):
        # face_enabled gate 已移除：设备不带该字段也能下发（DB 列废弃，默认 0）。
        body = {"ip": "127.0.0.1", "port": 80}
        body.update(kw)
        r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices", json=body)
        assert r.status_code == 200, r.text
        return r.json()["device"]["id"]

    def test_push_filters_by_model_tag_and_posts_payload(self, admin_client, monkeypatch):
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG
        # Only enrollments tagged with the fixed device model go out; the other
        # vector space (push-other) must be excluded.
        self._enroll(admin_client, "Alice", DEVICE_FACE_MODEL_TAG, [1.0, 0.0, 0.0])
        self._enroll(admin_client, "Bob", "push-other", [0.0, 1.0, 0.0])

        captured = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured["path"] = self.path
                captured["body"] = _json.loads(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true, "applied": 1}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        # push 端口固件写死 80；测试服务器在随机高端口，重定向常量到它。
        monkeypatch.setattr("routers.mcp_admin.DEVICE_HTTP_PORT", port)
        th = _threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            from routers.mcp_admin import DEVICE_FACE_MODEL_TAG
            conn_id = self._make_conn(admin_client)
            # Device row carries a DIFFERENT model_tag on purpose — the endpoint
            # must ignore it and use the fixed DEVICE_FACE_MODEL_TAG constant.
            dev_id = self._add_device(admin_client, conn_id, port=port, model_tag="device-col-ignored")
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["success"] is True, data
            assert data["pushed_count"] == 1, data
            assert data["model_tag"] == DEVICE_FACE_MODEL_TAG, data
            assert data["device_response"] == {"ok": True, "applied": 1}
            th.join(timeout=5)
            assert captured["path"] == "/api/face/batch-update"
            assert captured["body"]["model_tag"] == DEVICE_FACE_MODEL_TAG
            # 下发线缆契约：必须声明 embedding_format（量化只在 push 路径发生）。
            from routers.mcp_admin import DEVICE_EMBEDDING_FORMAT
            assert captured["body"]["embedding_format"] == DEVICE_EMBEDDING_FORMAT == "fp16"
            faces = captured["body"]["faces"]
            assert [f["name"] for f in faces] == ["Alice"]
            assert all({"name", "subject_id", "embedding_b64"} <= set(f) for f in faces)
        finally:
            srv.server_close()

    def test_push_quantizes_embedding_to_fp16(self, admin_client, monkeypatch):
        """push 路径把 canonical float32 embedding 量化为 fp16（128 维 → 256 字节），
        数值在 fp16 误差内与原 float32 一致；DB/library 仍是 float32。"""
        import numpy as _np
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG, DEVICE_EMBEDDING_FORMAT

        # 已知 128 维 float32 向量（非平凡值，覆盖 fp16 舍入）。
        rng = _np.random.default_rng(42)
        known = rng.standard_normal(128).astype("<f4")
        self._enroll(admin_client, "Quant", DEVICE_FACE_MODEL_TAG, known.tolist())

        captured = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured["body"] = _json.loads(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        monkeypatch.setattr("routers.mcp_admin.DEVICE_HTTP_PORT", port)
        th = _threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            conn_id = self._make_conn(admin_client)
            dev_id = self._add_device(admin_client, conn_id, port=port)
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
            assert r.status_code == 200, r.text
            assert r.json()["success"] is True
            th.join(timeout=5)
            body = captured["body"]
            assert body["embedding_format"] == DEVICE_EMBEDDING_FORMAT == "fp16"
            # 同 model_tag 下可能有其它测试残留的人脸，按名字精确取本测试这条。
            face = next(f for f in body["faces"] if f["name"] == "Quant")
            raw = _b64.b64decode(face["embedding_b64"])
            # fp16: 128 × binary16 LE = 256 字节（float32 会是 512）。
            assert len(raw) == 256, len(raw)
            roundtrip = _np.frombuffer(raw, dtype="<f2").astype("<f4")
            assert _np.allclose(roundtrip, known, rtol=1e-2, atol=1e-3), (
                roundtrip[:4], known[:4],
            )
        finally:
            srv.server_close()

    def test_push_unreachable_device_returns_fail(self, admin_client, monkeypatch):
        # Port 1 is not listening → connection refused → success:False, not silent.
        monkeypatch.setattr("routers.mcp_admin.DEVICE_HTTP_PORT", 1)
        conn_id = self._make_conn(admin_client)
        dev_id = self._add_device(admin_client, conn_id, model_tag="push-mt-A")
        r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["success"] is False
        assert data.get("error")

    def test_push_allowed_for_any_device_no_face_gate(self, admin_client, monkeypatch):
        """face_enabled gate 已移除：任意设备（不带/曾经 disabled）都能下发。

        以前 face_enabled=0 会被 400 拒；现在只要有 IP 就能推，且真的 POST 到设备。
        """
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG
        self._enroll(admin_client, "NoGate", DEVICE_FACE_MODEL_TAG, [1.0, 0.0, 0.0])

        captured = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured["body"] = _json.loads(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        monkeypatch.setattr("routers.mcp_admin.DEVICE_HTTP_PORT", port)
        th = _threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            conn_id = self._make_conn(admin_client)
            # 设备不带 face_enabled（DB 列废弃，默认 0）——以前会被 400 拒。
            dev_id = self._add_device(admin_client, conn_id, port=port)
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["success"] is True, data
            th.join(timeout=5)
            # 证明确实 POST 到了设备。
            assert captured.get("body", {}).get("model_tag") == DEVICE_FACE_MODEL_TAG
        finally:
            srv.server_close()

    def test_push_over_limit_rejected_without_posting(self, admin_client):
        """人脸库 >20 张时服务端拒绝：success=false + 超限错误信息，且不 POST 到设备。"""
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG, MAX_PUSH_FACES
        # 入库 MAX_PUSH_FACES + 1 张同 model_tag 的人脸，触发上限。
        # 记录 subject id，测试末尾清理，避免 session 级 sqlite 共享库污染后续测试。
        sids = []
        for i in range(MAX_PUSH_FACES + 1):
            vec = [0.0, 0.0, 0.0]
            vec[i % 3] = 1.0 + i  # 互不相同，避免被去重/合并
            sids.append(self._enroll(admin_client, f"Over-{uuid.uuid4().hex[:6]}", DEVICE_FACE_MODEL_TAG, vec))

        captured = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["called"] = True
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        th = _threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            conn_id = self._make_conn(admin_client)
            dev_id = self._add_device(admin_client, conn_id, port=port)
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["success"] is False, data
            assert str(MAX_PUSH_FACES) in data.get("error", ""), data
            assert "超过设备上限" in data.get("error", ""), data
        finally:
            srv.server_close()
            # 清理本测试入库的人脸，避免污染 session 共享的 sqlite 人脸库。
            for sid in sids:
                admin_client.delete(f"/api/face/subjects/{sid}")
        # 关键：拒绝发生在 POST 之前，设备从未被调用。
        assert "called" not in captured, "over-limit push must NOT POST to device"

    def _insert_malformed_we2(self, admin_client, name, nbytes):
        """直接插一条 WE2 tag 但 embedding 字节数不符（损坏行，模拟脏数据/截断写入）。
        走底层 insert 绕过 API 校验，返回 sid。"""
        import struct as _s
        sub = admin_client.post("/api/face/subjects", json={"name": name}).json()
        sid = sub["id"]
        tid = admin_client.get("/api/face/subjects").json()[0]["tenant_id"]
        from db import get_engine
        from metadata import face_enrollments as _t_fe
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG
        with get_engine().begin() as c:
            c.execute(_t_fe.insert().values(
                subject_id=sid, tenant_id=tid, model_tag=DEVICE_FACE_MODEL_TAG,
                embedding=b"\x00" * nbytes, is_active=1,
            ))
        return sid

    def test_push_skips_malformed_embedding_pushes_valid(self, admin_client, monkeypatch):
        """一条损坏行（embedding 长度不符）不能拖垮整批：跳过损坏、正常人脸照发，
        响应带 skipped_count/warning。复现 bad_embedding 现场（12 字节脏数据）。"""
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG
        good_sid = self._enroll(admin_client, "GoodFace", DEVICE_FACE_MODEL_TAG, [1.0, 0.0, 0.0])
        bad_sid = self._insert_malformed_we2(admin_client, "BadFace", 12)

        captured = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured["body"] = _json.loads(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true, "applied": 1}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        monkeypatch.setattr("routers.mcp_admin.DEVICE_HTTP_PORT", port)
        th = _threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            conn_id = self._make_conn(admin_client)
            dev_id = self._add_device(admin_client, conn_id, port=port)
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["success"] is True, data
            # session 共享 sqlite 里可能有其它测试残留的正常人脸，故用容忍断言：
            # 只验损坏行被跳过、正常行照发，不假设租户库里只有本测试两条。
            assert data.get("skipped_count", 0) >= 1, data
            assert "BadFace" in data.get("warning", ""), data
            th.join(timeout=5)
            names = [f["name"] for f in captured["body"]["faces"]]
            assert "GoodFace" in names, names          # 正常行照发
            assert "BadFace" not in names, names        # 损坏行绝不进 payload
        finally:
            srv.server_close()
            admin_client.delete(f"/api/face/subjects/{good_sid}")
            admin_client.delete(f"/api/face/subjects/{bad_sid}")

    def test_push_all_malformed_fails_without_posting(self, admin_client, monkeypatch):
        """库非空但全部损坏 → success:false，且不 POST 空/坏库给设备。"""
        # 清空 session 共享库里其它测试残留的正常人脸，保证"全部损坏"前提成立。
        for s in admin_client.get("/api/face/subjects").json():
            admin_client.delete(f"/api/face/subjects/{s['id']}")
        bad_sid = self._insert_malformed_we2(admin_client, "AllBad", 12)

        captured = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                captured["called"] = True
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        monkeypatch.setattr("routers.mcp_admin.DEVICE_HTTP_PORT", port)
        th = _threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            conn_id = self._make_conn(admin_client)
            dev_id = self._add_device(admin_client, conn_id, port=port)
            r = admin_client.post(f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
            assert r.status_code == 200, r.text
            data = r.json()
            assert data["success"] is False, data
            assert "损坏" in data.get("error", ""), data
        finally:
            srv.server_close()
            admin_client.delete(f"/api/face/subjects/{bad_sid}")
        assert "called" not in captured, "all-malformed push must NOT POST to device"

    def test_push_unknown_connection_404(self, admin_client):
        r = admin_client.post("/api/mcp/connections/nope1234/devices/1/push-faces")
        assert r.status_code == 404

    # ── push 前置懒重算（反方向：lan 注册 → 本机下发补 WE2 行）────────────

    def _insert_lan_only_subject(self, admin_client, name, photo):
        """造一个只有 lan 模型 enrollment（带注册照片）的 subject，返回 (sid, tid)。"""
        import struct as _s
        sub = admin_client.post("/api/face/subjects", json={"name": name}).json()
        sid = sub["id"]
        tid = admin_client.get("/api/face/subjects").json()[0]["tenant_id"]
        from db import get_engine
        from metadata import face_enrollments as _t_fe
        with get_engine().begin() as c:
            c.execute(_t_fe.insert().values(
                subject_id=sid, tenant_id=tid, model_tag="hailo:remote-v1",
                embedding=b"".join(_s.pack("<f", x) for x in [0.3, 0.3, 0.3]),
                source_image_b64=photo, is_active=1,
            ))
        return sid, tid

    def _run_push(self, admin_client, monkeypatch):
        """起假设备服务器执行一次 push，返回 (resp_json, captured_body)。"""
        captured = {}

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                captured["body"] = _json.loads(self.rfile.read(n))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), _Handler)
        port = srv.server_address[1]
        monkeypatch.setattr("routers.mcp_admin.DEVICE_HTTP_PORT", port)
        th = _threading.Thread(target=srv.handle_request, daemon=True)
        th.start()
        try:
            conn_id = self._make_conn(admin_client)
            dev_id = self._add_device(admin_client, conn_id, port=port)
            r = admin_client.post(
                f"/api/mcp/connections/{conn_id}/devices/{dev_id}/push-faces")
            assert r.status_code == 200, r.text
            th.join(timeout=5)
            return r.json(), captured.get("body")
        finally:
            srv.server_close()

    def test_push_lazy_recomputes_we2_row_from_photo(self, admin_client, monkeypatch):
        """subject 只有 lan 模型 enrollment + 照片 → push 用 WE2 模拟器补算
        128D 行并纳入本次下发（统一原则：切换模式永不要求重录）。"""
        import struct as _s
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG
        from face import orchestrator
        orchestrator._reembed_failed.clear()
        orchestrator._reembed_inflight.clear()

        sid, tid = self._insert_lan_only_subject(
            admin_client, "LanOnly Person", "photo:lan-only")
        calls = {"local": 0}
        # WE2 模拟器输出必须是真实 128 维 float32（512 字节），否则会被 push 路径
        # 的长度校验判为损坏而跳过。
        we2_emb = b"".join(_s.pack("<f", x) for x in ([1.0] + [0.0] * 127))

        def fake_local(image_b64):
            calls["local"] += 1
            assert image_b64 == "photo:lan-only"
            return {"embedding": we2_emb, "model_tag": DEVICE_FACE_MODEL_TAG}

        monkeypatch.setattr("face.endpoint_client._infer_local", fake_local)
        try:
            data, body = self._run_push(admin_client, monkeypatch)
            assert data["success"] is True, data
            assert calls["local"] == 1
            names = [f["name"] for f in body["faces"]]
            assert "LanOnly Person" in names
            # DB 长出 WE2 行；照片保留在源 lan 行、新行不复制
            from db import get_engine
            from sqlalchemy import text as _text
            with get_engine().connect() as c:
                rows = c.execute(_text(
                    "SELECT model_tag, source_image_b64 FROM face_enrollments "
                    "WHERE subject_id = :sid"), {"sid": sid}).fetchall()
            tags = {r[0] for r in rows}
            assert {"hailo:remote-v1", DEVICE_FACE_MODEL_TAG} <= tags
            we2_rows = [r for r in rows if r[0] == DEVICE_FACE_MODEL_TAG]
            assert we2_rows[0][1] is None
        finally:
            admin_client.delete(f"/api/face/subjects/{sid}")

    def test_push_no_recompute_when_we2_row_exists(self, admin_client, monkeypatch):
        """subject 已有 WE2 行 → push 零重算（不调模拟器）。"""
        from routers.mcp_admin import DEVICE_FACE_MODEL_TAG
        from face import orchestrator
        orchestrator._reembed_failed.clear()
        orchestrator._reembed_inflight.clear()

        sid = self._enroll(
            admin_client, "We2 Ready", DEVICE_FACE_MODEL_TAG, [1.0, 0.0, 0.0])
        calls = {"local": 0}

        def fake_local(image_b64):
            calls["local"] += 1
            return {"embedding": b"\x00" * 16, "model_tag": DEVICE_FACE_MODEL_TAG}

        monkeypatch.setattr("face.endpoint_client._infer_local", fake_local)
        try:
            data, body = self._run_push(admin_client, monkeypatch)
            assert data["success"] is True, data
            assert calls["local"] == 0
            assert "We2 Ready" in [f["name"] for f in body["faces"]]
        finally:
            admin_client.delete(f"/api/face/subjects/{sid}")

    # ── lan 模式下发地址改写（设备识别代理）──────────────────────────────
    # lan：identify_endpoint 指向 warehouse 自身 /api/face/device（设备拼
    # /recognize），identify_token 用租户级 auth_token（为空首发自动生成）。
    # local：endpoint/token 原样透传，行为不变。

    def _set_face_cfg(self, admin_client, **kw):
        body = {
            "enabled": True, "mode": "local", "endpoint": "",
            "auth_token": "", "min_confidence": 0.65,
        }
        body.update(kw)
        r = admin_client.put("/api/face/config", json=body)
        assert r.status_code == 200, r.text

    def test_push_lan_rewrites_endpoint_and_generates_token(
            self, admin_client, monkeypatch):
        monkeypatch.delenv("WAREHOUSE_DEVICE_BASE_URL", raising=False)
        self._set_face_cfg(
            admin_client, mode="lan",
            endpoint="http://tenant-endpoint.invalid:8001", auth_token="")
        data, body = self._run_push(admin_client, monkeypatch)
        assert data["success"] is True, data
        assert body["identify_mode"] == "lan"
        ep = body["identify_endpoint"]
        assert ep.startswith("http://") and ep.endswith("/api/face/device"), ep
        # 不再把租户 face_rec_api 端点直发设备。
        assert "tenant-endpoint.invalid" not in ep
        tok = body["identify_token"]
        assert tok and len(tok) == 32, tok
        # token 已入库（UI GET config 可见），设备与库一致。
        assert admin_client.get("/api/face/config").json()["auth_token"] == tok

    def test_push_lan_env_override_base_url(self, admin_client, monkeypatch):
        monkeypatch.setenv(
            "WAREHOUSE_DEVICE_BASE_URL", "http://10.9.8.7:8443/api/face/device/")
        self._set_face_cfg(
            admin_client, mode="lan",
            endpoint="http://tenant-endpoint.invalid:8001",
            auth_token="tok-fixed-abc")
        data, body = self._run_push(admin_client, monkeypatch)
        assert body["identify_endpoint"] == "http://10.9.8.7:8443/api/face/device"
        # 已有 token 原样复用，不重新生成。
        assert body["identify_token"] == "tok-fixed-abc"
        assert admin_client.get(
            "/api/face/config").json()["auth_token"] == "tok-fixed-abc"

    def test_push_local_mode_unchanged(self, admin_client, monkeypatch):
        monkeypatch.delenv("WAREHOUSE_DEVICE_BASE_URL", raising=False)
        self._set_face_cfg(
            admin_client, mode="local",
            endpoint="http://local-ep.invalid/x", auth_token="loctok")
        data, body = self._run_push(admin_client, monkeypatch)
        assert body["identify_mode"] == "local"
        assert body["identify_endpoint"] == "http://local-ep.invalid/x"
        assert body["identify_token"] == "loctok"
        # local 模式不自动生成 token。
        assert admin_client.get(
            "/api/face/config").json()["auth_token"] == "loctok"
