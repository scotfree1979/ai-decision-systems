#!/usr/bin/env python3
"""
engines/mastery/mastery_trainer_engine.py
───────────────────────────────────────────────
Consumes mastery_training_schema_v7.json,
computes each metric across live databases,
and writes scores to mastery_training_metrics.

Usage:
    python3 -m engines.mastery.mastery_trainer_engine --days 30
"""

from __future__ import annotations
import os, sys, json, sqlite3, traceback, math, statistics, pandas as pd
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# --- repo root import shim ---
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from engines.config_paths import autoscalp_db, bets_db, auto_conn as connect

from engines.config_core_values import get_core_values


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _safe_exec(con: sqlite3.Connection, sql: str) -> Optional[float]:
    """Executes numeric SQL and returns scalar safely."""
    try:
        cur = con.execute(sql)
        val = cur.fetchone()
        if val is None:
            return None
        v = list(val)[0]
        if v is None:
            return None
        return float(v)
    except Exception as e:
        print(f"[trainer] SQL warn: {e}\n→ {sql}")
        return None


def _ensure_metrics_table(con: sqlite3.Connection):
    con.execute("""
        CREATE TABLE IF NOT EXISTS mastery_training_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now','utc')),
            bucket TEXT,
            question_id TEXT,
            metric TEXT,
            value REAL,
            weight REAL,
            source TEXT,
            notes TEXT
        )
    """)
    con.commit()

# === PATCH START ===
# 📍 TARGET: engines/mastery/mastery_trainer_engine.py:_metric_to_sql
# 📆 PATCHED: 2025-11-02Z — sanitize pseudo-SQL syntax + safe corr fallback (V7 Intel)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _metric_to_sql(metric: str, source: str, days: int = 90) -> Optional[str]:
    """
    Translate simplified metric into SQL executed against v_mastery_intel_v7.
    Handles legacy aliases, pseudo-SQL syntax, and missing corr arguments.
    """

    tbl = "v_mastery_intel_v7"

    # ───────────────────────────────────────────────────────────────────
    # 1️⃣ Sanitize non-SQL / legacy syntax
    # ───────────────────────────────────────────────────────────────────
    import re

    # clean pseudo operators and aliases
    metric = re.sub(r"\bm\.", "", metric)                     # remove table aliases (m.)
    metric = re.sub(r"\bt\.", "", metric)
    metric = re.sub(r"\br\.", "", metric)
    metric = re.sub(r"\bf\.", "", metric)
    metric = re.sub(r"\bby\b", "", metric)                    # remove 'by' pseudo-op
    metric = re.sub(r"\|([A-Za-z0-9_]+)\|", r"ABS(\1)", metric)  # |col| → ABS(col)
    metric = metric.replace("<0", "< 0").replace(">0", "> 0")

    # replace boolean inequalities with CASE expressions
    metric = re.sub(
        r"([A-Za-z0-9_]+)\s*<\s*0",
        r"CASE WHEN \1 < 0 THEN 1 ELSE 0 END",
        metric
    )
    metric = re.sub(
        r"([A-Za-z0-9_]+)\s*>\s*0",
        r"CASE WHEN \1 > 0 THEN 1 ELSE 0 END",
        metric
    )

    # normalize known mislabels
    metric = metric.replace("segment_form_win_rate", "form_win_rate")
    metric = metric.replace("autoavg_volatility", "avg_volatility")

    # ───────────────────────────────────────────────────────────────────
    # 2️⃣ Column alias resolution
    # ───────────────────────────────────────────────────────────────────
    COL_MAP = {
        "success": "CASE WHEN success=1 OR pnl>0 THEN 1 ELSE 0 END",
        "pnl": "pnl",
        "net_pl": "pnl",
        "win_rate": "form_win_rate",
        "avg_pnl": "form_avg_pnl",
        "volatility_band": "avg_volatility",
        "drift_speed": "slope_ppm",
        "momentum": "momentum_class",
        "liability": "weight",
        "goal_alignment": "weight",
        "bias": "drift_pct",
        "band_width": "(range_high - range_low)",
        "distance_bin": "distance",
        "segment_win_rate": "form_win_rate",
    }

    for k, v in COL_MAP.items():
        metric = metric.replace(k, v)

    # ───────────────────────────────────────────────────────────────────
    # 3️⃣ Metric translation
    # ───────────────────────────────────────────────────────────────────
    if "corr(" in metric:
        inner = metric.replace("corr(", "").replace(")", "")
        parts = [p.strip() for p in inner.split(",") if p.strip()]
        if len(parts) == 1:
            x, y = parts[0], parts[0]  # self-corr fallback
        elif len(parts) >= 2:
            x, y = parts[0], parts[1]
        else:
            return None
        return f"""
            SELECT (AVG(({x})*({y})) - AVG({x})*AVG({y})) /
                   NULLIF(STDDEV({x})*STDDEV({y}),0)
              FROM {tbl}
             WHERE date(day) >= date('now','-{days} day');
        """

    elif "avg(" in metric:
        inner = metric.split("(")[1].split(")")[0]
        return f"SELECT AVG({inner}) FROM {tbl} WHERE date(day)>=date('now','-{days} day');"

    elif "sum(" in metric:
        inner = metric.split("(")[1].split(")")[0]
        return f"SELECT SUM({inner}) FROM {tbl} WHERE date(day)>=date('now','-{days} day');"

    elif "var(" in metric:
        inner = metric.split("(")[1].split(")")[0]
        return f"SELECT (AVG({inner}*{inner}) - AVG({inner})*AVG({inner})) FROM {tbl};"

    # fallback success rate
    return f"SELECT AVG(CASE WHEN success=1 OR pnl>0 THEN 1 ELSE 0 END) FROM {tbl};"
# === PATCH END ===



# ──────────────────────────────────────────────────────────────────────────────
# Core Engine
# ──────────────────────────────────────────────────────────────────────────────
def run_training_eval(schema_path: str, days: int = 90):
    """Main entrypoint: evaluate all schema metrics."""
    db_path = autoscalp_db()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    _ensure_metrics_table(con)

    with open(schema_path, "r", encoding="utf-8") as f:
        schema = json.load(f)

    results = []
    for bucket in schema.get("buckets", []):
        bname = bucket["bucket"]
        for q in bucket.get("questions", []):
            qid = q["id"]
            metric = q["metric"]
            source = q["source"]
            weight = float(q.get("weight", 1.0))
            sql = _metric_to_sql(metric, source, days)
            val = _safe_exec(con, sql) if sql else None
            results.append((bname, qid, metric, val, weight, source))

            print(f"[eval] {qid:<6} {bname:<25} = {val}")

            con.execute("""
                INSERT INTO mastery_training_metrics(bucket,question_id,metric,value,weight,source)
                VALUES(?,?,?,?,?,?)
            """, (bname, qid, metric, val, weight, source))
    con.commit()

    # Aggregate per-bucket averages
    bucket_scores = {}
    for b, qid, metric, val, w, src in results:
        if val is None:
            continue
        bucket_scores.setdefault(b, []).append(val * w)
    agg = {b: round(sum(v) / len(v), 6) for b, v in bucket_scores.items() if v}

    print("\n=== Bucket Summary ===")
    for k, v in agg.items():
        print(f"{k:<25} → {v:.4f}")

    con.close()
    return agg


# ──────────────────────────────────────────────────────────────────────────────
# CLI Entrypoint
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--schema", default="data/configs/mastery_training_schema_v7.json")
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    try:
        print(f"[trainer] Running Mastery v7 Training Evaluation — last {args.days} days")
        scores = run_training_eval(args.schema, days=args.days)
        print(f"\n[trainer] ✅ Evaluation complete: {len(scores)} buckets")
    except Exception:
        traceback.print_exc()
        sys.exit(1)
