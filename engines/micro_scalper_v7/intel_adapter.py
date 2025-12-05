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

# engines/micro_scalper_v7/intel_adapter.py

from typing import Dict, Any

def build_micro_state(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    FULL V7 INTELLIGENCE ADAPTER
    -----------------------------
    Converts CTXv7 into a MICRO_STATE used by MicroScalperEngine.

    Every relevant signal is mapped cleanly, with graceful fallback.
    """

    state = {}

    # ------------------------------------------------------------
    # IDENTITY (always required)
    # ------------------------------------------------------------
    state["marketId"]     = ctx.get("marketId")
    state["selectionId"]  = ctx.get("selectionId")
    state["engine"]       = ctx.get("engine")
    state["source"]       = ctx.get("source")

    # ------------------------------------------------------------
    # PRICE / MOVEMENT
    # ------------------------------------------------------------
    state["px"]               = ctx.get("px")
    state["ltp"]              = ctx.get("ltp")
    state["tape_px"]          = ctx.get("tape_px")

    state["slope_ppm"]        = ctx.get("slope_ppm")
    state["recent_net_ticks"] = ctx.get("recent_net_ticks")
    state["oc_momentum_ticks"] = ctx.get("oc_momentum_ticks")

    state["tick_vel_1s_up"]   = ctx.get("tick_vel_1s_up")
    state["tick_vel_3s_up"]   = ctx.get("tick_vel_3s_up")
    state["tick_vel_3s_down"] = ctx.get("tick_vel_3s_down")

    state["drift_speed"]      = ctx.get("drift_speed")
    state["momentum_class"]   = ctx.get("momentum_class")
    state["band_stability"]   = ctx.get("band_stability")

    # ------------------------------------------------------------
    # RANGE / POSITION / FAVOURITES
    # ------------------------------------------------------------
    state["range_pos"]         = ctx.get("range_pos")
    state["range_span_ticks"]  = ctx.get("range_span_ticks")

    state["fav_rank"]          = ctx.get("fav_rank")
    state["fav_rank_now"]      = ctx.get("fav_rank_now")

    # ------------------------------------------------------------
    # LIQUIDITY
    # ------------------------------------------------------------
    state["liq_score"]           = ctx.get("liq_score")
    state["depth10s"]            = ctx.get("depth10s")
    state["traded_recent_amt"]   = ctx.get("traded_recent_amt")
    state["traded_recent_sec"]   = ctx.get("traded_recent_sec")

    # ------------------------------------------------------------
    # BLUEPRINTS / PLAYBOOKS / FORM
    # ------------------------------------------------------------
    state["blueprint_conf"]      = ctx.get("blueprint_confidence")
    state["blueprint_key"]       = ctx.get("blueprint_key")

    state["playbook_cluster"]    = ctx.get("playbook_cluster")
    state["playbook_win_rate"]   = ctx.get("playbook_win_rate")

    state["form_class"]          = ctx.get("form_class")
    state["form_win_rate"]       = ctx.get("form_win_rate")

    # ------------------------------------------------------------
    # OPPORTUNITY / INDICATORS
    # ------------------------------------------------------------
    state["opportunity"]         = ctx.get("micro_opportunity")
    state["volatility_state"]    = ctx.get("volatility_state")

    # WOM (weight of money)
    state["wom_ratio"]           = ctx.get("wom_ratio")
    state["wom_back_6"]          = ctx.get("wom_back_6")
    state["wom_lay_6"]           = ctx.get("wom_lay_6")

    # ------------------------------------------------------------
    # ODDS HISTORY / SNAPSHOTS
    # ------------------------------------------------------------
    state["anchor_odd"]          = ctx.get("anchor_odd")
    state["oc1"]                 = ctx.get("oc1")  # newest oc1

    state["oc_movement"]         = ctx.get("oc_movement")
    state["oc_phase"]            = ctx.get("oc_phase")

    # ------------------------------------------------------------
    # BIAS (MSC uses this heavily)
    # ------------------------------------------------------------
    state["bias"]                = ctx.get("bias")
    state["bias_dir"]            = ctx.get("bias_dir")
    state["bias_conf"]           = ctx.get("bias_conf")

    return state

