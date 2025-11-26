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

NAME = "BTL_AGGR"

def decide(ctx: StrategyCtx) -> dict | None:
    """
    BTL_AGGR — PRE (Back-to-Lay), hedge-only signal scaffold.
    Emits small B2L ideas in a sensible window; Mastery/CAPs still own final placement.
    """
    try:
        # minutes to off: accept pre-off; be slightly more permissive than A while testing
        mto = getattr(ctx, "minutes_to_off", None) or getattr(ctx, "tto_minutes", None)
        try:
            mto = float(mto) if mto is not None else 9999.0
        except Exception:
            mto = 9999.0
        if not (3.0 <= mto <= 90.0):  # widen from 5–60 to 3–90 so you can see it today
            return None

        # price band
        price = getattr(ctx, "price", None) or getattr(ctx, "odds", None) or getattr(ctx, "ltp", None)
        try:
            price = float(price or 0.0)
        except Exception:
            price = 0.0
        if not (2.0 <= price <= 12.0):
            return None

        # basic stake cap (respect ctx size_cap if present)
        size_cap = getattr(ctx, "size_cap", None)
        try:
            size_cap = float(size_cap) if size_cap is not None else 2.0
        except Exception:
            size_cap = 2.0
        stake = max(2.0, size_cap)

        return {
            "enter": True,
            "letter": "B",
            "direction": "BACK->LAY",
            "target_ticks": 2,
            "hedge_ticks": 2,
            "size": stake,
            "px": price,
            "plan_why": f"B2L-aggr mto={mto:.1f}",
            "meta": {"edge": "B2L", "hedge_only": True}
        }
    except Exception:
        return None