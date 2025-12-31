from __future__ import annotations
from .common import StrategyCtx, Instruction

def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    BTL_SCOUT — fires on early opportunity emergence.
    Uses MarketMonitor PASSIVE→ACTIVE transition.
    """
    try:
        if getattr(ctx, "phase", None) != "PRE":
            return None

        sig = getattr(ctx, "signals", {}) or {}
        if not sig.get("p2a_recent"):
            return None

        side = "LAY"
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        return Instruction(
            side=side,
            hedge_ticks=2,
            stake=max(2.0, size_cap * 0.4),
            source="B",
        )
    except Exception:
        return None
