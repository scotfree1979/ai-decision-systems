#!/usr/bin/env python3
"""
engines/mastery/mastery_weight_interface.py
────────────────────────────────────────────
Adaptive diagnostic and weighting engine for Mastery v7.

1️⃣ Scans mastery_posteriors for structural and performance drift.
2️⃣ Joins with mastery_training_metrics for contextual weighting.
3️⃣ Produces mastery_posterior_adjustments and a JSON summary.

Usage:
    python3 -m engines.mastery.mastery_weight_interface --days 90
"""

from __future__ import annotations
import os, sys, sqlite3, json, math, statistics
from datetime import datetime, timezone
from typing import Dict, Any, List, Tuple

# --- repo root import shim ---
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from engines.config_paths import autoscalp_db
from engines.config_core_values import get_core_values

# ───────────────────────────────────────────────────────────────
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _safe(val, default=0.0) -> float:
    try:
        if val is None:
            return default
        return float(val)
    except Exception:
        return default

# ───────────────────────────────────────────────────────────────
# DB initialisation helpers
# ───────────────────────────────────────────────────────────────
def _ensure_adjustment_table(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS mastery_posterior_adjustments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now','utc')),
            bin_key TEXT,
            letter TEXT,
            bucket TEXT,
            prior_weight REAL,
            delta REAL,
            proposed_weight REAL,
            reason TEXT
        )
    """)
    con.commit()

# ───────────────────────────────────────────────────────────────
# 1️⃣ Diagnostic Scan
# ───────────────────────────────────────────────────────────────
def diagnostic_scan(con: sqlite3.Connection) -> List[Dict[str, Any]]:
    """
    Analyses mastery_posteriors and returns a list of diagnostic dicts.
    Each record represents one bin_key with diagnostic stats.
    """
    # --- check schema dynamically ---
    cols = {r[1] for r in con.execute("PRAGMA table_info(mastery_posteriors);").fetchall()}

    # defensive select (some deployments don't yet have win_rate/live_pnl_ratio/weight)
    sel_cols = [
        "bin_key",
        "letter",
        "n_total",
        "n_success",
    ]
    if "win_rate" in cols:
        sel_cols.append("win_rate")
    else:
        sel_cols.append("(n_success * 1.0 / NULLIF(n_total,0)) AS win_rate")
    if "live_pnl_ratio" in cols:
        sel_cols.append("live_pnl_ratio")
    else:
        sel_cols.append("0.0 AS live_pnl_ratio")
    if "weight" in cols:
        sel_cols.append("weight")
    else:
        sel_cols.append("1.0 AS weight")
    if "updated_at" in cols:
        sel_cols.append("updated_at")
    else:
        sel_cols.append("datetime('now','utc') AS updated_at")

    sql = f"SELECT {', '.join(sel_cols)} FROM mastery_posteriors;"
    rows = con.execute(sql).fetchall()

    # --- analysis as before ---
    diags = []
    for r in rows:
        n_total = _safe(r["n_total"])
        n_success = _safe(r["n_success"])
        wr = _safe(r["win_rate"])
        pnlr = _safe(r["live_pnl_ratio"])
        weight = _safe(r["weight"], 1.0)
        drift = abs(pnlr - wr)
        sparsity = 1.0 if n_total < 20 else 0.0
        overfit = 1.0 if n_total > 1000 and drift > 0.3 else 0.0
        underperform = 1.0 if pnlr < 0 and wr < 0.4 else 0.0

        state = "GOOD"
        if sparsity:
            state = "SPARSE"
        elif overfit:
            state = "OVERFIT"
        elif underperform:
            state = "WEAK"

        diags.append({
            "bin_key": r["bin_key"],
            "letter": r["letter"],
            "n_total": n_total,
            "win_rate": wr,
            "pnl_ratio": pnlr,
            "weight": weight,
            "drift": drift,
            "state": state
        })
    print(f"[diagnostics] analysed {len(diags)} posterior bins")
    return diags

# ───────────────────────────────────────────────────────────────
# 2️⃣ Compute Bucket Weight Strengths
# ───────────────────────────────────────────────────────────────
def compute_bucket_weights(con: sqlite3.Connection) -> Dict[str, float]:
    """
    Compute mean z-scored strength per bucket from mastery_training_metrics.
    Returns a dict {bucket: score in range [-1, +1]}.
    """
    rows = con.execute("""
        SELECT bucket, value, weight FROM mastery_training_metrics
         WHERE ts >= datetime('now','-2 day','utc');
    """).fetchall()

    df: Dict[str, List[Tuple[float,float]]] = {}
    for r in rows:
        df.setdefault(r["bucket"], []).append((_safe(r["value"]), _safe(r["weight"],1.0)))

    scores = {}
    for b, vals in df.items():
        values = [v for v, _ in vals if not math.isnan(v)]
        if not values:
            continue
        μ = statistics.mean(values)
        σ = statistics.pstdev(values) or 1e-6
        wz = []
        for v, w in vals:
            z = (v - μ) / σ
            wz.append(z * w)
        s = sum(wz) / max(1, len(wz))
        scores[b] = max(-1.0, min(1.0, s))
    print(f"[weighting] bucket strengths computed: {scores}")
    return scores

# ───────────────────────────────────────────────────────────────
# 3️⃣ Propose Posterior Adjustments
# ───────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: engines/mastery/mastery_weight_interface.py:propose_posterior_adjustments
# 📆 PATCHED: 2025-11-02Z — safe null-letter guard for diagnostics
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def propose_posterior_adjustments(con, diags, buckets, alpha: float = 0.15):
    """Combine diagnostics and bucket strengths to generate adjustment proposals."""
    _ensure_adjustment_table(con)
    con.execute("DELETE FROM mastery_posterior_adjustments;")

    letter_map = {
        "Market Conditions":["A","S","B"],
        "Timing":["G","X","R"],
        "Bias / Drift":["F","L"],
        "Execution":["T","P"],
        "Blueprint Recall":["M","Z"],
        "Venue / Type / Going":["V","J"],
        "Risk / Hedge":["H","D","R"],
        "Stability / Goal Alignment":["ALL"]
    }

    proposals = []
    for d in diags:
        # ✅ Guard against NoneType letters
        raw_letter = d.get("letter") or "UNK"
        letter = str(raw_letter).strip().upper() if isinstance(raw_letter, str) else "UNK"
        state = d.get("state", "GOOD")
        prior = _safe(d.get("weight"), 1.0)
        delta = 0.0
        reason = state

        for bname, letters in letter_map.items():
            if letter in letters or "ALL" in letters:
                bs = buckets.get(bname, 0.0)
                if state == "SPARSE":
                    delta = +alpha * bs
                elif state == "OVERFIT":
                    delta = -alpha * abs(bs)
                elif state == "WEAK":
                    delta = +alpha * abs(bs)
                else:
                    delta = +0.05 * bs
                break

        proposed = max(0.1, round(prior * (1 + delta), 4))
        proposals.append({
            "bin_key": d.get("bin_key"),
            "letter": letter,
            "bucket": bname,
            "prior_weight": prior,
            "delta": round(delta, 4),
            "proposed_weight": proposed,
            "reason": reason
        })

    con.executemany("""
        INSERT INTO mastery_posterior_adjustments
        (bin_key,letter,bucket,prior_weight,delta,proposed_weight,reason)
        VALUES(:bin_key,:letter,:bucket,:prior_weight,:delta,:proposed_weight,:reason)
    """, proposals)
    con.commit()
    print(f"[adjustments] ✅ inserted {len(proposals)} posterior adjustments.")
# === PATCH END ===

    # Summary by state
    summary = {
        "timestamp": _utcnow(),
        "total_bins": len(diags),
        "proposals": len(proposals),
        "avg_delta": round(statistics.mean([p["delta"] for p in proposals]), 5),
        "distribution": {
            "GOOD": sum(1 for d in diags if d["state"]=="GOOD"),
            "SPARSE": sum(1 for d in diags if d["state"]=="SPARSE"),
            "OVERFIT": sum(1 for d in diags if d["state"]=="OVERFIT"),
            "WEAK": sum(1 for d in diags if d["state"]=="WEAK"),
        },
        "bucket_strengths": buckets
    }

    path = "data/posteriors/mastery_weighting_summary.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2)
    print(f"[adjustments] summary written → {path}")
    return summary

# ───────────────────────────────────────────────────────────────
# Orchestration
# ───────────────────────────────────────────────────────────────
def main(days:int=90):
    db = autoscalp_db()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    print(f"[weight-interface] 🔍 Running diagnostics on {db}")
    diags = diagnostic_scan(con)
    buckets = compute_bucket_weights(con)
    summary = propose_posterior_adjustments(con, diags, buckets)

    print(f"\n[weight-interface] ✅ complete — {summary['proposals']} proposals generated.")
    print(json.dumps(summary, indent=2))
    con.close()

# ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    main(days=args.days)
