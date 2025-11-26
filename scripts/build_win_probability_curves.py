#!/usr/bin/env python3
"""
scripts/build_win_probability_curves.py
───────────────────────────────────────────────
Phase 7 – Dynamic Win-Probability Curves
Computes per-runner win probability for each OC
based on historical v7 intelligence and timing features.
Writes to mastery_v7.db → probability_curves + summary columns.
"""

import sqlite3, math, json, time
from engines.config_paths import connect_mastery_v7_cache
con_out = connect_mastery_v7_cache()

# ───────────────────────────────────────────────────────────────────────
# Utility helpers
# ───────────────────────────────────────────────────────────────────────
def _safe_float(x, default=0.0):
    try:
        return float(x or 0)
    except Exception:
        return default

def _logistic(x): return 1.0 / (1.0 + math.exp(-x))

# ───────────────────────────────────────────────────────────────────────
# 1️⃣  Logistic model coefficients (heuristic / seed)
#     These will later be retrained by the trainer.
# ───────────────────────────────────────────────────────────────────────
COEFS = {
    "bias": -1.2,
    "anchor": -0.35,
    "odds": -0.25,
    "drift": -1.0,
    "fav_rank": -0.15,
    "momentum": 0.4,
    "drift_speed": -0.2,
}

# ───────────────────────────────────────────────────────────────────────
# 2️⃣  Compute P(win | OC snapshot)
# ───────────────────────────────────────────────────────────────────────
def compute_p_win(anchor, odds, drift, fav_rank, momentum_class, drift_speed):
    x = (COEFS["bias"] +
         COEFS["anchor"] * math.log(max(anchor, 1.01)) +
         COEFS["odds"] * math.log(max(odds, 1.01)) +
         COEFS["drift"] * drift +
         COEFS["fav_rank"] * (float(fav_rank or 6) - 1) +
         COEFS["momentum"] * (1 if (momentum_class or "").upper() in ("UP","RISING") else -1) +
         COEFS["drift_speed"] * float(drift_speed or 0))
    return round(_logistic(x), 4)

# ───────────────────────────────────────────────────────────────────────
# 3️⃣  Build probability curves per runner
# ───────────────────────────────────────────────────────────────────────
def build_curves(day="2025-10-17"):
    print(f"[PROB] building win-probability curves for {day}…")
    t0 = time.time()
    con_auto = sqlite3.connect(autoscalp_db()); con_auto.row_factory = sqlite3.Row
    con_bets = sqlite3.connect(bets_db()); con_bets.row_factory = sqlite3.Row
    con_out  = sqlite3.connect(mastery_v7_db()); con_out.row_factory = sqlite3.Row
    cur_out  = con_out.cursor()

    # destination table
    cur_out.execute("""
        CREATE TABLE IF NOT EXISTS probability_curves(
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            oc_idx INTEGER,
            anchor_odd REAL,
            oc_val REAL,
            drift REAL,
            fav_rank INTEGER,
            momentum_class TEXT,
            drift_speed REAL,
            p_win REAL,
            created_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)
    cur_out.execute("DELETE FROM probability_curves WHERE day=?", (day,))

    # load timing + intelligence (for drift_speed, momentum, fav_rank)
    v7map = { (r["marketId"],r["selectionId"]): r for r in
        con_auto.execute("""
            SELECT marketId, selectionId, fav_rank, drift_speed, momentum_class
              FROM v7_intelligence
             WHERE day=? OR day IS NULL
        """,(day,)).fetchall()
    }

    # join bets + inbound_oc_cache (OC0..OC20)
    rows = con_bets.execute("""
        SELECT marketId, selectionId, anchor_odd, OC0
          FROM bets
         WHERE date=? AND anchor_odd>0
    """, (day,)).fetchall()

    inserted = 0
    for r in rows:
        mid, sid = r["marketId"], r["selectionId"]
        anchor = _safe_float(r["anchor_odd"])
        base_odds = _safe_float(r["OC0"] or anchor)
        meta = v7map.get((mid,sid), {})
        fav_rank = meta.get("fav_rank", 6)
        momentum = meta.get("momentum_class", "NEUTRAL")
        drift_speed = _safe_float(meta.get("drift_speed"))

        # pull inbound OCs from autoscalp_gui
        inbound = con_auto.execute("""
            SELECT * FROM inbound_oc_cache
             WHERE marketId=? AND selectionId=? LIMIT 1
        """, (mid, sid)).fetchone()
        if not inbound:
            continue

        for i in range(1,21):
            val = _safe_float(inbound[f"oc{i}"])
            if val <= 0: continue
            drift = (val - anchor) / max(anchor, 1e-9)
            p = compute_p_win(anchor, val, drift, fav_rank, momentum, drift_speed)
            cur_out.execute("""
                INSERT INTO probability_curves
                (day, marketId, selectionId, oc_idx, anchor_odd, oc_val,
                 drift, fav_rank, momentum_class, drift_speed, p_win)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """,(day, mid, sid, i, anchor, val, drift, fav_rank, momentum, drift_speed, p))
            inserted += 1

    con_out.commit()
    print(f"[PROB] ✅ inserted {inserted:,} curve points in {time.time()-t0:.2f}s")

    # summarise to join later into story_training_orders
    cur_out.execute("""
        CREATE TABLE IF NOT EXISTS probability_summary AS
        SELECT day, marketId, selectionId,
               MIN(p_win) AS p_min,
               MAX(p_win) AS p_max,
               AVG(p_win) AS p_avg,
               MAX(p_win)-MIN(p_win) AS p_range,
               p_win AS p_last
          FROM probability_curves
         GROUP BY day, marketId, selectionId
    """)
    con_out.commit()
    con_bets.close(); con_auto.close(); con_out.close()
    print("[PROB] done.")

# ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    build_curves(sys.argv[1] if len(sys.argv)>1 else "2025-10-17")
