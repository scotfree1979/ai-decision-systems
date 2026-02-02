from __future__ import annotations
from .common import StrategyCtx, Instruction

# 📍 TARGET: engines/decision_engine/strategies/crossover.py
# 📆 REWRITE: 2026-03-20 — structural crossover (any runner)
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    S4_CROSSOVER — PRE, structural runner-order crossover.

    Fires when THIS runner changes rank relative to others.
    Favourite status is annotation only, not a requirement.
    """
    try:
        if getattr(ctx, "phase", None) != "PRE":
            return None

        tto_s = int(getattr(ctx, "tto_s", 0) or 0)
        if not (60 <= tto_s <= 3600):
            return None

        sig = getattr(ctx, "signals", {}) or {}
        if not sig.get("crossed_over_recent"):
            return None

        # Optional refinement: only upward moves
        if sig.get("rank_delta", 0) <= 0:
            return None

        return Instruction(
            side="LAY",
            hedge_ticks=2,
            stake=float(getattr(ctx, "size_cap", 2.0) or 2.0) * 0.6,
            source="X",
        )

    except Exception:
        return None
