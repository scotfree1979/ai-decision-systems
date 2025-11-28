# /engines/micro_scalper_v7/intel_adapter.py

"""
intel_adapter.py
----------------
Fuses:

- v7 intelligence
- bias engine
- trend analyzer
- indicators/opportunities
- blueprints
- playbooks
- form book
- oc snapshots

into a single MICRO_STATE object used by all MicroScalper engines.
"""

from typing import Dict, Any

def build_micro_state(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Consume the context_builder output (v7 enriched), plus any 
    supplemental trend/indicator signals, and return a fused
    MICRO_STATE used by MicroScalperEngine.

    Assumes `ctx` already includes v7 intel fields such as:
        - slope_ppm
        - tick_vel_3s_up
        - momentum_class
        - band_stability
        - drift_speed
        - inplay_progress
        - blueprint_confidence
        - playbook_cluster_stats
        - bias_value / bias_conf
        - form metrics
    """
    state = {}

    # V7 Intelligence fields
    state["slope_ppm"]        = ctx.get("slope_ppm")
    state["tick_vel_3s_up"]   = ctx.get("tick_vel_3s_up")
    state["momentum_class"]   = ctx.get("momentum_class")
    state["band_stability"]   = ctx.get("band_stability")
    state["drift_speed"]      = ctx.get("drift_speed")
    state["inplay_progress"]  = ctx.get("inplay_progress")

    # Bias
    state["bias_value"]       = ctx.get("bias_value")
    state["bias_conf"]        = ctx.get("bias_conf")

    # Blueprints
    state["blueprint_conf"]   = ctx.get("blueprint_confidence")
    state["blueprint_key"]    = ctx.get("blueprint_key")

    # Playbooks
    state["playbook_cluster"] = ctx.get("playbook_cluster")
    state["playbook_win_rate"] = ctx.get("playbook_win_rate")

    # Form
    state["form_class"]       = ctx.get("form_class")
    state["form_win_rate"]    = ctx.get("form_win_rate")

    # Indicators / opportunities
    state["opportunity"]      = ctx.get("micro_opportunity")
    state["volatility_state"] = ctx.get("volatility_state")

    # Odds snapshots
    state["oc_movement"]      = ctx.get("oc_movement")
    state["oc_phase"]         = ctx.get("oc_phase")  # PRE-OFF or IN-PLAY

    return state
