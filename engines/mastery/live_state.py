# === PATCH START ===
# 📍 TARGET: engines/mastery/live_state.py
# 📆 PATCHED: 2025-10-24T14:25Z — live market state view
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, pandas as pd, os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "autoscalp_gui.db")

def get_live_market_state(limit: int = 20):
    """
    Return the latest cash-out & liability per market from mastery_cache.
    Used by Mastery planners and risk dashboard.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    q = f"""
        SELECT marketId,
               json_extract(json_payload,'$.cashout_total')  AS cashout,
               json_extract(json_payload,'$.liability_total') AS liability,
               MAX(ts) AS ts
          FROM mastery_cache
         WHERE event_type='cashout_tick'
      GROUP BY marketId
      ORDER BY ts DESC
         LIMIT {limit};
    """
    df = pd.read_sql_query(q, con)
    con.close()
    return df
# === PATCH END ===
# === PATCH START ===
# 📍 TARGET: engines/mastery/live_state.py
# 📆 PATCHED: 2025-10-25T12:40Z — add record() for cashout→Mastery bridge
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3
from datetime import datetime, timezone

def record(marketId: str, pnl_now: float, liability: float, status: str = "OPEN"):
    """
    Append or update the current live state for a given market.
    Called by cashout_calc to persist real-time P&L snapshots.
    """
    
    # === PATCH START ===
    # 📍 TARGET: engines/mastery/live_state.py:record
    # 📆 PATCHED: 2025-11-26Z — Unified Hijack → AlphaX → DAL writer
    from engines.database_hijack_monitor import enqueue_write

    # 1) Lazy-create the live_state table (AlphaX will route to AUTO family)
    sql_create = """
        CREATE TABLE IF NOT EXISTS live_state(
            marketId TEXT PRIMARY KEY,
            pnl_now REAL,
            liability REAL,
            status TEXT,
            updated_at TEXT DEFAULT (datetime('now','utc'))
        );
    """
    enqueue_write(sql_create)

    # 2) UPSERT current market PnL snapshot
    sql_upsert = """
        INSERT INTO live_state (marketId, pnl_now, liability, status, updated_at)
        VALUES (?, ?, ?, ?, datetime('now','utc'))
        ON CONFLICT(marketId)
        DO UPDATE SET
            pnl_now   = excluded.pnl_now,
            liability = excluded.liability,
            status    = excluded.status,
            updated_at = datetime('now','utc');
    """
    enqueue_write(sql_upsert, (marketId, float(pnl_now), float(liability), status), priority=3)
    # === PATCH END ===


