from __future__ import annotations
from .common import StrategyCtx, Instruction

# 📍 TARGET: engines/decision_engine/strategies/crossover.py
# 🔎 SEARCH: def decide(ctx: StrategyCtx) -> Instruction | None:
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    CROSSOVER — PRE, lay-first bias.
    Only trades when odds cross UP (drifting runner).
    Ignores cross-down steam in lay-first architecture.
    """
    try:
        phase = getattr(ctx, "phase", None) or "UNKNOWN"
        tto_s = getattr(ctx, "tto_s", None)
        try:
            tto_s = int(tto_s or 0)
        except Exception:
            tto_s = 0
        if phase != "PRE" or not (60 <= tto_s <= 3600):
            return None

        price = getattr(ctx, "price", None)
        try:
            price = float(price if price is not None else 0.0)
            if price != price:  # NaN
                price = 0.0
        except Exception:
            price = 0.0

        ma_fast = getattr(ctx, "ma_fast", None)
        ma_slow = getattr(ctx, "ma_slow", None)
        try:
            ma_fast = float(ma_fast if ma_fast is not None else 0.0)
        except Exception:
            ma_fast = 0.0
        try:
            ma_slow = float(ma_slow if ma_slow is not None else 0.0)
        except Exception:
            ma_slow = 0.0

        # Cross-up condition: fast MA above slow AND price above fast MA
        crossed_up = (ma_fast > 0 and ma_slow > 0 and ma_fast > ma_slow and price > ma_fast)
        if not crossed_up:
            return None

        # Drift crossover → L2B parent (LAY now, BACK child later)
        side = "LAY"
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.6)
        return Instruction(side=side, hedge_ticks=2, stake=stake, source="CROSSOVER")
    except Exception:
        return None

