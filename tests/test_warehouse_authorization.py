"""Warehouse authorization regressions."""
import uuid


def test_user_without_warehouse_grants_sees_no_warehouses_or_inventory(
        admin_client, app_instance, sample_material):
    suffix = uuid.uuid4().hex[:8]
    username = f"nogrant-{suffix}"
    password = "Pass123!"

    created = admin_client.post("/api/users", json={
        "username": username,
        "password": password,
        "display_name": "No Grant",
        "role": "operate",
    })
    assert created.status_code == 200, created.text

    from fastapi.testclient import TestClient
    client = TestClient(app_instance)
    login = client.post("/api/auth/login", json={
        "username": username,
        "password": password,
    })
    assert login.status_code == 200, login.text
    assert login.json()["success"] is True

    my_warehouses = client.get("/api/auth/warehouses")
    assert my_warehouses.status_code == 200, my_warehouses.text
    assert my_warehouses.json()["warehouses"] == []

    warehouse_list = client.get("/api/warehouses")
    assert warehouse_list.status_code == 200, warehouse_list.text
    assert warehouse_list.json() == []

    inventory = client.get("/api/materials/list")
    assert inventory.status_code == 200, inventory.text
    body = inventory.json()
    assert body["items"] == []
    assert body["total"] == 0

    forbidden = client.get(
        f"/api/materials/list?warehouse_id={sample_material['warehouse_id']}"
    )
    assert forbidden.status_code == 403, forbidden.text
