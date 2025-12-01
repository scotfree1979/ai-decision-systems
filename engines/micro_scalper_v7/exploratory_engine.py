# /engines/micro_scalper_v7/exploratory_engine.py

from typing import Dict, Any, Optional
from .utils import (
    classify_direction_from_legacy,
    same_direction_microtrade,
)
from .state_machine import ExploratorySubState
from .intel_adapter import build_micro_state


class ExploratoryEngine:
    """
    PRE-OFF Exploratory MicroScalper Engine (Engine A)
    -------------------------------------------------
    This engine scans PRE-OFF markets for micro-opportunities
    using v7 intelligence, blueprints, playbooks, and trend/bias
    signals.

    It always trades in the *same* direction as the Legacy thesis:
        - DRIFT (LAY→BACK)  → price expected UP
        - STEAM (BACK→LAY)  → price expected DOWN

    Produces micro-scalp plans using MasteryBridge-compatible dicts:
        {
            "enter": True,
            "role": "CHILD",
            "family": "MSC",
            "subtype": "SS"/"BB"/"XX"/"RR"/"FF"/"LL",
            "direction": "LAY" or "BACK",
            "target_ticks": 1–3,
            "size": float(dynamic stake),
            "px": float(entry odds),
            "why": string(reason),
        }
    """

    def __init__(self):
        self.state = ExploratorySubState.IDLE
        self.last_px = None  # track micro price action
        self.active_plan = None

    # -----------------------------------------------------------
    # PUBLIC API
    # -----------------------------------------------------------
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Called every PRE-OFF tick by MicroScalperEngine.

        ctx must contain:
            - oc_phase (0–7 PRE-OFF, >=7 IN-PLAY)
            - odds data
            - v7 intel fields
            - legacy direction hint (ctx["legacy_expected_direction"])
            - dynamic stake function: ctx["dynamic_stake_fn"]
        """
        # PRE-OFF only
        if ctx.get("oc_phase", 100) >= 7:
            self.state = ExploratorySubState.IDLE
            return None

        micro_state = build_micro_state(ctx)
        from .direction_engine import compute_msc_decision

        # Compute full MSC direction + mode
        msc_decision   = compute_msc_decision(ctx)
        direction_label = msc_decision["direction"]       # "LAY->BACK" or "BACK->LAY"
        mode            = msc_decision["mode"]
        ticks_override  = msc_decision["entry_ticks"]
        stop_ticks      = msc_decision["stop_ticks"]

        # Convert LAY->BACK into actual order SIDE
        order_side = "LAY" if direction_label == "LAY->BACK" else "BACK"

        # Override target ticks in ctx (used by MSC plan builders)
        ctx["msc_direction"]  = direction_label
        ctx["msc_mode"]       = mode
        ctx["msc_entry_ticks"] = ticks_override
        ctx["msc_stop_ticks"]  = stop_ticks
        # === PATCH END ==============================================================

        # === PATCH START ==========================================
        # Add parent id to MSC parent plan so child engine can attach
        if plan and plan.get("enter") and plan.get("role") == "CHILD":
            parent_id = ctx.get("legacy_parent_id")
            if parent_id:
                plan["hedge_of"] = parent_id
        # === PATCH END ============================================



    # -----------------------------------------------------------
    # INTERNAL LOGIC
    # -----------------------------------------------------------
    def _evaluate(self, ctx: Dict[str, Any], micro_state: Dict[str, Any], order_side: str):
        """
        Determine whether a micro scalp is appropriate.
        Must meet v7_intelligence + blueprint + playbook criteria.
        """

        # basic opportunity check
        if not micro_state.get("opportunity"):
            return None

        conf = self._compute_confidence(micro_state)
        if conf < 0.55:
            return None

        ticks = self._compute_target_ticks(micro_state)
        size = ctx["dynamic_stake_fn"](
            family="MSC",
            confidence=conf,
            vol_state=micro_state.get("volatility_state"),
            expected_ticks=ticks,
        )

        plan = {
            "enter": True,
            "role": "PARENT",
            "family": "MSC",
            "subtype": self._classify_subtype(micro_state),
            "direction": order_side,
            "target_ticks": ticks,
            "size": size,
            "px": ctx.get("current_price"),
            "why": micro_state.get("momentum_class") or "micro_opportunity",
        }

        self.state = ExploratorySubState.MONITOR
        self.active_plan = plan
        self.last_px = ctx.get("current_price")
        return plan

    def _monitor(self, ctx: Dict[str, Any], micro_state: Dict[str, Any]):
        """
        Monitor whether the ongoing micro scalp should exit early or 
        continue holding. For simplicity, exits are routed to LiveRouter 
        when micro target or reversal signals occur.
        """
        current_px = ctx.get("current_price")

        # reversal or collapse
        if micro_state.get("momentum_class") == "REVERSAL":
            self.state = ExploratorySubState.EXIT
            return self._exit_plan()

        # profit achieved (simple check)
        if current_px and self.last_px:
            diff = abs((current_px - self.last_px) * 100)
            if diff >= (self.active_plan["target_ticks"] * 1.0):
                self.state = ExploratorySubState.EXIT
                return self._exit_plan()

        return None

    def _exit_plan(self):
        """
        Build a minimal exit signal. LiveRouter will handle closing.
        """
        if not self.active_plan:
            return None

        plan = {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "family": "MSC",
            "subtype": self.active_plan.get("subtype"),
            "why": "micro_exit"
        }

        self.state = ExploratorySubState.IDLE
        self.active_plan = None
        return plan

    # -----------------------------------------------------------
    # SUPPORT FUNCTIONS
    # -----------------------------------------------------------
    def _compute_confidence(self, m: Dict[str, Any]) -> float:
        """
        Fuse blueprint, playbook, and bias confidence.
        This will be improved once training loop attaches fully.
        """
        base = 0.5
        if m.get("blueprint_conf"):
            base += 0.1 * m["blueprint_conf"]
        if m.get("playbook_win_rate"):
            base += 0.1 * m["playbook_win_rate"]
        if m.get("bias_conf"):
            base += 0.05 * m["bias_conf"]
        return min(max(base, 0.0), 1.0)

    def _compute_target_ticks(self, m: Dict[str, Any]) -> int:
        # === PATCH START ============================================================
        # 📍 TARGET: exploratory_engine._compute_target_ticks
        # 📆 PATCHED: 2025-12-01 — obey direction_engine entry ticks
        override = m.get("msc_entry_ticks")
        if override is not None:
            try:
                return int(override)
            except Exception:
                pass
        # === PATCH END ==============================================================

        """
        PRE-OFF micro scalps are always 1–3 ticks.
        """
        vol = m.get("volatility_state", "LOW")
        if vol == "HIGH":
            return 3
        if vol == "MED":
            return 2
        return 1

    def _classify_subtype(self, m: Dict[str, Any]) -> str:
        """
        Pick a micro strategy subtype (SS/BB/XX/FF/RR/LL)
        based on pattern + momentum.
        """
        mc = m.get("momentum_class")
        if mc == "STEAM": return "BB"
        if mc == "DRIFT": return "SS"
        if mc == "BREAKOUT": return "RR"
        if mc == "CROSSOVER": return "XX"
        if mc == "FADE": return "FF"
        return "LL"
