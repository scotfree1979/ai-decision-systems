# /engines/decision_engine/microscalper/context_adapter.py

from typing import Dict, Any

def build_msc_context(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Transform DecideOnce context into MicroScalper v7 context.

    ctx is the standard DecideOnce context object containing:
      - marketId
      - selectionId
      - current_price
      - oc_phase
      - v7-intel fields
      - legacy signals (if any)
      - dynamic_stake_fn injected by DecideOnce
    """

    msc_ctx = {}

    # Core identifiers
    msc_ctx["marketId"] = ctx.get("marketId")
    msc_ctx["selectionId"] = ctx.get("selectionId")

    # Price / OC-phase
    msc_ctx["current_price"] = ctx.get("current_price")
    msc_ctx["oc_phase"] = ctx.get("oc_phase")

    # Dynamic stake engine (from DecideOnce)
    msc_ctx["dynamic_stake_fn"] = ctx.get("dynamic_stake_fn")

    # Legacy parent linkage
    msc_ctx["legacy_parent_id"] = ctx.get("legacy_parent_id")
    msc_ctx["legacy_entry_side"] = ctx.get("legacy_entry_side")

    # StopLoss event
    msc_ctx["stoploss_triggered_for_parent"] = ctx.get("stoploss_triggered_for_parent")

    # Copy ALL v7-intelligence fields directly
    for k, v in ctx.items():
        if k.startswith("v7_") or k in (
            "slope_ppm", "tick_vel_3s_up", "momentum_class", "drift_speed",
            "inplay_progress", "bias_value", "bias_conf",
            "blueprint_key", "blueprint_confidence",
            "playbook_cluster", "playbook_win_rate",
            "form_class", "form_win_rate",
            "volatility_state", "band_stability",
            "oc_movement"
        ):
            msc_ctx[k] = v

    return msc_ctx
