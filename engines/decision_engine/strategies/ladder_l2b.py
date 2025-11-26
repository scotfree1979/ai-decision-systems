from __future__ import annotations
from .common import StrategyCtx, Instruction

def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    LADDER_L2B — PRE, explicit lay-to-back ladder.

    Ensures parent is LAY, with children staged below for back hedging.
    """
    try:
        phase = getattr(ctx, "phase", None) or "UNKNOWN"
        if phase != "PRE":
            return None

        price = float(getattr(ctx, "price", 0.0) or 0.0)
        if price <= 0.0:
            return None

        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.6)

        ticks = int(getattr(ctx, "ladder_ticks", 3) or 3)
        return Instruction(
            side="LAY",
            hedge_ticks=ticks,
            stake=stake,
            source="LADDER_L2B"
        )
    except Exception:
        return None

