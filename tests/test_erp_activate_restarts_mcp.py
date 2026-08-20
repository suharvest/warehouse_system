"""激活/停用 ERP Provider 必须重启该租户运行中的 MCP 连接。

回归一个现场事故：Provider 在 MCP 子进程里是懒加载单例
（mcp/warehouse_mcp.py:_get_provider），首次工具调用后常驻内存，没有任何失效
路径。上传→校验→激活整条向导全绿，但运行中的手表仍在用旧 Provider —— DB 说
已生效、实际没有，用户没有任何途径能看出来，只能靠听播报发现字段错乱。
"""

import json
import uuid

import pytest


def _seed_provider(tenant_id, *, is_active=0, level1_passed=True):
    """直插一行 erp_providers，带上激活所需的 Level 1 测试结果。"""
    from database import get_db_connection
    pname = f"prov_{uuid.uuid4().hex[:8]}"
    results = json.dumps({"level1": {"all_passed": level1_passed}})
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO erp_providers (name, provider_name, class_name, filename, "
        "config, is_active, tenant_id, test_results) VALUES (?,?,?,?,?,?,?,?)",
        (pname, pname, f"{pname.title()}Provider", f"{pname}.py",
         json.dumps({}), is_active, tenant_id, results),
    )
    pid = cur.lastrowid
    conn.commit()
    conn.close()
    return pid


def _seed_connection(tenant_id, name="手表A"):
    from database import get_db_connection
    conn_id = uuid.uuid4().hex[:12]
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO mcp_connections (id, name, mcp_endpoint, api_key, "
        "auto_start, status, tenant_id) VALUES (?,?,?,?,?,?,?)",
        (conn_id, name, "ws://stub/mcp", "k-" + conn_id, 0, "running", tenant_id),
    )
    conn.commit()
    conn.close()
    return conn_id


class _FakeManager:
    """只实现被调到的两个方法；记录重启了谁。"""

    def __init__(self, running_ids, fail_ids=()):
        self.running = set(running_ids)
        self.fail = set(fail_ids)
        self.restarted = []

    def get_connection_status(self, conn_id):
        return {"status": "running" if conn_id in self.running else "stopped"}

    async def restart_connection(self, conn_id, endpoint=None, api_key=None,
                                 log_context=None):
        self.restarted.append(conn_id)
        if conn_id in self.fail:
            raise RuntimeError("boom")
        return True


@pytest.fixture()
def fake_manager(monkeypatch):
    """替换 get_mcp_manager 依赖，避免真的拉起子进程。"""
    holder = {}

    def _install(manager):
        import app as app_module
        from deps import get_mcp_manager
        app_module.app.dependency_overrides[get_mcp_manager] = lambda: manager
        holder["manager"] = manager
        return manager

    yield _install

    import app as app_module
    from deps import get_mcp_manager
    app_module.app.dependency_overrides.pop(get_mcp_manager, None)


def _tenant_of(admin_client):
    r = admin_client.get("/api/auth/me")
    assert r.status_code == 200, r.text
    return r.json().get("tenant_id")


def test_activate_restarts_running_connections(admin_client, fake_manager):
    tid = _tenant_of(admin_client)
    conn_id = _seed_connection(tid)
    pid = _seed_provider(tid)
    mgr = fake_manager(_FakeManager(running_ids=[conn_id]))

    r = admin_client.post(f"/api/erp/providers/{pid}/activate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    # 回归点：不重启的话 DB 已激活、手表仍用旧 Provider。
    assert mgr.restarted == [conn_id]
    assert body["restarted_connections"] == [
        {"id": conn_id, "name": "手表A", "restarted": True}
    ]


def test_stopped_connections_are_left_alone(admin_client, fake_manager):
    """没在跑的连接下次启动时自然加载新 Provider，不该无谓重启。"""
    tid = _tenant_of(admin_client)
    conn_id = _seed_connection(tid)
    pid = _seed_provider(tid)
    mgr = fake_manager(_FakeManager(running_ids=[]))

    r = admin_client.post(f"/api/erp/providers/{pid}/activate")
    assert r.status_code == 200
    assert mgr.restarted == []
    assert r.json()["restarted_connections"] == []


def test_restart_failure_does_not_fail_activation(admin_client, fake_manager):
    """DB 变更已提交，重启失败只能如实回报，不能把激活也判失败。"""
    tid = _tenant_of(admin_client)
    conn_id = _seed_connection(tid)
    pid = _seed_provider(tid)
    mgr = fake_manager(_FakeManager(running_ids=[conn_id], fail_ids=[conn_id]))

    r = admin_client.post(f"/api/erp/providers/{pid}/activate")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    entry = body["restarted_connections"][0]
    assert entry["restarted"] is False
    assert "boom" in entry["error"]

    from database import get_db_connection
    conn = get_db_connection()
    try:
        active = conn.execute(
            "SELECT is_active FROM erp_providers WHERE id = ?", (pid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert active == 1, "重启失败不该回滚激活"


def test_deactivate_also_restarts(admin_client, fake_manager):
    """停用意味着回落到默认 Provider —— 不重启的话外部 Provider 还在服务。"""
    tid = _tenant_of(admin_client)
    conn_id = _seed_connection(tid)
    pid = _seed_provider(tid, is_active=1)
    mgr = fake_manager(_FakeManager(running_ids=[conn_id]))

    r = admin_client.post(f"/api/erp/providers/{pid}/deactivate")
    assert r.status_code == 200, r.text
    assert mgr.restarted == [conn_id]
    assert r.json()["restarted_connections"][0]["restarted"] is True
