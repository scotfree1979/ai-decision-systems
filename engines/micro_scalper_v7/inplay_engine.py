# === PATCH START ===
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: ^from typing import Dict, Any, Optional
# 🛠 ACTION: Replace entire file with this implementation
# 📆 PATCHED: 2025-12-01 — Complete MSC In-Play Engine (Engine C)
# ============================================================================

from typing import Dict, Any, Optional
from .state_machine import InPlaySubState
from .intel_adapter import build_micro_state
from engines.micro_scalper_v7.direction_engine import compute_msc_decision
from engines.cashout_calc import cashout_calc


class InPlayEngine:
    """
    IN-PLAY MSC Engine (Engine C)
    -------------------------------------------
    Rules (as specified):

    • Only LAY collapsing runners.
    • Trigger requires:
          - oc_phase >= 7
          - win_probability collapse OR OC collapse signals
    • Stake MUST NOT create net negative PnL for the runner.
      That is: stake <= runner’s canonical positive PnL.
    • Sweetspot ≈ odds ≥ 7.0 (configurable)
    • Exit conditions:
          - Trailing/boundary exit → CHILD_T
          - Collapse reversal → CHILD_S
          - Profit reached → CHILD_H
    • No exit until boundary or reversal.
    """

    SWEETSPOT_MIN_ODDS = 7.0
    HARD_LOWER_BOUND = 1.5     # protective close
    HARD_UPPER_BOUND = 12.0    # protective close

    def __init__(self):
        self.state = InPlaySubState.IDLE
        self.active_plan = None
        self.entry_px = None
        self.parent_pnl_cache = {}   # per-runner PnL from cashout system

    # ----------------------------------------------------------------------
    # PUBLIC API
    # ----------------------------------------------------------------------
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        oc_phase = ctx.get("oc_phase", 0)
        if oc_phase < 7:
            self.state = InPlaySubState.IDLE
            self.active_plan = None
            return None

        # Build v7 microstate
        m = build_micro_state(ctx)

        # Load canonical PnL for this runner (if possible)
        self._load_runner_pnl(ctx)

        if self.state == InPlaySubState.IDLE:
            return self._try_open(ctx, m)

        if self.state == InPlaySubState.MONITOR:
            return self._monitor(ctx, m)

        return None

    # ----------------------------------------------------------------------
    # INTERNAL LOGIC — ENTRY
    # ----------------------------------------------------------------------
# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: def _try_open(self, ctx, m):
# 🛠 ACTION: Replace entire _try_open() with ticks-to-50 parent plan
# 📆 PATCHED: 2025-12-06 — In-Play MSC Engine uses parent-plan routed to lanes
# ==============================================================================

    def _try_open(self, ctx: Dict[str, Any], m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            """
            Decide whether to open an in-play micro-LAY.
            """

            current = ctx.get("current_price")
            if not current or current <= 0:
                return None

            # Must be in sweetspot
            if current < self.SWEETSPOT_MIN_ODDS:
                return None

            # Collapse detection from direction engine
            dec = compute_msc_decision(ctx)
            win_prob = dec["win_prob"]
            direction = dec["direction"]   # BACK->LAY or LAY->BACK

            # In-play collapse = strong loser → direction must be BACK->LAY
            if direction != "BACK->LAY":
                return None

            # Confidence threshold
            if win_prob > 0.40:
                return None

            # Must have enough guaranteed profit to keep runner ≥ 0
            stake_limit = self._max_allowed_stake(ctx)
            if stake_limit <= 0:
                return None

            stake = min(2.0, stake_limit)

            # --------------------------------------------------------------
            # Compute ticks required to place hedge at target ODDS = 50.0
            # --------------------------------------------------------------
            try:
                from engines.price_math import ticks_between
                hedge_target_odds = 50.0
                ticks_to_hedge = ticks_between(float(current), float(hedge_target_odds))
                ticks_to_hedge = max(1, int(ticks_to_hedge))
            except Exception:
                ticks_to_hedge = 50   # safe fallback

            # --------------------------------------------------------------
            # Emit MSC_IP *PARENT* plan routed through lanes/run_all
            # --------------------------------------------------------------
            plan = {
                "enter": True,
                "role": "PARENT",
                "family": "MSC_IP",
                "source": "V",                     # ← HARD-CODED FOR MSC-INPLAY
                "engine": "MSC_INPLAY",            # ← DB bucket
                "subtype": "LAYDOWN",
                "direction": "LAY",
                "target_ticks": ticks_to_hedge,
                "size": stake,
                "px": current,
                "why": "inplay_collapse_to_50",
            }

            self.active_plan = plan
            self.entry_px = current
            self.state = InPlaySubState.MONITOR
            return plan

# === PATCH END ==============================================================

