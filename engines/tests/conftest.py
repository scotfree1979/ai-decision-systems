import os, sys, sqlite3, pytest
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engines.config_paths import set_db_paths, connect_db, autoscalp_db
from engines.db_migrations import ensure_tables
from engines.tests.helpers.db_utils import open_auto_db, ensure_orders_schema

def pytest_sessionstart(session):
    set_db_paths(mode='TEST')
    ensure_tables()
    # Ensure the Orders table exists in AUTO_DB too (for GUI/order tests)
    adb = autoscalp_db()
    with open_auto_db(adb) as conn:
        ensure_orders_schema(conn)

@pytest.fixture
def auto_conn():
    from engines.config_paths import autoscalp_db
    conn = open_auto_db(autoscalp_db())
    try:
        ensure_orders_schema(conn)
        yield conn
    finally:
        conn.close()
