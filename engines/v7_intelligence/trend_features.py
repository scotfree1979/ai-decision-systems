#!/usr/bin/env python3
"""
trend_features.py — simplified per-runner slope + tick velocity metrics
derived from odds_current history for Mastery Intelligence layer.
"""

import sqlite3
from engines.config_paths import autoscalp_db

def build_view():
    db = autoscalp_db()
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.executescript("""
    DROP VIEW IF EXISTS trend_features;
    CREATE VIEW trend_features AS
    SELECT
        marketId,
        selectionId,
        ROUND(AVG(slope_ppm),5) AS slope_ppm,
        ROUND(AVG(tick_vel_3s_up),2) AS tick_vel_3s_up,
        CASE
            WHEN AVG(slope_ppm)<-0.02 THEN 'STEAM'
            WHEN AVG(slope_ppm)>0.02  THEN 'DRIFT'
            ELSE 'FLAT'
        END AS momentum_class
      FROM odds_current
     WHERE slope_ppm IS NOT NULL
     GROUP BY marketId,selectionId;
    """)
    con.commit(); con.close()
    print("[v7_intel] ✅ trend_features built.")

if __name__ == "__main__":
    build_view()
