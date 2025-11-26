"""
Stake sizing for Phase 1 (learning/live share the same logic).
Simple, monotonic with confidence, with a breakout multiplier and 2% budget cap.
"""
from __future__ import annotations

from typing import Tuple

from engines.decision_engine.constants import (
    DEFAULT_UNIT_STAKE,
    SIZE_MULTIPLIER_BREAKOUT,
    MAX_BUDGET_FRACTION_PER_TRADE,
)

# optional: read a base from daily_config if present
try:
    from engines.daily_config import DEFAULT_UNIT_STAKE as _CFG_UNIT
    _BASE = float(_CFG_UNIT)
except Exception:
    _BASE = float(DEFAULT_UNIT_STAKE)


def propose_stake(*, available_budget: float, confidence: float, breakout: bool) -> float:
    """Return a stake respecting budget cap; smooth growth with confidence."""
    base = _BASE
    # scale 0.5 → 1.0 maps to ×1.0 → ×2.0
    scale = 1.0 + max(0.0, confidence - 0.5) * 2.0
    if breakout:
        scale *= SIZE_MULTIPLIER_BREAKOUT

    stake = base * scale

    # hard cap at N% of available budget
    cap = max(1.0, available_budget * MAX_BUDGET_FRACTION_PER_TRADE)
    return round(min(stake, cap), 2)
