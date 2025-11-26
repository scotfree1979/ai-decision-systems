"""
Range tracking + breakout detection fed by OC cache/bands.
- Anchor is the first seen price (OC0) for the runner.
- Range low/high are the observed extrema so far for the day.
- Breakouts are declared once price holds beyond the edge + buffer for N seconds.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Tuple
from collections import defaultdict

from engines.decision_engine.adapters import tick_diff, add_ticks
try:
    from engines.utils.time_compat import now_utc
except Exception:
    from datetime import datetime, timezone
    def now_utc():
        return datetime.now(timezone.utc)

from engines.decision_engine.constants import (
    RANGE_BUFFER_TICKS,
    RANGE_HOLD_SECS,
)


@dataclass
class RangeSnapshot:
    anchor: Optional[float]
    low: Optional[float]
    high: Optional[float]
    position_ratio: Optional[float]   # 0..1 between low..high (None if undefined)
    breakout: str                     # 'none' | 'up' | 'down'
    breakout_confirmed: bool


class RangeTracker:
    """In‑memory per (marketId, selectionId) range state."""
    def __init__(self):
        # key → (low, high, last_break_dir, last_break_since_ts)
        self._state: Dict[Tuple[str,int], Tuple[Optional[float], Optional[float], Optional[str], Optional[float]]] = {}

    def update(self, marketId: str, selectionId: int, anchor: Optional[float], current: Optional[float]) -> RangeSnapshot:
        k = (marketId, selectionId)
        now = now_utc().timestamp()

        low, high, last_dir, since_ts = self._state.get(k, (anchor, anchor, None, None))

        if current is None:
            return RangeSnapshot(anchor, low, high, _pos_ratio(low, high, None), 'none', False)

        # Initialize bounds if needed
        if low is None: low = current
        if high is None: high = current

        # Extend range with fresh extrema
        if current < low:
            low = current
        if current > high:
            high = current

        # Check for breakout relative to *current* range edges with buffer
        breakout = 'none'
        confirmed = False

        if high is not None and tick_diff(high, current) >= RANGE_BUFFER_TICKS:
            # price above high + buffer → upward breakout
            if last_dir != 'up':
                last_dir, since_ts = 'up', now
            breakout = 'up'
            confirmed = (since_ts is not None and (now - since_ts) >= RANGE_HOLD_SECS)
            if confirmed:
                # ratchet range upward to include current
                high = current
        elif low is not None and tick_diff(current, low) >= RANGE_BUFFER_TICKS:
            # price below low - buffer → downward breakout
            if last_dir != 'down':
                last_dir, since_ts = 'down', now
            breakout = 'down'
            confirmed = (since_ts is not None and (now - since_ts) >= RANGE_HOLD_SECS)
            if confirmed:
                low = current
        else:
            # No breakout pressure → clear timer
            last_dir, since_ts = None, None

        self._state[k] = (low, high, last_dir, since_ts)
        pos = _pos_ratio(low, high, current)
        return RangeSnapshot(anchor, low, high, pos, breakout, confirmed)


def _pos_ratio(low: Optional[float], high: Optional[float], current: Optional[float]) -> Optional[float]:
    try:
        if None in (low, high, current):
            return None
        span_ticks = max(1, abs(tick_diff(low, high)))
        pos_ticks = max(0, min(span_ticks, tick_diff(low, current)))
        return pos_ticks / float(span_ticks)
    except Exception:
        return None
