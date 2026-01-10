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
from engines.market_monitor.monitor import get_market_state

from engines.micro_scalper_v7.event_receiver import get_engine_outcomes

from engines.mastery.event_sink import emit
from engines.micro_scalper_v7.v7_snapshot_helper import get_v7_inplay_snapshot

class InPlayEngine:
    SWEETSPOT = 7.0
    ODDS_MAX  = 12.0

    def __init__(self):
        self.state = InPlaySubState.IDLE
        self.last_odds = {}   # (mid, sid) -> last odds

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        mid = ctx.get("marketId")
        sid = ctx.get("selectionId")

        snap = get_v7_inplay_snapshot(mid, sid)
        if not snap:
            return self._no_signal("intel_missing")

        odds = snap["odds"]
        fav_rank = snap["fav_rank"]
        win_prob = snap["win_prob"]
        race_q   = snap["race_quartile"]

        # ---- hard filters ------------------------------------------------
        if odds is None:
            return self._no_signal("odds_unavailable")

        if odds > self.ODDS_MAX:
            return self._no_signal("odds_too_high")

        if fav_rank == 1 and snap["is_leading"]:
            return self._no_signal("favourite_leading")

        if race_q in ("Q1", "Q4"):
            return self._no_signal("race_phase_invalid")

        # ---- anchor logic ------------------------------------------------
        key = (mid, sid)
        prev = self.last_odds.get(key)
        self.last_odds[key] = odds

        # below anchor → monitor
        if odds < self.SWEETSPOT:
            return self._no_signal("below_anchor_monitoring")

        # observation zone
        if self.SWEETSPOT < odds <= self.ODDS_MAX:
            if prev is None:
                return self._no_signal("observation_zone")

            # CROSS UP through 7
            if prev < self.SWEETSPOT and odds >= self.SWEETSPOT:
                if win_prob < 0.25:
                    return self._emit_plan(odds)
                return self._no_signal("anchor_cross_fav_filtered")

            # CROSS DOWN then UP (reversal)
            if prev > self.SWEETSPOT and odds >= self.SWEETSPOT:
                return self._no_signal("observation_zone")

        return self._no_signal("no_signal")

    # ------------------------------------------------------------------

    def _emit_plan(self, odds: float) -> Dict[str, Any]:
        return {
            "enter": True,
            "engine": "MSC_INPLAY",
            "source": "V",
            "direction": "LAY->BACK",
            "px": odds,
            "target_ticks": 50,
            "why": "inplay_anchor_cross",
        }

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
        return payload


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

            mid = ctx.get("marketId")
            sid = str(ctx.get("selectionId"))

            st = get_market_state(mid) or {}
            rn = (st.get("runners") or {}).get(sid) or {}

            current = rn.get("px") or ctx.get("px")
            if not current or current <= 0:
                return None


            # Must be in sweetspot
            if current < self.SWEETSPOT_MIN_ODDS:
                return None

            # Collapse detection from direction engine
            dec = compute_msc_decision(ctx)
            win_prob = dec["win_prob"]
            direction = dec["direction"]   # BACK->LAY or LAY->BACK

            # In-play collapse = strong loser → direction must be LAY->BACK
            if direction != "LAY->BACK":
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
                # expose implied execution direction
                "direction": "LAY->BACK",
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

