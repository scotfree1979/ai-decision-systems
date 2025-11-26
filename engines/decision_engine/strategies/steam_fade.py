# 📍 TARGET: engines/decision_engine/strategies/steam_fade.py
# 🔎 SEARCH: from __future__ import_annotations
# (Replace the whole module with the corrected content.)

from __future__ import annotations
from .common import StrategyCtx, Instruction

# 📍 TARGET: engines/decision_engine/strategies/steam_fade.py
# 🔎 SEARCH: def decide(ctx: StrategyCtx) -> Instruction | None:
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    STEAM_FADE — PRE, hedge-only in lay-first architecture.

    Always returns a B2L instruction tagged as hedge_only.
    The bias filter (apply_bias_to_plan) will drop it unless:
      - significant steam is detected, AND
      - there is exposure to reduce.
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

        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.6)

        # Emit as BACK (B2L) but mark hedge_only
        return Instruction(
            side="BACK",
            hedge_ticks=2,
            stake=stake,
            source="STEAM_FADE",
            meta={"edge": "B2L", "hedge_only": True}
        )
    except Exception:
        return None

