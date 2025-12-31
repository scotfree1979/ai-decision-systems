from __future__ import annotations
from .common import StrategyCtx, Instruction
from engines.market_monitor.monitor import get_moved_signals

def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    BTL_AGGR — fires on strong movement event (≥2%).
    """
    try:
        if getattr(ctx, "phase", None) != "PRE":
            return None

        mid = getattr(ctx, "marketId", None)
        sid = getattr(ctx, "selectionId", None)
        if not mid or not sid:
            return None

        moves = get_moved_signals()
        hit = any(m == mid and s == sid and d >= 0.02 for m, s, d in moves)
        if not hit:
            return None

        side = "LAY"
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        return Instruction(
            side=side,
            hedge_ticks=3,
            stake=max(2.0, size_cap * 0.8),
            source="G",
        )
    except Exception:
        return None
