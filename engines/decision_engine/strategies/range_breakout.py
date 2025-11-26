from __future__ import annotations
from .common import StrategyCtx, Instruction

# 📍 TARGET: engines/decision_engine/strategies/range_breakout.py
# 🔎 SEARCH: def decide(ctx: StrategyCtx) -> Instruction | None:
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    S5_BREAKOUT — PRE, lay-first bias.
    Only trades breakouts through the TOP of the recent range (drift).
    Ignores bottom breakouts (steam) in lay-first architecture.
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

        hi = getattr(ctx, "recent_high", None)
        lo = getattr(ctx, "recent_low", None)
        try:
            hi = float(hi if hi is not None else 0.0)
        except Exception:
            hi = 0.0
        try:
            lo = float(lo if lo is not None else 0.0)
        except Exception:
            lo = 0.0

        slope = getattr(ctx, "slope_per_min", None)
        try:
            slope = float(slope if slope is not None else 0.0)
        except Exception:
            slope = 0.0

        # Breakout conditions
        breakout_up = (hi > 0 and price >= hi and slope > +0.08)
        # We deliberately IGNORE breakout_dn (steam) in lay-first mode
        if not breakout_up:
            return None

        # Drift breakout → L2B parent (LAY now, BACK child later)
        side = "LAY"
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.6)
        return Instruction(side=side, hedge_ticks=2, stake=stake, source="RANGE_BREAKOUT")
    except Exception:
        return None


