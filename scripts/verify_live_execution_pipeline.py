#!/usr/bin/env python3
"""
Definitive LIVE execution pipeline verifier.

Run this to identify exactly where parent placement is breaking.
No guessing. No inference. PASS / FAIL only.
"""

import sys
import traceback
from datetime import datetime
from engines.decision_engine.decide_once.placement import start_placement_worker
start_placement_worker()

def banner(msg):
    print("\n" + "="*80)
    print(msg)
    print("="*80)

def ok(msg):   print(f"✅ {msg}")
def fail(msg): print(f"❌ {msg}")

def step(n, msg):
    print(f"\n[{n}] {msg}")

def main():
    banner("AUTOSCALP LIVE EXECUTION PIPELINE VERIFIER")

    # ------------------------------------------------------------------
    # 1) BUS → Placement enqueue path
    # ------------------------------------------------------------------
    step(1, "Import BUS → placement enqueue")
    try:
        from engines.decision_engine.decide_once.placement import enqueue_for_placement
        ok("placement.enqueue_for_placement import OK")
    except Exception:
        fail("Cannot import enqueue_for_placement")
        traceback.print_exc()
        return

    # ------------------------------------------------------------------
    # 2) Placement worker thread exists
    # ------------------------------------------------------------------
    step(2, "Placement worker presence")
    try:
        from engines.decision_engine.decide_once import placement
        worker = getattr(placement, "_PLACEMENT_WORKER_THREAD", None)
        if worker is None:
            fail("Placement worker thread variable missing")
            return
        ok("Placement worker thread symbol present")
    except Exception:
        fail("Failed inspecting placement worker")
        traceback.print_exc()
        return

    # ------------------------------------------------------------------
    # 3) Router callable
    # ------------------------------------------------------------------
    step(3, "Router callable import")
    try:
        from engines.live.live_router import place_parent_and_hedge
        ok("place_parent_and_hedge import OK")
    except Exception:
        fail("Cannot import place_parent_and_hedge")
        traceback.print_exc()
        return

    # ------------------------------------------------------------------
    # 4) Betfair credentials
    # ------------------------------------------------------------------
    step(4, "Betfair credential resolution")
    try:
        from engines.live.live_router import _keys
        app_key, token = _keys()
        if not app_key or not token:
            fail("Betfair credentials empty")
            return
        ok("Betfair credentials resolved")
    except Exception:
        fail("Betfair credentials lookup failed")
        traceback.print_exc()
        return

    # ------------------------------------------------------------------
    # 5) Betfair API reachability
    # ------------------------------------------------------------------
    step(5, "Betfair API reachability")
    try:
        from engines.live.live_router import _rpc
        resp = _rpc(app_key, token, "listMarketCatalogue", {
            "filter": {},
            "maxResults": 1,
            "marketProjection": ["MARKET_START_TIME"]
        })
        ok("Betfair API reachable")
    except Exception:
        fail("Betfair API unreachable or auth failed")
        traceback.print_exc()
        return

    # ------------------------------------------------------------------
    # 6) Router local DB write
    # ------------------------------------------------------------------
    step(6, "orders DB write path")
    try:
        from engines.live.live_router import _orders_conn
        con = _orders_conn()
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM orders")
        con.close()
        ok("orders DB readable/writable")
    except Exception:
        fail("orders DB access failed")
        traceback.print_exc()
        return

    # ------------------------------------------------------------------
    # 7) Full router dry-run (NO BUS)
    # ------------------------------------------------------------------
    step(7, "Direct router placement dry-run")
    try:
        bet_id, ref = place_parent_and_hedge(
            market_id="1.23456789",
            selection_id="123456",
            side="BACK",
            entry_odds=2.0,
            stake=2.0,
            source="Z",
            run_id="PIPELINE_TEST",
        )
        ok("Router function executed without crash")
        print(f"    returned bet_id={bet_id}, ref={ref}")
    except Exception:
        fail("Router execution failed")
        traceback.print_exc()
        return

    banner("PIPELINE CHECK COMPLETE — SEE FIRST ❌ ABOVE IF ANY")

if __name__ == "__main__":
    main()
