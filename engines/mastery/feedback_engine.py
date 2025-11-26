# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_engine.py
# 📆 PATCHED: 2025-10-24T17:15Z — Stage 3 Adaptive Feedback Loop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
engines/mastery/feedback_engine.py — Adaptive Feedback Loop (Stage 3)

Compares Mastery intent (plan_ledger) vs live market state (live_state)
and derives learning metrics for Mastery policy tuning.

Outputs:
  • mastery_feedback table  ← persistent bias/exposure deltas
  • feedback_tick events    ← streamed via event_sink for training/replay
"""

from __future__ import annotations
import sqlite3, math, json, os
from datetime import datetime, timezone

from engines.config_paths import autoscalp_db
from engines.mastery import event_sink

DB_PATH = autoscalp_db()

# ────────────────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────────────────
def _ensure_table(conn: sqlite3.Connection):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mastery_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now','utc')),
            marketId TEXT,
            plan_bias REAL,
            actual_bias REAL,
            bias_delta REAL,
            exposure_target REAL,
            exposure_actual REAL,
            exposure_gap REAL,
            hedge_eff REAL,
            pnl_now REAL,
            status TEXT
        )
    """)
    conn.commit()

# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_engine.py:_fetch_plan_intent
# 📆 PATCHED: 2025-10-25T11:20Z — fallback to JSON fields if columns missing
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _fetch_plan_intent(conn: sqlite3.Connection):
    """
    Return latest plan intent per market from plan_ledger.
    Falls back to JSON extraction if plan_bias / target_exposure columns
    are not yet materialized.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(plan_ledger)")}
    if "plan_bias" in cols and "target_exposure" in cols:
        q = """
            SELECT marketId,
                   AVG(plan_bias) AS plan_bias,
                   AVG(target_exposure) AS exposure_target
              FROM plan_ledger
             WHERE datetime(decided_at) >= datetime('now','-6 hour')
          GROUP BY marketId
        """
    else:
        # fallback: extract values directly from JSON
        q = """
            SELECT marketId,
                   AVG(CAST(json_extract(plan_json,'$.plan_bias') AS REAL)) AS plan_bias,
                   AVG(CAST(json_extract(plan_json,'$.target_exposure') AS REAL)) AS exposure_target
              FROM plan_ledger
             WHERE datetime(decided_at) >= datetime('now','-6 hour')
          GROUP BY marketId
        """

    return {r[0]: dict(plan_bias=r[1] or 0.0,
                       exposure_target=r[2] or 0.0)
            for r in conn.execute(q)}
# === PATCH END ===


def _fetch_live_state(conn: sqlite3.Connection):
    """Return latest state per market from live_state (updated by cashout_calc)."""
    q = """
        SELECT marketId, pnl_now, liability
          FROM live_state
      ORDER BY updated_at DESC
    """
    return {r[0]: dict(pnl_now=r[1] or 0.0, liability=r[2] or 0.0)
            for r in conn.execute(q)}

# ────────────────────────────────────────────────────────────────────────
# Core feedback engine
# ────────────────────────────────────────────────────────────────────────
def compute_feedback(limit: int = 50):
    """
    Join plan_ledger ↔ live_state, compute bias / exposure deltas,
    log to mastery_feedback, and emit feedback_tick events.
    """
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    _ensure_table(con)

    plan = _fetch_plan_intent(con)
    live = _fetch_live_state(con)

    rows = []
    ts_now = datetime.now(timezone.utc).isoformat()

    for mid, p in plan.items():
        l = live.get(mid)
        if not l:
            continue

        plan_bias = float(p["plan_bias"])
        actual_bias = float(l["pnl_now"]) / max(abs(l["liability"]), 1e-6)
        bias_delta = actual_bias - plan_bias

        exposure_target = float(p["exposure_target"])
        exposure_actual = float(l["liability"])
        exposure_gap = exposure_actual - exposure_target

        hedge_eff = 1.0 - min(1.0, abs(exposure_gap / (exposure_target or 1e-6)))
        pnl_now = float(l["pnl_now"])
        # === PATCH START ===
        # 📍 TARGET: engines/mastery/feedback_engine.py
        # 📆 PATCHED: 2025-10-25T18:40Z — Canonical trend awareness
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        from engines.mastery.canonical_digest import total_pnl

        canonical_total = total_pnl()
        status = "GAIN" if canonical_total > 0 else "LOSS"
        # === PATCH END ===

        status = "ACTIVE" if abs(pnl_now) < exposure_target else "VOLATILE"

        rows.append((ts_now, mid, plan_bias, actual_bias, bias_delta,
                     exposure_target, exposure_actual, exposure_gap,
                     hedge_eff, pnl_now, status))

    if not rows:
        con.close()
        return 0

    con.executemany("""
        INSERT INTO mastery_feedback
        (ts, marketId, plan_bias, actual_bias, bias_delta,
         exposure_target, exposure_actual, exposure_gap,
         hedge_eff, pnl_now, status)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, rows)
    con.commit()

    # ── Emit feedback_tick events for Mastery / Replay ───────────────────
    try:
        for r in rows:
            payload = dict(
                ts=r[0], marketId=r[1],
                bias_delta=r[4], exposure_gap=r[7],
                hedge_eff=r[8], pnl_now=r[9], status=r[10]
            )
            event_sink.emit("feedback_tick", payload)
    except Exception as e:
        print(f"[feedback] emit warn: {e}")

    con.close()
    return len(rows)

# ────────────────────────────────────────────────────────────────────────
# Convenience CLI
# ────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    n = compute_feedback()
    print(f"[feedback] computed {n} feedback rows")
# === PATCH END ===
