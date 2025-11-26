from __future__ import annotations
import sqlite3
from engines.tests.helpers.db_utils import ensure_orders_schema, insert_order

def test_partial_then_cancel(auto_conn: sqlite3.Connection):
    ensure_orders_schema(auto_conn)
    oid = insert_order(auto_conn, entry_status="placed", entry_stake=10.0)

    # simulate partials via status notes/ stake reduction – minimal schema, so we emulate
    # first partial
    auto_conn.execute("UPDATE orders SET entry_status='partially_matched', entry_stake=8.0 WHERE id=?", (oid,))
    # second partial
    auto_conn.execute("UPDATE orders SET entry_status='partially_matched', entry_stake=5.0 WHERE id=?", (oid,))
    # cancel remainder
    auto_conn.execute("UPDATE orders SET entry_status='cancelled', closed_at=datetime('now','utc') WHERE id=?", (oid,))
    auto_conn.commit()

    row = auto_conn.execute("SELECT entry_status, entry_stake, closed_at FROM orders WHERE id=?", (oid,)).fetchone()
    assert row["entry_status"] == "cancelled"
    assert float(row["entry_stake"]) == 5.0  # remaining unmatched stake snapshot
    assert row["closed_at"] is not None
