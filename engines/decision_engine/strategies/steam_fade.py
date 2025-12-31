from __future__ import annotations
from .common import StrategyCtx, Instruction

def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    S6_STEAM_FADE — fires when favourite just lost status.
    Exhaustion proxy via fav transition.
    """
    try:
        if getattr(ctx, "phase", None) != "PRE":
            return None

        sig = getattr(ctx, "signals", {}) or {}
        if not sig.get("lost_fav_recent"):
            return None

        side = "BACK"
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        return Instruction(
            side=side,
            hedge_ticks=2,
            stake=max(2.0, size_cap * 0.7),
            source="F",
        )
    except Exception:
        return None
