#!/usr/bin/env python3
"""
volatility_meta.py — summarises avg_volatility & trading range per runner.
"""

import sqlite3
from engines.config_paths import autoscalp_db

def build_table():
    db = autoscalp_db()
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.executescript("""
    DROP VIEW IF EXISTS volatility_meta;
    CREATE VIEW volatility_meta AS
    SELECT
        marketId,
        selectionId,
        ROUND(AVG(abs(odd - LAG(odd) OVER (
            PARTITION BY marketId,selectionId ORDER BY datetime(snapshot_ts)
        ))/odd),4) AS avg_volatility,
        MIN(odd) AS range_low,
        MAX(odd) AS range_high
      FROM oc_series
     WHERE odd IS NOT NULL
     GROUP BY marketId,selectionId;
    """)
    con.commit(); con.close()
    print("[v7_intel] ✅ volatility_meta built.")

if __name__ == "__main__":
    build_table()
