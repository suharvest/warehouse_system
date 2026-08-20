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


def test_other_tenant_connections_are_not_touched(admin_client, fake_manager):
    """重启范围必须限定在本租户 —— 误伤别人的手表比不重启更糟。"""
    tid = _tenant_of(admin_client)
    mine = _seed_connection(tid, name="本租户手表")
    # 另造一个租户 id，确保不等于当前租户（None 时用 999）
    other_tid = 999 if tid != 999 else 998
    theirs = _seed_connection(other_tid, name="他人手表")
    pid = _seed_provider(tid)
    mgr = fake_manager(_FakeManager(running_ids=[mine, theirs]))

    r = admin_client.post(f"/api/erp/providers/{pid}/activate")
    assert r.status_code == 200, r.text
    assert mgr.restarted == [mine], "只应重启本租户的连接"


def test_partial_failure_continues_with_remaining_connections(admin_client,
                                                              fake_manager):
    """一个连接重启炸了，后面的还得继续 —— 否则半数手表停在旧 Provider。"""
    tid = _tenant_of(admin_client)
    c1 = _seed_connection(tid, name="手表1")
    c2 = _seed_connection(tid, name="手表2")
    c3 = _seed_connection(tid, name="手表3")
    pid = _seed_provider(tid)
    mgr = fake_manager(_FakeManager(running_ids=[c1, c2, c3], fail_ids=[c2]))

    r = admin_client.post(f"/api/erp/providers/{pid}/activate")
    assert r.status_code == 200, r.text
    assert set(mgr.restarted) == {c1, c2, c3}, "失败的那个不能中断后续"
    by_id = {e["id"]: e for e in r.json()["restarted_connections"]}
    assert by_id[c1]["restarted"] is True
    assert by_id[c2]["restarted"] is False
    assert by_id[c3]["restarted"] is True


def test_status_probe_failure_does_not_abort_the_sweep(admin_client, fake_manager):
    """get_connection_status 也可能抛 —— 抛出去会中断后面所有连接。"""
    tid = _tenant_of(admin_client)
    c1 = _seed_connection(tid, name="探测炸的")
    c2 = _seed_connection(tid, name="正常的")
    pid = _seed_provider(tid)

    mgr = _FakeManager(running_ids=[c1, c2])
    original = mgr.get_connection_status

    def _boom(conn_id):
        if conn_id == c1:
            raise RuntimeError("status boom")
        return original(conn_id)

    mgr.get_connection_status = _boom
    fake_manager(mgr)

    r = admin_client.post(f"/api/erp/providers/{pid}/activate")
    assert r.status_code == 200, r.text
    assert mgr.restarted == [c2], "探测失败的跳过，其余照常重启"
    by_id = {e["id"]: e for e in r.json()["restarted_connections"]}
    assert by_id[c1]["restarted"] is False
    assert "status boom" in by_id[c1]["error"]


def test_enumeration_failure_is_swallowed_and_reported(monkeypatch):
    """连接枚举失败不能往外抛。

    DB 的激活变更此时已经提交，helper 再抛异常会让接口返回 500，用户以为激活
    没成功而重试 —— 实际状态早已改变。直接单元测试 helper：走 HTTP 的话
    patch get_engine 会把 activate 自身也打断，测不到这一段。
    """
    import asyncio
    import routers.erp as erp_mod

    class _Boom:
        def connect(self):
            raise RuntimeError("db gone")

    monkeypatch.setattr(erp_mod, "get_engine", lambda: _Boom())
    mgr = _FakeManager(running_ids=[])

    out = asyncio.run(
        erp_mod._restart_tenant_mcp_connections(mgr, 1, reason="激活")
    )

    assert len(out) == 1
    assert out[0]["restarted"] is False
    assert "db gone" in out[0]["error"]
    assert mgr.restarted == []
