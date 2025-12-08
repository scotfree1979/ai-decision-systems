# === PATCH START ============================================================
# 📍 TARGET: engines/analytics/engine_state.py
# 🆕 NEW FILE — used by Bus analytics panel
# 📆 PATCHED: 2026-02-12
# ============================================================================

from engines.config_paths import auto_conn, q_retry as _q
import sqlite3

def count_open_parents():
    con = auto_conn(rw=False); con.row_factory = sqlite3.Row
    r = _q(con, """
        SELECT COUNT(*) AS n FROM orders
         WHERE role='PARENT' AND exit_status IS NULL
    """).fetchone()
    con.close()
    return int(r["n"] or 0)

def count_open_children():
    con = auto_conn(rw=False); con.row_factory = sqlite3.Row
    r = _q(con, """
        SELECT COUNT(*) AS n FROM orders
         WHERE role='CHILD' AND exit_status IS NULL
    """).fetchone()
    con.close()
    return int(r["n"] or 0)

def get_daily_pnl():
    con = auto_conn(rw=False); con.row_factory = sqlite3.Row
    r = _q(con, """
        SELECT SUM(pnl) AS p
          FROM v_dashboard_cashout
         WHERE day = date('now','utc')
    """).fetchone()
    con.close()
    return float(r["p"] or 0.0)

def get_engine_firing_stats():
    con = auto_conn(rw=False); con.row_factory = sqlite3.Row
    rows = _q(con, """
        SELECT source AS engine, COUNT(*) AS n
          FROM decisions
         WHERE datetime(decided_at) >= datetime('now','utc','-10 seconds')
         GROUP BY source
    """).fetchall()
    con.close()
    return {str(r["engine"]): int(r["n"]) for r in rows}

# === PATCH END ================================================================
