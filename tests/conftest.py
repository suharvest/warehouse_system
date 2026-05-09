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
    """Create a temporary test database, isolated from production.

    If ``DATABASE_URL`` is set (e.g. for MySQL portability tests), honour it
    directly: assume the schema has already been created via
    ``alembic upgrade head`` and skip the sqlite-specific
    ``database.init_database()`` path. Per-test isolation is provided by the
    ``_mysql_truncate`` fixture below.

    Otherwise, fall back to the historical sqlite-temp-file behaviour.
    """
    database_url = os.environ.get('DATABASE_URL')
    if database_url and not database_url.startswith('sqlite'):
        os.environ.setdefault('INIT_MOCK_DATA', '0')
        os.environ.setdefault('ENABLE_AUDIT_LOG', '0')
        # database.py is still hardcoded to sqlite3, but app.py routes go
        # through SQLAlchemy + DATABASE_URL. We import database to expose
        # constants/helpers but skip init_database (alembic owns the schema).
        import database  # noqa: F401
        importlib.reload(database)
        # Seed minimal data the sqlite init_database() would have inserted:
        # default tenant (id=1), default warehouse (id=1), system_mode setting.
        from db import get_engine
        from sqlalchemy import text, inspect
        eng = get_engine()
        # Wipe every table (preserving alembic_version) so each pytest session
        # starts clean. The MySQL DB persists across runs in the docker
        # container, so without this the admin user from a prior run would
        # block /api/auth/setup with "系统已初始化".
        with eng.begin() as conn:
            insp = inspect(eng)
            tables = [t for t in insp.get_table_names() if t != 'alembic_version']
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for t in tables:
                conn.execute(text(f"TRUNCATE TABLE `{t}`"))
            # Seed minimal data the sqlite init_database() would have inserted.
            conn.execute(text(
                "INSERT INTO tenants (id, slug, name) VALUES (1, 'default', '默认租户')"
            ))
            conn.execute(text(
                "INSERT INTO warehouses (id, slug, name, is_default) "
                "VALUES (1, 'default', '默认仓库', 1)"
            ))
            conn.execute(text(
                "INSERT INTO system_settings (`key`, `value`) "
                "VALUES ('system_mode', 'self_owned')"
            ))
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
        yield database_url
        return

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


def _mysql_truncate_disabled(request):
    """Per-test cleanup for MySQL: TRUNCATE all tables (preserving alembic_version).

    Only active when DATABASE_URL points at a non-sqlite backend. Runs *after*
    the test, ensuring the next test starts from a clean slate. The session-
    level admin/setup will be re-created on demand by _admin_setup since
    admin_client is function-scoped.
    """
    yield
    database_url = os.environ.get('DATABASE_URL', '')
    if not database_url or database_url.startswith('sqlite'):
        return
    try:
        from db import get_engine
        from sqlalchemy import text
        eng = get_engine()
        with eng.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            rows = conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name <> 'alembic_version'"
            )).fetchall()
            for (tbl,) in rows:
                conn.execute(text(f"TRUNCATE TABLE `{tbl}`"))
            # Re-seed minimal rows so session-scoped admin/warehouse fixtures
            # remain valid across tests.
            conn.execute(text(
                "INSERT IGNORE INTO tenants (id, slug, name) VALUES (1, 'default', '默认租户')"
            ))
            conn.execute(text(
                "INSERT IGNORE INTO warehouses (id, slug, name, is_default) "
                "VALUES (1, 'default', '默认仓库', 1)"
            ))
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    except Exception:
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
def admin_client(app_instance, _admin_setup, test_db):
    """
    A fresh TestClient that is logged in as admin.
    Function-scoped: each test gets a clean admin session.

    Defensive resets so a previous test's mutations don't poison this one:
    - test_face.py reloads `database` while monkeypatching DATABASE_PATH;
      its module-level value can stick around at a deleted temp file.
    - test_tenants.py / test_multi_tenant_isolation.py promote admin to
      global admin (tenant_id = NULL); subsequent tests assume tenant_id = 1.
    """
    import os as _os
    _os.environ['DATABASE_PATH'] = test_db
    import database as _database
    _database.DATABASE_PATH = test_db
    try:
        conn = _database.get_db_connection()
        cur = conn.cursor()
        cur.execute("UPDATE users SET tenant_id = 1 WHERE username = ?",
                    (_admin_setup['username'],))
        conn.commit()
        conn.close()
    except Exception:
        pass

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


@pytest.fixture(scope="session")
def default_warehouse_id(test_db):
    """Get the default warehouse ID (created during init_database)."""
    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM warehouses WHERE is_default = 1 LIMIT 1')
    row = cursor.fetchone()
    conn.close()
    return row['id']


@pytest.fixture()
def sample_material(admin_client, default_warehouse_id):
    """Create a sample material for testing and return its info."""
    from database import get_db_connection
    conn = get_db_connection()
    cursor = conn.cursor()

    # Use a unique SKU to avoid conflicts
    import uuid
    sku = f"TEST-{uuid.uuid4().hex[:8].upper()}"
    name = f"Test Material {sku}"

    cursor.execute('''
        INSERT INTO materials (name, sku, category, quantity, unit, safe_stock, location, warehouse_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    ''', (name, sku, 'Test Category', 100, 'pcs', 20, 'A-01', default_warehouse_id))

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
        'safe_stock': 20,
        'warehouse_id': default_warehouse_id
    }
