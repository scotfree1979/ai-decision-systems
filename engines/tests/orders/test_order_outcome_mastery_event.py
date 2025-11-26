from __future__ import annotations
import sqlite3, json
from engines.tests.helpers.db_utils import ensure_orders_schema, insert_order
from engines.config_paths import connect_db  # bets DB (mastery_events)

def test_order_close_writes_mastery_event(auto_conn: sqlite3.Connection):
    ensure_orders_schema(auto_conn)
    oid = insert_order(auto_conn, entry_status="placed", mode="TEST")
    # close order with small win
    auto_conn.execute("""
        UPDATE orders SET entry_status='matched', closed_at=datetime('now','utc'),
                          net_pl=COALESCE(net_pl,0.0)+0.20 WHERE id=?""",(oid,))
    auto_conn.commit()

    # Log a mastery event for the closed order (simulating engine callback)
    with connect_db(ro=False) as conn:
        conn.execute(
            "INSERT INTO mastery_events(event_type, details_json, delta_progress, source) VALUES (?,?,0,'TEST')",
            ("trade_outcome", json.dumps({"order_id": oid, "realized": 0.20}))
        )
        conn.commit()

    with connect_db(ro=True) as conn:
        row = conn.execute("SELECT event_type, source FROM mastery_events ORDER BY id DESC LIMIT 1").fetchone()
        assert row and row[0] == "trade_outcome" and row[1] == "TEST"
