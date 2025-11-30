#!/usr/bin/env python3
"""
PHASE 1 — MSC EXPLORATORY ISOLATION TEST
----------------------------------------
Directly loads MicroScalperEngine and feeds it a valid synthetic context.

If this produces a plan → MSC works.
If this returns None → MSC exploratory is broken internally.
"""

import sys, os, json

# add project root
sys.path.append("/Users/malachikelly/Dev/analytics_beta_dev")

print("\n=== MSC EXPLORATORY ISOLATION TEST ===\n")

# ------------------------------------------------------------
# Load MSC engine + context adapter
# ------------------------------------------------------------
try:
    from engines.micro_scalper_v7.micro_scalper_engine import MicroScalperEngine
    from engines.micro_scalper_v7.intel_adapter import build_micro_state

except Exception as e:
    print("[FATAL] Could not import MSC components:", e)
    sys.exit(1)

msc = MicroScalperEngine()

# ------------------------------------------------------------
# Synthetic runner context
# ------------------------------------------------------------
ctx = {
    "marketId": "1.TEST",
    "selectionId": "101",
    "current_price": 3.00,
    "oc_phase": 0,       # MUST BE INT (0–6 PRE-OFF, >=7 IN-PLAY)
    "legacy_parent_id": None,
    "legacy_entry_side": None,
    "dynamic_stake_fn": None,
    "stoploss_triggered_for_parent": None,

    # minimal v7 fields (placeholders)
    "slope_ppm": 0.0,
    "tick_vel_3s_up": 0.0,
    "momentum_class": 0,
    "band_stability": 0,
    "drift_speed": 0,
    "inplay_progress": 0,
    "bias_value": 0.0,
    "bias_conf": 0.0,
    "blueprint_confidence": 0.0,
    "blueprint_key": "",
    "playbook_cluster": "",
    "playbook_win_rate": 0.0,
    "form_class": "",
    "form_win_rate": 0.0,
    "micro_opportunity": 0.0,
    "volatility_state": "",
    "oc_movement": 0.0,
}


print("[DEBUG] Synthetic ctx →", ctx)

# ------------------------------------------------------------
# Build MSC-compatible context
# ------------------------------------------------------------
try:
    msc_ctx = build_micro_state(ctx)

    print("\n[DEBUG] Built MSC ctx:")
    print(json.dumps(msc_ctx, indent=2))
except Exception as e:
    print("\n[FATAL] build_msc_context FAILED:", e)
    sys.exit(1)

# ------------------------------------------------------------
# Run MSC tick
# ------------------------------------------------------------
try:
    plan = msc.tick(msc_ctx)
    print("\n=== MSC RESULT ===")
    print(plan if plan else "MSC returned None")
except Exception as e:
    print("\n[FATAL] MSC.tick FAILED:", e)
    sys.exit(1)

print("\n=== TEST COMPLETE ===\n")
