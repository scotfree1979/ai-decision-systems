# ─────────────────────────────────────────────────────────────────────────────
# LadderStrategy (Plan §3.1 / 1c)
# LTB invariant: Lay first → Back higher (child hedge is +ticks above parent).
# This strategy places multiple parent LAYs stepped OUT across the drift range.
# Router/housekeeping will create the BACK child for each parent.
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from typing import Dict, List

from .strategy_base import StrategyBase

def _next_ticks(p: float, n: int, step: float) -> float:
    # Replace with your real Betfair tick ladder util when available.
    return round(p + n * step, 2)

class LadderStrategy(StrategyBase):
    name = "LADDER_STRATEGY"

    def __init__(self, cfg: Dict):
        """
        cfg keys:
          min_price, max_price, tick_size, stake,
          hedge_ticks, num_rungs, rung_gap_ticks, max_liability
        """
        self.cfg = cfg

    def evaluate(self, market_ctx: Dict, runner_ctx: Dict) -> List[Dict]:
        # Stop in-play and inside T-5m (housekeeping handles existing orders)
        if market_ctx.get("in_play"):
            return []
        mto = market_ctx.get("tto_min")
        if mto is not None and mto < 5:
            return []

        p_anchor = runner_ctx.get("ltp")
        if p_anchor is None:
            return []
        if not (self.cfg["min_price"] <= p_anchor <= self.cfg["max_price"]):
            return []

        open_liab = float(runner_ctx.get("open_liability", 0.0))
        max_liab  = float(self.cfg["max_liability"])

        out: List[Dict] = []
        for i in range(int(self.cfg["num_rungs"])):
            p_parent = _next_ticks(
                p_anchor,
                i * int(self.cfg["rung_gap_ticks"]),
                float(self.cfg["tick_size"])
            )
            if not (self.cfg["min_price"] <= p_parent <= self.cfg["max_price"]):
                continue

            # Per-rung liability check; cap cumulative exposure per runner
            rung_liab = max(0.0, (p_parent - 1.0) * float(self.cfg["stake"]))
            if open_liab + rung_liab > max_liab:
                continue
            open_liab += rung_liab

            out.append({
                "marketId": market_ctx["marketId"],
                "selectionId": runner_ctx["selectionId"],
                "side": "LAY",                       # always lay-first
                "price": float(p_parent),
                "size": float(self.cfg["stake"]),
                "meta": {
                    "source": self.name,
                    "edge": "L2B",                   # explicit lay-to-back edge
                    "hedge_ticks": int(self.cfg["hedge_ticks"])  # child BACK is +ticks above parent
                }
            })


        return out

