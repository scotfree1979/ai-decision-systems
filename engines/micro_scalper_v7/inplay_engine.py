# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: ^from typing import Dict, Any, Optional
# 🛠 ACTION: Replace entire file
# 📆 PATCHED: 2026-03-XX — First-Class MSC In-Play Engine (Arming → Trigger → Batch Emit)
# ==============================================================================

from typing import Dict, Any, List, Optional
import time

from engines.micro_scalper_v7.direction_engine import compute_msc_decision
from engines.mastery.event_sink import emit

from tools.betfair_runner_trend_surface import get_runner_trend


class InPlayEngine:
    """
    MSC_INPLAY — first-class in-play ladder engine.

    MODEL:
    - Arm runners continuously (pre-off + in-play)
    - Detect race start via volatility (NOT time)
    - Trigger batch ladder emission when conditions met
    - Emit ALL ladder parents at once
    - No sequencing, no sizing, no matching logic

    BUS + Router own execution.
    """

    # ----------------------------
    # Configuration
    # ----------------------------

    SWEETSPOT = 7.0

    LAY_LADDER  = [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    BACK_LADDER = [5.0, 4.0, 3.0]

    VOLATILITY_TICKS_TRIGGER = 0.8     # race start detection
    ARM_CONFIDENCE_MIN       = 0.15    # minimum drift confidence

    # ----------------------------
    # Lifecycle
    # ----------------------------

    def __init__(self):
        # per-runner memory
        self.last_px: Dict[tuple, float] = {}

        # per-runner arming state
        self.armed_lay:  Dict[tuple, bool] = {}
        self.armed_back: Dict[tuple, bool] = {}

        # per-runner trigger state (one-shot)
        self.triggered: Dict[tuple, bool] = {}

        # per-market in-play detection
        self.market_inplay: Dict[str, float] = {}

    # ----------------------------
    # Main entrypoint
    # ----------------------------

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:


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
            return self._no_signal("ignored_band")


        if ctx.get("band") == "IGNORED":
            return self._no_signal("ignored_band")



        if not mid or not sid or not px or px <= 0:
            return self._no_signal("missing_identity_or_px")

        key = (mid, sid)

        # --------------------------------------------------
        # Race status detection (Betfair authoritative)
        # --------------------------------------------------

        from engines.flags.betfair_flag_surface import get_race_status_cached

        race_status = get_race_status_cached(mid)

        # Hard stop: finished market
        if race_status == "FINISHED":
            return self._no_signal("race_finished")

        # Arm only after OFF
        in_play = (race_status == "OFF")

        if not in_play:
            return self._no_signal("waiting_for_off_flag")


        # --------------------------------------------------
        # Market-truth trend (authoritative)
        # --------------------------------------------------

        trend = get_runner_trend(mid, sid)

        direction    = trend.get("direction")
        ticks_moved  = trend.get("ticks_moved", 0)
        confidence   = trend.get("confidence", 0.0)
        to_price     = trend.get("to_price") or px

        if not direction or direction == "FLAT":
            return self._no_signal("flat_trend")

        # --------------------------------------------------
        # Arming logic (always on)
        # --------------------------------------------------

        if confidence >= self.ARM_CONFIDENCE_MIN:

            # DRIFT → potential loser → LAY ladder
            if direction == "LAY->BACK":
                self.armed_lay[key] = True

            # STEAM → exposure risk → BACK ladder
            elif direction == "BACK->LAY":
                self.armed_back[key] = True

        # --------------------------------------------------
        # Triggering (in-play only, one-shot)
        # --------------------------------------------------

        if not in_play:
            return self._no_signal("armed_pre_inplay")

        if self.triggered.get(key):
            return self._no_signal("already_triggered")

        plans: List[Dict[str, Any]] = []

        # ---- LAY ladder trigger ----
        if self.armed_lay.get(key):
            if to_price >= self.SWEETSPOT:
                plans.extend(self._emit_lay_ladder(ctx, to_price))
                self.triggered[key] = True

        # ---- BACK ladder trigger ----
        elif self.armed_back.get(key):
            if to_price <= self.SWEETSPOT:
                plans.extend(self._emit_back_ladder(ctx, to_price))
                self.triggered[key] = True

# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: return {
# 🛠 ACTION: Replace batch return with BUS-native flat emission
# 📆 PATCHED: 2026-02-20 — Fix Phase 3 break (remove nested plans contract)
#
# PURPOSE:
# - BUS expects flat plan objects
# - Nested "plans" payload caused missing direction/side error
# - Restore compatibility with existing routing system
#
# INVARIANT:
# - One plan returned per tick
# - No nested batch payload
# ==============================================================================

        if not plans:
            return self._no_signal("armed_not_triggered")

        # Emit first valid ladder level per tick (BUS-native contract)
        first = plans[0]

        return {
            "enter": True,
            "engine": "MSC_INPLAY",
            "role": first["role"],
            "direction": first["direction"],
            "px": first["px"],
            "target_ticks": first["target_ticks"],
            "why": first["why"],
        }

# === PATCH END ==============================================================

    # ----------------------------
    # Ladder emitters (BATCH)
    # ----------------------------

    def _emit_lay_ladder(self, ctx: Dict[str, Any], px: float) -> List[Dict[str, Any]]:
        return [
            {
                "enter": True,
                "engine": "MSC_INPLAY",
                "role": "PARENT",
                "direction": "LAY->BACK",
                "px": lvl,
                "target_ticks": 50,
                "why": f"inplay_lay_ladder_{lvl}",
            }
            for lvl in self.LAY_LADDER
            if lvl >= px
        ]

    def _emit_back_ladder(self, ctx: Dict[str, Any], px: float) -> List[Dict[str, Any]]:
        return [
            {
                "enter": True,
                "engine": "MSC_INPLAY",
                "role": "PARENT",
                "direction": "BACK->LAY",
                "px": lvl,
                "target_ticks": 50,
                "why": f"inplay_back_ladder_{lvl}",
            }
            for lvl in self.BACK_LADDER
            if lvl <= px
        ]

    # ----------------------------
    # No-signal helper
    # ----------------------------

    def _no_signal(self, reason: str) -> Dict[str, Any]:
        payload = {
            "enter": False,
            "engine": "MSC_INPLAY",
            "reason": reason,
            "re_eval": True,
        }
        try:
            emit("msc_inplay.no_signal", payload)
        except Exception:
            pass
        return payload

# === PATCH END ==============================================================
