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
        if win_prob > 0.40:  # high win_prob → do NOT lay
            return None

        # Must have enough guaranteed profit to keep this runner ≥ 0 after lay
        stake_limit = self._max_allowed_stake(ctx)
        if stake_limit <= 0:
            return None

        stake = min(2.0, stake_limit)   # floor implementation: small IP stakes

        plan = {
            "enter": True,
            "role": "CHILD",
            "family": "MSC_IP",
            "subtype": "LAYDOWN",
            "direction": "LAY",
            "target_ticks": 1,             # in-play: always 1 tick
            "size": stake,
            "px": current,
            "why": "inplay_collapse",
        }

        self.active_plan = plan
        self.entry_px = current
        self.state = InPlaySubState.MONITOR
        return plan

    # ----------------------------------------------------------------------
    # INTERNAL LOGIC — MONITOR
    # ----------------------------------------------------------------------
    def _monitor(self, ctx: Dict[str, Any], m: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Monitors trend → returns exit plan when:
            • collapse reverses
            • profit achieved
            • boundaries hit (classified as T)
        """

        current = ctx.get("current_price")
        if current is None or self.entry_px is None or self.active_plan is None:
            return None

        # ---- 1) HARD BOUNDARIES ----
        if current <= self.HARD_LOWER_BOUND or current >= self.HARD_UPPER_BOUND:
            return self._exit("boundary_exit", tag="T")

        # ---- 2) COLLAPSE REVERSAL ----
        momentum = m.get("momentum_class")
        if momentum in ("STEAM", "REVERSAL", "RECOVERY"):
            return self._exit("collapse_reversal", tag="S")

        # ---- 3) PROFIT HIT ----
        ticks = int(round((self.entry_px - current) * 100))
        if ticks >= self.active_plan["target_ticks"]:
            return self._exit("inplay_profit", tag="H")

        return None

    # ----------------------------------------------------------------------
    # EXIT BUILDER
    # ----------------------------------------------------------------------
    def _exit(self, reason: str, tag: str) -> Dict[str, Any]:
        """
        tag:
            H = hedge/profit
            S = stop-loss-style reversal
            T = trailing/boundary exit
        """
        plan = {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "family": "MSC_IP",
            "subtype": "LAYDOWN",
            "why": reason,
            "exit_tag": tag,
        }
        self.state = InPlaySubState.IDLE
        self.active_plan = None
        self.entry_px = None
        return plan

    # ----------------------------------------------------------------------
    # SUPPORT: Load canonical PnL for stake limit
    # ----------------------------------------------------------------------
    def _load_runner_pnl(self, ctx: Dict[str, Any]):
        """
        Fetches canonical cash-out PnL for this runner to determine
        the maximum safe in-play lay stake that keeps PnL ≥ 0.
        """

        mid = str(ctx.get("marketId"))
        sid = str(ctx.get("selectionId"))

        try:
            # one market-wide call cached per tick
            if not self.parent_pnl_cache:
                # NOTE: ctx includes active DB connection in orchestrator
                results = cashout_calc(ctx["db_conn"])  # safe: conn is passed from orchestrator
                self.parent_pnl_cache = results or {}
        except Exception:
            return

        if mid not in self.parent_pnl_cache:
            return

        runner_info = self.parent_pnl_cache[mid]["runner_pnls"]
        if sid in runner_info:
            ctx["canonical_pnl_runner"] = float(runner_info[sid])
        else:
            ctx["canonical_pnl_runner"] = 0.0

    # ----------------------------------------------------------------------
    # SUPPORT: Determine max allowed in-play stake
    # ----------------------------------------------------------------------
    def _max_allowed_stake(self, ctx: Dict[str, Any]) -> float:
        """
        stake ≤ canonical positive runner PnL / (odds-1)
        Ensures laying cannot push runner negative.
        """

        pnl = float(ctx.get("canonical_pnl_runner") or 0.0)
        current = float(ctx.get("current_price") or 0.0)

        if pnl <= 0 or current <= 1.0:
            return 0.0

        # Liability = stake * (odds - 1)
        # → stake ≤ pnl / (odds - 1)
        liab_factor = max(current - 1.0, 0.01)
        return pnl / liab_factor

# === PATCH END ===
