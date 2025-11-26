# engines/db_triggers.py
from __future__ import annotations
from engines.config_paths import connect_db

def ensure_order_events_triggers():
    """
    Creates order_events table and triggers so events are logged automatically
    when orders are inserted or when their status changes.
    Idempotent (safe to call on every start).
    """
    with connect_db(ro=False) as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS order_events(
          id INTEGER PRIMARY KEY,
          order_id INTEGER,
          oco_group_id TEXT,
          market_id TEXT,
          runner_id TEXT,
          side TEXT,
          role TEXT,
          event TEXT,
          price REAL, size REAL,
          ts TEXT DEFAULT (datetime('now')),
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );
        """)

        # clean old triggers if re-running
        for tname in ("orders_ai_log_created","orders_au_log_status","orders_au_log_entry_status"):
            conn.execute(f"DROP TRIGGER IF EXISTS {tname};")

        # INSERT -> CREATED
        conn.execute("""
        CREATE TRIGGER IF NOT EXISTS orders_ai_log_created
        AFTER INSERT ON orders
        BEGIN
          INSERT INTO order_events(order_id, oco_group_id, market_id, runner_id, side, role, event, price, size)
          VALUES (
            NEW.id, COALESCE(NEW.oco_group_id,''), COALESCE(NEW.market_id,''), COALESCE(NEW.selectionId, NEW.runner_id, ''),
            COALESCE(NEW.side,''), COALESCE(NEW.role,''), 'CREATED', CAST(COALESCE(NEW.price, NEW.entry_odds) AS REAL), CAST(COALESCE(NEW.size, NEW.entry_stake) AS REAL)
          );
        END;
        """)

        # UPDATE status -> LIVE/MATCHED/CANCELLED/CLOSED (HEDGED)
        conn.execute("""
        CREATE TRIGGER IF NOT EXISTS orders_au_log_status
        AFTER UPDATE OF status ON orders
        WHEN NEW.status IS NOT OLD.status
        BEGIN
          INSERT INTO order_events(order_id, oco_group_id, market_id, runner_id, side, role, event, price, size)
          VALUES (
            NEW.id, COALESCE(NEW.oco_group_id,''), COALESCE(NEW.market_id,''), COALESCE(NEW.selectionId, NEW.runner_id, ''),
            COALESCE(NEW.side,''), COALESCE(NEW.role,''),
            CASE
              WHEN NEW.status='live' THEN 'LIVE'
              WHEN NEW.status='matched' THEN 'MATCHED'
              WHEN NEW.status='cancelled' THEN 'CANCELLED'
              WHEN NEW.status='closed' THEN 'HEDGED'
              ELSE COALESCE(NEW.status,'UPDATED')
            END,
            CAST(COALESCE(NEW.price, NEW.entry_odds) AS REAL),
            CAST(COALESCE(NEW.size, NEW.entry_stake) AS REAL)
          );
        END;
        """)

        # Some installs use entry_status instead of status
        conn.execute("""
        CREATE TRIGGER IF NOT EXISTS orders_au_log_entry_status
        AFTER UPDATE OF entry_status ON orders
        WHEN NEW.entry_status IS NOT OLD.entry_status
        BEGIN
          INSERT INTO order_events(order_id, oco_group_id, market_id, runner_id, side, role, event, price, size)
          VALUES (
            NEW.id, COALESCE(NEW.oco_group_id,''), COALESCE(NEW.market_id,''), COALESCE(NEW.selectionId, NEW.runner_id, ''),
            COALESCE(NEW.side,''), COALESCE(NEW.role,''),
            CASE
              WHEN NEW.entry_status='live' THEN 'LIVE'
              WHEN NEW.entry_status='matched' THEN 'MATCHED'
              WHEN NEW.entry_status='cancelled' THEN 'CANCELLED'
              WHEN NEW.entry_status='closed' THEN 'HEDGED'
              ELSE COALESCE(NEW.entry_status,'UPDATED')
            END,
            CAST(COALESCE(NEW.price, NEW.entry_odds) AS REAL),
            CAST(COALESCE(NEW.size, NEW.entry_stake) AS REAL)
          );
        END;
        """)
        conn.commit()

