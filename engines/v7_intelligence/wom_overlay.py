#!/usr/bin/env python3
"""
wom_overlay.py — Weight of Money overlay
Computes v_weight_of_money (WOM ratio and state).
"""

import sqlite3
from engines.config_paths import autoscalp_db

def build_view():
    db = autoscalp_db()
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.executescript("""
    DROP VIEW IF EXISTS v_weight_of_money;
    CREATE VIEW v_weight_of_money AS
    SELECT
        marketId,
        selectionId,
        ROUND(100.0 * SUM(l1_available_back) /
              NULLIF(SUM(l1_available_back)+SUM(l1_available_lay),0),2) AS wom_ratio,
        CASE
          WHEN ROUND(100.0 * SUM(l1_available_back) /
                     NULLIF(SUM(l1_available_back)+SUM(l1_available_lay),0),2) > 60
               THEN 'BACK_DOM'
          WHEN ROUND(100.0 * SUM(l1_available_back) /
                     NULLIF(SUM(l1_available_back)+SUM(l1_available_lay),0),2) < 40
               THEN 'LAY_DOM'
          ELSE 'BALANCED'
        END AS wom_state
      FROM odds_current
     WHERE date(updated_ts)=date('now','utc')
     GROUP BY marketId,selectionId;
    """)
    con.commit(); con.close()
    print("[v7_intel] ✅ v_weight_of_money built.")

if __name__ == "__main__":
    build_view()
