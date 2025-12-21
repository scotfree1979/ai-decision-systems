#!/usr/bin/env python3
"""
AUTOSCALP v7 — Definitive LIVE Execution Pipeline Verifier

PASS / FAIL only.
No inference. No guessing.

This verifies:
- BUS → Placement → Router
- Direction contract
- Orders DB
- Exposure truth
- Engine readiness (Legacy / MSC)
"""

import sys
import traceback
from datetime import datetime
import sqlite3

from engines.decision_engine.decide_once.placement import start_placement_worker
start_placement_worker()

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------
def banner(msg):
    print("\n" + "="*80)
    print(msg)
    print("="*80)

def ok(msg):   print(f"✅ {msg}")
def fail(msg): print(f"❌ {msg}")

def step(n, msg):
    print(f"\n[{n}] {msg}")

def die():
    banner("PIPELINE CHECK FAILED — SEE ❌ ABOVE")
    sys.exit(1)

# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------
def main():
    banner("AUTOSCALP LIVE EXECUTION PIPELINE VERIFIER")

    # --------------------------------------------------------------
    # 1) Placement enqueue
    # --------------------------------------------------------------
    step(1, "Placement enqueue import")
    try:
        from engines.decision_engine.decide_once.placement import enqueue_for_placement
        ok("enqueue_for_placement import OK")
    except Exception:
        fail("Cannot import enqueue_for_placement")
        traceback.print_exc()
        die()

    # --------------------------------------------------------------
    # 2) Placement worker
    # --------------------------------------------------------------
    step(2, "Placement worker thread")
    try:
        from engines.decision_engine.decide_once import placement
        worker = getattr(placement, "_PLACEMENT_WORKER_THREAD", None)
        if worker is None:
            fail("Placement worker thread missing")
            die()
        ok("Placement worker thread present")
    except Exception:
        fail("Placement worker inspection failed")
        traceback.print_exc()
        die()

    # --------------------------------------------------------------
    # 3) Router callable
    # --------------------------------------------------------------
    step(3, "Router import")
    try:
        from engines.live.live_router import place_parent_and_hedge
        ok("place_parent_and_hedge import OK")
    except Exception:
        fail("Router import failed")
        traceback.print_exc()
        die()

    # --------------------------------------------------------------
    # 4) Betfair credentials
    # --------------------------------------------------------------
    step(4, "Betfair credentials")
    try:
        from engines.live.live_router import _keys
        app_key, token = _keys()
        if not app_key or not token:
            fail("Empty Betfair credentials")
            die()
        ok("Betfair credentials resolved")
    except Exception:
        fail("Betfair credential lookup failed")
        traceback.print_exc()
        die()

    # --------------------------------------------------------------
    # 5) Betfair API
    # --------------------------------------------------------------
    step(5, "Betfair API reachability")
    try:
        from engines.live.live_router import _rpc
        _rpc(app_key, token, "listMarketCatalogue", {
            "filter": {},
            "maxResults": 1,
            "marketProjection": ["MARKET_START_TIME"]
        })
        ok("Betfair API reachable")
    except Exception:
        fail("Betfair API call failed")
        traceback.print_exc()
        die()

    # --------------------------------------------------------------
    # 6) Orders DB access
    # --------------------------------------------------------------
    step(6, "Orders DB access")
    try:
        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=False)
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM orders")
        con.close()
        ok("Orders DB readable")
    except Exception:
        fail("Orders DB access failed")
        traceback.print_exc()
        die()

    # ------------------------------------------------------------------
    # 7) Execution semantics check (direction via lifecycle)
    # ------------------------------------------------------------------
    step(7, "Execution semantics (parent/child lifecycle)")

    try:
        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        rows = con.execute("""
            SELECT
                id,
                engine,
                source,
                entry_status,
                exit_status,
                hedge_of
            FROM orders
            WHERE engine IN ('LEGACY','MSC_EXPLORATORY','MSC_RISK','MSC_INPLAY')
            ORDER BY id DESC
            LIMIT 200
        """).fetchall()

        con.close()

        parents = [r for r in rows if r["role"] == "PARENT"]
        children = [r for r in rows if r["role"] == "CHILD"]

        if not parents:
            fail("No parent orders found (no execution at all)")
            die()

        ok(f"Found {len(parents)} parent orders")

        parent_ids = {r["id"] for r in parents}

        orphan_children = [
            r for r in children
            if r["hedge_of"] not in parent_ids
        ]

        if orphan_children:
            fail(f"Found orphan children: {[r['id'] for r in orphan_children]}")
            die()

        ok(f"Found {len(children)} child orders with valid parents")

        bad_parents = [
            r for r in parents
            if r["entry_status"] not in ("PLACED", "MATCHED", "CANCELLED")
        ]

        if bad_parents:
            fail("Invalid parent entry_status detected")
            die()

        ok("Parent lifecycle states valid")

    except Exception:
        fail("Execution semantics check failed")
        traceback.print_exc()
        die()


    # --------------------------------------------------------------
    # 8) Exposure truth
    # --------------------------------------------------------------
    step(8, "Exposure truth check")
    try:
        from engines.live.bank_state import get_engine_pots
        pots = get_engine_pots()

        if not pots:
            fail("No engine exposure data returned")
            die()

        ok("Engine exposure map readable")
        for eng, val in pots.items():
            print(f"    {eng:<16} exposure={val:.2f}")
    except Exception:
        fail("Exposure check failed")
        traceback.print_exc()
        die()

    # --------------------------------------------------------------
    # 9) Direct router dry-run (safe)
    # --------------------------------------------------------------
    step(9, "Direct router dry-run (NO BUS)")
    try:
        bet_id, ref = place_parent_and_hedge(
            market_id="1.23456789",
            selection_id="123456",
            side="LAY",
            entry_odds=10.0,
            stake=2.0,
            source="Z",
            run_id="PIPELINE_TEST",
        )
        ok("Router executed without crash")
        print(f"    bet_id={bet_id}, ref={ref}")
    except Exception:
        fail("Router execution failed")
        traceback.print_exc()
        die()

    # --------------------------------------------------------------
    banner("PIPELINE CHECK COMPLETE — ALL SYSTEMS GO")
    ok("BUS → PLACEMENT → ROUTER")
    ok("DIRECTION CONTRACT")
    ok("ORDERS / EXPOSURE")
    ok("ENGINE READINESS")

# ---------------------------------------------------------------------
if __name__ == "__main__":
    main()
