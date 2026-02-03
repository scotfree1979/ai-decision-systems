from __future__ import annotations
from typing import Dict, Optional

from tools.betfair_runner_trend_surface import get_runner_trend
from tools.betfair_match_surface import get_direction_confidence


NAME = "ALWAYS_ON"


def decide(ctx: Dict) -> Optional[Dict]:
    """
    ALWAYS_ON (A)

    Primitive, truth-driven strategy:
    - Direction from MARKET TRUTH (runner trend)
    - Confidence from EXECUTION TRUTH (matched hedge cycles)

    No time gating.
    No heuristics.
    No firing logic.

    Mastery decides whether to act.
    """
    try:
        market_id = ctx.get("marketId")
        selection_id = ctx.get("selectionId")
        px = ctx.get("px") or ctx.get("odds")

        if not market_id or not selection_id or px is None:
            return None

        # --------------------------------------------------
        # 1️⃣ MARKET TRUTH — price movement direction
        # --------------------------------------------------
        trend = get_runner_trend(
            str(market_id),
            str(selection_id),
        )

        direction = trend.get("direction")
        if direction == "FLAT":
            direction = None  # explicit neutrality

        # --------------------------------------------------
        # 2️⃣ EXECUTION TRUTH — hedge-cycle confidence
        # --------------------------------------------------
        confidence = get_direction_confidence(
            str(market_id),
            str(selection_id),
        )

        # --------------------------------------------------
        # 3️⃣ Emit plan context ONLY
        # --------------------------------------------------
        return {
            "enter": True,               # ALWAYS evaluated
            "letter": "A",
            "engine": "LEGACY",

            # authoritative signals
            "direction": direction,      # BACK->LAY / LAY->BACK / None
            "confidence": confidence,    # 0.0 → 1.0

            # execution context
            "px": float(px),
            "target_ticks": 1,

            # audit trail
            "plan_why": (
                f"A trend={trend.get('direction')} "
                f"ticks={trend.get('ticks_moved')} "
                f"conf={confidence}"
            ),
        }

    except Exception:
        return None
