# /engines/micro_scalper_v7/risk_engine.py

from typing import Dict, Any, Optional
from .utils import (
    classify_direction_from_legacy,
    same_direction_microtrade,
    opposite_exit_direction,
    tick_diff,
)
from .state_machine import RiskSubState
from .intel_adapter import build_micro_state


class RiskEngine:
    """
    Risk-Reactive MicroScalper (Engine B)
    ------------------------------------
    This engine attaches to a specific Legacy parent order and
    scalps tick-by-tick price movement to minimise drawdown.

    BEHAVIOUR (PRE-OFF ONLY):
      • When price moves IN the Legacy direction → scalp WITH Legacy.
      • When price pulls back → scalp along the CSL EXIT direction.
      • Repeats this oscillation endlessly for ticks up/down.
      • Ends immediately when StopLossEngine triggers for that parent.
      • Cancels ONLY children for that specific parent.

    STATES:
      IDLE → ATTACHED → (WITH_PARENT ↔ AGAINST_PARENT) → APPROACH_CSL → STOP_CLEANUP → DETACHED
    """

    def __init__(self, parent_id: int):
        self.parent_id = parent_id          # legacy parent identifier
        self.state = RiskSubState.IDLE
        self.entry_side = None              # "LAY" or "BACK"
        self.expected_direction = None      # DRIFT or STEAM
        self.exit_side = None               # opposite action required to close parent
        self.last_price = None
        self.active_child = None            # currently open micro child position

        # --- NEW: trailing stop-loss state (MSC only) ---
        self.best_favourable_px = None      # highest (LAY) / lowest (BACK) px reached while active child open
        self.trailing_ticks = 10            # default baseline; expanded by SLEQ multiplier

    # -----------------------------------------------------------
    # INTERNAL: update trailing stop state each tick
    # -----------------------------------------------------------
    def _update_trailing_state(self, current_px: float, sleq_mult: float):
        """
        Maintains trailing stop state for MSC risk-engine.

        Rules:
          • For LAY parent → favourable = price drifting UP.
          • For BACK parent → favourable = price steaming DOWN.
          • trailing_threshold = trailing_ticks * sleq_mult
          • best_favourable_px only moves when new extremes reached.
        """
        if current_px is None:
            return

        side = (self.entry_side or "").upper()
        mult = float(max(1.0, sleq_mult))  # ensure >=1
        tdist = int(max(1, round(self.trailing_ticks * mult)))

        # initialise anchor for trailing
        if self.best_favourable_px is None:
            self.best_favourable_px = current_px
            return

        # update best favourable price
        if side == "LAY":       # price UP = favourable
            if current_px > self.best_favourable_px:
                self.best_favourable_px = current_px
        else:                   # BACK → price DOWN = favourable
            if current_px < self.best_favourable_px:
                self.best_favourable_px = current_px

        # compute distance from favourable
        from .utils import tick_diff
        ticks_away = abs(tick_diff(current_px, self.best_favourable_px))

        return ticks_away, tdist

    # -----------------------------------------------------------
    # INTERNAL: trailing stop trigger check
    # -----------------------------------------------------------
    def _check_trailing_stop(self, current_px: float, sleq_mult: float):
        """
        Returns:
           None         → no stop triggered
           dict(plan)   → trailing stop close plan
        """
        out = self._update_trailing_state(current_px, sleq_mult)
        if out is None:
            return None

        ticks_away, threshold = out
        if ticks_away < threshold:
            return None

        # classify good stop vs bad stop
        entry_px_child = None
        if self.active_child:
            entry_px_child = self.active_child.get("px")

        good_stop = False
        if entry_px_child is not None and self.best_favourable_px is not None:
            # if the two are NOT equal → market moved in favour first
            good_stop = (self.best_favourable_px != entry_px_child)

        reason = "msc_sl_good" if good_stop else "msc_sl_bad"

        # unified close plan
        plan = {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "family": "MSC_RISK",
            "subtype": "RISK",
            "parent_id": self.parent_id,
            "why": reason,
        }

        # reset trailing state
        self.best_favourable_px = None
        self.active_child = None

        return plan

# === PATCH END ================================================================

    # -----------------------------------------------------------
    # PUBLIC API
    # -----------------------------------------------------------
    def attach(self, ctx: Dict[str, Any]):
        """
        Attach RiskEngine to a Legacy parent.
        """
        self.state = RiskSubState.ATTACHED
        self.entry_side = ctx.get("legacy_entry_side")
        self.expected_direction = classify_direction_from_legacy(self.entry_side)
        self.exit_side = opposite_exit_direction(self.entry_side)
        self.last_price = ctx.get("current_price")

    def detach(self):
        """
        Clean detachment after StopLoss cleanup or race end.
        """
        self.state = RiskSubState.DETACHED
        self.active_child = None

    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Called every PRE-OFF tick by MicroScalperEngine.
        """
        if self.state in (RiskSubState.IDLE, RiskSubState.DETACHED):
            return None

        # IN-PLAY → RiskEngine disabled
        if ctx.get("oc_phase", 100) >= 7:
            return None

        if ctx.get("stoploss_triggered_for_parent") == self.parent_id:
            return self._stop_cleanup()

        micro_state = build_micro_state(ctx)

        if self.state == RiskSubState.ATTACHED:
            return self._decide_with_or_against(ctx)

        if self.state == RiskSubState.WITH_PARENT:
            return self._with_parent_logic(ctx, micro_state)

        if self.state == RiskSubState.AGAINST_PARENT:
            return self._against_parent_logic(ctx, micro_state)

        return None

    # -----------------------------------------------------------
    # INTERNAL LOGIC
    # -----------------------------------------------------------
    def _decide_with_or_against(self, ctx: Dict[str, Any]):
        """
        First decision after attaching: are we drifting/steaming 
        with Legacy or retracing?
        """
        current_px = ctx.get("current_price")

        if current_px is None or self.last_price is None:
            return None

        diff = tick_diff(current_px, self.last_price)

        # DRIFT: price_up = good, price_down = bad
        # STEAM: price_down = good, price_up = bad
        if self._is_with_parent(diff):
            self.state = RiskSubState.WITH_PARENT
            return self._enter_with_parent(ctx)
        else:
            self.state = RiskSubState.AGAINST_PARENT
            return self._enter_against_parent(ctx)

    def _with_parent_logic(self, ctx: Dict[str, Any], m: Dict[str, Any]):
        """
        Price is moving correctly for Legacy. We scalp WITH the thesis.
        If momentum collapses or flips, switch to AGAINST_PARENT mode.
        """
        current_px = ctx.get("current_price")

        if current_px is None:
            return None

        diff = tick_diff(current_px, self.last_price)

        # If momentum reverses → flip
        if not self._is_with_parent(diff):
            self.state = RiskSubState.AGAINST_PARENT
            return self._enter_against_parent(ctx)

# === PATCH START ===============================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: if self.active_child and self._child_profit_hit(current_px):
# ⛏️ ACTION: insert trailing-stop check BEFORE profit exit
# 📆 PATCHED: 2025-11-28
# ===============================================================================

        # --- NEW: MSC trailing stop-loss check ---
        sleq_mult = float(ctx.get("sleq_multiplier", 1.0))
        tsl_plan = self._check_trailing_stop(current_px, sleq_mult)
        if tsl_plan:
            return tsl_plan

# === PATCH END ================================================================


        # Profit threshold hit → exit
        if self.active_child and self._child_profit_hit(current_px):
            return self._close_child("with_parent_profit")

        return None

    def _against_parent_logic(self, ctx: Dict[str, Any], m: Dict[str, Any]):
        """
        Price is moving counter to Legacy, so we scalp ALONG the CSL exit direction.
        If price resumes Legacy direction, flip back to WITH_PARENT.
        """
        current_px = ctx.get("current_price")

        if current_px is None:
            return None

        diff = tick_diff(current_px, self.last_price)

        # If trending back into Legacy direction → flip
        if self._is_with_parent(diff):
            self.state = RiskSubState.WITH_PARENT
            return self._enter_with_parent(ctx)

        # Profit threshold hit → exit
        if self.active_child and self._child_profit_hit(current_px):
            return self._close_child("against_parent_profit")

        return None

    # -----------------------------------------------------------
    # CHILD ORDER GENERATION
    # -----------------------------------------------------------
    def _enter_with_parent(self, ctx: Dict[str, Any]):
        """
        Open a micro scalp in the SAME direction as Legacy's thesis.
        """
        side = same_direction_microtrade(self.expected_direction)
        return self._create_child(ctx, side, why="ms_with_parent")

    def _enter_against_parent(self, ctx: Dict[str, Any]):
        """
        Open a micro scalp in the CSL EXIT direction during adverse movement.
        """
        side = self.exit_side
        return self._create_child(ctx, side, why="ms_against_parent")

    def _create_child(self, ctx, side, why):
        """
        Create a full micro child entry plan.
        """
        ticks = 1  # risk-engine always scalps 1 tick microsegments
        stake = ctx["dynamic_stake_fn"](
            family="MSC_RISK",
            confidence=0.5,
            vol_state=ctx.get("volatility_state"),
            expected_ticks=ticks,
        )

        plan = {
            "enter": True,
            "role": "CHILD",
            "family": "MSC_RISK",
            "subtype": "RISK",
            "direction": side,
            "target_ticks": ticks,
            "size": stake,
            "px": ctx.get("current_price"),
            "why": why,
            "parent_id": self.parent_id,
        }

# === PATCH START ===============================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: self.active_child = plan
# 📆 PATCHED: 2025-11-28 — reset trailing anchor
# ===============================================================================

        self.active_child = plan
        # NEW: reset trailing state on new micro-child
        self.best_favourable_px = plan.get("px")

# === PATCH END ================================================================

        self.last_price = ctx.get("current_price")
        return plan

    def _close_child(self, why: str):
        """
        Signal LiveRouter to close the active micro position.
        """
        if not self.active_child:
            return None

        plan = {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "family": "MSC_RISK",
            "subtype": "RISK",
            "why": why,
            "parent_id": self.parent_id,
        }

        self.active_child = None
        return plan

    # -----------------------------------------------------------
    # SUPPORT
    # -----------------------------------------------------------
    def _is_with_parent(self, diff: int) -> bool:
        """
        Determine whether tick movement is WITH Legacy.
        DRIFT expects +ticks
        STEAM expects -ticks
        """
        if self.expected_direction == "DRIFT":
            return diff > 0
        if self.expected_direction == "STEAM":
            return diff < 0
        return False

    def _child_profit_hit(self, current_px: float) -> bool:
        """
        Simple single-tick profit detection.
        """
        if not self.active_child:
            return False

        entry_px = self.active_child.get("px")
        if entry_px is None or current_px is None:
            return False

        diff = abs(tick_diff(current_px, entry_px))
        return diff >= self.active_child.get("target_ticks", 1)

    # -----------------------------------------------------------
    # STOPLOSS CLEANUP
    # -----------------------------------------------------------
    def _stop_cleanup(self):
        """
        StopLossEngine has fired for this parent.
        Cancel ONLY this parent's micro children.
        """
        self.state = RiskSubState.STOP_CLEANUP
        self.active_child = None
        self.best_favourable_px = None  # trailing-stop reset

        return {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "family": "MSC_RISK",
            "subtype": "RISK",
            "parent_id": self.parent_id,
            "why": "stoploss_cleanup"
        }

