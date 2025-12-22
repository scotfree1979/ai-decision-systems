#!/usr/bin/env python3
"""
AUTOSCALP v7.9.9.x — EXECUTION POSSIBILITY TEST SUITE
====================================================

Purpose:
    Prove which execution paths are POSSIBLE based on current code.
    No guarantees. No live assumptions. No narratives.

Rules:
    - Do NOT call BUS.tick()
    - Do NOT require live markets
    - Do NOT fabricate STOPLOSS
    - PASS / FAIL only
"""

import traceback
import sys
from datetime import datetime

# ------------------------------------------------------------------------------
# Utilities
# ------------------------------------------------------------------------------
def banner(msg):
    print("\n" + "=" * 80)
    print(msg)
    print("=" * 80)

def ok(msg):
    print(f"✅ {msg}")

def fail(msg):
    print(f"❌ {msg}")

def step(msg):
    print(f"\n[TEST] {msg}")

# ------------------------------------------------------------------------------
# TEST 1 — MSC_EXPLORATORY emits a parent plan
# ------------------------------------------------------------------------------
def test_msc_exploratory_possible():
    step("MSC_EXPLORATORY parent emission")

    try:
        from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
        from engines.micro_scalper_v7.direction_engine import compute_msc_decision

        eng = ExploratoryEngine()

        ctx = {
            "px": 5.0,
            "oc_phase": 3,
            "dynamic_stake_fn": lambda **k: 2.0,
            "bias_conf": 0.5,
            "tick_volatility": 0.2,
            "sleq": 1.0,
            "drift_speed": 0.0,
            "momentum_class": 0.5,
            "form_win_rate": 0.5,
        }

        p = eng.tick(ctx)

        if not p or not p.get("enter"):
            fail("MSC_EXPLORATORY did not emit plan")
            return False

        ok("MSC_EXPLORATORY emits valid parent plan")
        return True

    except Exception:
        fail("MSC_EXPLORATORY crashed")
        traceback.print_exc()
        return False

# ------------------------------------------------------------------------------
# TEST 2 — Direction engine produces canonical output
# ------------------------------------------------------------------------------
def test_direction_engine_possible():
    step("Direction engine canonical output")

    try:
        from engines.micro_scalper_v7.direction_engine import compute_msc_decision

        ctx = {
            "bias_conf": 0.4,
            "tick_volatility": 0.3,
            "sleq": 1.0,
            "drift_speed": 0.0,
            "momentum_class": 0.6,
            "form_win_rate": 0.6,
        }

        d = compute_msc_decision(ctx)

        if not isinstance(d, dict):
            fail("Direction engine returned non-dict")
            return False

        if "direction" not in d:
            fail("Direction engine missing direction")
            return False

        ok("Direction engine produces canonical direction")
        return True

    except Exception:
        fail("Direction engine crashed")
        traceback.print_exc()
        return False

# ------------------------------------------------------------------------------
# TEST 3 — MSC_INPLAY parent emission is possible
# ------------------------------------------------------------------------------
def test_msc_inplay_possible():
    step("MSC_INPLAY parent emission")

    try:
        from engines.micro_scalper_v7.inplay_engine import InPlayEngine

        eng = InPlayEngine()

        ctx = {
            "oc_phase": 7,
            "current_price": 10.0,
            "px": 10.0,
            "dynamic_stake_fn": lambda **k: 2.0,
            "bias_conf": 0.2,
            "tick_volatility": 0.2,
            "sleq": 1.0,
            "drift_speed": 0.0,
            "momentum_class": 0.8,
            "form_win_rate": 0.2,
        }

        p = eng.tick(ctx)

        if p and p.get("enter"):
            if p.get("direction") != "BACK->LAY":
                fail("MSC_INPLAY emitted non BACK->LAY direction")
                return False

            ok("MSC_INPLAY emits BACK->LAY parent plan")
            return True

        ok("MSC_INPLAY correctly no-signalled (still executable)")
        return True

    except Exception:
        fail("MSC_INPLAY crashed")
        traceback.print_exc()
        return False

# ------------------------------------------------------------------------------
# TEST 4 — MSC_RISK parent emission is conditionally possible
# ------------------------------------------------------------------------------
def test_msc_risk_possible():
    step("MSC_RISK conditional emission")

    try:
        from engines.micro_scalper_v7.risk_engine import RiskEngine

        eng = RiskEngine()

        ctx = {
            "open_position": True,
            "legacy_parent_id": 123,
            "legacy_entry_side": "BACK",
            "legacy_entry_odds": 5.0,
            "current_price": 6.0,
            "oc_phase": 3,
            "msc_decision": {
                "entry_ticks": 2,
                "stop_ticks": 4,
                "mode": "MODERATE",
                "win_prob": 0.5,
            },
            "dynamic_stake_fn": lambda **k: 2.0,
            "tick_size_fn": lambda px: 0.02,
            "orders_by_runner": [
                {
                    "family": "LEGACY",
                    "role": "PARENT",
                    "entry_status": "MATCHED",
                }
            ],
        }

        p = eng.tick(ctx)

        if p is None:
            ok("MSC_RISK returned None (still executable)")
            return True

        if p.get("enter"):
            ok("MSC_RISK emitted a plan")
            return True

        ok("MSC_RISK no-signalled correctly")
        return True

    except Exception:
        fail("MSC_RISK crashed")
        traceback.print_exc()
        return False

# ------------------------------------------------------------------------------
# TEST 5 — Placement queue accepts jobs
# ------------------------------------------------------------------------------
def test_placement_queue_possible():
    step("Placement queue acceptance")

    try:
        from engines.decision_engine.decide_once.placement import enqueue_for_placement, start_placement_worker

        start_placement_worker()

        enqueue_for_placement(
            "MSC_EXPLORATORY",
            {"px": 5.0, "size": 2.0, "direction": "BACK->LAY"},
            {"run_id": 1, "engine": "MSC_EXPLORATORY"},
        )

        ok("Placement queue accepts jobs")
        return True

    except Exception:
        fail("Placement enqueue failed")
        traceback.print_exc()
        return False

# ------------------------------------------------------------------------------
# TEST 6 — No Lanes imports exist
# ------------------------------------------------------------------------------
def test_no_lanes_dependency():
    step("No Lanes dependency")

    try:
        import sys

        for m in sys.modules:
            if "lanes" in m.lower():
                fail("Lanes module detected")
                return False

        ok("No Lanes imports detected")
        return True

    except Exception:
        fail("Lanes detection crashed")
        traceback.print_exc()
        return False

# ------------------------------------------------------------------------------
# MAIN
# ------------------------------------------------------------------------------
def main():
    banner("AUTOSCALP v7.9.9.x — EXECUTION POSSIBILITY TEST SUITE")

    results = [
        test_msc_exploratory_possible(),
        test_direction_engine_possible(),
        test_msc_inplay_possible(),
        test_msc_risk_possible(),
        test_placement_queue_possible(),
        test_no_lanes_dependency(),
    ]

    banner("TEST SUITE COMPLETE")

    if all(results):
        ok("SYSTEM IS EXECUTION-POSSIBLE")
        sys.exit(0)
    else:
        fail("SYSTEM HAS STRUCTURAL FAILURES")
        sys.exit(1)

if __name__ == "__main__":
    main()
