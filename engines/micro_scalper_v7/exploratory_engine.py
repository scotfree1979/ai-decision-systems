# /engines/micro_scalper_v7/exploratory_engine.py


from .intel_adapter import build_micro_state

# /engines/micro_scalper_v7/exploratory_engine.py

from typing import Dict, Any, Optional
from .intel_adapter import build_micro_state
from engines.micro_scalper_v7.event_receiver import get_engine_outcomes
from engines.mastery.event_sink import emit



class ExploratoryEngine:
    """
    MSC Exploratory Engine (Engine A)
    ---------------------------------
    Emits **only** MSC PARENT plans.
    All lifecycle (match/hedge/stoploss) is handled by:
        • lanes → placement → live_router
        • MSC RiskEngine (engine B)
        • router rehedge/match loops
    """

    def __init__(self):
        # No internal state in v7
        pass

# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalper_v7/exploratory_engine.py
# 🔎 SEARCH: class ExploratoryEngine(
# 🆕 ADD: direction_only(ctx)
# 📆 PATCHED: 2026-02-12
# ============================================================================

    def direction_only(self, ctx: dict):
        """
        Lightweight direction probe for Legacy (Bus usage).
        Does NOT emit a plan, does NOT place trades.
        Returns "LAY->BACK" | "BACK->LAY" | None.
        """
        try:
            # Reuse MSC microstructure direction inference
            from .direction_engine import compute_msc_decision
            dec = compute_msc_decision(ctx)
            return dec.get("direction")
        except Exception:
            return None

# === PATCH END ================================================================


    # -----------------------------------------------------------
    # PUBLIC API
    # -----------------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/exploratory_engine.py
# 🔎 SEARCH: def tick(self, ctx:
# 📆 PATCHED: 2025-12-12 — eliminate silent None, emit NO-SIGNAL + training event
# PURPOSE:
#   • Preserve existing MSC logic
#   • Replace silent None with explicit NO-SIGNAL contract
#   • Enable BUS observability + future training
# ======================================================================================================

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        PRE-OFF only. Returns an MSC PARENT plan or a NO-SIGNAL envelope.
        """

        # --------------------------------------------------
        # HARD GATE: in-play
        # --------------------------------------------------
        if ctx.get("oc_phase", 100) >= 7:
            return self._no_signal(ctx, reason="not_preoff")

        # --------------------------------------------------
        # Build micro state (existing logic)
        # --------------------------------------------------
        micro_state = build_micro_state(ctx)

        # --------------------------------------------------
        # Direction model (existing logic)
        # --------------------------------------------------
        from .direction_engine import compute_msc_decision
        msc_decision = compute_msc_decision(ctx)

        if not msc_decision:
            return self._no_signal(ctx, reason="no_direction")

        # --------------------------------------------------
        # Existing conversion logic
        # --------------------------------------------------
        direction_label = msc_decision["direction"]
        order_side = "LAY" if direction_label == "LAY->BACK" else "BACK"

        # --------------------------------------------------
        # Enrich ctx (unchanged)
        # --------------------------------------------------
        ctx["msc_direction"]    = direction_label
        ctx["msc_mode"]         = msc_decision["mode"]
        ctx["msc_entry_ticks"]  = msc_decision["entry_ticks"]
        ctx["msc_stop_ticks"]   = msc_decision["stop_ticks"]
        ctx["msc_decision"]     = msc_decision

        # --------------------------------------------------
        # Existing confidence / filters remain untouched
        # (any early exit now becomes NO-SIGNAL)
        # --------------------------------------------------
        try:
            plan = {
                "enter": True,
                "engine": "MSC_EXPLORATORY",
                "side": order_side,
                "entry_ticks": msc_decision["entry_ticks"],
                "stop_ticks": msc_decision["stop_ticks"],
                "px": ctx.get("px"),
            }
            return plan

        except Exception:
            return self._no_signal(ctx, reason="exception")

    # --------------------------------------------------
    # INTERNAL: NO-SIGNAL helper
    # --------------------------------------------------
    def _no_signal(self, ctx: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
        from engines.mastery.event_sink import emit

        payload = {
            "enter": False,
            "blocked": True,
            "engine": "MSC_EXPLORATORY",
            "reason": reason,
            "re_eval": True,
            "features": {
                "px": ctx.get("px"),
                "band": ctx.get("band"),
                "minutes_to_off": ctx.get("minutes_to_off"),
            },
        }

        try:
            emit("msc.no_signal", payload)
        except Exception:
            pass

        return payload


        # -------------------------------------------------------
        # RUN EVALUATION
        # -------------------------------------------------------
        try:
            plan = self._evaluate(ctx, micro_state, order_side)
        except Exception as e:
            print(f"[MSC-EXP] evaluate error mid={ctx.get('marketId')} sid={ctx.get('selectionId')}: {e}")
            return None

        return plan

    # -----------------------------------------------------------
    # INTERNAL LOGIC
    # -----------------------------------------------------------
    def _evaluate(self, ctx: Dict[str, Any], micro_state: Dict[str, Any], order_side: str):
        """
        Determines if a PRE-OFF micro scalp should be emitted.
        Returns an MSC PARENT plan dict or None.
        """

        # Basic opportunity
        if not micro_state.get("opportunity"):
            return None

        conf = self._compute_confidence(micro_state)
        if conf < 0.55:
            return None

        # Ticks based on MSC direction-engine
        ticks = self._compute_target_ticks(micro_state)

        # Dynamic stake (tool-provided)
        size = ctx["dynamic_stake_fn"](
            family="MSC",
            confidence=conf,
            vol_state=micro_state.get("volatility_state"),
            expected_ticks=ticks,
        )

        # === PATCH START: add baseline 3-tick stop-loss ======================
        from engines.price_math import walk_ticks

        baseline_ticks = 3

        sl_dir = "up" if order_side == "LAY" else "down"
        stop_loss_px = walk_ticks(float(ctx.get("px")), baseline_ticks, sl_dir)

        return {
            "enter": True,
            "role": "PARENT",
            "family": "MSC",
            "source": "D",                     # ← HARD-CODED MSC EXPLORATORY
            "engine": "MSC_EXPLORATORY",       # ← DB bucket
            "subtype": self._classify_subtype(micro_state),
            "direction": order_side,
            "target_ticks": ticks,
            "size": size,
            "px": ctx.get("px"),
            "why": micro_state.get("momentum_class") or "micro_opportunity",

            # NEW STOP-LOSS FIELDS
            "stop_loss_ticks": baseline_ticks,
            "stop_loss_px": float(stop_loss_px),
        }
        # === PATCH END =======================================================


    # -----------------------------------------------------------
    # SUPPORT FUNCTIONS
    # -----------------------------------------------------------
    def _compute_confidence(self, m: Dict[str, Any]) -> float:
        base = 0.5
        if m.get("blueprint_conf"): base += 0.1 * m["blueprint_conf"]
        if m.get("playbook_win_rate"): base += 0.1 * m["playbook_win_rate"]
        if m.get("bias_conf"): base += 0.05 * m["bias_conf"]
        return min(max(base, 0.0), 1.0)

    def _compute_target_ticks(self, m: Dict[str, Any]) -> int:
        override = m.get("msc_entry_ticks")
        if override is not None:
            try: return int(override)
            except: pass

        vol = m.get("volatility_state", "LOW")
        if vol == "HIGH": return 3
        if vol == "MED":  return 2
        return 1

    def _classify_subtype(self, m: Dict[str, Any]) -> str:
        mc = m.get("momentum_class")
        if mc == "STEAM": return "BB"
        if mc == "DRIFT": return "SS"
        if mc == "BREAKOUT": return "RR"
        if mc == "CROSSOVER": return "XX"
        if mc == "FADE": return "FF"
        return "LL"

