#!/usr/bin/env python3
"""
bias_summary.py — aggregates bias_engine outputs into SQL view.
"""

import sqlite3
from engines.config_paths import autoscalp_db

def build_view():
    db = autoscalp_db()
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.executescript("""
    DROP VIEW IF EXISTS bias_summary;
    CREATE VIEW bias_summary AS
    SELECT
        marketId,
        selectionId,
        AVG(bias_value) AS bias_value,
        MAX(bias_dir)   AS bias_dir,
        AVG(bias_conf)  AS bias_conf
      FROM bias_signals
     WHERE date(updated_at)=date('now','utc')
     GROUP BY marketId,selectionId;
    """)
    con.commit(); con.close()
    print("[v7_intel] ✅ bias_summary built.")

if __name__ == "__main__":
    build_view()
