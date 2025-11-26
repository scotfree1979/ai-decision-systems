#!/usr/bin/env python3
"""
Phase 2 – Training / Adaptation
──────────────────────────────────────────────────────────────────────────────
Reads Phase 1 results (mastery_training_metrics + brain_state_v7),
evaluates 18 brain-coherence questions, computes per-bucket strengths,
updates mastery_posteriors, and records an ADAPTIVE layer snapshot.


Run standalone:
    python3 -m engines.mastery.phase2_training
"""

import pandas as pd, numpy as np, sqlite3, math
from datetime import datetime, timezone
from engines.config_paths import connect_mastery_v7_db

# --- FIX: ensure local connection helper is available ---
from engines.mastery.train_mastery_v7 import _connect_local
def _local_db():
    import os
    return os.path.join(os.path.dirname(__file__), "../../data/mastery_v7.db")
# --------------------------------------------------------


# inside engines/mastery/phase2_training.py near top
import os, sqlite3

DATA_DIR = os.path.join(os.path.dirname(__file__), "../../data")
MASTERY_DB_PATH = os.path.join(DATA_DIR, "mastery_v7.db")

def _local_db() -> str:
    """Return absolute path to the local mastery_v7.db."""
    return MASTERY_DB_PATH



con = _connect_local(MASTERY_DB_PATH)


# ---------------------------------------------------------------------------
def _connect():
    """Canonical connector to mastery_v7.db (WAL, busy_timeout)."""
    return connect_mastery_v7_db(ro=False)


# ---------------------------------------------------------------------------
def _safe(x):
    try:
        return float(x)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
def phase2_training():
    con = _connect()
    cur = con.cursor()
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    print("[phase2] 🧠 starting adaptation / training pass …")

    # ── Load Phase 1 metrics --------------------------------------------------
    try:
        df = pd.read_sql("SELECT * FROM mastery_training_metrics", con)
    except Exception as e:
        print(f"[phase2] ❌ failed to read mastery_training_metrics: {e}")
        return

    if df.empty:
        print("[phase2] ⚠️  no Phase 1 metrics found — nothing to train.")
        return

    # Aggregate mean metric per bucket
    agg = (
        df.groupby("bucket", dropna=False)["value"]
        .mean()
        .reset_index()
        .rename(columns={"value": "mean_value"})
    )
    agg["mean_value"] = agg["mean_value"].fillna(0)
    buckets = dict(zip(agg["bucket"], agg["mean_value"]))
    if not buckets:
        print("[phase2] ⚠️  no buckets detected.")
        return

    # ── Define the 18 brain questions ----------------------------------------
    questions = [
        "How stable is the brain’s global coherence (p1_mean) across live and replay modes?",
        "Which macro buckets diverge most between confidence and realised outcomes?",
        "Can shifts in drift_speed or inplay_progress predict degradation in coherence?",
        "How tightly is coherence linked to goal alignment index (GAI)?",
        "Are blueprint matches strengthening model confidence faster than non-matches?",
        "Does exposure stress from high-liability markets reduce coherence stability?",
        "How quickly does feedback correction restore equilibrium after a large delta?",
        "Are replay outcomes converging with live outcomes?",
        "Do confidence spikes precede changes in PnL volatility?",
        "What is the latency between training snapshot and dashboard reflection?",
        "Are strong form bands maintaining coherence alignment in live trades?",
        "How effective is Overwatcher feedback during in-play drift extremes?",
        "At what sample size does live data stop improving variance reduction?",
        "Are there fatigue patterns when coherence decays?",
        "Does goal-reinforcement rise predictably after coherence correction?",
        "How consistent is coherence recovery between sessions?",
        "Can coherence deltas be a leading indicator of near-term PnL?",
        "Are coherence and goal alignment trending toward equilibrium?",
    ]

    # ── Score questions as relative strengths (mocked proportional weights) ---
    # For now use normalised bucket means as signal source.
    mean_abs = max(1e-9, np.mean(list(buckets.values())))
    rows = []
    qid = 1
    for qtext in questions:
        q_code = f"Q-BRAIN-{qid:02d}"
        for bname, val in buckets.items():
            strength = max(0.0, min(1.0, (val / mean_abs) * 0.5 + 0.5))
            rows.append(
                (bname, q_code, "brain_question", strength, 1.0, "phase2", qtext)
            )
        qid += 1

    # ── Insert metrics --------------------------------------------------------
    cur.executemany(
        """
        INSERT INTO mastery_training_metrics
        (bucket,question_id,metric,value,weight,source,notes)
        VALUES (?,?,?,?,?,?,?)
        """,
        rows,
    )
    con.commit()
    print(f"[phase2] ✅ inserted {len(rows)} Q-BRAIN metrics.")

    # ── Compute per-bucket adaptive strengths ---------------------------------
    df2 = pd.DataFrame(rows, columns=["bucket", "qid", "metric", "value", "w", "src", "notes"])
    strength_map = df2.groupby("bucket")["value"].mean().to_dict()
    print("[phase2] 🔍 bucket strength map:", strength_map)

    # 🚫 Skip live posteriors update — deferred to River phase
    print("[phase2] ℹ️ Skipping live posteriors update (handled later by River).")

    # ── Record ADAPTIVE layer snapshot ---------------------------------------
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

    for b, s in strength_map.items():
        cur.execute(
            """
            INSERT INTO brain_state_v7(layer,bucket,samples,total_pnl,coherence,adjustment,notes)
            VALUES('ADAPTIVE',?,?,0,?, ?,?)
            """,
            (
                b,
                s,
                s,
                s - 0.5,
                f"Phase 2 adaptation {now_iso}",
            ),
        )
    con.commit()
    print(f"[phase2] 🧩 inserted {len(strength_map)} ADAPTIVE rows into brain_state_v7.")

    # ── Snapshot to brain_history_v7 ----------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS brain_history_v7(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT DEFAULT (datetime('now','utc')),
            run_tag TEXT,
            layer TEXT,
            bucket TEXT,
            samples INTEGER,
            coherence REAL,
            adjustment REAL,
            notes TEXT
        );
    """)
    run_tag = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for b, s in strength_map.items():
        cur.execute(
            """
            INSERT INTO brain_history_v7(run_tag,layer,bucket,samples,coherence,adjustment,notes)
            VALUES(?,?,?,?,?,?,?)
            """,
            (
                run_tag,
                "ADAPTIVE",
                b,
                0,
                s,
                s - 0.5,
                f"Phase 2 adaptive snapshot {now_iso}",
            ),
        )
    con.commit()
    con.close()

    print("[phase2] ✅ Phase 2 complete — adaptive strengths recorded.")
    print("[phase2] — Summary —")
    for b, s in strength_map.items():
        label = "↑" if s > 0.55 else "↓" if s < 0.45 else "→"
        print(f"  {b:<30} {label} {s:.3f}")

    print("[phase2] done.")

# === PATCH START ===
# 📍 TARGET: engines/mastery/phase2_training.py : phase2_training()
# 📆 PATCHED: 2025-11-14Z — local bridge-feedback assimilation (no orders)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- NEW: assimilate bridge feedback directly from autoscalp_gui.db ---
    try:
        import sqlite3, os, json
        gui_db = os.path.join(os.path.dirname(__file__), "../../data/autoscalp_gui.db")
        if not os.path.exists(gui_db):
            print(f"[phase2] ⚠️ bridge feedback skipped — GUI DB missing ({gui_db})")
        else:
            gcon = sqlite3.connect(gui_db); gcon.row_factory = sqlite3.Row
            df_bridge = pd.read_sql("""
                SELECT bucket,
                       AVG(coherence) AS mean_coh,
                       AVG(adjustment) AS mean_adj,
                       AVG(goal_alignment) AS mean_goal
                  FROM bridge_decision_log
                 WHERE ts_sent >= datetime('now','-7 day','utc')
                 GROUP BY bucket
            """, gcon)
            gcon.close()

            if df_bridge.empty:
                print("[phase2] ℹ️ no recent bridge feedback found.")
            else:
                rows = []
                for _, r in df_bridge.iterrows():
                    rows.append((
                        r["bucket"],
                        "BRIDGE_FEEDBACK",
                        "bridge_metric",
                        float(r["mean_coh"] or 0.0),
                        1.0,
                        "phase2_bridge",
                        json.dumps({
                            "adjustment": r["mean_adj"],
                            "goal_alignment": r["mean_goal"]
                        })
                    ))
                con.executemany("""
                    INSERT INTO mastery_training_metrics
                        (bucket,question_id,metric,value,weight,source,notes)
                    VALUES (?,?,?,?,?,?,?)
                """, rows)
                con.commit()
                print(f"[phase2] ✅ assimilated {len(rows)} bridge feedback metrics into training pool.")
    except Exception as e:
        print(f"[phase2] ⚠️ bridge feedback assimilation warn: {e}")
# === PATCH END ===


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    phase2_training()
