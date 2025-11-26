# ─────────────────────────────────────────────────────────────────────────────
# Strategy base (returns placement instructions; router lives elsewhere)
# Plan §3.1 / 1a
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from typing import Dict, List, Optional, Union

Instruction = Dict[str, Union[str, float, int, Dict[str, Union[str, int, float]]]]

class StrategyBase:
    name: str = "BASE"

    def evaluate(self, market_ctx: Dict, runner_ctx: Dict) -> List[Instruction]:
        """
        Return a list of placement instructions shaped like:
        {
          'marketId': str,
          'selectionId': str | int,
          'side': 'LAY' | 'BACK',
          'price': float,
          'size': float,
          'meta': {
              'source': str,          # strategy name for attribution
              'hedge_ticks': int      # +ticks for LTB hedge
          }
        }
        Router/housekeeping handles OCO pairing, events, and lifecycle.
        """
        raise NotImplementedError("Strategies must implement evaluate()")

