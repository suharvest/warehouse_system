"""
Warehouse System - Test fixtures

Provides shared fixtures for all backend API tests:
- Temporary SQLite database (isolated per session)
- FastAPI TestClient
- Pre-authenticated admin client
- Helper utilities for creating test data
"""
import pytest
import os
import sys
import tempfile
import importlib

# Add backend to path
backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'backend')
sys.path.insert(0, backend_dir)


@pytest.fixture(scope="session")
def test_db():
    """Create a temporary test database, isolated from production."""
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    # Configure environment before importing any app modules
    os.environ['DATABASE_PATH'] = db_path
    os.environ['INIT_MOCK_DATA'] = '0'
    os.environ['ENABLE_AUDIT_LOG'] = '0'

    import database
    importlib.reload(database)
    database.init_database()

    yield db_path

    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest.fixture(scope="session")
def app_instance(test_db):
    """Reload and return the FastAPI app instance."""
    import app as app_module
    importlib.reload(app_module)

    # Disable rate limiting for tests
    app_module.limiter.enabled = False

    return app_module.app


@pytest.fixture(scope="session")
def client(app_instance):
    """A plain (unauthenticated) FastAPI TestClient instance."""
    from fastapi.testclient import TestClient
    return TestClient(app_instance)


@pytest.fixture(scope="session")
def _admin_setup(client):
    """One-time admin user setup (session-scoped)."""
    resp = client.post("/api/auth/setup", json={
        "username": "admin",
        "password": "Admin123!",
        "display_name": "Test Admin"
    })
    assert resp.status_code == 200, f"Setup failed: {resp.text}"
    return {"username": "admin", "password": "Admin123!"}


@pytest.fixture()
def admin_client(app_instance, _admin_setup):
    """
    A fresh TestClient that is logged in as admin.
    Function-scoped: each test gets a clean admin session.
    """
    from fastapi.testclient import TestClient
    c = TestClient(app_instance)

    resp = c.post("/api/auth/login", json={
        "username": _admin_setup['username'],
        "password": _admin_setup['password']
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data['success'] is True, f"Admin login failed: {data}"

    return c


@pytest.fixture()
def sample_material(admin_client):
    """Create a sample material for testing and return its info."""
    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    # Use a unique SKU to avoid conflicts
    import uuid
    sku = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    name = f"Test Material {sku}"

    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    ''', (name, sku, 'Test Category', 100, 'pcs', 20, 'A-01'))

    material_id = cursor.lastrowid
    conn.commit()
    conn.close()

    return {
        'id': material_id,
        'name': name,
        'sku': sku,
        'category': 'Test Category',
        'quantity': 100,
        'unit': 'pcs',
        'safe_stock': 20
    }
