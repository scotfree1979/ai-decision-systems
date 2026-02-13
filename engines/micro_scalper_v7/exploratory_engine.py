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

# ======================================================================
# 📍 TARGET: exploratory_engine.py
# 🧩 ACTION: add ranking buffer
# ======================================================================

    def __init__(self):
        self._rank_buffer = []     # (score, ctx_snapshot)
        self._max_per_tick = 15    # configurable cap

# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/exploratory_engine.py
# 🧩 ACTION: ADD _score_runner
# 📆 PATCHED: 2026-04-01 — Runner ranking intelligence
# ======================================================================

    def _score_runner(self, ctx: Dict[str, Any], msc: Dict[str, Any]) -> float:
        score = 0.0

        trend = ctx.get("runner_trend") or {}

        # Direction alignment with market truth
        if trend.get("direction") == msc.get("direction"):
            score += 3.0

        # Tick movement magnitude
        score += min(5.0, float(trend.get("ticks_moved", 0.0)))

        # Favourite weighting
        if ctx.get("fav_rank") == 1:
            score += 1.0

        # Pre-off sweet zone
        mto = float(ctx.get("mto_minutes") or 999)
        if 3 <= mto <= 20:
            score += 1.0

        return score

# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/exploratory_engine.py
# 🔎 SEARCH: def flush_ranked(self)
# 🧩 ACTION: return plan + ctx
# 📆 PATCHED: 2026-04-01
# ==============================================================================

    def flush_ranked(self) -> list:
        if not self._rank_buffer:
            return []

        ranked = sorted(
            self._rank_buffer,
            key=lambda x: x[0],
            reverse=True
        )

        selected = ranked[:self._max_per_tick]

        output = []

        for score, ctx in selected:

            size = compute_dynamic_stake(
                ctx=ctx,
                engine="MSC_EXPLORATORY"
            )

            plan = {
                "enter": True,
                "engine": "MSC_EXPLORATORY",
                "role": "PARENT",
                "direction": ctx.get("msc_direction"),
                "target_ticks": 1,
                "stop_ticks": 4,
                "px": ctx.get("px"),
                "size": float(size),
                "why": "exploratory_ranked",
            }

            output.append((plan, ctx))

        self._rank_buffer.clear()
        return output

# === PATCH END ==============================================================

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

# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/exploratory_engine.py
# 🔎 SEARCH: def tick(self, ctx:
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-04-01 — Convert to ranked collector
#
# CONTRACT:
# - No direct emission
# - Collect candidate + score
# - flush_ranked() emits top-N
# ======================================================================

    def tick(self, ctx: Dict[str, Any]) -> None:
        """
        Collect exploratory candidate.
        Emission handled via flush_ranked().
        """

# ============================================================================
# 📍 TARGET: <engine_file_here>
# 🔎 SEARCH: def tick(self, ctx):
# 🧩 ACTION: INSERT — Band Guard (IGNORED filter)
# 📆 PATCHED: 2026-04-14 — Enforce IGNORED runner exclusion
#
# PURPOSE:
# - Engines must never process IGNORED band runners
# - BusRoute supplies full-day surface
# - Engine owns eligibility decision
#
# INVARIANT:
# - If ctx["band"] == "IGNORED", engine returns None
# - No execution logic runs for ignored runners
# ============================================================================

        from engines.bus_route import DAY_RUNNER_SURFACE

        mid = str(ctx.get("marketId"))
        sid = str(ctx.get("selectionId"))

        runner = DAY_RUNNER_SURFACE.get_runner(mid, sid)

        if runner:
            if ctx.get("px") is None:
                ctx["px"] = runner["px"]
            ctx["band"] = runner["band"]

        px = float(ctx.get("px") or 0.0)
        if px <= 0:
            return None

        if ctx.get("band") == "IGNORED":
            return None


        try:
            from .direction_engine import compute_msc_decision
            msc = compute_msc_decision(ctx)

            ctx_snapshot = dict(ctx)
            ctx_snapshot["msc_direction"] = msc["direction"]
            ctx_snapshot["msc_entry_ticks"] = msc["entry_ticks"]
            ctx_snapshot["msc_stop_ticks"] = msc["stop_ticks"]

            score = self._score_runner(ctx_snapshot, msc)

            self._rank_buffer.append((score, ctx_snapshot))

            return None

        except Exception as e:
            try:
                emit("msc_exploratory.exception", {
                    "error": str(e),
                    "marketId": ctx.get("marketId"),
                    "selectionId": ctx.get("selectionId"),
                })
            except Exception:
                pass

            return None

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

