#!/usr/bin/env python3
"""
Phase 1 – Understanding (Cognitive Perception Pass)
────────────────────────────────────────────────────
Reads v7_mastery_race_view_new from mastery_v7.db, derives narrative /
coherence metrics, and records them into mastery_training_metrics and
brain_state_v7.

Safe to run standalone.
"""

import pandas as pd, numpy as np
from datetime import datetime, timezone
from engines.config_paths import connect_mastery_v7_db


def _connect():
    """Open canonical mastery_v7.db via config_paths (WAL + busy_timeout)."""
    return connect_mastery_v7_db(ro=False)


def phase1_understanding():
    con = _connect()
    cur = con.cursor()

    # ── verify that the expected view exists ─────────────────────────────
    chk = cur.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='view' AND name='v7_mastery_race_view_new'"
    ).fetchone()
    if not chk:
        print("❌  [phase1] v7_mastery_race_view_new not found in mastery_v7.db.")
        print("👉  Re-create the race-intelligence views, then rerun Phase 1.")
        con.close()
        return

    print("[phase1] 🧠 starting cognitive understanding pass …")

    # ── load unified race-intelligence data ──────────────────────────────
    df = pd.read_sql("SELECT * FROM v7_mastery_race_view_new", con)
    if df.empty:
        print("[phase1] ⚠️  v7_mastery_race_view_new is empty — nothing to analyse.")
        con.close()
        return

    # ── derived cognitive features ───────────────────────────────────────
    df["coherence_gap"] = (df["pre_conf"] - df["inplay_conf"]).abs()
    df["story_weight"] = 1.0 / (1.0 + df["story_divergence"].abs().fillna(0))
    df["confidence_shift"] = df["inplay_conf"] - df["pre_conf"]

    # ── aggregate by narrative bucket ────────────────────────────────────
    aggs = (
        df.groupby("bucket", dropna=False)
          .agg(
              samples=("selectionId", "count"),
              mean_pnl=("pnl", "mean"),
              mean_coherence=("coherence", "mean"),
              mean_story_weight=("story_weight", "mean"),
              mean_coherence_gap=("coherence_gap", "mean"),
              success_rate=("success", "mean"),
          )
          .reset_index()
    )

    # ── ensure metrics table exists ──────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mastery_training_metrics(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now','utc')),
            bucket TEXT,
            question_id TEXT,
            metric TEXT,
            value REAL,
            weight REAL,
            source TEXT,
            notes TEXT
        );
    """)

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for _, r in aggs.iterrows():
        bucket = r["bucket"]
        rows.extend([
            (bucket, "Q-STORY-01", "mean_pnl", r["mean_pnl"], 1.0, "phase1", "avg PnL by bucket"),
            (bucket, "Q-STORY-02", "mean_coherence", r["mean_coherence"], 1.0, "phase1", "avg coherence"),
            (bucket, "Q-STORY-03", "mean_story_weight", r["mean_story_weight"], 1.0, "phase1", "avg story weight"),
            (bucket, "Q-STORY-04", "mean_coherence_gap", r["mean_coherence_gap"], 1.0, "phase1", "avg coherence gap"),
            (bucket, "Q-STORY-05", "success_rate", r["success_rate"], 1.0, "phase1", "success rate by story"),
        ])

    cur.executemany("""
        INSERT INTO mastery_training_metrics(
            bucket,question_id,metric,value,weight,source,notes
        ) VALUES(?,?,?,?,?,?,?)
    """, rows)
    con.commit()

    # ── ensure brain_state_v7 exists and insert snapshot ─────────────────
    cur.execute("""
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

    for _, r in aggs.iterrows():
        cur.execute("""
            INSERT INTO brain_state_v7(
                layer,bucket,samples,total_pnl,coherence,adjustment,notes
            ) VALUES('COGNITIVE',?,?,?,?,?,?)
        """, (
            r["bucket"],
            int(r["samples"]),
            float(r["mean_pnl"]),
            float(r["mean_coherence"]),
            float(r["mean_coherence_gap"]),
            f"Phase 1 snapshot {now_iso}",
        ))


    con.commit()
    con.close()
    print(f"[phase1] ✅ recorded {len(rows)} metrics across {len(aggs)} buckets.")


if __name__ == "__main__":
    phase1_understanding()
