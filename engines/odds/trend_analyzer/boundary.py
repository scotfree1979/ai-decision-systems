from __future__ import annotations
from .types import Basis

class BoundaryDetector:
    def __init__(self, bounce_confirm_ticks: int = 1, breakout_eps: float = 0.03, breakout_hold_ms: int = 500):
        self.bounce_confirm_ticks = bounce_confirm_ticks
        self.breakout_eps = breakout_eps
        self.breakout_hold_ms = breakout_hold_ms
        self._last_dir: int = 0  # -1 down, +1 up
        self._near_high = False
        self._near_low = False

    def detect(self, odds_now: float | None, range_low: float | None, range_high: float | None) -> list[str]:
        events: list[str] = []
        if odds_now is None or range_low is None or range_high is None:
            return events
        rng = max(range_high - range_low, 1e-9)
        near_tol = 0.05 * rng
        self._near_low = abs(odds_now - range_low) <= near_tol
        self._near_high = abs(range_high - odds_now) <= near_tol
        # Note: precise bounce/breakout confirmation would need history & time; keep simple for MVP.
        if self._near_high and self._last_dir < 0:
            events.append("BOUNCE_HIGH")
        if self._near_low and self._last_dir > 0:
            events.append("BOUNCE_LOW")
        # breakout placeholders (require bigger infra in ticker)
        if odds_now > range_high + self.breakout_eps:
            events.append("BREAKOUT_UP")
        if odds_now < range_low - self.breakout_eps:
            events.append("BREAKOUT_DOWN")
        return events

    def note_move(self, delta: float | None) -> None:
        if delta is None:
            return
        self._last_dir = -1 if delta < 0 else (1 if delta > 0 else 0)
