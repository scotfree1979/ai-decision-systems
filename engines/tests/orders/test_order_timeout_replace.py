from __future__ import annotations
import sqlite3
from engines.tests.helpers.db_utils import ensure_orders_schema, insert_order

def test_timeout_then_replace(auto_conn: sqlite3.Connection):
    ensure_orders_schema(auto_conn)
    oid = insert_order(auto_conn, entry_status="placed", entry_odds=6.2)

    # timeout → mark as replaced (audit via status)
    auto_conn.execute("UPDATE orders SET entry_status='replaced' WHERE id=?", (oid,))
    # place new (simulate new odds)
    auto_conn.execute("UPDATE orders SET entry_status='placed', entry_odds=6.1 WHERE id=?", (oid,))
    auto_conn.commit()

    row = auto_conn.execute("SELECT entry_status, entry_odds FROM orders WHERE id=?", (oid,)).fetchone()
    assert row["entry_status"] == "placed"
    assert abs(float(row["entry_odds"]) - 6.1) < 1e-6
