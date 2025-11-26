#!/usr/bin/env python3
"""
scripts/summarise_race_stories.py
───────────────────────────────────────────────
Phase 5 — Race-Level Narrative Summaries
Aggregates runner-level enriched narratives from
story_enriched_cache → race_narratives.
"""

import sqlite3, time
from collections import Counter
from engines.config_paths import mastery_v7_db

def main(day="2025-10-17"):
    print(f"[RACES] building race summaries for {day}…")
    con = sqlite3.connect(mastery_v7_db()); con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS race_narratives (
            day TEXT,
            marketId TEXT,
            n_runners INTEGER,
            winners INTEGER,
            avg_conf REAL,
            dominant_pattern TEXT,
            dominant_momentum TEXT,
            avg_drift REAL,
            headline TEXT,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY(day, marketId)
        )
    """)

    # fetch all runner-level stories for the chosen day
    rows = con.execute("""
        SELECT marketId, narrative, conf, success_flag,
               momentum_class, race_pattern, drift_speed
          FROM story_enriched_cache
         WHERE day=? 
    """, (day,)).fetchall()
    if not rows:
        print(f"[RACES] ⚠ no enriched stories found for {day}")
        return

    # group by market
    by_mkt = {}
    for r in rows:
        by_mkt.setdefault(r["marketId"], []).append(r)

    t0 = time.time(); inserted = 0
    for mid, runners in by_mkt.items():
        n = len(runners)
        winners = sum(int(r["success_flag"]) for r in runners)
        avg_conf = round(sum(float(r["conf"]) for r in runners) / max(1, n), 3)
        avg_drift = round(sum(float(r["drift_speed"]) for r in runners) / max(1, n), 3)
        momentum_mode = Counter(r["momentum_class"] for r in runners).most_common(1)[0][0]
        pattern_mode = Counter(r["race_pattern"] for r in runners).most_common(1)[0][0]

        # headline synthesis
        if winners == 0:
            headline = f"No clear finisher; {pattern_mode.lower()} shape with {momentum_mode.lower()} momentum."
        elif winners == 1:
            headline = f"Winner emerged from {pattern_mode.lower()} pattern; {momentum_mode.lower()} momentum dominated."
        else:
            headline = f"Multiple contenders; {pattern_mode.lower()} pattern showed volatility."

        cur.execute("""
            INSERT OR REPLACE INTO race_narratives
            (day, marketId, n_runners, winners, avg_conf,
             dominant_pattern, dominant_momentum, avg_drift, headline)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (day, mid, n, winners, avg_conf,
              pattern_mode, momentum_mode, avg_drift, headline))
        inserted += 1

    con.commit()
    print(f"[RACES] ✅ built {inserted} race summaries in {time.time()-t0:.2f}s")

    # sample output
    for r in con.execute("""
        SELECT marketId, headline, dominant_pattern, dominant_momentum, avg_conf
          FROM race_narratives
         WHERE day=? ORDER BY RANDOM() LIMIT 3
    """, (day,)):
        print(f"🏇 {r['marketId']} → {r['headline']} (conf={r['avg_conf']:.2f})")

    con.close()

if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "2025-10-17")
