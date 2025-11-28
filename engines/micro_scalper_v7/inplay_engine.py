# /engines/micro_scalper_v7/inplay_engine.py

from typing import Dict, Any, Optional
from .state_machine import InPlaySubState
from .intel_adapter import build_micro_state


class InPlayEngine:
    """
    IN-PLAY Intelligent Laying Engine (Engine C)
    -------------------------------------------
    Activated once OC >= 7. Uses V7 in-play intelligence
    to identify horses whose winning chances are collapsing.

    Logic:
      • Only LAYS collapsing runners (never back-to-lay in-play)
      • Uses drift_speed, momentum_class, inplay_progress,
        volatility, exhaustion, and form book to confirm weakness.
      • Small, safe, dynamic micro-stakes (1–2 ticks profit targets)
      • Learns through feedback_assimilator
    """

    def __init__(self):
        self.state = InPlaySubState.IDLE
        self.active_plan = None
        self.last_px = None

    # -----------------------------------------------------------
    # PUBLIC API
    # -----------------------------------------------------------
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Called on every IN-PLAY tick by MicroScalperEngine.

        ctx must contain:
            - oc_phase >= 7
            - current_price
            - dynamic_stake_fn
            - v7-intel fields (drift_speed, inplay_progress, etc.)
        """

        if ctx.get("oc_phase", 0) < 7:
            # move back to idle if race not yet in-play
            self.state = InPlaySubState.IDLE
            return None

        micro_state = build_micro_state(ctx)

        if self.state == InPlaySubState.IDLE:
            return self._analyse(ctx, micro_state)

        if self.state == InPlaySubState.ANALYSE:
            return self._evaluate_lay(ctx, micro_state)

        if self.state == InPlaySubState.MONITOR:
            return self._monitor(ctx, micro_state)

        return None

    # -----------------------------------------------------------
    # INTERNAL LOGIC
    # -----------------------------------------------------------
    def _analyse(self, ctx: Dict[str, Any], m: Dict[str, Any]):
        """
        Decide whether this runner is showing collapse signals.
        """
        collapse = self._collapse_signal(m)
        if not collapse:
            return None

        self.state = InPlaySubState.ANALYSE
        return None

    def _evaluate_lay(self, ctx: Dict[str, Any], m: Dict[str, Any]):
        """
        If enough collapse evidence exists, open a micro lay.
        """
        if not self._collapse_signal(m):
            self.state = InPlaySubState.IDLE
            return None

        # dynamic confidence
        conf = self._compute_confidence(m)
        if conf < 0.60:
            return None

        ticks = self._compute_target_ticks(m)
        size = ctx["dynamic_stake_fn"](
            family="MSC_IP",
            confidence=conf,
            vol_state=m.get("volatility_state"),
            expected_ticks=ticks,
        )

        plan = {
            "enter": True,
            "role": "CHILD",
            "family": "MSC_IP",
            "subtype": "LAYDOWN",
            "direction": "LAY",  # always lay collapsing horses
            "target_ticks": ticks,
            "size": size,
            "px": ctx.get("current_price"),
            "why": "inplay_collapse",
        }

        self.active_plan = plan
        self.last_px = ctx.get("current_price")
        self.state = InPlaySubState.MONITOR
        return plan

    def _monitor(self, ctx: Dict[str, Any], m: Dict[str, Any]):
        """
        Monitor momentum. If collapse slows or reverses → exit.
        If profit target reached → exit.
        """
        current_px = ctx.get("current_price")

        if current_px is None or self.active_plan is None:
            return None

        # reversal: steam event or in-play momentum flips
        if m.get("momentum_class") == "STEAM":
            return self._exit_plan("inplay_reversal")

        # profit hit
        diff = abs(int((current_px - self.active_plan["px"]) * 100))
        if diff >= self.active_plan["target_ticks"]:
            return self._exit_plan("inplay_profit")

        return None

    # -----------------------------------------------------------
    # EXIT
    # -----------------------------------------------------------
    def _exit_plan(self, why: str) -> Dict[str, Any]:
        """
        Build an exit plan to close the in-play micro scalp.
        """
        plan = {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "family": "MSC_IP",
            "subtype": "LAYDOWN",
            "why": why,
        }

        self.state = InPlaySubState.IDLE
        self.active_plan = None
        return plan

    # -----------------------------------------------------------
    # SUPPORT FUNCTIONS
    # -----------------------------------------------------------
    def _collapse_signal(self, m: Dict[str, Any]) -> bool:
        """
        Determines whether a runner is collapsing in-play.
        Must use multiple conditions:
          - drift_speed positive + strong
          - inplay_progress between ~0.2–0.9 (mid race)
          - form_class indicates weakness
          - momentum_class indicates collapse
          - blueprint + playbook confirm losing conditions
        """
        if m.get("drift_speed") and m["drift_speed"] > 0.3:
            if m.get("momentum_class") in ("DRIFT", "COLLAPSE"):
                if 0.1 < (m.get("inplay_progress") or 0) < 0.95:
                    return True
        return False

    def _compute_confidence(self, m: Dict[str, Any]) -> float:
        """
        Confidence for in-play micro-lay based on v7 intelligence.
        """
        base = 0.5
        if m.get("drift_speed"):
            base += min(m["drift_speed"], 1.0) * 0.2
        if m.get("bias_conf"):
            base += m["bias_conf"] * 0.1
        if m.get("form_class") == "OUTSIDER":
            base += 0.1
        return min(base, 1.0)

    def _compute_target_ticks(self, m: Dict[str, Any]) -> int:
        """
        IN-PLAY micro scalps are 1–2 ticks max.
        """
        if m.get("volatility_state") == "HIGH":
            return 2
        return 1
