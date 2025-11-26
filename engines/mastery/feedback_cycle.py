#!/usr/bin/env python3
"""
engines/mastery/feedback_cycle.py — Unified daily reinforcement cycle
───────────────────────────────────────────────────────────────────────────────
Runs at the end of a trading day to consolidate live results into Mastery.

Pipeline:
  1️⃣ Aggregate today's cash-out + feedback signals.
  2️⃣ Assimilate them into mastery_posteriors.
  3️⃣ Compute goal-alignment delta vs Core Values.
  4️⃣ Update mastery_state with daily learning snapshot.
  5️⃣ Trigger auto-checkpoint for next-day strategy weighting.
───────────────────────────────────────────────────────────────────────────────
Usage:
    python3 -m engines.mastery.feedback_cycle
"""

from __future__ import annotations
import sqlite3, json
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db
from engines.mastery import event_sink
from engines.mastery.feedback_assimilator import assimilate_feedback
from engines.mastery.goal_adapter import as_feedback_dict
from engines.config_core_values import get_core_values
from engines.mastery.canonical_digest import total_pnl
from engines.cashout_calc import cashout_calc

# ───────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def run_cycle(limit_minutes: int = 1440) -> None:
    """Execute a full reinforcement cycle for today's markets."""
    print("\n[feedback_cycle] 🚀 Starting daily Mastery reinforcement …")

    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row

    # 1️⃣ Aggregate today's settled results (KPI-aligned)
    try:
        print("[feedback_cycle] collecting settlement-based totals (KPI parity) …")
        import os
        db_path = os.path.join(os.path.dirname(autoscalp_db()), "settlements.db")
        con_set = sqlite3.connect(db_path)
        con_set.row_factory = sqlite3.Row

        # same logic as dashboard KPIs
        row = con_set.execute("""
            SELECT 
                COALESCE(SUM(net),0.0) AS pnl_today,
                SUM(CASE WHEN net>0 THEN 1 ELSE 0 END)*1.0/NULLIF(COUNT(*),0) AS win_rate
            FROM v_settle_mkt_day
            WHERE day=date('now','utc')
        """).fetchone()
        pnl_today = float(row["pnl_today"] or 0.0)
        win_rate = float(row["win_rate"] or 0.0)
        con_set.close()
        print(f"[feedback_cycle] settled snapshot → pnl={pnl_today:.2f}  win_rate={win_rate:.2%}")
    except Exception as e:
        print(f"[feedback_cycle] settlement snapshot warn: {e}")
        pnl_today, win_rate = 0.0, 0.0

    # 2️⃣ Assimilate feedback
    try:
        a = assimilate_feedback(limit_minutes)
        print(f"[feedback_cycle] feedback assimilated rows={a}")
    except Exception as e:
        print(f"[feedback_cycle] assimilator warn: {e}")

    # 3️⃣ Compute goal alignment vs Core Values
    vals = get_core_values()
    try:
        # derive from mastery_posteriors success ratio
        row = con.execute("""
            SELECT SUM(n_success)*1.0/NULLIF(SUM(n_total),0)
              FROM mastery_posteriors
             WHERE updated_at >= datetime('now','utc','-1 day')
        """).fetchone()
        if row and row[0] is not None:
            win_rate = float(row[0])

        payload = as_feedback_dict(
            live_pnl=pnl_today,
            win_rate=win_rate,
            matched_ratio=vals["matched_target"]
        )
        goal_score = payload.get("goal_alignment", 0.0)
        event_sink.emit("goal_alignment_tick", payload)
        print(f"[feedback_cycle] goal_alignment={goal_score:.3f}  win_rate={win_rate:.2%}")
    except Exception as e:
        print(f"[feedback_cycle] goal_adapter warn: {e}")
        goal_score = 0.0

    # 4️⃣ Update mastery_state with learning checkpoint
    try:
        # auto-create or migrate schema if columns missing
        con.execute("""
            CREATE TABLE IF NOT EXISTS mastery_state(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT DEFAULT (datetime('now','utc')),
                version INTEGER DEFAULT 0,
                thresholds_json TEXT,
                progress INTEGER DEFAULT 0,
                source TEXT,
                goal_alignment REAL DEFAULT 0.0,
                pnl_today REAL DEFAULT 0.0,
                win_rate_today REAL DEFAULT 0.0
            )
        """)
        existing = [r[1] for r in con.execute("PRAGMA table_info(mastery_state)")]
        if "pnl_today" not in existing:
            con.execute("ALTER TABLE mastery_state ADD COLUMN pnl_today REAL DEFAULT 0.0;")
        if "win_rate_today" not in existing:
            con.execute("ALTER TABLE mastery_state ADD COLUMN win_rate_today REAL DEFAULT 0.0;")
        con.commit()

        meta = {
            "pnl_today": pnl_today,
            "goal_alignment": goal_score,
            "win_rate": win_rate,
            "core_values": vals,
        }

        con.execute("""
            INSERT INTO mastery_state(ts, version, thresholds_json,
                                      progress, source,
                                      goal_alignment, pnl_today, win_rate_today)
            VALUES(datetime('now','utc'), 1, ?, 1, 'FEEDBACK_CYCLE',
                   ?, ?, ?)
        """, (json.dumps(meta), goal_score, pnl_today, win_rate))
        con.commit()
        print(f"[feedback_cycle] mastery_state updated → goal_alignment={goal_score:.3f}")
    except Exception as e:
        print(f"[feedback_cycle] mastery_state warn: {e}")

    # 5️⃣ Close + summary
    con.close()
    print(f"[feedback_cycle] ✅ complete @ {_now()}\n")


# ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    run_cycle()
