#!/usr/bin/env python3
# engines/mastery/brain_trainer.py
"""
Mastery Brain Trainer — coherence alignment layer
=================================================
Reads macro/global coherence from autoscalp_gui.db
and records alignment results into mastery_v7.db.
"""

import os, sqlite3, statistics, datetime
from engines.config_paths import autoscalp_db

# =====================================================================
# 🧠 MICRO / MACRO POT DERIVATION (AUTHORITATIVE)
# =====================================================================

def _compute_engine_pots(con_gui: sqlite3.Connection) -> dict:
    """
    Compute GLOBAL + MICRO pots using settlement truth.
    MICRO pots are split by engine only.

    Returns:
        {
          "GLOBAL": float,
          "ENGINE": { engine_name: pnl }
        }
    """

    cur = con_gui.cursor()

    cur.execute("ATTACH DATABASE 'data/settlements.db' AS settle;")

    rows = cur.execute("""
        WITH winners AS (
            SELECT
                mb.marketId,
                json_extract(r.value, '$.selectionId') AS selectionId
            FROM settle.bf_market_book mb,
                 json_each(mb.resultJson) r
            WHERE
                mb.status = 'CLOSED'
                AND json_extract(r.value, '$.status') = 'WINNER'
        ),

        parent_orders AS (
            SELECT
                o.marketId,
                o.selectionId,
                o.engine,
                UPPER(o.side)  AS side,
                o.entry_odds   AS odds,
                o.entry_stake AS stake
            FROM orders o
            WHERE
                o.mode = 'LIVE'
                AND (o.role IS NULL OR o.role = 'PARENT')
                AND o.entry_status = 'MATCHED'
        ),

        runner_pnl AS (
            SELECT
                p.engine,
                CASE
                    WHEN p.side = 'BACK' AND w.selectionId IS NOT NULL
                        THEN (p.odds - 1.0) * p.stake
                    WHEN p.side = 'BACK' AND w.selectionId IS NULL
                        THEN -p.stake
                    WHEN p.side = 'LAY'  AND w.selectionId IS NOT NULL
                        THEN -(p.odds - 1.0) * p.stake
                    WHEN p.side = 'LAY'  AND w.selectionId IS NULL
                        THEN p.stake
                    ELSE 0.0
                END AS pnl
            FROM parent_orders p
            LEFT JOIN winners w
              ON w.marketId = p.marketId
             AND w.selectionId = p.selectionId
        )

        SELECT
            engine,
            ROUND(SUM(pnl), 2) AS total_pnl
        FROM runner_pnl
        GROUP BY engine;
    """).fetchall()

    cur.execute("DETACH DATABASE settle;")

    engine_pots = {}
    global_pnl = 0.0

    for r in rows:
        engine = r["engine"] or "UNKNOWN"
        pnl = float(r["total_pnl"] or 0.0)
        engine_pots[engine] = pnl
        global_pnl += pnl

    return {
        "GLOBAL": round(global_pnl, 2),
        "ENGINE": engine_pots
    }

# ======================================================================================================
# 📍 TARGET: engines/mastery/brain_trainer.py
# 🔎 SEARCH: def train_brain():
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-01-12 — restore engine-based lobes + settlement-truth pots
#
# RATIONALE:
# - Old brain architecture expected "lobes"
# - Lobes are now ENGINE families
# - MONEY truth comes ONLY from settlements + orders
# - BELIEF truth comes ONLY from mastery_posteriors
# - Cache is NEVER used for P&L
# ======================================================================================================

def train_brain():
    """
    Authoritative Brain Trainer

    Responsibilities:
    - Compute settlement-truth GLOBAL + MICRO (engine) pots
    - Persist pots into brain_state_v7
    - Compute coherence ONLY from mastery_posteriors (belief-space)
    """

    # --------------------------------------------------
    # Connections
    # --------------------------------------------------
    gui_db   = autoscalp_db()
    brain_db = os.path.join(os.path.dirname(__file__), "../../data/mastery_v7.db")

    con_gui = sqlite3.connect(gui_db)
    con_gui.row_factory = sqlite3.Row

    con_brain = sqlite3.connect(brain_db)
    con_brain.row_factory = sqlite3.Row
    cur_brain = con_brain.cursor()

    # --------------------------------------------------
    # Ensure brain_state_v7 exists
    # --------------------------------------------------
    cur_brain.execute("""
        CREATE TABLE IF NOT EXISTS brain_state_v7(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT DEFAULT (datetime('now','utc')),
            layer TEXT,
            bucket TEXT,
            samples INTEGER,
            total_pnl REAL,
            coherence REAL,
            adjustment REAL,
            notes TEXT
        );
    """)

    # --------------------------------------------------
    # 1️⃣ SETTLEMENT-TRUTH POT DERIVATION (ENGINE = LOBE)
    # --------------------------------------------------
    pots = _compute_engine_pots(con_gui)

    # GLOBAL pot
    cur_brain.execute("""
        INSERT INTO brain_state_v7
        (layer, bucket, samples, total_pnl, coherence, adjustment, notes)
        VALUES ('GLOBAL', 'GLOBAL', 0, ?, NULL, NULL, 'settlement-truth global pot')
    """, (pots["GLOBAL"],))

    # MICRO pots (engine-scoped lobes)
    for engine, pnl in pots["ENGINE"].items():
        cur_brain.execute("""
            INSERT INTO brain_state_v7
            (layer, bucket, samples, total_pnl, coherence, adjustment, notes)
            VALUES ('MICRO', ?, 0, ?, NULL, NULL, 'engine-scoped settlement pot')
        """, (engine, pnl))

    # --------------------------------------------------
    # 2️⃣ BELIEF-SPACE COHERENCE (NO MONEY)
    # --------------------------------------------------
    try:
        rows = con_gui.execute("""
            SELECT
                bucket,
                AVG(confidence)        AS river_mean,
                AVG(bucket_confidence) AS forest_mean
            FROM mastery_posteriors
            GROUP BY bucket;
        """).fetchall()
    except Exception as e:
        print(f"[brain_trainer] ⚠️ coherence skipped ({e})")
        con_gui.close()
        con_brain.commit()
        con_brain.close()
        return

    if rows:
        deltas = [
            abs((r["river_mean"] or 0.5) - (r["forest_mean"] or 0.5))
            for r in rows
        ]
        coherence = 1.0 - (sum(deltas) / len(deltas)) if deltas else 0.0

        cur_brain.execute("""
            INSERT INTO brain_state_v7
            (layer, bucket, samples, total_pnl, coherence, adjustment, notes)
            VALUES ('MACRO', 'COHERENCE', ?, NULL, ?, NULL, 'posterior belief coherence')
        """, (len(rows), coherence))

        print(f"[brain_trainer] 🧠 coherence={coherence:.3f}")

    # --------------------------------------------------
    # Cleanup
    # --------------------------------------------------
    con_gui.close()
    con_brain.commit()
    con_brain.close()



if __name__ == "__main__":
    train_brain()
