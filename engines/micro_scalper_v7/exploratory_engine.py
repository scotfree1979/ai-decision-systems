# /engines/micro_scalper_v7/exploratory_engine.py


from .intel_adapter import build_micro_state

# /engines/micro_scalper_v7/exploratory_engine.py

from typing import Dict, Any, Optional
from .intel_adapter import build_micro_state
from engines.micro_scalper_v7.event_receiver import get_engine_outcomes
from engines.mastery.event_sink import emit

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/exploratory_engine.py
# 🔎 SEARCH: from engines.mastery.event_sink import emit
# 📆 PATCHED: 2026-03-08 — fix missing compute_dynamic_stake import (MSC Exploratory exception)
# PURPOSE:
#   • Prevent NameError during MSC sizing
#   • Allow valid Exploratory plans to be emitted
#   • Preserve existing behaviour and NO-SIGNAL contract
# ======================================================================================================

from engines.math.dynamic_stake_v7 import compute_dynamic_stake


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
#  ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/exploratory_engine.py
# 🔎 SEARCH: def tick(self, ctx:
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-10 — Exploratory fail-open scout engine
#
# RATIONALE:
# - Exploratory is a scout, not a signal engine
# - It must NEVER block on confidence, direction, or budget
# - Placement/BankState own rejection
# - Direction engine ALWAYS provides bias
#
# INVARIANTS:
# - Always emits a plan for runnable runners
# - Never emits NO-SIGNAL in normal flow
# - Uses dynamic stake with engine min/max clamp
# ======================================================================================================

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Exploratory scout tick.
        Always emits a plan for a runnable runner.
        """

        try:
            # --------------------------------------------------
            # Direction & mode inference (authoritative)
            # --------------------------------------------------
            from .direction_engine import compute_msc_decision
            msc = compute_msc_decision(ctx)

            # --------------------------------------------------
            # Enrich ctx (telemetry / downstream use)
            # --------------------------------------------------
            ctx["msc_decision"]    = msc
            ctx["msc_direction"]   = msc["direction"]
            ctx["msc_mode"]        = msc["mode"]
            ctx["msc_entry_ticks"] = msc["entry_ticks"]
            ctx["msc_stop_ticks"]  = msc["stop_ticks"]

            # --------------------------------------------------
            # Stake (engine-level min/max enforced downstream)
            # --------------------------------------------------
            size = compute_dynamic_stake(
                ctx=ctx,
                engine="MSC_EXPLORATORY",
            )

            # --------------------------------------------------
            # Emit parent plan (no blockers)
            # --------------------------------------------------
            return {
                "enter": True,
                "engine": "MSC_EXPLORATORY",
                "role": "PARENT",
                "direction": msc["direction"],
                "entry_ticks": msc["entry_ticks"],
                "target_ticks": msc["entry_ticks"],
                "stop_ticks": msc["stop_ticks"],
                "px": ctx.get("px"),
                "size": float(size),
                "why": "exploratory_scout",
            }

        except Exception as e:
            # Telemetry only — NEVER block
            try:
                emit("msc_exploratory.exception", {
                    "error": str(e),
                    "marketId": ctx.get("marketId"),
                    "selectionId": ctx.get("selectionId"),
                })
            except Exception:
                pass

            # Fail-open fallback: minimal scout probe
            return {
                "enter": True,
                "engine": "MSC_EXPLORATORY",
                "role": "PARENT",
                "direction": "LAY->BACK",
                "entry_ticks": 1,
                "target_ticks": 1,
                "stop_ticks": 4,
                "px": ctx.get("px"),
                "size": float(compute_dynamic_stake(ctx, "MSC_EXPLORATORY")),
                "why": "exploratory_fallback",
            }


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

