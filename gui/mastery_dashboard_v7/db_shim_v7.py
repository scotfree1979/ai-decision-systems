#!/usr/bin/env python3
"""
db_shim_v7 — non-intrusive overlay for Mastery Dashboard v7.
All reads are live, read-only, and confined to v7 code only.
"""

import sqlite3, os

DATA_DIR = os.path.join(os.path.dirname(__file__), "../..", "data")

DB_PATHS = {
    "AUTO": os.path.join(DATA_DIR, "autoscalp_gui.db"),
    "BETS": os.path.join(DATA_DIR, "bets.db"),
    "SETTLE": os.path.join(DATA_DIR, "settlements.db"),
}

# ────────────────────────────────────────────────────────────────
def connect_v7(which="AUTO"):
    """Return a read-only, shared-cache connection to the chosen DB."""
    path = DB_PATHS[which]
    uri = f"file:{path}?mode=ro&cache=shared"
    con = sqlite3.connect(uri, uri=True, isolation_level=None, timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only = ON;")
    con.execute("PRAGMA journal_mode = WAL;")
    con.execute("PRAGMA synchronous = NORMAL;")
    return con

# ────────────────────────────────────────────────────────────────
# Registry of SQL snippets used by helpers (kept inside v7)
SQL_VIEWS = {
    "mastery_snapshot": """
        SELECT letter,
               ROUND(SUM(net_pnl),2) AS pnl,
               COUNT(*) AS trades,
               ROUND(AVG(weight_applied),3) AS avg_weight,
               MAX(updated_at) AS last_update
          FROM mastery_posteriors
         GROUP BY letter
         ORDER BY pnl DESC;
    """,

    "liability_top": """
        SELECT letter,
               ROUND(SUM(open_liability),2) AS open_liability,
               ROUND(SUM(net_pl),2) AS net_pl
          FROM book_state
         GROUP BY letter
         ORDER BY open_liability DESC;
    """,

    "scalper_opportunities": """
        SELECT marketId, selectionId,
               ltp, back1, lay1, slope_ppm, fav_rank_now
          FROM odds_current
         WHERE slope_ppm IS NOT NULL
         ORDER BY ABS(slope_ppm) DESC
         LIMIT 50;
    """,

    "runner_form": """
        SELECT runner_name, runs, wins, losses, net_pnl,
               trainerName, jockeyName, updated_at
          FROM runner_history
         ORDER BY updated_at DESC
         LIMIT 30;
    """,

    "internal_bank": """
        SELECT current_balance, start_balance, updated_at
          FROM internal_bank
         ORDER BY updated_at DESC
         LIMIT 1;
    """,
}

# ────────────────────────────────────────────────────────────────
def fetch_v7(name, params=(), db="AUTO"):
    """Execute a registered SQL safely in read-only mode."""
    sql = SQL_VIEWS.get(name)
    if not sql:
        raise KeyError(f"[v7] SQL not registered: {name}")
    with connect_v7(db) as con:
        cur = con.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]
