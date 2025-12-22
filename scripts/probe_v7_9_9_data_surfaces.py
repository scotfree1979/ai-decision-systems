#!/usr/bin/env python3
"""
AUTOSCALP v7.9.9.x — SCHEMA-GROUNDED DATA SURFACE PROBE

Purpose:
- Verify that ALL dashboard + execution data surfaces are
  ACTUALLY QUERYABLE given CURRENT schemas.
- No legacy assumptions.
- No inferred columns.
- PASS / FAIL only.

Authoritative rules:
- decisions outcome == (why + meta_json)
- confidence lives in decisions + mastery_posteriors
- v_mastery_intel_v7 is execution-outcome centric
- v_mastery_live is posterior aggregation only
"""

import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("data/autoscalp_gui.db")

def ok(msg):   print(f"✅ {msg}")
def fail(msg): print(f"❌ {msg}")

def banner(msg):
    print("\n" + "=" * 80)
    print(msg)
    print("=" * 80)

def open_db():
    if not DB_PATH.exists():
        fail(f"DB missing: {DB_PATH}")
        sys.exit(1)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def table_columns(con, name):
    rows = con.execute(f"PRAGMA table_info({name})").fetchall()
    return {r["name"] for r in rows}

def view_exists(con, name):
    r = con.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type IN ('view','table') AND name=?
    """, (name,)).fetchone()
    return bool(r)

def sample_query(con, sql, label):
    try:
        rows = con.execute(sql).fetchall()
        if rows:
            ok(f"{label}: {len(rows)} rows")
            return True
        else:
            fail(f"{label}: query returned 0 rows")
            return False
    except Exception as e:
        fail(f"{label}: query failed ({e})")
        return False

def main():
    banner("AUTOSCALP v7.9.9.x — DATA SURFACE PROBE (SCHEMA-TRUE)")

    con = open_db()
    failures = 0

    # ------------------------------------------------------------------
    # 1) decisions — execution outcome surface
    # ------------------------------------------------------------------
    print("\n[1] decisions (execution outcome surface)")
    if not view_exists(con, "decisions"):
        fail("decisions table missing")
        failures += 1
    else:
        cols = table_columns(con, "decisions")
        required = {
            "run_id","marketId","selectionId","decided_at",
            "confidence","scalp_direction","proposed_odds",
            "proposed_stake","why","meta_json"
        }
        missing = required - cols
        if missing:
            fail(f"decisions missing columns: {missing}")
            failures += 1
        else:
            ok("decisions schema OK")

        failures += not sample_query(
            con,
            """
            SELECT run_id, marketId, selectionId, why, meta_json
            FROM decisions
            ORDER BY decided_at DESC
            LIMIT 5
            """,
            "decisions sample"
        )

    # ------------------------------------------------------------------
    # 2) v_mastery_intel_v7 — execution intelligence
    # ------------------------------------------------------------------
    print("\n[2] v_mastery_intel_v7 (execution intelligence)")
    if not view_exists(con, "v_mastery_intel_v7"):
        fail("v_mastery_intel_v7 missing")
        failures += 1
    else:
        cols = table_columns(con, "v_mastery_intel_v7")
        required = {
            "marketId","selectionId","day","letter",
            "pnl","success","drift_speed",
            "inplay_progress","band_stability"
        }
        missing = required - cols
        if missing:
            fail(f"v_mastery_intel_v7 missing columns: {missing}")
            failures += 1
        else:
            ok("v_mastery_intel_v7 schema OK")

        failures += not sample_query(
            con,
            """
            SELECT marketId, selectionId, letter, pnl, success, band_stability
            FROM v_mastery_intel_v7
            ORDER BY day DESC
            LIMIT 5
            """,
            "v_mastery_intel_v7 sample"
        )

    # ------------------------------------------------------------------
    # 3) mastery_posteriors — belief state
    # ------------------------------------------------------------------
    print("\n[3] mastery_posteriors (belief state)")
    if not view_exists(con, "mastery_posteriors"):
        fail("mastery_posteriors missing")
        failures += 1
    else:
        cols = table_columns(con, "mastery_posteriors")
        required = {"bin_key","confidence","bucket_confidence"}
        missing = required - cols
        if missing:
            fail(f"mastery_posteriors missing columns: {missing}")
            failures += 1
        else:
            ok("mastery_posteriors schema OK")

        failures += not sample_query(
            con,
            """
            SELECT bin_key, confidence, bucket_confidence
            FROM mastery_posteriors
            ORDER BY updated_at DESC
            LIMIT 5
            """,
            "mastery_posteriors sample"
        )

    # ------------------------------------------------------------------
    # 4) v_mastery_live — posterior aggregation
    # ------------------------------------------------------------------
    print("\n[4] v_mastery_live (posterior aggregation)")
    if not view_exists(con, "v_mastery_live"):
        fail("v_mastery_live missing")
        failures += 1
    else:
        cols = table_columns(con, "v_mastery_live")
        required = {
            "distance_band","fav_rank_bin",
            "total_pnl","total_weight",
            "p1_mean","p2_mean","p3_mean"
        }
        missing = required - cols
        if missing:
            fail(f"v_mastery_live missing columns: {missing}")
            failures += 1
        else:
            ok("v_mastery_live schema OK")

        failures += not sample_query(
            con,
            """
            SELECT distance_band, fav_rank_bin, total_pnl, p1_mean
            FROM v_mastery_live
            LIMIT 5
            """,
            "v_mastery_live sample"
        )

    # ------------------------------------------------------------------
    # 5) v_strategy_perf — strategy performance
    # ------------------------------------------------------------------
    print("\n[5] v_strategy_perf (strategy performance)")
    if not view_exists(con, "v_strategy_perf"):
        fail("v_strategy_perf missing")
        failures += 1
    else:
        ok("v_strategy_perf present")
        failures += not sample_query(
            con,
            """
            SELECT strategy, pnl_today
            FROM v_strategy_perf
            LIMIT 5
            """,
            "v_strategy_perf sample"
        )

    con.close()

    banner("PROBE RESULT")
    if failures:
        fail(f"SYSTEM NOT READY — {failures} failures")
        sys.exit(1)
    else:
        ok("ALL DATA SURFACES PRESENT AND QUERYABLE")
        print("➡ Dashboard v2 wiring can proceed safely")

if __name__ == "__main__":
    main()
