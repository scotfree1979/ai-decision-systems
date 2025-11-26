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


# === PATCH START ===
# 📍 TARGET: engines/mastery/brain_trainer.py:train_brain
# 📆 PATCHED: 2025-11-10Z — safe key access for sqlite3.Row
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def train_brain():
    live_db  = autoscalp_db()
    local_db = os.path.join(os.path.dirname(__file__), "../../data/mastery_v7.db")
    con = sqlite3.connect(live_db); con.row_factory = sqlite3.Row
    con.execute(f"ATTACH DATABASE '{local_db}' AS brain;")

    con.executescript("""
        DROP VIEW IF EXISTS main.v_mastery_brain_macro;
        CREATE VIEW main.v_mastery_brain_macro AS
        SELECT
            bucket,
            COUNT(*) AS samples,
            ROUND(AVG(confidence),4) AS p1_mean,
            ROUND(AVG(bucket_confidence),4) AS p2_mean,
            ROUND(AVG(live_pnl_ratio),4) AS p3_mean,
            ROUND(AVG(COALESCE(weight, weight_applied, 1.0)),4) AS weight
        FROM mastery_posteriors
        GROUP BY bucket;
    """)
    con.commit()

    try:
        lobes = con.execute("SELECT * FROM main.v_mastery_brain_macro").fetchall()
        globals_ = con.execute("SELECT * FROM main.v_mastery_brain_global").fetchone()
    except sqlite3.OperationalError as e:
        print(f"[brain_trainer] ❌ view read failed: {e}")
        con.close(); return

    if not lobes or not globals_:
        print("[brain_trainer] ⚠️ no brain data found — skipping.")
        con.close(); return

    import statistics, datetime
    p1s = [float(r["p1_mean"] or 0.0) for r in lobes]
    mean_p1 = statistics.mean(p1s) if p1s else 0.0
    var_p1 = statistics.pstdev(p1s) if len(p1s) > 1 else 0.0
    coherence = max(0.0, 1.0 - var_p1)
    g_keys = globals_.keys()
    g_p1 = float(globals_["p1_mean"] or 0.0) if "p1_mean" in g_keys else 0.0
    adjustment = round(mean_p1 - g_p1, 4)
    now = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    if coherence > 0.7 and abs(adjustment) > 0.05:
        payload = {"ts": now, "coherence": coherence, "adjustment": adjustment,
                   "reason": "Brain–Overwatcher bridge pulse"}
        from engines.mastery import event_sink
        event_sink.emit("bridge_pulse", payload)
        print(f"[brain_trainer] ⚡ emitted bridge_pulse (coh={coherence:.2f}, Δ={adjustment:+.3f})")

    con.execute("""
        CREATE TABLE IF NOT EXISTS brain.brain_state_v7(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT DEFAULT (datetime('now','utc')),
            layer TEXT,bucket TEXT,samples INTEGER,total_pnl REAL,
            coherence REAL,adjustment REAL,notes TEXT
        );
    """)

    total_pnl = float(globals_["total_pnl"]) if "total_pnl" in g_keys else 0.0
    con.execute("""
        INSERT INTO brain.brain_state_v7(layer,bucket,samples,total_pnl,coherence,adjustment,notes)
        VALUES('GLOBAL','GLOBAL',?,?,?,?,?)
    """, (globals_["samples"], total_pnl, coherence, adjustment, "global alignment"))

    for r in lobes:
        keys = r.keys()
        total_pnl = float(r["total_pnl"]) if "total_pnl" in keys else 0.0
        con.execute("""
            INSERT INTO brain.brain_state_v7(layer,bucket,samples,total_pnl,coherence,adjustment,notes)
            VALUES('MACRO',?,?,?,?,?,?)
        """, (r["bucket"], r["samples"], total_pnl, coherence, adjustment, "lobe alignment"))

    con.commit(); con.close()
    print(f"[brain_trainer] 🧠 coherence={coherence:.3f} Δ={adjustment:+.4f} ({len(lobes)} lobes synced)")
# === PATCH END ===


if __name__ == "__main__":
    train_brain()
