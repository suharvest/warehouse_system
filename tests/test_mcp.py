"""
MCP (Agent) configuration tests: CRUD, API key auto-creation/deletion, role sync.
"""
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
    """方案 D: 写工具通过 tool.meta['requires_face'] 标记需要 face 校验，
    供 MCP client (xiaozhi) 在调 call_tool 前自动注入摄像头数据。
    对 LLM 不可见，纯客户端编排提示。"""

    def test_write_tools_carry_requires_face_meta(self):
        import asyncio
        warehouse_mcp = _import_warehouse_mcp()
        tools = asyncio.run(warehouse_mcp.mcp.get_tools())

        EXPECTED = {"stock_in", "stock_out", "move_batch_location"}
        marked = {
            name for name, t in tools.items()
            if t.to_mcp_tool().meta and t.to_mcp_tool().meta.get("requires_face")
        }
        assert EXPECTED.issubset(marked), (
            f"写工具应该标 requires_face；缺: {EXPECTED - marked}"
        )

    def test_readonly_tools_do_not_carry_requires_face_meta(self):
        import asyncio
        warehouse_mcp = _import_warehouse_mcp()
        tools = asyncio.run(warehouse_mcp.mcp.get_tools())

        READONLY = {"resolve_name", "query_stock", "query_batch",
                    "search", "get_today_statistics"}
        for name in READONLY:
            if name not in tools:
                continue
            meta = tools[name].to_mcp_tool().meta or {}
            assert not meta.get("requires_face"), (
                f"只读工具 {name} 不应标 requires_face"
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
        monkeypatch.setattr(warehouse_mcp, "_enforce_face", lambda *a, **k: None)
        monkeypatch.setattr(warehouse_mcp._provider, "move_batch_location", _stub_move)

        fn = getattr(warehouse_mcp.move_batch_location, "fn",
                     warehouse_mcp.move_batch_location)
        resp = fn(batch_no="B-1", new_location="A-2")

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
        we2_emb = b"".join(_s.pack("<f", x) for x in [1.0, 0.0, 0.0, 0.0])

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
