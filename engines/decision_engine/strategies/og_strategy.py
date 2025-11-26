# ─────────────────────────────────────────────────────────────────────────────
# OGStrategy (Plan §3.1 / 1b)
# Direction-aware LAY: active T-60m..T-5m, only lays with drift (positive slope).
# Router/housekeeping will create the BACK child (LTB invariant).
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from typing import Dict, List
from .strategy_base import StrategyBase

class OGStrategy(StrategyBase):
    name = "OG_STRATEGY"

    def __init__(self, cfg: Dict):
        # cfg: {'min_price','max_price','stake','hedge_ticks','tick_size'}
        self.cfg = cfg

    def _slope(self, xs: List[float]) -> float:
        n = len(xs)
        if n < 3:
            return 0.0
        xbar = (n - 1) / 2
        ybar = sum(xs) / n
        num = sum((i - xbar) * (xs[i] - ybar) for i in range(n))
        den = sum((i - xbar) ** 2 for i in range(n)) or 1.0
        return num / den

    def evaluate(self, market_ctx: Dict, runner_ctx: Dict) -> List[Dict]:
        # Stop in-play or outside window
        if market_ctx.get("in_play"):
            return []
        mto = market_ctx.get("tto_min")  # minutes to off
        if mto is None or not (5 <= mto <= 60):
            return []

        p = runner_ctx.get("ltp")
        band = runner_ctx.get("band", [])
        if p is None or not (self.cfg["min_price"] <= p <= self.cfg["max_price"]):
            return []
        if len(band) < 5:
            return []

        # Lay only with drift bias (positive slope over recent window)
        slope = self._slope(band[-12:])
        if slope <= 0.0:
            return []

        return [{
            "marketId": market_ctx["marketId"],
            "selectionId": runner_ctx["selectionId"],
            "side": "LAY",
            "price": float(p),
            "size": float(self.cfg["stake"]),
            "meta": {
                "source": self.name,
                "edge": "L2B",  # explicit lay-to-back edge
                "hedge_ticks": int(self.cfg["hedge_ticks"])
            }

        }]

