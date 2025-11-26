from __future__ import annotations
from .utils import clamp

class ConfidenceCombiner:
    def __init__(self, w_slope: float, w_boundary: float, w_xo: float, w_liq: float) -> None:
        self.w_slope = w_slope
        self.w_boundary = w_boundary
        self.w_xo = w_xo
        self.w_liq = w_liq

    def combine(self, slope_score: float, boundary_score: float, xo_score: float, liq_score: float) -> tuple[float, dict]:
        comp = {
            "slope": self.w_slope * slope_score,
            "boundary": self.w_boundary * boundary_score,
            "xo": self.w_xo * xo_score,
            "liq": self.w_liq * liq_score,
        }
        conf = clamp(sum(comp.values()), 0.0, 1.0)
        return conf, comp
