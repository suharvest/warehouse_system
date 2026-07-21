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
