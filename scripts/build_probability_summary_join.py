#!/usr/bin/env python3
"""
scripts/build_probability_summary_join.py
───────────────────────────────────────────────
Phase 8 — Probability Join
Integrates probability_summary into story_training_orders
to form a fully enriched training dataset with
win-probability dynamics.
"""

import sqlite3, time
from engines.config_paths import connect_mastery_v7_cache
con_out = connect_mastery_v7_cache()

# ───────────────────────────────────────────────────────────────────────
def join_probability(day="2025-10-17"):
    print(f"[JOIN] merging probability_summary into story_training_orders for {day}…")
    t0 = time.time()
    con = sqlite3.connect(mastery_v7_db()); con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 1️⃣ ensure both source and target tables exist
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_training_orders (
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            label TEXT,
            narrative TEXT,
            conf REAL,
            drift_speed REAL,
            momentum_class TEXT,
            race_pattern TEXT,
            success_flag INTEGER,
            open_parents INTEGER,
            matched_children INTEGER,
            unhedged_liability REAL,
            net_pl REAL,
            open_loss_flag INTEGER,
            order_summary TEXT,
            p_min REAL,
            p_max REAL,
            p_avg REAL,
            p_range REAL,
            p_last REAL,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY(day, marketId, selectionId)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS probability_summary (
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            p_min REAL,
            p_max REAL,
            p_avg REAL,
            p_range REAL,
            p_last REAL
        )
    """)

    # 2️⃣ attach temporary merged table
    cur.execute("DROP TABLE IF EXISTS story_training_orders_merged")

    cur.execute("""
        CREATE TABLE story_training_orders_merged AS
        SELECT
            s.*,
            p.p_min, p.p_max, p.p_avg, p.p_range, p.p_last
          FROM story_training_orders s
          LEFT JOIN probability_summary p
            USING(day, marketId, selectionId)
         WHERE s.day=?
    """, (day,))

    # 3️⃣ replace old table with merged one
    cur.execute("DROP TABLE story_training_orders")
    cur.execute("ALTER TABLE story_training_orders_merged RENAME TO story_training_orders")
    con.commit()

    # 4️⃣ diagnostic summary
    rows = cur.execute("""
        SELECT
          ROUND(AVG(p_avg),3) AS p_avg_mean,
          ROUND(AVG(p_range),3) AS avg_vol,
          SUM(CASE WHEN p_avg>0.5 THEN 1 ELSE 0 END) AS likely_winners,
          COUNT(*) AS total
        FROM story_training_orders
        WHERE day=?
    """,(day,)).fetchone()
    print(f"[JOIN] ✅ merged {rows['total']} runners "
          f"| mean p_win={rows['p_avg_mean']:.3f} | volatility={rows['avg_vol']:.3f} "
          f"| likely_winners={rows['likely_winners']}")
    print(f"[JOIN] done in {time.time()-t0:.2f}s.")
    con.close()

# ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    join_probability(sys.argv[1] if len(sys.argv)>1 else "2025-10-17")
