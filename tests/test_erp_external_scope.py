"""外部 ERP 模式下的作用域绑定与身份导入。

覆盖三块此前只有手工验证、没有回归保护的逻辑：

1. **身份导入** —— 这是会创建登录账号的路径，行为必须被钉死：
   幂等、重名保护（尤其不能覆盖本地管理员）、角色白名单、密码只在新建时写。
2. **外部作用域探测** —— Provider 是客户上传的任意代码，未实现探测方法是**预期
   路径**，必须表现为 HTTP 200 + not_implemented，而不是 5xx。
3. **运行时注入** —— 连接级绑定必须压过 Provider 自身的静态配置，否则多个智能体
   会全部打到同一个外部仓库。
"""
import json
import os
import uuid

import pytest


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _set_system_mode(mode: str):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO system_settings (key, value) VALUES ('system_mode', ?)",
        (mode,),
    )
    conn.commit()
    conn.close()


def _insert_provider(tenant_id=1, filename=None, config=None, is_active=1):
    """直接插 erp_providers 行（探测走的是文件加载，文件由调用方准备）。"""
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    pname = f"prov_{uuid.uuid4().hex[:8]}"
    cur.execute(
        """
        INSERT INTO erp_providers
            (name, provider_name, class_name, filename, config, is_active, tenant_id)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (pname, pname, "ProbeProvider", filename or f"{pname}.py",
         json.dumps(config or {}), is_active, tenant_id),
    )
    conn.commit()
    conn.close()
    return pname


# 本文件造出来的 provider_name 都带这个前缀，清理时据此精确定位。
# 早先用的是全表 DELETE FROM erp_providers——测试库是 session 级共享的，
# 会把别的用例建的 provider 一起抹掉。
_PROV_PREFIX = "prov_"


def _clear_providers():
    from database import get_db_connection
    conn = get_db_connection()
    conn.cursor().execute(
        "DELETE FROM erp_providers WHERE provider_name LIKE ?", (_PROV_PREFIX + "%",))
    conn.commit()
    conn.close()


def _get_user(username):
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(
        "SELECT id, username, display_name, role, password_hash, external_user_id "
        "FROM users WHERE username = ?", (username,))
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def _import_users(client, users, password="Init@12345"):
    return client.post("/api/erp/external/import/users", json={
        "default_password": password,
        "users": users,
    })


def _cleanup_imported_rows():
    """删掉本文件导入出来的用户/仓库及其派生数据。

    测试库是 session 级共享的。导入用例会建出带 external_* 标记的行，其中还包括
    role=admin 的用户——不清理会打破 TestLastAdminGuard 之类「当前有几个管理员」
    的前提，表现为一批看似无关的用例集体变红（已踩过）。

    这些用户还会产生登录 session、api_key 等派生行，只删 users 会留下悬挂引用，
    所以按 user_id 一并收走。顺序：session/api_key/user_warehouses → users →
    warehouses，避免外键悬挂。
    """
    from database import get_db_connection
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("SELECT id FROM users WHERE external_user_id IS NOT NULL")
    uids = [r[0] for r in cur.fetchall()]
    for uid in uids:
        cur.execute("DELETE FROM sessions WHERE user_id = ?", (uid,))
        cur.execute("DELETE FROM api_keys WHERE user_id = ?", (uid,))
        cur.execute("DELETE FROM user_warehouses WHERE user_id = ?", (uid,))
    cur.execute("DELETE FROM users WHERE external_user_id IS NOT NULL")
    cur.execute("DELETE FROM mcp_connections WHERE external_warehouse_id IS NOT NULL")
    cur.execute("DELETE FROM warehouses WHERE external_warehouse_id IS NOT NULL")
    conn.commit()
    conn.close()


def _cleanup_stray_provider_files():
    """清掉用例写进 providers/custom/ 的临时 Provider 文件。

    这个目录会被 providers/__init__ 的 _discover() 扫描，残留文件会污染后续
    进程的 provider 注册表。用例内已有 finally，这里是兜底（断言失败时也能收干净）。
    """
    from routers import erp as erp_router
    import glob
    base = os.path.join(erp_router._mcp_dir, "providers", "custom")
    for pat in ("probe_*.py", "bindtest_*.py"):
        for f in glob.glob(os.path.join(base, pat)):
            os.path.exists(f) and os.unlink(f)


@pytest.fixture()
def external_mode(admin_client):
    """把系统切到 external_erp，测试结束后恢复现场，避免污染其他用例。"""
    _set_system_mode("external_erp")
    try:
        yield admin_client
    finally:
        _set_system_mode("self_owned")
        _clear_providers()
        _cleanup_imported_rows()
        _cleanup_stray_provider_files()


# ---------------------------------------------------------------------------
# 身份导入
# ---------------------------------------------------------------------------

class TestImportUsers:

    def test_creates_users_with_role_and_external_id(self, external_mode):
        suffix = uuid.uuid4().hex[:6]
        resp = _import_users(external_mode, [
            {"external_user_id": f"ext-{suffix}-1", "username": f"u{suffix}a",
             "display_name": "张三", "role": "operate"},
            {"external_user_id": f"ext-{suffix}-2", "username": f"u{suffix}b",
             "display_name": "李四", "role": "view"},
        ])
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] == 2
        assert body["updated"] == 0
        assert body["skipped"] == []

        u = _get_user(f"u{suffix}a")
        assert u["role"] == "operate"
        assert u["external_user_id"] == f"ext-{suffix}-1"
        assert u["display_name"] == "张三"

    def test_reimport_is_idempotent_and_keeps_password(self, external_mode):
        suffix = uuid.uuid4().hex[:6]
        ext = f"ext-{suffix}"
        username = f"u{suffix}"

        assert _import_users(external_mode, [
            {"external_user_id": ext, "username": username, "role": "view"},
        ]).json()["created"] == 1
        pw_before = _get_user(username)["password_hash"]

        # 同 external_user_id 再导：更新而非重复创建，且不动密码
        resp = _import_users(external_mode, [
            {"external_user_id": ext, "username": username,
             "display_name": "改名了", "role": "admin"},
        ], password="CompletelyDifferent@999")
        body = resp.json()
        assert body["created"] == 0
        assert body["updated"] == 1

        u = _get_user(username)
        assert u["role"] == "admin"
        assert u["display_name"] == "改名了"
        assert u["password_hash"] == pw_before, "更新已有用户时不应重置密码"

    def test_username_clash_is_skipped_not_overwritten(self, external_mode):
        """本地已有同名用户（且不是同一外部账号）必须跳过 —— 这条保护的是本地管理员。"""
        admin_before = _get_user("admin")
        assert admin_before is not None

        resp = _import_users(external_mode, [
            {"external_user_id": "ext-evil", "username": "admin",
             "display_name": "外部的admin", "role": "admin"},
        ])
        body = resp.json()
        assert body["created"] == 0
        assert len(body["skipped"]) == 1
        assert body["skipped"][0]["username"] == "admin"

        admin_after = _get_user("admin")
        assert admin_after["password_hash"] == admin_before["password_hash"]
        assert admin_after["display_name"] == admin_before["display_name"]
        assert admin_after["external_user_id"] is None, "本地管理员不应被打上外部账号标记"

    def test_invalid_role_falls_back_to_operate(self, external_mode):
        """非法角色不能放大权限。"""
        suffix = uuid.uuid4().hex[:6]
        _import_users(external_mode, [
            {"external_user_id": f"ext-{suffix}", "username": f"u{suffix}",
             "role": "superuser"},
        ])
        assert _get_user(f"u{suffix}")["role"] == "operate"

    def test_imported_user_can_log_in_with_initial_password(self, external_mode, app_instance):
        from fastapi.testclient import TestClient
        suffix = uuid.uuid4().hex[:6]
        username = f"u{suffix}"
        _import_users(external_mode, [
            {"external_user_id": f"ext-{suffix}", "username": username, "role": "view"},
        ], password="Init@12345")

        c = TestClient(app_instance)
        ok = c.post("/api/auth/login", json={"username": username, "password": "Init@12345"})
        assert ok.json()["success"] is True

        bad = c.post("/api/auth/login", json={"username": username, "password": "wrong"})
        assert bad.json()["success"] is False

    def test_short_password_rejected(self, external_mode):
        resp = _import_users(external_mode, [
            {"external_user_id": "ext-x", "username": "whatever"},
        ], password="ab")
        assert resp.status_code == 400

    def test_rows_missing_key_fields_are_skipped(self, external_mode):
        resp = _import_users(external_mode, [
            {"external_user_id": "", "username": "no-ext-id"},
            {"external_user_id": "ext-no-name", "username": "   "},
        ])
        body = resp.json()
        assert body["created"] == 0
        assert len(body["skipped"]) == 2

    def test_empty_payload_is_noop(self, external_mode):
        body = _import_users(external_mode, []).json()
        assert body["created"] == 0 and body["updated"] == 0


# ---------------------------------------------------------------------------
# 仓库导入（对方没有租户概念时用作权限锚点）
# ---------------------------------------------------------------------------

class TestImportWarehouses:

    def _import(self, client, warehouses):
        return client.post("/api/erp/external/import/warehouses",
                           json={"warehouses": warehouses})

    def test_creates_anchor_with_external_id(self, external_mode):
        code = f"WH-{uuid.uuid4().hex[:6]}"
        resp = self._import(external_mode, [{"external_warehouse_id": code, "name": "北京中心仓"}])
        assert resp.status_code == 200, resp.text
        assert resp.json()["created"] == 1

        listed = external_mode.get("/api/warehouses").json()
        anchor = [w for w in listed if w.get("external_warehouse_id") == code]
        assert len(anchor) == 1, "仓库列表必须回传 external_warehouse_id，前端靠它做联动"
        assert anchor[0]["name"] == "北京中心仓"

    def test_reimport_updates_instead_of_duplicating(self, external_mode):
        code = f"WH-{uuid.uuid4().hex[:6]}"
        self._import(external_mode, [{"external_warehouse_id": code, "name": "旧名"}])
        body = self._import(external_mode, [{"external_warehouse_id": code, "name": "新名"}]).json()
        assert body["created"] == 0
        assert body["updated"] == 1

        listed = external_mode.get("/api/warehouses").json()
        matched = [w for w in listed if w.get("external_warehouse_id") == code]
        assert len(matched) == 1
        assert matched[0]["name"] == "新名"


# ---------------------------------------------------------------------------
# 探测：Provider 是第三方代码，失败必须收敛成结构化响应
# ---------------------------------------------------------------------------

class TestProbeEndpoints:

    @pytest.mark.parametrize("path", [
        "/api/erp/external/tenants",
        "/api/erp/external/warehouses",
        "/api/erp/external/users",
    ])
    def test_404_when_no_active_provider(self, external_mode, path):
        _clear_providers()
        assert external_mode.get(path).status_code == 404

    @pytest.mark.parametrize("path", [
        "/api/erp/external/tenants",
        "/api/erp/external/warehouses",
        "/api/erp/external/users",
    ])
    def test_missing_provider_file_is_404_not_500(self, external_mode, path):
        """DB 有行但文件不在（换镜像/漏拷贝）时不能打成 500。"""
        _clear_providers()
        _insert_provider(filename="definitely_missing_provider.py")
        assert external_mode.get(path).status_code == 404

    def test_not_implemented_is_http_200(self, external_mode, tmp_path):
        """未实现探测是预期路径，必须 200 + not_implemented，前端据此退化为手工填写。"""
        from routers import erp as erp_router

        custom_dir = os.path.join(erp_router._mcp_dir, "providers", "custom")
        os.makedirs(custom_dir, exist_ok=True)
        fname = f"probe_{uuid.uuid4().hex[:8]}.py"
        path = os.path.join(custom_dir, fname)
        with open(path, "w", encoding="utf-8") as f:
            f.write(
                "try:\n"
                "    from providers.base import BaseProvider\n"
                "except ImportError:\n"
                "    from ..base import BaseProvider\n"
                "class ProbeProvider(BaseProvider):\n"
                "    PROVIDER_NAME = 'probe_only'\n"
                "    def resolve_name(self, t, entity_type='all'): return {}\n"
                "    def query_stock(self, p, show_batches=False): return {}\n"
                "    def stock_in(self, *a, **k): return {}\n"
                "    def stock_out(self, *a, **k): return {}\n"
                "    def search(self, *a, **k): return {}\n"
                "    def get_today_statistics(self): return {}\n"
            )
        try:
            _clear_providers()
            _insert_provider(filename=fname)
            for ep in ("tenants", "warehouses", "users"):
                r = external_mode.get(f"/api/erp/external/{ep}")
                assert r.status_code == 200, f"{ep}: {r.text}"
                body = r.json()
                assert body["success"] is False
                assert body["error"] == "not_implemented"
                assert body["items"] == []
        finally:
            os.path.exists(path) and os.unlink(path)


# ---------------------------------------------------------------------------
# 运行时注入：连接级绑定必须压过 Provider 的静态配置
# ---------------------------------------------------------------------------

class TestRuntimeScopeInjection:

    def _warehouse_mcp(self):
        import importlib
        import sys
        mcp_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp")
        if mcp_dir not in sys.path:
            sys.path.insert(0, mcp_dir)
        return importlib.import_module("warehouse_mcp")

    def test_external_scope_lands_in_config(self):
        w = self._warehouse_mcp()
        st = w.create_runtime_state(
            "http://x/api", "key",
            external_tenant_id="ORG-9", external_warehouse_id="WH-SH",
        )
        assert st["config"]["external_tenant_id"] == "ORG-9"
        assert st["config"]["external_warehouse_id"] == "WH-SH"

    def test_absent_scope_adds_no_keys(self):
        """自有模式不应凭空多出这两个键，否则 Provider 会误以为绑了外部仓库。"""
        w = self._warehouse_mcp()
        st = w.create_runtime_state("http://x/api", "key")
        assert "external_tenant_id" not in st["config"]
        assert "external_warehouse_id" not in st["config"]

    def test_connection_binding_wins_over_stored_provider_config(self, monkeypatch, tmp_path):
        """连接级绑定必须压过 Provider 存的静态配置。

        这条**必须走生产实现**（_load_provider_from_db_or_default）：早先的版本在测试里
        自己重写了一遍 merge，生产代码改坏了照样绿——等于没有保护。
        """
        w = self._warehouse_mcp()

        # 造一个真的 Provider 文件，放在加载器会找的扁平路径下
        import providers as _providers  # noqa: F401  (确保包可导入)
        custom_dir = os.path.join(os.path.dirname(w.__file__), "providers", "custom")
        os.makedirs(custom_dir, exist_ok=True)
        fname = f"bindtest_{uuid.uuid4().hex[:8]}.py"
        fpath = os.path.join(custom_dir, fname)
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(
                "try:\n"
                "    from providers.base import BaseProvider\n"
                "except ImportError:\n"
                "    from ..base import BaseProvider\n"
                "class BindProvider(BaseProvider):\n"
                "    PROVIDER_NAME = 'bindtest'\n"
                "    def resolve_name(self, t, entity_type='all'): return {}\n"
                "    def query_stock(self, p, show_batches=False): return {}\n"
                "    def stock_in(self, *a, **k): return {}\n"
                "    def stock_out(self, *a, **k): return {}\n"
                "    def search(self, *a, **k): return {}\n"
                "    def get_today_statistics(self): return {}\n"
            )

        # active-for-mcp 返回的 stored_config 里**也**写了 external_warehouse_id，
        # 模拟「Provider 配置写死了一个仓库」的情况
        class _FakeResp:
            status_code = 200

            @staticmethod
            def json():
                return {
                    "mode": "external_erp",
                    "provider": {
                        "id": 1,
                        "provider_name": "bindtest",
                        "filename": fname,
                        "tenant_id": None,
                        "config": {
                            "external_warehouse_id": "WH-STATIC",
                            "api_base_url": "http://ext",
                        },
                    },
                }

        import requests
        monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResp())

        try:
            state = w.create_runtime_state(
                "http://x/api", "key",
                external_tenant_id="ORG-A", external_warehouse_id="WH-A",
            )
            provider = w._load_provider_from_db_or_default(state["config"])
            cfg = provider.config

            assert cfg["external_warehouse_id"] == "WH-A", (
                "Provider 静态配置盖掉了连接级绑定——多个智能体会全打到同一个外部仓库")
            assert cfg["external_tenant_id"] == "ORG-A"
            assert cfg["api_base_url"] == "http://ext", "其余键仍应由 Provider 配置决定"
        finally:
            os.path.exists(fpath) and os.unlink(fpath)


# ---------------------------------------------------------------------------
# 容器内网地址探测：静默产出不可达地址是最难查的一类问题
# ---------------------------------------------------------------------------

class TestDeviceBaseUrlGuard:

    def _fn(self):
        from routers.mcp_admin import _looks_unreachable_from_device
        return _looks_unreachable_from_device

    def test_loopback_unreachable_only_when_device_is_remote(self):
        f = self._fn()
        assert f("127.0.0.1", "192.168.1.50") is True
        assert f("127.0.0.1", "127.0.0.1") is False   # 测试环境设备就是本机

    def test_container_ip_flagged_only_inside_container(self, monkeypatch):
        f = self._fn()
        real_exists = os.path.exists

        monkeypatch.setattr(os.path, "exists",
                            lambda p: True if p == "/.dockerenv" else real_exists(p))
        assert f("172.18.0.3", "192.168.101.50") is True, "容器内拿到 docker 网段应判定不可达"
        assert f("192.168.101.107", "192.168.101.50") is False, "host 网络模式不应误伤"

        monkeypatch.setattr(os.path, "exists", real_exists)
        assert f("172.18.0.3", "192.168.101.50") is False, "非容器环境不做此判定"

    def test_explicit_override_skips_detection(self, monkeypatch):
        from routers.mcp_admin import _device_facing_base_url
        monkeypatch.setenv("WAREHOUSE_DEVICE_BASE_URL",
                           "http://10.0.0.5:2125/api/face/device")
        assert _device_facing_base_url("192.168.1.9") == "http://10.0.0.5:2125/api/face/device"


# ---------------------------------------------------------------------------
# 改了绑定必须重启连接（codex review 发现）
# ---------------------------------------------------------------------------

class TestScopeChangeTriggersRestart:
    """外部作用域是连接启动时读进 runtime state 的。

    改了不重启的话，运行中的 Provider 会继续按**旧的**租户/仓库往对方系统写——
    界面显示已改、实际写错仓库，且没有任何报错。
    """

    def test_changing_external_scope_restarts_running_connection(
            self, external_mode, monkeypatch):
        from routers import mcp_admin

        calls = []

        class _FakeManager:
            def get_connection_status(self, conn_id):
                return {"status": "running"}

            async def start_connection(self, *a, **k):
                return True

            async def stop_connection(self, *a, **k):
                return True

            async def restart_connection(self, conn_id, endpoint, api_key):
                calls.append(conn_id)
                return True

        fake = _FakeManager()
        external_mode.app.dependency_overrides[mcp_admin.get_mcp_manager] = lambda: fake
        try:
            created = external_mode.post("/api/mcp/connections", json={
                "name": f"agent-{uuid.uuid4().hex[:6]}",
                "mcp_endpoint": f"wss://example.test/{uuid.uuid4().hex[:8]}/",
                "external_warehouse_id": "WH-A",
            })
            assert created.status_code == 200, created.text
            conn_id = created.json()["connection"]["id"]
            calls.clear()

            # 只改外部仓库，endpoint 不动
            upd = external_mode.put(f"/api/mcp/connections/{conn_id}",
                                    json={"external_warehouse_id": "WH-B"})
            assert upd.status_code == 200, upd.text
            assert calls == [conn_id], "改了外部作用域必须重启，否则旧作用域会一直生效"

            # 值没变则不该重启
            calls.clear()
            external_mode.put(f"/api/mcp/connections/{conn_id}",
                              json={"external_warehouse_id": "WH-B"})
            assert calls == [], "值未变化不应触发重启"
        finally:
            external_mode.app.dependency_overrides.pop(mcp_admin.get_mcp_manager, None)
