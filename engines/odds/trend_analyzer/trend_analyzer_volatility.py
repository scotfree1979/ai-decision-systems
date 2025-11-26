from __future__ import annotations
from collections import deque
from .types import VolRegime

class VolEstimator:
    def __init__(self, window: int = 8) -> None:
        self.window = window
        self.buf = deque(maxlen=window)

    def update(self, odds: float | None) -> VolRegime:
        if odds is None:
            return "MED"
        self.buf.append(float(odds))
        if len(self.buf) < 3:
            return "MED"
        # naive variance proxy
        m = sum(self.buf) / len(self.buf)
        var = sum((x - m) ** 2 for x in self.buf) / len(self.buf)
        if var < 0.0025:
            return "LOW"
        if var < 0.01:
            return "MED"
        return "HIGH"
