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

def _to_int(val):
    try:
        return int(val)
    except Exception:
        return None


# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 ACTION: Replace entire file
# 📆 PATCHED: 2026-03-XX — In-Play Ladder Engine (Drift Harvest + Steam Protection)
# ==============================================================================



class InPlayEngine:
    """
    MSC_INPLAY — ladder-based in-play loss harvester.

    Responsibilities:
    - Harvest guaranteed losers via DRIFT → LAY ladder
    - Protect exposure via STEAM → BACK ladder
    - No stake sizing (BUS-owned)
    - No lifecycle logic (BUS-owned)
    """

    SWEETSPOT = 7.0
    ODDS_MAX  = 12.0

    LAY_LEVELS  = [7.0, 8.0, 9.0, 10.0, 12.0]
    BACK_LEVELS = [5.0, 4.0, 3.0]

    def __init__(self):
        self.state = InPlaySubState.IDLE

        # price memory
        self.last_odds: dict[tuple, float] = {}

        # ladder state
        self.lay_fired:  dict[tuple, set] = {}   # (mid,sid) -> {levels}
        self.back_fired: dict[tuple, set] = {}   # (mid,sid) -> {levels}

    # ------------------------------------------------------------------

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        mid = ctx.get("marketId")
        sid = ctx.get("selectionId")
        key = (mid, sid)

# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: odds = ctx.get("px")
# 🧩 ACTION: ADD CONTROLLED BETFAIR PX + MACRO TREND FALLBACK
# 📆 PATCHED: 2026-03-22 — Pattern B (engine-gated, BUS-authorised)
#
# WHY:
# - Recover from transient PX starvation
# - Provide macro (market-truth) trend context
# - Preserve BUS authority and determinism
#
# INVARIANTS:
# - Never overrides BUS PX
# - Never blocks
# - Never runs without explicit BUS permission
# ======================================================================

        odds = ctx.get("px")

        # --------------------------------------------------
        # 🔁 OPTIONAL BETFAIR PX + MACRO TREND FALLBACK
        # --------------------------------------------------
        if odds is None and ctx.get("_allow_bf_px_fallback"):
            try:
                from tools.betfair_runner_trend_surface import get_runner_trend

                trend = get_runner_trend(mid, sid)

                bf_px = trend.get("to_price")
                if bf_px and bf_px > 0:
                    odds = float(bf_px)
                    ctx["px"] = odds   # local use only

                    # Inject MACRO trend context (non-authoritative)
                    ctx["bf_trend_direction"] = trend.get("direction")
                    ctx["bf_trend_ticks"]     = trend.get("ticks_moved")
                    ctx["bf_trend_conf"]      = trend.get("confidence")

                else:
                    return self._no_signal("px_missing_bf_fallback")

            except Exception:
                return self._no_signal("px_missing_bf_error")

        if odds is None or odds <= 0:
            return self._no_signal("odds_unavailable")


        # decision intelligence
        dec = compute_msc_decision(ctx)
        if not dec:
            return self._no_signal("decision_missing")

        win_prob  = dec.get("win_prob")
        direction = dec.get("direction")
        if win_prob is None or direction is None:
            return self._no_signal("decision_incomplete")

        # rolling price memory
        prev = self.last_odds.get(key)
        self.last_odds[key] = odds

        if prev is None:
            return self._no_signal("first_touch")

        # initialise ladder memory
        self.lay_fired.setdefault(key, set())
        self.back_fired.setdefault(key, set())

        move_class   = ctx.get("inplay_move_class")
        base_rank    = ctx.get("inplay_rank_base_px")
        pnl_if_win   = ctx.get("inplay_pnl_if_win")

        # ==============================================================
        # 🟥 DRIFT → LAY LADDER (primary)
        # ==============================================================

        if (
            move_class
            and move_class.startswith("DRIFT")
            and direction == "LAY->BACK"
            and isinstance(win_prob, (int, float)) and win_prob < 0.40
            and isinstance(base_rank, (int, float)) and base_rank <= 6
        ):

            for lvl in self.LAY_LEVELS:
                if odds >= lvl and lvl not in self.lay_fired[key]:
                    self.lay_fired[key].add(lvl)
                    return self._emit_lay(ctx, odds, lvl)

        # ==============================================================
        # 🟦 STEAM → BACK PROTECTION (exposure only)
        # ==============================================================

        if (
            move_class
            and move_class.startswith("STEAM")
            and base_rank is not None
            and base_rank > self.SWEETSPOT   # came from outside
            and pnl_if_win is not None
            and isinstance(pnl_if_win, (int, float)) and pnl_if_win < 0 # we lose if it wins
        ):
            for lvl in self.BACK_LEVELS:
                if odds <= lvl and lvl not in self.back_fired[key]:
                    self.back_fired[key].add(lvl)
                    return self._emit_back(ctx, odds, lvl)

        return self._no_signal("no_signal")

    # ------------------------------------------------------------------
    # PLAN EMITTERS
    # ------------------------------------------------------------------

    def _emit_lay(self, ctx: Dict[str, Any], odds: float, lvl: float) -> Dict[str, Any]:
        return {
            "enter": True,
            "engine": "MSC_INPLAY",
            "role": "PARENT",
            "direction": "LAY->BACK",
            "px": odds,
            "target_ticks": 50,
            "why": f"inplay_drift_lay_{lvl}",
        }

    def _emit_back(self, ctx: Dict[str, Any], odds: float, lvl: float) -> Dict[str, Any]:
        return {
            "enter": True,
            "engine": "MSC_INPLAY",
            "role": "PARENT",
            "direction": "BACK->LAY",
            "px": odds,
            "target_ticks": 50,
            "why": f"inplay_steam_protect_{lvl}",
        }

    # ------------------------------------------------------------------

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



