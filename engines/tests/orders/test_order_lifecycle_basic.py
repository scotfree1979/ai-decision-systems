from __future__ import annotations
import sqlite3
from engines.tests.helpers.db_utils import ensure_orders_schema, insert_order

def test_order_lifecycle_basic(auto_conn: sqlite3.Connection):
    ensure_orders_schema(auto_conn)
    oid = insert_order(auto_conn, entry_status="queued", side="LAY", entry_odds=6.2, entry_stake=2.0)

    # placed
    auto_conn.execute("UPDATE orders SET entry_status='placed' WHERE id=?", (oid,))
    # matched & closed (+P/L)
    auto_conn.execute("""
        UPDATE orders
           SET entry_status='matched',
               closed_at=datetime('now','utc'),
               net_pl=COALESCE(net_pl,0.0)+0.46
         WHERE id=?""", (oid,))
    auto_conn.commit()

    row = auto_conn.execute("SELECT entry_status, closed_at, net_pl FROM orders WHERE id=?", (oid,)).fetchone()
    assert row["entry_status"] == "matched"
    assert row["closed_at"] is not None and len(row["closed_at"]) > 0
    assert abs(float(row["net_pl"]) - 0.46) < 1e-6
