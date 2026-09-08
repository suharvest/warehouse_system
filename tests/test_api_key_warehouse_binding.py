"""API Key 的仓库绑定：创建校验、事后补绑（PATCH）、无绑定时的报错文案。

背景（2026-09-08 reComputer R1000 实测）：后台「添加API密钥」只有名称 +
角色两个字段，建出来的 operate 角色 Key `warehouse_id` 为 NULL。
``deps.CurrentUser.can_access_warehouse``（backend/deps.py:174-201）对
``source='api_key'`` 且 ``warehouse_id is None`` 的非 admin 身份，会回落到
按 ``api_keys.user_id``（即创建它的管理员）查 ``user_warehouses``；管理员在
该表通常没有行，于是任何仓库都判 false，stock-in/stock-out 一律 403
「无权访问该仓库」。而当时没有任何接口能事后补绑仓库（PATCH 405）。
"""
import uuid

import pytest
from fastapi.testclient import TestClient


def _new_client(app_instance):
    return TestClient(app_instance)


def _key_headers(key):
    return {"X-API-Key": key}


def _create_key(admin_client, *, role='operate', warehouse_id=None):
    payload = {"name": f"whbind-{uuid.uuid4().hex[:6]}", "role": role}
    if warehouse_id is not None:
        payload["warehouse_id"] = warehouse_id
    return admin_client.post("/api/api-keys", json=payload)


# ---------------------------------------------------------------------------
# 1. 创建时的校验
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["operate", "view"])
def test_create_non_admin_key_without_warehouse_is_rejected(admin_client, role):
    resp = _create_key(admin_client, role=role)
    assert resp.status_code == 400, resp.text
    assert "warehouse_id" in resp.text


def test_create_admin_key_without_warehouse_is_allowed(admin_client):
    """admin 角色的 NULL warehouse_id 语义是「全仓」，保持可空。"""
    resp = _create_key(admin_client, role='admin')
    assert resp.status_code == 200, resp.text


def test_create_operate_key_with_warehouse_then_stock_in(
        admin_client, app_instance, sample_material, default_warehouse_id):
    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    assert resp.status_code == 200, resp.text
    info = resp.json()

    c = _new_client(app_instance)
    r = c.post("/api/materials/stock-in",
               headers=_key_headers(info['key']),
               json={
                   "product_name": sample_material['name'],
                   "quantity": 1,
                   "reason_category": "purchase",
                   "warehouse_id": default_warehouse_id,
               })
    assert r.status_code == 200, r.text
    assert r.json()['success'] is True


# ---------------------------------------------------------------------------
# 2. 存量无绑定 Key：报错文案 + PATCH 补绑
# ---------------------------------------------------------------------------

def _unbound_operate_key(admin_client, default_warehouse_id):
    """绕过创建校验，直接把 warehouse_id 改回 NULL —— 模拟修复前建出的存量 Key。"""
    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    assert resp.status_code == 200, resp.text
    info = resp.json()
    from db import get_engine
    from sqlalchemy import text
    with get_engine().begin() as conn:
        conn.execute(
            text("UPDATE api_keys SET warehouse_id = NULL WHERE id = :i"),
            {"i": info['id']},
        )
    return info


def test_unbound_key_write_error_names_the_missing_binding(
        admin_client, app_instance, sample_material, default_warehouse_id):
    info = _unbound_operate_key(admin_client, default_warehouse_id)

    c = _new_client(app_instance)
    r = c.post("/api/materials/stock-in",
               headers=_key_headers(info['key']),
               json={
                   "product_name": sample_material['name'],
                   "quantity": 1,
                   "reason_category": "purchase",
                   "warehouse_id": default_warehouse_id,
               })
    assert r.status_code == 403, r.text
    # 旧文案只说"无权访问该仓库"，会把人引向查仓库 ID / 租户。
    assert "未绑定仓库" in r.text, r.text


def test_patch_binds_warehouse_and_unblocks_stock_in(
        admin_client, app_instance, sample_material, default_warehouse_id):
    info = _unbound_operate_key(admin_client, default_warehouse_id)
    c = _new_client(app_instance)
    body = {
        "product_name": sample_material['name'],
        "quantity": 1,
        "reason_category": "purchase",
        "warehouse_id": default_warehouse_id,
    }
    assert c.post("/api/materials/stock-in",
                  headers=_key_headers(info['key']), json=body).status_code == 403

    patch = admin_client.patch(f"/api/api-keys/{info['id']}",
                               json={"warehouse_id": default_warehouse_id})
    assert patch.status_code == 200, patch.text
    assert patch.json()["warehouse_id"] == default_warehouse_id

    r = c.post("/api/materials/stock-in",
               headers=_key_headers(info['key']), json=body)
    assert r.status_code == 200, r.text


def test_patch_shows_up_in_list(admin_client, default_warehouse_id):
    info = _unbound_operate_key(admin_client, default_warehouse_id)
    listed = admin_client.get("/api/api-keys").json()
    row = next(k for k in listed if k['id'] == info['id'])
    assert row['warehouse_id'] is None

    admin_client.patch(f"/api/api-keys/{info['id']}",
                       json={"warehouse_id": default_warehouse_id})
    listed = admin_client.get("/api/api-keys").json()
    row = next(k for k in listed if k['id'] == info['id'])
    assert row['warehouse_id'] == default_warehouse_id
    assert row['warehouse_name']


def test_patch_can_toggle_enabled(admin_client, app_instance, default_warehouse_id):
    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    info = resp.json()

    r = admin_client.patch(f"/api/api-keys/{info['id']}", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["is_disabled"] is True

    c = _new_client(app_instance)
    assert c.get("/api/materials/all",
                 headers=_key_headers(info['key'])).status_code == 401

    r = admin_client.patch(f"/api/api-keys/{info['id']}", json={"enabled": True})
    assert r.json()["is_disabled"] is False
    assert c.get("/api/materials/all",
                 headers=_key_headers(info['key'])).status_code == 200


# ---------------------------------------------------------------------------
# 3. PATCH 不得成为提权/越权通道
# ---------------------------------------------------------------------------

def test_patch_ignores_role_and_name(admin_client, default_warehouse_id):
    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    info = resp.json()
    r = admin_client.patch(f"/api/api-keys/{info['id']}", json={
        "warehouse_id": default_warehouse_id,
        "role": "admin",
        "name": "escalated",
    })
    assert r.status_code == 200, r.text
    assert r.json()["role"] == "operate"
    assert r.json()["name"] == info["name"]


def test_patch_rejects_unbinding_non_admin_key(admin_client, default_warehouse_id):
    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    info = resp.json()
    r = admin_client.patch(f"/api/api-keys/{info['id']}", json={"warehouse_id": None})
    assert r.status_code == 400, r.text


def test_patch_requires_at_least_one_field(admin_client, default_warehouse_id):
    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    info = resp.json()
    assert admin_client.patch(f"/api/api-keys/{info['id']}", json={}).status_code == 400


def test_patch_missing_key_is_404(admin_client):
    r = admin_client.patch("/api/api-keys/99999999", json={"enabled": True})
    assert r.status_code == 404, r.text


def test_patch_requires_admin(admin_client, app_instance, default_warehouse_id):
    """operate 角色的 Key 不能改任何 Key 的仓库绑定。"""
    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    info = resp.json()
    c = _new_client(app_instance)
    r = c.patch(f"/api/api-keys/{info['id']}",
                headers=_key_headers(info['key']),
                json={"warehouse_id": default_warehouse_id})
    assert r.status_code == 403, r.text


def test_patch_rejects_cross_tenant_warehouse(admin_client, default_warehouse_id):
    """把 Key 绑到别的租户的仓库上必须失败。"""
    from db import get_engine
    from sqlalchemy import text
    suffix = uuid.uuid4().hex[:6]
    with get_engine().begin() as conn:
        conn.execute(text(
            "INSERT INTO tenants (name, slug, is_active) "
            "VALUES (:n, :s, 1)"), {"n": f"T{suffix}", "s": f"t-{suffix}"})
        tid = conn.execute(text(
            "SELECT id FROM tenants WHERE slug = :s"), {"s": f"t-{suffix}"}).scalar()
        conn.execute(text(
            "INSERT INTO warehouses (tenant_id, slug, name, is_disabled) "
            "VALUES (:t, :s, :n, 0)"),
            {"t": tid, "s": f"wh-{suffix}", "n": f"WH{suffix}"})
        other_wh = conn.execute(text(
            "SELECT id FROM warehouses WHERE slug = :s"),
            {"s": f"wh-{suffix}"}).scalar()

    resp = _create_key(admin_client, role='operate',
                       warehouse_id=default_warehouse_id)
    info = resp.json()
    r = admin_client.patch(f"/api/api-keys/{info['id']}",
                           json={"warehouse_id": other_wh})
    assert r.status_code == 403, r.text

    with get_engine().begin() as conn:
        conn.execute(text("UPDATE warehouses SET is_disabled = 1 WHERE id = :i"),
                     {"i": other_wh})
