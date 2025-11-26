{}# engines/replay_orders.py
from __future__ import annotations
import datetime as dt
from engines.config_paths import is_replay_mode
from engines.config_paths import autoscalp_db, connect_db  # or your DB connect helper

def seed_replay_fill(*, amount: float, pnl: float, when_utc: dt.datetime | None = None):
    """
    Inserts a simple parent+child matched pair with realized_pnl for visual testing.
    Only active in REPLAY mode.
    """
    if not is_replay_mode():
        return
    when = (when_utc or dt.datetime.utcnow()).isoformat(sep=" ")
    with connect_db(ro=False) as conn:
        c = conn.cursor()
        # Ensure columns exist in your schema; if 'mode' not present, remove that column.
        c.execute("""
            INSERT INTO orders (order_id, parent_order_id, side, amount, price, created_ts, exit_status, realized_pnl, mode)
            VALUES (?, NULL, 'BACK', ?, 2.00, ?, 'matched', 0.0, 'REPLAY')
        """, (f"replay_parent_{when}", amount, when))
        c.execute("""
            INSERT INTO orders (order_id, parent_order_id, side, amount, price, created_ts, exit_status, realized_pnl, mode)
            VALUES (?, ?, 'LAY', ?, 2.02, ?, 'matched', ?, 'REPLAY')
        """, (f"replay_child_{when}", f"replay_parent_{when}", amount, when, pnl))
        conn.commit()
