# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: ^from typing import Dict, Any, Optional
# 🛠 ACTION: Replace entire file
# 📆 PATCHED: 2026-03-XX — First-Class MSC In-Play Engine (Arming → Trigger → Batch Emit)
# ==============================================================================

from typing import Dict, Any, List, Optional
import time
from datetime import datetime, timezone
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
        intel = {}
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
        # Race Start Authority (Non-Blocking)
        # --------------------------------------------------

        race_started = False
        race_quartile = None
        race_confidence = 0.0

        try:
            from engines.inplay.inplay_flag_helper import build_race_intelligence
            from engines.config_paths import open_bets_db
            import sqlite3

            con = open_bets_db(rw=False)
            row = con.execute("""
                SELECT marketStartTime
                FROM bets
                WHERE marketId = ?
                LIMIT 1
            """, (mid,)).fetchone()
            con.close()

            if row and row[0]:
                market_start_ts = datetime.fromisoformat(
                    row[0].replace("Z","")
                ).replace(tzinfo=timezone.utc).timestamp()

                from tools.betfair_runner_trend_surface import _TREND_CACHE

                runner_prices = {}
                for (m, s), v in list(_TREND_CACHE.items()):
                    if str(m) != mid:
                        continue
                    px_val = v.get("micro_to_price") or v.get("struct_to_price")
                    if px_val:
                        runner_prices[str(s)] = float(px_val)

                if runner_prices:
                    intel = build_race_intelligence(
                        mid,
                        market_start_ts,
                        runner_prices
                    )

                    race_started = intel["market"]["is_inplay"]
                    race_quartile = intel["market"]["race_quartile"]
                    race_confidence = intel["market"]["confidence"]

        except Exception:
            # Helper failure must NEVER block InPlay
            race_started = False

        # --------------------------------------------------
        # Authority Influence Layer (Ranking, not veto)
        # --------------------------------------------------
# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: # Authority Influence Layer (Ranking, not veto)
# 🛠 ACTION: Refine authority gating (LEADING + PROMINENT protection)
# 📆 PATCHED: 2026-02-24 — Balanced authority layer
#
# PURPOSE:
# - Authority ranks, not vetoes
# - Protect LEADING strongly
# - Protect PROMINENT unless collapse strong
# - Allow multiple runners per market
# ==============================================================================

        authoritative_runners = intel.get("runners", [])
        authority_available = bool(authoritative_runners)

        collapse_score = 0.0
        role = None

        if authority_available:

            collapse_map = {
                str(r.get("selectionId")): r.get("collapse_score", 0)
                for r in authoritative_runners
            }

            role_map = {
                str(r.get("selectionId")): r.get("role")
                for r in authoritative_runners
            }

            collapse_score = collapse_map.get(sid, 0.0)
            role = role_map.get(sid)

            # Block very weak collapse entirely
            if collapse_score < 1.0:
                return self._no_signal("collapse_score_too_low")

            # Protect LEADING unless extreme collapse
            if role == "LEADING" and collapse_score < 2.5:
                return self._no_signal("leader_protected")

            # Protect PROMINENT unless solid collapse
            if role == "PROMINENT" and collapse_score < 2.0:
                return self._no_signal("prominent_protected")

# === PATCH END ==============================================================
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
        # Atomic V7 Report
        # --------------------------------------------------

        self._print_v7_inplay_report(
            market_id=mid,
            sid=sid,
            px=px,
            direction=direction,
            ticks_moved=ticks_moved,
            race_started=race_started,
            race_quartile=race_quartile,
            race_confidence=race_confidence,
        )

        # --------------------------------------------------
        # Triggering (in-play only, one-shot)
        # --------------------------------------------------

        if not race_started:
            return self._no_signal("armed_pre_inplay")

        if self.triggered.get(key):
            return self._no_signal("already_triggered")

        plans: List[Dict[str, Any]] = []

# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 INSERT BEFORE: # ---- LAY ladder trigger ----
# 📆 PATCHED: 2026-02-20 — Secondary Harvest Boundary (15–20 zone)
#
# PURPOSE:
# - Harvest money from clearly losing runners
# - Flat lay £10
# - Mutually exclusive with primary ladder
# - One-shot per runner
# ==============================================================================

        HARVEST_MIN = 15.0
        HARVEST_MAX = 20.0
        HARVEST_STAKE = 10.0

        # Secondary harvest condition
        if (
            HARVEST_MIN <= px <= HARVEST_MAX
            and not self.triggered.get(key)
            and not getattr(self, "harvested", {}).get(key)
        ):

            # Avoid harvesting leaders
  
            if role not in ("LEADING", "PROMINENT"):

                # Mark as harvested
                if not hasattr(self, "harvested"):
                    self.harvested = {}

                self.harvested[key] = True
                self.triggered[key] = True  # prevent primary ladder later

                return {
                    "enter": True,
                    "engine": "MSC_INPLAY",
                    "role": "PARENT",
                    "direction": "LAY->BACK",
                    "px": px,
                    "stake": HARVEST_STAKE,
                    "target_ticks": 100,  # deeper scalp
                    "why": "inplay_secondary_harvest",
                }

# === PATCH END ==============================================================


# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: # ---- LAY ladder trigger ----
# 🛠 ACTION: Add authority ranking bias
# 📆 PATCHED: 2026-02-24 — Allow multiple runners but prioritise higher collapse
#
# PURPOSE:
# - Permit several runners per market
# - Prefer higher collapse_score
# - Maintain sweetspot discipline
# ==============================================================================

        # ---- LAY ladder trigger ----
        if self.armed_lay.get(key):

            # If authority available, prefer higher collapse runners
            if authority_available and collapse_score < 1.0:
                return self._no_signal("collapse_below_trade_threshold")

            if to_price >= self.SWEETSPOT:
                plans.extend(self._emit_lay_ladder(ctx, to_price))
                self.triggered[key] = True            

# === PATCH END ==============================================================

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

    # --------------------------------------------------
    # V7 Atomic Report
    # --------------------------------------------------

    def _print_v7_inplay_report(
        self,
        market_id: str,
        sid: str,
        px: float,
        direction: str,
        ticks_moved: float,
        race_started: bool,
        race_quartile: Optional[str],
        race_confidence: float,
    ):

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")

        print("\n══════════════════════════════════════════════════════")
        print("V7 MSC_INPLAY REPORT")
        print(f"t={now_str}   mode=LIVE")
        print("══════════════════════════════════════════════════════")

        print("\nMARKET")
        print("------------------------------------------------------")
        print(f"marketId                : {market_id}")
        print(f"race_started            : {'YES' if race_started else 'NO'}")
        print(f"race_quartile           : {race_quartile}")
        print(f"race_confidence         : {round(race_confidence,2)}")
        print("------------------------------------------------------")

        print("\nRUNNER")
        print("------------------------------------------------------")
        print(f"selectionId             : {sid}")
        print(f"price                   : {px}")
        print(f"direction               : {direction}")
        print(f"ticks_moved             : {ticks_moved}")
        print(f"armed_lay               : {self.armed_lay.get((market_id, sid), False)}")
        print(f"armed_back              : {self.armed_back.get((market_id, sid), False)}")
        print(f"triggered               : {self.triggered.get((market_id, sid), False)}")
        print("------------------------------------------------------")
        print("══════════════════════════════════════════════════════")

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
