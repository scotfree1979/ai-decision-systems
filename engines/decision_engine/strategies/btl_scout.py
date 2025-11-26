from __future__ import annotations
from .common import StrategyCtx, Instruction

# --- safety shim (top of file) ---
import math as _math
def _safe_int(x, default=1):
    try:
        v = float(x)
        if _math.isnan(v):
            return default
        return int(v)
    except Exception:
        try:
            return int(x)
        except Exception:
            return default
# ---------------------------------

# 📍 TARGET: engines/decision_engine/strategies/btl_scout.py
# 🔎 SEARCH: def decide(ctx: StrategyCtx) -> Instruction | None:
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    BTL_SCOUT — PRE, hedge-only in lay-first architecture.

    Scouts steam entries but always returns B2L with hedge_only flag.
    Bias filter will only allow them when there is liability to reduce.
    """
    try:
        phase = getattr(ctx, "phase", None) or "UNKNOWN"
        if phase != "PRE":
            return None

        price = getattr(ctx, "price", None)
        try:
            price = float(price if price is not None else 0.0)
            if price != price:  # NaN
                price = 0.0
        except Exception:
            price = 0.0
        if price <= 0.0:
            return None

        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.5)

        return Instruction(
            side="BACK",
            hedge_ticks=2,
            stake=stake,
            source="BTL_SCOUT",
            meta={"edge": "B2L", "hedge_only": True}
        )
    except Exception:
        return None

