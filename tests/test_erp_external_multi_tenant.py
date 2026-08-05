"""外部 ERP 新增能力在**多租户**下的隔离与可用性。

此前这批功能只在 single_tenant 下验证过。多租户是风险最高的地方：探测会拿着某个
租户保存的凭据去访问对方系统，导入会创建登录账号——任何一处串租户都是事故。

覆盖三件事：
1. 探测/导入严格按调用者的租户隔离，A 租户看不到也改不了 B 租户的东西
2. 全局管理员（tenant_id 为 NULL）必须显式指定 tenant_id，且指定后能正确落到该租户
3. Provider 文件按 custom/<tenant_id>/ 存放时能被正确解析（这条早期是 bug）
"""
import json
import os
import uuid

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _as_global_admin():
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = NULL WHERE username = 'admin'")
    conn.commit()
    conn.close()


def _restore_admin_tenant():
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE users SET tenant_id = 1 WHERE username = 'admin'")
    conn.commit()
    conn.close()


def _set_system_mode(mode: str):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO system_settings (key, value) VALUES ('system_mode', ?)",
        (mode,))
    conn.commit()
    conn.close()


def _make_tenant(admin_client, suffix=None):
    suffix = suffix or uuid.uuid4().hex[:8]
    t = admin_client.post("/api/tenants", json={
        "slug": f"t-{suffix}", "name": f"Tenant {suffix}"})
    assert t.status_code == 200, t.text
    return t.json()["id"], suffix


def _make_tenant_admin(admin_client, tenant_id, suffix):
    username = f"adm-{suffix}"
    password = "Pass123!"
    u = admin_client.post("/api/users", json={
        "username": username, "password": password,
        "display_name": f"Admin {suffix}", "role": "admin",
        "tenant_id": tenant_id})
    assert u.status_code == 200, u.text
    return username, password


def _login(app_instance, username, password):
    from fastapi.testclient import TestClient
    c = TestClient(app_instance)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200 and r.json()["success"] is True, r.text
    return c


def _write_provider_file(tenant_id, warehouses=None, users=None):
    """把一个可探测的 Provider 放到 custom/<tenant_id>/ 下，返回文件名。

    早期 bug：上传按租户建子目录，加载器却只找扁平路径，多租户下必然回退到默认
    Provider。这里刻意只放子目录，用来守住那个修复。
    """
    from routers import erp as erp_router
    custom_dir = os.path.join(erp_router._mcp_dir, "providers", "custom", str(tenant_id))
    os.makedirs(custom_dir, exist_ok=True)
    fname = f"mt_{uuid.uuid4().hex[:8]}.py"
    body = f'''
try:
    from providers.base import BaseProvider
except ImportError:
    from ..base import BaseProvider


class MtProvider(BaseProvider):
    PROVIDER_NAME = "mt_probe"

    def resolve_name(self, text, entity_type="all"): return {{}}
    def query_stock(self, p, show_batches=False): return {{}}
    def stock_in(self, *a, **k): return {{}}
    def stock_out(self, *a, **k): return {{}}
    def search(self, *a, **k): return {{}}
    def get_today_statistics(self): return {{}}

    def list_warehouses(self, tenant_id=None):
        return {{"success": True, "items": {json.dumps(warehouses or [])}}}

    def list_users(self, tenant_id=None):
        return {{"success": True, "items": {json.dumps(users or [])}}}
'''
    with open(os.path.join(custom_dir, fname), "w", encoding="utf-8") as f:
        f.write(body)
    return fname, os.path.join(custom_dir, fname)


def _activate_provider(tenant_id, filename):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    pname = f"mt_{uuid.uuid4().hex[:8]}"
    cur.execute(
        """INSERT INTO erp_providers
              (name, provider_name, class_name, filename, config, is_active, tenant_id)
           VALUES (?, ?, ?, ?, ?, 1, ?)""",
        (pname, pname, "MtProvider", filename,
         json.dumps({"api_base_url": "http://127.0.0.1:9", "timeout": 2}), tenant_id))
    conn.commit()
    conn.close()


@pytest.fixture()
def mt_env(admin_client, monkeypatch):
    """多租户环境：切 DEPLOY_MODE、切 external_erp，退出时彻底复原。

    测试库是 session 级共享的，清理必须**精确**：早先这里用全表
    DELETE FROM erp_providers，会连带抹掉别的用例建的数据；建出来的
    tenant / 该 tenant 下的用户与会话也没回收，后续用例看到的租户数就不对了。
    这里按本文件自己造的标记（slug 前缀 t- + external_* 非空）逐项收走。
    """
    monkeypatch.setenv("DEPLOY_MODE", "multi_tenant")
    _set_system_mode("external_erp")
    created_files = []
    from database import get_db_connection
    conn = get_db_connection()
    before = {r[0] for r in conn.cursor().execute("SELECT id FROM tenants")}
    conn.close()
    try:
        yield admin_client, created_files
    finally:
        for p in created_files:
            os.path.exists(p) and os.unlink(p)
        # 文件删了但 custom/<tenant_id>/ 目录会留下来，且里面还有动态导入生成的
        # __pycache__——按"空目录"判定删不掉。这些目录会被 _discover() 扫描，
        # 残留的 .pyc 可能让后续进程加载到已删除的 Provider。整棵删掉。
        import shutil
        from routers import erp as erp_router
        _base = os.path.join(erp_router._mcp_dir, "providers", "custom")
        if os.path.isdir(_base):
            for _d in os.listdir(_base):
                _full = os.path.join(_base, _d)
                # 只删本文件建的租户子目录（纯数字命名），不碰 .gitkeep 与真实 Provider
                if _d.isdigit() and os.path.isdir(_full):
                    shutil.rmtree(_full, ignore_errors=True)
        _set_system_mode("self_owned")
        _restore_admin_tenant()
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT id FROM tenants")
        new_tenants = [r[0] for r in cur.fetchall() if r[0] not in before]
        cur.execute("DELETE FROM erp_providers WHERE provider_name LIKE 'mt_%'")
        cur.execute("DELETE FROM user_warehouses WHERE user_id IN "
                    "(SELECT id FROM users WHERE external_user_id IS NOT NULL)")
        cur.execute("DELETE FROM users WHERE external_user_id IS NOT NULL")
        cur.execute("DELETE FROM warehouses WHERE external_warehouse_id IS NOT NULL")
        for tid in new_tenants:
            # 顺序：会话 → api_key → 用户 → 仓库 → 租户，避免外键悬挂
            cur.execute("DELETE FROM sessions WHERE user_id IN "
                        "(SELECT id FROM users WHERE tenant_id = ?)", (tid,))
            cur.execute("DELETE FROM api_keys WHERE tenant_id = ?", (tid,))
            cur.execute("DELETE FROM user_warehouses WHERE user_id IN "
                        "(SELECT id FROM users WHERE tenant_id = ?)", (tid,))
            cur.execute("DELETE FROM mcp_connections WHERE tenant_id = ?", (tid,))
            cur.execute("DELETE FROM users WHERE tenant_id = ?", (tid,))
            cur.execute("DELETE FROM warehouses WHERE tenant_id = ?", (tid,))
            cur.execute("DELETE FROM erp_providers WHERE tenant_id = ?", (tid,))
            cur.execute("DELETE FROM tenants WHERE id = ?", (tid,))
        conn.commit()
        conn.close()


# ---------------------------------------------------------------------------
# 探测：按租户隔离
# ---------------------------------------------------------------------------

class TestProbeTenantIsolation:

    def test_each_tenant_probes_only_its_own_provider(self, mt_env, app_instance):
        """A 租户探测拿到 A 的 Provider 结果，B 租户拿到 B 的——绝不串。"""
        admin_client, files = mt_env
        _as_global_admin()

        ta, sa = _make_tenant(admin_client)
        tb, sb = _make_tenant(admin_client)

        fa, pa = _write_provider_file(ta, warehouses=[{"id": "A-WH", "name": "A仓"}])
        fb, pb = _write_provider_file(tb, warehouses=[{"id": "B-WH", "name": "B仓"}])
        files.extend([pa, pb])
        _activate_provider(ta, fa)
        _activate_provider(tb, fb)

        ua, pwa = _make_tenant_admin(admin_client, ta, sa)
        ub, pwb = _make_tenant_admin(admin_client, tb, sb)

        ca = _login(app_instance, ua, pwa)
        cb = _login(app_instance, ub, pwb)

        ra = ca.get("/api/erp/external/warehouses").json()
        rb = cb.get("/api/erp/external/warehouses").json()

        assert [i["id"] for i in ra["items"]] == ["A-WH"]
        assert [i["id"] for i in rb["items"]] == ["B-WH"], "B 租户拿到了别人的 Provider 结果"

    def test_tenant_cannot_probe_another_tenant_by_passing_tenant_id(
            self, mt_env, app_instance):
        """带上别人的 tenant_id 也不能越权——租户用户的租户由登录态决定。"""
        admin_client, files = mt_env
        _as_global_admin()

        ta, sa = _make_tenant(admin_client)
        tb, sb = _make_tenant(admin_client)
        fa, pa = _write_provider_file(ta, warehouses=[{"id": "A-WH", "name": "A仓"}])
        fb, pb = _write_provider_file(tb, warehouses=[{"id": "B-WH", "name": "B仓"}])
        files.extend([pa, pb])
        # 故意先激活 B 的：加载器按 id 升序取第一条，若租户过滤失效就会返回 B 的
        # Provider。否则这个断言会因为排序恰好命中 A 而"碰巧绿"。
        _activate_provider(tb, fb)
        _activate_provider(ta, fa)

        ua, pwa = _make_tenant_admin(admin_client, ta, sa)
        ca = _login(app_instance, ua, pwa)

        resp = ca.get(f"/api/erp/external/warehouses?tenant_id={tb}").json()
        assert [i["id"] for i in resp["items"]] == ["A-WH"], (
            "显式传别人的 tenant_id 竟然生效了——存在跨租户探测")


class TestGlobalAdminProbe:

    def test_global_admin_must_specify_tenant(self, mt_env):
        admin_client, files = mt_env
        _as_global_admin()
        t, _s = _make_tenant(admin_client)
        f, p = _write_provider_file(t, warehouses=[{"id": "X", "name": "X"}])
        files.append(p)
        _activate_provider(t, f)

        # 不带 tenant_id → 400
        assert admin_client.get("/api/erp/external/warehouses").status_code == 400
        # 带上 → 正常
        ok = admin_client.get(f"/api/erp/external/warehouses?tenant_id={t}")
        assert ok.status_code == 200, ok.text
        assert [i["id"] for i in ok.json()["items"]] == ["X"]


# ---------------------------------------------------------------------------
# 导入：按租户落地，互不影响
# ---------------------------------------------------------------------------

class TestImportTenantIsolation:

    def _users_of(self, tenant_id):
        from database import get_db_connection
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT username, external_user_id FROM users "
                    "WHERE tenant_id = ? AND external_user_id IS NOT NULL", (tenant_id,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

    def test_import_lands_in_callers_tenant_only(self, mt_env, app_instance):
        admin_client, _files = mt_env
        _as_global_admin()
        ta, sa = _make_tenant(admin_client)
        tb, sb = _make_tenant(admin_client)
        ua, pwa = _make_tenant_admin(admin_client, ta, sa)
        ca = _login(app_instance, ua, pwa)

        ext = f"ext-{uuid.uuid4().hex[:6]}"
        r = ca.post("/api/erp/external/import/users", json={
            "default_password": "Init@12345",
            "users": [{"external_user_id": ext, "username": f"imp-{sa}", "role": "view"}],
        })
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1

        assert any(u["external_user_id"] == ext for u in self._users_of(ta))
        assert self._users_of(tb) == [], "导入落到了别的租户"

    def test_same_external_id_allowed_in_different_tenants(self, mt_env, app_instance):
        """唯一约束是 (tenant_id, external_user_id) 复合的，不同租户可以有同一个外部账号。"""
        admin_client, _files = mt_env
        _as_global_admin()
        ta, sa = _make_tenant(admin_client)
        tb, sb = _make_tenant(admin_client)

        shared_ext = f"ext-shared-{uuid.uuid4().hex[:6]}"
        for tid, sfx in ((ta, sa), (tb, sb)):
            r = admin_client.post("/api/erp/external/import/users", json={
                "default_password": "Init@12345",
                "tenant_id": tid,
                "users": [{"external_user_id": shared_ext,
                           "username": f"imp-{sfx}", "role": "view"}],
            })
            assert r.status_code == 200, r.text
            assert r.json()["created"] == 1, r.text

        assert any(u["external_user_id"] == shared_ext for u in self._users_of(ta))
        assert any(u["external_user_id"] == shared_ext for u in self._users_of(tb))

    def test_global_admin_import_requires_tenant(self, mt_env):
        admin_client, _files = mt_env
        _as_global_admin()
        r = admin_client.post("/api/erp/external/import/users", json={
            "default_password": "Init@12345",
            "users": [{"external_user_id": "e1", "username": "u1"}],
        })
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Provider 文件按租户子目录存放（早期 bug 的回归守卫）
# ---------------------------------------------------------------------------

class TestTenantSubdirResolution:

    def test_provider_in_tenant_subdir_is_found(self, mt_env, app_instance):
        """文件只放 custom/<tenant_id>/，不放扁平路径——必须能解析到。"""
        admin_client, files = mt_env
        _as_global_admin()
        t, s = _make_tenant(admin_client)
        f, p = _write_provider_file(t, users=[{"id": "u1", "name": "zhangsan"}])
        files.append(p)
        _activate_provider(t, f)

        u, pw = _make_tenant_admin(admin_client, t, s)
        c = _login(app_instance, u, pw)

        resp = c.get("/api/erp/external/users")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True, "租户子目录下的 Provider 没被找到（早期 bug 回归）"
        assert [i["id"] for i in body["items"]] == ["u1"]
