#!/usr/bin/env python3
"""
scripts/enrich_story_cache.py
───────────────────────────────────────────────
Phase 4 — Narrative Enrichment (Historical Mode)
Uses v7_intelligence and v7_shape_summary to expand
story_alignment_cache into story_enriched_cache.
"""

import sys, os, sqlite3, math, statistics, time
from datetime import datetime

# ── sys.path injection so engines.* imports resolve ────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engines.config_paths import mastery_v7_db, autoscalp_db

def main(day="2025-10-17"):
    print(f"[ENRICH] building enriched narratives for {day}…")

    t0 = time.time()
    con_out = sqlite3.connect(mastery_v7_db()); con_out.row_factory = sqlite3.Row
    con_v7  = sqlite3.connect(autoscalp_db());  con_v7.row_factory  = sqlite3.Row
    cur_o   = con_out.cursor()

    # 🔧 Speed pragmas
    con_out.execute("PRAGMA synchronous=OFF;")
    con_out.execute("PRAGMA journal_mode=MEMORY;")
    con_out.execute("PRAGMA temp_store=MEMORY;")

    # ensure output table exists
    cur_o.execute("""
        CREATE TABLE IF NOT EXISTS story_enriched_cache (
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            narrative TEXT,
            conf REAL,
            success_flag INTEGER,
            drift_speed REAL,
            momentum_class TEXT,
            race_pattern TEXT,
            inplay_progress REAL,
            expected_race_mins REAL,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY(day, marketId, selectionId)
        )
    """)
    cur_o.execute("DELETE FROM story_enriched_cache WHERE day=?;", (day,))

    # 🧩 One SQL join instead of thousands of queries
    # 🧩 Attach GUI DB for cross-database join
    con_out.execute(f"ATTACH DATABASE '{autoscalp_db()}' AS gui")

    cur_o.execute(f"""
        INSERT INTO story_enriched_cache
        (day, marketId, selectionId, narrative, conf, success_flag,
         drift_speed, momentum_class, race_pattern,
         inplay_progress, expected_race_mins)
        SELECT
            '{day}' AS day,
            a.marketId,
            a.selectionId,
            'Momentum class ' || LOWER(COALESCE(v.momentum_class,'unknown')) ||
            ' — drift ' || printf('%+.2f', COALESCE(v.drift_speed,0)) ||
            '. In-play progress ' || printf('%.2f', COALESCE(v.inplay_progress,0)) ||
            '/' || printf('%.2f', COALESCE(v.expected_race_mins,0)) ||
            ' min. Pattern: ' || COALESCE(s.race_pattern,'NA') || '. Runner ' ||
            CASE
                WHEN a.story_divergence < -0.1 THEN 'strengthened markedly from pre-off to finish.'
                WHEN a.story_divergence >  0.1 THEN 'weakened after showing early promise.'
                ELSE 'held steady without major change.'
            END AS narrative,
            ROUND((a.pre_conf + a.inplay_conf)/2.0,3) AS conf,
            COALESCE(v.success, a.winner_flag, 0) AS success_flag,

            COALESCE(v.drift_speed,0),
            COALESCE(v.momentum_class,'UNKNOWN'),
            COALESCE(s.race_pattern,'NA'),
            COALESCE(v.inplay_progress,0),
            COALESCE(v.expected_race_mins,0)
        FROM story_alignment_cache AS a
        LEFT JOIN gui.v7_intelligence AS v
               ON a.marketId=v.marketId AND a.selectionId=v.selectionId
        LEFT JOIN gui.v7_shape_summary AS s
               ON a.marketId=s.marketId AND a.selectionId=s.selectionId
        WHERE a.day='{day}';
    """)

    con_out.commit()
    con_out.execute("DETACH DATABASE gui")

    n = cur_o.execute("SELECT COUNT(*) FROM story_enriched_cache WHERE day=?;", (day,)).fetchone()[0]
    print(f"[ENRICH] ✅ built {n} enriched stories in {time.time()-t0:.2f}s")

    con_out.close(); con_v7.close()


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "2025-10-17")
