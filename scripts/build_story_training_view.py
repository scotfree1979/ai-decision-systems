#!/usr/bin/env python3
"""
scripts/build_story_training_view.py
───────────────────────────────────────────────
Phase 6 — Trainer-ready View Generator
Combines story_enriched_cache + race_narratives into
story_training_view for Mastery v7 Trainer ingestion.
"""

import sqlite3, time
from engines.config_paths import connect_mastery_v7_cache
con_out = connect_mastery_v7_cache()

def main(day="2025-10-17"):
    print(f"[TRAINER] building trainer view for {day}…")

    con = sqlite3.connect(mastery_v7_db()); con.row_factory = sqlite3.Row
    cur = con.cursor()

    # unified view/table for trainer ingestion
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_training_view (
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            narrative TEXT,
            race_headline TEXT,
            conf REAL,
            drift_speed REAL,
            momentum_class TEXT,
            race_pattern TEXT,
            inplay_progress REAL,
            expected_race_mins REAL,
            success_flag INTEGER,
            dominant_pattern TEXT,
            dominant_momentum TEXT,
            avg_conf REAL,
            avg_drift REAL,
            label TEXT,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY(day, marketId, selectionId)
        )
    """)

    cur.execute("DELETE FROM story_training_view WHERE day=?", (day,))

    # join runner + race narrative caches
    cur.execute("""
        INSERT INTO story_training_view
        (day, marketId, selectionId, narrative, race_headline,
         conf, drift_speed, momentum_class, race_pattern,
         inplay_progress, expected_race_mins, success_flag,
         dominant_pattern, dominant_momentum, avg_conf, avg_drift, label)
        SELECT
            e.day,
            e.marketId,
            e.selectionId,
            e.narrative,
            r.headline,
            e.conf,
            e.drift_speed,
            e.momentum_class,
            e.race_pattern,
            e.inplay_progress,
            e.expected_race_mins,
            e.success_flag,
            r.dominant_pattern,
            r.dominant_momentum,
            r.avg_conf,
            r.avg_drift,
            CASE
                WHEN e.success_flag=1 THEN 'WINNER'
                WHEN e.momentum_class IN ('BULLISH','RALLY') THEN 'STRONG_FINISH'
                WHEN e.momentum_class IN ('BEARISH','FADE') THEN 'WEAK_FINISH'
                ELSE 'NEUTRAL'
            END AS label
        FROM story_enriched_cache e
        LEFT JOIN race_narratives r
              ON e.day=r.day AND e.marketId=r.marketId
        WHERE e.day=?
    """, (day,))
    con.commit()

    # sample output
    rows = con.execute("""
        SELECT label, COUNT(*) AS n, ROUND(AVG(conf),3) AS avg_conf
          FROM story_training_view
         WHERE day=?
         GROUP BY label ORDER BY n DESC
    """, (day,)).fetchall()

    print(f"[TRAINER] ✅ inserted {sum(r['n'] for r in rows)} rows.")
    for r in rows:
        print(f"  {r['label']:<12} → {r['n']:>5} stories (avg_conf={r['avg_conf']:.2f})")

    con.close()
    print(f"[TRAINER] done in {time.time():.2f}s.")

if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "2025-10-17")
