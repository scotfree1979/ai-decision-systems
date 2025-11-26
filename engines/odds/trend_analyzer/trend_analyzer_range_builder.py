from __future__ import annotations
from dataclasses import dataclass
from .types import Basis

@dataclass
class RangeState:
    low: float | None = None
    high: float | None = None

    def update(self, odds: float | None) -> None:
        if odds is None:
            return
        if self.low is None or odds < self.low:
            self.low = float(odds)
        if self.high is None or odds > self.high:
            self.high = float(odds)

    def pos_and_dist(self, odds: float | None) -> tuple[float | None, float | None]:
        if odds is None or self.low is None or self.high is None:
            return None, None
        rng = max(self.high - self.low, 1e-9)
        pos = (odds - self.low) / rng
        dist = min(abs(odds - self.low), abs(self.high - odds))
        return pos, dist


def write_range_to_basis(b: Basis, rs: RangeState, odds: float | None) -> Basis:
    b.range_low = rs.low
    b.range_high = rs.high
    pos, d = rs.pos_and_dist(odds)
    b.pos_in_range = pos
    b.distance_to_boundary = d
    return b
