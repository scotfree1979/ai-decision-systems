# === PATCH START ===
# 📍 TARGET: engines/mastery/canonical_digest.py
# 📆 PATCHED: 2025-10-25T18:20Z — Canonical digest integration for Mastery
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
Bridges the canonical analytics digest (from analytics_report.py)
into Mastery runtime.

The goal: make Mastery aware of true post-settlement profitability
and bias learning to maximize canonical P&L.
"""

import os, json, ast
from collections import defaultdict

DIGEST_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "reports", "digest_context_latest.json")

def load():
    """Load canonical digest JSON and normalize it into structured rows."""
    if not os.path.exists(DIGEST_PATH):
        return []
    try:
        with open(DIGEST_PATH, "r") as f:
            raw = json.load(f)
    except Exception:
        return []

    rows = []
    for k, v in raw.items():
        try:
            key = ast.literal_eval(k)
            # expected: (letter, venue, country, fav_rank, mto)
            letter, venue, country, fav_rank, mto = (list(key) + ["unk"] * 5)[:5]
            rows.append({
                "letter": str(letter),
                "venue": str(venue),
                "country": str(country),
                "fav_rank": str(fav_rank),
                "mto_band": str(mto),
                "pnl": float(v or 0.0)
            })
        except Exception:
            continue
    return rows

def total_pnl() -> float:
    """Return total canonical P&L."""
    rows = load()
    return round(sum(r["pnl"] for r in rows), 2)

def avg_by_letter() -> dict:
    """Return per-letter averages, used for adaptive weighting."""
    rows = load()
    agg = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    for r in rows:
        agg[r["letter"]]["pnl"] += r["pnl"]
        agg[r["letter"]]["n"] += 1
    return {k: v["pnl"] / max(v["n"], 1) for k, v in agg.items()}

def weight_for_letter(letter: str) -> float:
    """
    Return multiplier (0.5–1.5×) to emphasize underperforming letters.
    """
    avgs = avg_by_letter()
    if not avgs:
        return 1.0
    mean = sum(avgs.values()) / max(len(avgs), 1)
    val = avgs.get(letter, mean)
    delta = val - mean
    # if below mean → higher weight; above mean → lower weight
    w = 1.0 - (delta / (abs(mean) + 1e-9))
    return round(max(0.5, min(1.5, w)), 3)
# === PATCH END ===
# === PATCH START ===
# 📍 TARGET: engines/mastery/canonical_digest.py
# 📆 PATCHED: 2025-10-26Z — add win_rate_today() + matched_ratio_today() helpers for feedback_scheduler
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3
from engines.config_paths import autoscalp_db

def win_rate_today() -> float:
    """Compute live win rate (settled winners / settled total) for today."""
    try:
        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
        row = con.execute("""
            SELECT 
              SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)*1.0 / 
              MAX(COUNT(*),1)
            FROM mastery_outcomes_raw
            WHERE date(created_at)=date('now','utc')
        """).fetchone()
        return round(float(row[0] or 0.0), 4)
    except Exception:
        return 0.0
    finally:
        try: con.close()
        except Exception: pass

def matched_ratio_today() -> float:
    """Compute how many parents generated children (match conversion ratio) for today."""
    try:
        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
        row = con.execute("""
            SELECT 
              SUM(CASE WHEN matched_children>0 THEN 1 ELSE 0 END)*1.0 /
              MAX(COUNT(*),1)
            FROM dashboard_orders_tape
            WHERE date(opened_at)=date('now','utc')
        """).fetchone()
        return round(float(row[0] or 0.0), 4)
    except Exception:
        return 0.0
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===
