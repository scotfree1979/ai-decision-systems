# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/inplay_hybrid.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create this bridge so IP strategies are active
from __future__ import annotations
from types import SimpleNamespace as _NS
from typing import Optional, Dict, Any

# typed strategy funcs you already have
from .inplay import (
    shock_drift_lay,   # -> Instruction | None
    tired_leader,      # -> Instruction | None
    close_finish,      # -> Instruction | None
    jump_mistake,      # -> Instruction | None
    collapse_fade,     # -> Instruction | None
)

def _mk_ctx(rctx: Dict[str, Any]) -> _NS:
    """Build a duck-typed ctx with all attrs the IP funcs read. Defaults are safe."""
    get = rctx.get
    # price from runner ctx (odds/ltp/price)
    price = get("odds", get("ltp", get("price", None)))
    return _NS(
        phase=str(get("phase", "")).upper(),
        price=float(price) if price is not None else None,
        # velocity / ticks windows (default 0 if absent)
        tick_vel_1s_up=float(get("tick_vel_1s_up", 0.0)),
        tick_vel_3s_up=float(get("tick_vel_3s_up", 0.0)),
        up_ticks_10s=int(get("up_ticks_10s", 0)),
        down_ticks_10s=int(get("down_ticks_10s", 0)),
        # ranks / flags
        fav_rank_30s=int(get("fav_rank_30s", 99)),
        fav_rank_now=int(get("fav_rank_now", 99)),
        is_jumps=bool(get("is_jumps", False)),
        # history prices
        price_5s_ago=float(get("price_5s_ago", get("ltp_5s_ago", price or 0.0))),
        price_30s_ago=float(get("price_30s_ago", get("ltp_30s_ago", price or 0.0))),
        # sizing cap
        size_cap=float(get("size_cap", get("max_stake_cap", 0.0))),
    )

def _to_plan(source_name: str, instr: Any, rctx: Dict[str, Any]) -> Dict[str, Any]:
    """Translate Instruction -> plan dict lanes expect."""
    # price for entry = current ltp/odds from runner ctx
    px = rctx.get("odds", rctx.get("ltp", rctx.get("price", None)))
    plan = {
        "enter": True,
        "side": getattr(instr, "side", "LAY"),
        "price": float(px) if px is not None else None,
        "size": float(getattr(instr, "stake", 0.0)),
        "hedge_ticks": int(getattr(instr, "hedge_ticks", 0)),
        "source": source_name or getattr(instr, "source", source_name),
        # (optional) pack meta too
        "meta": {
            "source": source_name or getattr(instr, "source", source_name),
            "edge": "L2B",  # explicit lay-to-back edge for all in-play strategies
            "hedge_ticks": int(getattr(instr, "hedge_ticks", 0)),
        },
    }
    return plan

# ---- exported decide(ctx) callables -----------------------------------------

def decide_shock_drift(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ipctx = _mk_ctx(ctx)
    instr = shock_drift_lay(ipctx)
    return _to_plan("IP1_SHOCK_DRIFT", instr, ctx) if instr else None

def decide_tired_leader(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ipctx = _mk_ctx(ctx)
    instr = tired_leader(ipctx)
    return _to_plan("IP2_TIRED_LEADER", instr, ctx) if instr else None

def decide_close_finish(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ipctx = _mk_ctx(ctx)
    instr = close_finish(ipctx)
    return _to_plan("IP3_CLOSE_FINISH", instr, ctx) if instr else None

def decide_fence_error(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ipctx = _mk_ctx(ctx)
    instr = jump_mistake(ipctx)
    return _to_plan("IP4_FENCE_ERROR", instr, ctx) if instr else None

def decide_collapse_fade(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    ipctx = _mk_ctx(ctx)
    instr = collapse_fade(ipctx)
    return _to_plan("IP5_COLLAPSE_FADE", instr, ctx) if instr else None

__all__ = [
    "decide_shock_drift",
    "decide_tired_leader",
    "decide_close_finish",
    "decide_fence_error",
    "decide_collapse_fade",
]
# === PATCH END ===
