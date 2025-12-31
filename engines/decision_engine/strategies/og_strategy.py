from __future__ import annotations
from .common import StrategyCtx, Instruction

class OGStrategy:
    """
    OG_STRATEGY (S) — baseline opportunity.

    Class wrapper required by registry.
    Delegates to event-based decide(ctx).
    """

    name = "OG_STRATEGY"

    def __init__(self, cfg: dict | None = None):
        self.cfg = cfg or {}

    def evaluate(self, mkt_ctx: dict, runner_ctx: dict):
        """
        Registry / DecideOnce entrypoint.
        runner_ctx is the StrategyCtx-like dict.
        """
        return self.decide(runner_ctx)

    def decide(self, ctx: StrategyCtx) -> Instruction | None:
        try:
            if getattr(ctx, "phase", None) != "PRE":
                return None

            # MarketMonitor signals injected upstream
            sig = getattr(ctx, "signals", {}) or {}

            # Baseline opportunity = PASSIVE → ACTIVE just happened
            if not sig.get("p2a_recent"):
                return None

            side = "LAY"
            size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)

            return Instruction(
                side=side,
                hedge_ticks=2,
                stake=max(2.0, size_cap * 0.5),
                source="S",
            )
        except Exception:
            return None
