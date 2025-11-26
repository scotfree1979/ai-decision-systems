#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_posteriors.py — consolidate mastery_outcomes_raw → mastery_posteriors

Run this AFTER replay has finished writing outcomes.
Safe to re-run multiple times; uses UPSERT to refresh posteriors.
"""

import sqlite3, sys
from datetime import datetime

AUTO_DB = "data/autoscalp_gui.db"

def consolidate(batch_days=30):
    con = sqlite3.connect(AUTO_DB, timeout=60)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # ensure schema
    cur.executescript("""
    CREATE TABLE IF NOT EXISTS mastery_outcomes_raw(
        day TEXT,
        marketId TEXT,
        selectionId TEXT,
        distance_band TEXT,
        fav_rank INTEGER,
        dow TEXT,
        surface TEXT,
        pnl REAL
    );
    CREATE TABLE IF NOT EXISTS mastery_posteriors(
        band_key TEXT PRIMARY KEY,
        total_obs INTEGER,
        win_obs INTEGER,
        avg_pnl REAL,
        updated_at TEXT
    );
    """)

    # group raw outcomes into bands
    q = """
    SELECT distance_band, fav_rank, dow, surface,
           COUNT(*) AS n, SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END) AS wins,
           AVG(pnl) AS avg_pnl
      FROM mastery_outcomes_raw
     GROUP BY distance_band, fav_rank, dow, surface
    """

    rows = cur.execute(q).fetchall()
    now = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    for r in rows:
        key = f"{r['distance_band']}|{r['fav_rank']}|{r['dow']}|{r['surface']}"
        cur.execute("""
        INSERT INTO mastery_posteriors(band_key,total_obs,win_obs,avg_pnl,updated_at)
        VALUES(?,?,?,?,?)
        ON CONFLICT(band_key) DO UPDATE SET
          total_obs=excluded.total_obs,
          win_obs=excluded.win_obs,
          avg_pnl=excluded.avg_pnl,
          updated_at=excluded.updated_at
        """, (key, r["n"], r["wins"], r["avg_pnl"], now))

    con.commit()
    con.close()
    print(f"[POSTERIORS] consolidated {len(rows)} bands at {now}")

if __name__ == "__main__":
    consolidate()
