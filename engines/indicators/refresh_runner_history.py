#!/usr/bin/env python3
"""
refresh_runner_history.py — Build/refresh runner_history table

- Always: internal form (runs, wins, losses, net_pnl, last_run_date, form_string)
- Optionally: enrichments from market_data (trainer, jockey, OR, age, stall, weight)
- Source DBs:
    bets.db (runner identity)
    settlements.db (outcomes)
    autoscalp_gui.db (target + optional market_data)
"""

import os
import sqlite3
from datetime import datetime, timezone

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
BETS_DB = os.path.join(ROOT, "data", "bets.db")
SETTLE_DB = os.path.join(ROOT, "data", "settlements.db")
AUTO_DB = os.path.join(ROOT, "data", "autoscalp_gui.db")

def refresh_runner_history():
    # === DAL-SAFE WRITER (replace raw sqlite.connect) ===
    from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    # =====================================================

    # Ensure runner_history table exists
    con.execute("""
    CREATE TABLE IF NOT EXISTS runner_history (
        runner_name     TEXT NOT NULL,
        selectionId     INTEGER NOT NULL,
        marketId        TEXT NOT NULL,
        runs            INTEGER DEFAULT 0,
        wins            INTEGER DEFAULT 0,
        losses          INTEGER DEFAULT 0,
        net_pnl         REAL DEFAULT 0.0,
        last_run_date   TEXT,
        last_win_date   TEXT,
        form_string     TEXT,
        trainerName     TEXT,
        jockeyName      TEXT,
        age             INTEGER,
        stallDraw       INTEGER,
        officialRating  INTEGER,
        weightValue     REAL,
        updated_at      TEXT DEFAULT (datetime('now','utc')),
        PRIMARY KEY (runner_name, selectionId)
    )
    """)
    con.commit()


    # Attach supporting DBs
    con.execute(f"ATTACH '{BETS_DB}' AS betsdb")
    con.execute(f"ATTACH '{SETTLE_DB}' AS setdb")

    q = """
    WITH outcomes AS (
      SELECT 
        b.horse_name,
        b.selectionId,
        b.marketId,
        SUM(s.profit) AS net_pnl,
        MAX(s.settledDate) AS last_run_date,
        CASE WHEN SUM(s.profit) > 0 THEN 1 ELSE 0 END AS win
      FROM betsdb.bets b
      JOIN setdb.bf_cleared_orders s
        ON b.marketId=s.marketId AND b.selectionId=s.selectionId
      GROUP BY b.marketId, b.selectionId
    ),
    agg AS (
      SELECT 
        horse_name,
        selectionId,
        marketId,
        COUNT(*) AS runs,
        SUM(win) AS wins,
        COUNT(*)-SUM(win) AS losses,
        SUM(net_pnl) AS net_pnl,
        MAX(last_run_date) AS last_run_date,
        MAX(CASE WHEN win=1 THEN last_run_date END) AS last_win_date
      FROM outcomes
      GROUP BY horse_name, selectionId, marketId
    )
    INSERT OR REPLACE INTO runner_history
    (runner_name, selectionId, marketId, runs, wins, losses, net_pnl, last_run_date, last_win_date,
     form_string, trainerName, jockeyName, age, stallDraw, officialRating, weightValue, updated_at)
    SELECT 
        a.horse_name,
        a.selectionId,
        a.marketId,
        a.runs,
        a.wins,
        a.losses,
        a.net_pnl,
        a.last_run_date,
        a.last_win_date,
        (
          SELECT GROUP_CONCAT(CASE WHEN o.win=1 THEN '1' ELSE '0' END,'')
          FROM outcomes o
          WHERE o.horse_name=a.horse_name AND o.selectionId=a.selectionId
          ORDER BY o.last_run_date DESC
          LIMIT 5
        ) AS form_string,
        m.trainerName,
        m.jockeyName,
        m.age,
        m.stallDraw,
        m.officialRating,
        m.weightValue,
        datetime('now','utc')
    FROM agg a
    LEFT JOIN market_data m
      ON a.selectionId=m.selectionId AND a.horse_name=m.runnerName;
    """

    try:
        con.execute("DELETE FROM runner_history")  # clear old data
        con.execute(q)
        con.commit()
        print(f"[runner_history] refreshed OK @ {datetime.now(timezone.utc).isoformat()}")
    except Exception as e:
        print(f"[runner_history] refresh error: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    refresh_runner_history()
