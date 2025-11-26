#!/usr/bin/env python3
"""
market_sentiment.py — drift, steam & volatility overlay
Builds v_market_sentiment for use in v_mastery_intel_v7.
"""

import sqlite3
from engines.config_paths import autoscalp_db

def build_view():
    db = autoscalp_db()
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.executescript("""
    DROP VIEW IF EXISTS v_market_sentiment;
    CREATE VIEW v_market_sentiment AS
    SELECT
        first.marketId,
        first.selectionId,
        ROUND(100*(first.odd - last.odd)/first.odd,2) AS drift_pct,
        CASE
            WHEN (first.odd - last.odd)>0 THEN 'DRIFT'
            WHEN (first.odd - last.odd)<0 THEN 'STEAM'
            ELSE 'FLAT'
        END AS sentiment,
        ROUND(STDEV(oc.odd),4) AS vol,
        CASE
            WHEN STDEV(oc.odd)>0.25 THEN 'HIGH'
            WHEN STDEV(oc.odd)>0.10 THEN 'MEDIUM'
            ELSE 'LOW'
        END AS volatility_state
    FROM oc_series oc
    JOIN (
        SELECT marketId,selectionId,
               MIN(stage) AS min_stage,MAX(stage) AS max_stage
        FROM oc_series GROUP BY marketId,selectionId
    ) rng USING(marketId,selectionId)
    JOIN oc_series first
      ON first.marketId=rng.marketId AND first.selectionId=rng.selectionId AND first.stage=rng.min_stage
    JOIN oc_series last
      ON last.marketId=rng.marketId AND last.selectionId=rng.selectionId AND last.stage=rng.max_stage
    GROUP BY oc.marketId,oc.selectionId;
    """)
    con.commit(); con.close()
    print("[v7_intel] ✅ v_market_sentiment built.")

if __name__ == "__main__":
    build_view()
