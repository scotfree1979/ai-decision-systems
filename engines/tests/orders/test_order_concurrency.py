from __future__ import annotations
import sqlite3
from engines.tests.helpers.db_utils import ensure_orders_schema, insert_order

def test_multi_orders_separate_rows(auto_conn: sqlite3.Connection):
    ensure_orders_schema(auto_conn)
    a = insert_order(auto_conn, marketId="M1", selectionId="S1", side="LAY")
    b = insert_order(auto_conn, marketId="M1", selectionId="S2", side="BACK")
    c = insert_order(auto_conn, marketId="M2", selectionId="S3", side="LAY")
    assert len({a,b,c}) == 3

    # Update one should not clobber others
    auto_conn.execute("UPDATE orders SET entry_status='matched' WHERE id=?", (b,))
    auto_conn.commit()
    st = {r["id"]: r["entry_status"] for r in auto_conn.execute("SELECT id, entry_status FROM orders WHERE id IN (?,?,?)",(a,b,c))}
    assert st[a] == "queued"
    assert st[b] == "matched"
    assert st[c] == "queued"
