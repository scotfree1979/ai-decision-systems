from __future__ import annotations
from .common import StrategyCtx, Instruction

def shock_drift_lay(ctx: StrategyCtx) -> Instruction | None:
    if ctx.phase not in ("IN_PLAY",): return None
    if not (3.0 <= ctx.price <= 20.0): return None
    if not (ctx.tick_vel_1s_up >= 15 or ctx.tick_vel_3s_up >= 30): return None
    stake = max(2.0, ctx.size_cap * 0.6)
    return Instruction(side="LAY", hedge_ticks=2, stake=stake, source="IP_SHOCK_DRIFT")

def tired_leader(ctx: StrategyCtx) -> Instruction | None:
    if ctx.phase != "IN_PLAY": return None
    if not (1.6 <= ctx.price <= 6.0): return None
    # was fav 30s ago, now odds bouncing up; rivals likely steaming (proxy via up_ticks)
    if not (ctx.fav_rank_30s == 1 and ctx.up_ticks_10s >= 3): return None
    stake = max(2.0, ctx.size_cap * 0.5)
    return Instruction(side="LAY", hedge_ticks=2, stake=stake, source="IP_TIRED_LEADER")

def close_finish(ctx: StrategyCtx) -> Instruction | None:
    if ctx.phase != "IN_PLAY": return None
    if ctx.price > 3.5: return None
    # oscillation proxy: up & down ticks both present in last 10s and rank no longer clear #1
    if not (ctx.up_ticks_10s >= 2 and ctx.down_ticks_10s >= 2 and ctx.fav_rank_now <= 2): return None
    stake = max(2.0, ctx.size_cap * 0.3)
    return Instruction(side="LAY", hedge_ticks=1, stake=stake, source="IP_CLOSE_FINISH")

def jump_mistake(ctx: StrategyCtx) -> Instruction | None:
    if ctx.phase != "IN_PLAY" or not ctx.is_jumps: return None
    # huge spike
    if not (ctx.tick_vel_1s_up >= 25 or (ctx.price - ctx.price_5s_ago) >= 0.5): return None
    if ctx.price > 40.0: return None
    stake = max(2.0, ctx.size_cap * 0.5)
    return Instruction(side="LAY", hedge_ticks=3, stake=stake, source="IP_FENCE_ERROR")

def collapse_fade(ctx: StrategyCtx) -> Instruction | None:
    if ctx.phase != "IN_PLAY": return None
    # big collapse then stall/uptick
    drop = (ctx.price_30s_ago - ctx.price)
    if not (drop >= 3.0 and ctx.up_ticks_10s >= 3): return None
    if ctx.price < 2.0: return None
    stake = max(2.0, ctx.size_cap * 0.3)
    return Instruction(side="LAY", hedge_ticks=1, stake=stake, source="IP_COLLAPSE_FADE")
