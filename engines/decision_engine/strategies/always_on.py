# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/always_on.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create file with AlwaysOnStrategy (OG clone, name=ALWAYS_ON)
from __future__ import annotations
from typing import Dict, List
from .strategy_base import StrategyBase
from engines.mastery.plan_ledger import record_plan, concurrency_ok

NAME = "ALWAYS_ON"

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/always_on.py
# 🔎 SEARCH: ^def decide\(ctx: Dict\[str, Any\]\) -> Optional\[Dict\[str, Any\]\]:
# ⛏️ ACTION: replace entire decide() with below
def decide(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Always-On: simple gate on price band and minutes-to-off."""
    try:
        # odds
        ltp = ctx.get("odds") or ctx.get("ltp")
        if ltp is None:
            return None
        ltp = float(ltp)

        # minutes-to-off: compute if missing
        mto = ctx.get("minutes_to_off") or ctx.get("tto_minutes")
        try:
            mto = float(mto)
        except Exception:
            # fallback: compute from orchestrator if possible
            try:
                from engines.decision_engine.orchestrator import _compute_minutes_to_off
                mid = str(ctx.get("marketId") or "")
                mto, _ = _compute_minutes_to_off(mid, source="LIVE")
            except Exception:
                mto = 9999.0

        if not (5.0 <= mto <= 60.0):
            return None
        if not (2.0 <= ltp <= 12.0):
            return None

        return {
            "enter": True,
            "letter": "A",
            "direction": "LAY->BACK" if ltp >= 4.0 else "BACK->LAY",
            "target_ticks": 1,
            "hedge_ticks": 1,
            "size": 2.0,
            "px": ltp,
            "plan_why": f"A-on px={ltp:.2f} mto={mto:.1f}"
        }
    except Exception:
        return None
# === PATCH END ===
