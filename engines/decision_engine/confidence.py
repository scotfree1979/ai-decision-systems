"""
Confidence model (Phase 1):
- Start at 0.50 baseline.
- Edge proximity bump (toward edge with matching trend).
- Breakout confirmation bump.
- Light penalties on false/weak signals.
Returns confidence ∈ [0, 1], proposed target ticks, and direction.
"""
from __future__ import annotations

from typing import Optional, Tuple

from engines.decision_engine.constants import (
    CONF_BUMP_EDGE,
    CONF_BUMP_BREAKOUT,
    CONF_PENALTY_FALSE_BREAK,
    TARGET_TICKS_DEFAULT,
    TARGET_TICKS_ON_BREAKOUT,
)


def compute_confidence(
    *,
    tier: str,                       # 'active' | 'passive'
    trend_sign: int,                 # -1 steam, 0 flat, +1 drift
    range_breakout: str,             # 'none' | 'up' | 'down'
    range_breakout_confirmed: bool,
    near_edge: bool,
) -> Tuple[float, int, str]:
    """
    Direction mapping:
      + trend_sign > 0 (drift)  → lay_to_back
      + trend_sign < 0 (steam)  → back_to_lay
    Range breakout overrides trend direction if confirmed.
    """
    # 1) Baseline by tier
    base = 0.52 if tier == 'active' else 0.50

    # 2) Edge proximity bump when trend supports the push
    if near_edge and trend_sign != 0:
        base += CONF_BUMP_EDGE

    # 3) Breakout bump
    if range_breakout != 'none' and range_breakout_confirmed:
        base += CONF_BUMP_BREAKOUT

    # 4) Light clip
    base = max(0.0, min(1.0, base))

    # 5) Direction + ticks
    if range_breakout_confirmed:
        # Up breakout = drift → lay_to_back, down = steam → back_to_lay
        direction = 'lay_to_back' if range_breakout == 'up' else 'back_to_lay'
        ticks = TARGET_TICKS_ON_BREAKOUT
    else:
        direction = 'lay_to_back' if trend_sign > 0 else ('back_to_lay' if trend_sign < 0 else 'lay_to_back')
        ticks = TARGET_TICKS_DEFAULT

    return base, ticks, direction
