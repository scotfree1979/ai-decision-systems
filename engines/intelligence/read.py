# engines/intelligence/read.py

import sqlite3
from datetime import datetime, timezone

DB_PATH = "data/mastery_v7.db"


def get_intelligence_snapshot(marketId, selectionId, *, engine_family=None):
    bin_key = f"{marketId}|{selectionId}"

    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row

    try:
        row = con.execute("""
            SELECT
                c.marketId,
                c.selectionId,
                c.engine_family,
                c.letter,
                c.band,
                c.fav_rank,
                c.minutes_to_off,
                c.in_play,
                c.fav_bucket,
                c.fav_percentile,
                c.is_favourite,
                c.is_top_3,
                c.is_longshot,

                p.bucket_confidence AS forest_conf,
                p.confidence        AS river_conf,

                (0.6 * p.bucket_confidence +
                 0.4 * p.confidence) AS hybrid_conf

            FROM v_mastery_v7_context c
            LEFT JOIN mastery_posteriors p
              ON p.bin_key = ?

            WHERE c.marketId = ?
              AND c.selectionId = ?
            LIMIT 1;
        """, (bin_key, marketId, selectionId)).fetchone()

        if not row:
            return None

        snap = dict(row)

        # --- Brain overlay (global) ---
        brain = con.execute("""
            SELECT coherence, divergence, entropy
            FROM brain_prints
            ORDER BY recorded_at DESC
            LIMIT 1;
        """).fetchone()

        if brain:
            snap["brain_coherence"]  = brain["coherence"]
            snap["brain_divergence"] = brain["divergence"]
            snap["brain_entropy"]    = brain["entropy"]
        else:
            snap["brain_coherence"]  = None
            snap["brain_divergence"] = None
            snap["brain_entropy"]    = None

        snap["bin_key"] = bin_key
        snap["ts"] = datetime.now(timezone.utc).isoformat()
        snap["source"] = "mastery_v7"

        return snap

    finally:
        con.close()
