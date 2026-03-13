# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🧩 ACTION: CREATE FILE
# 📆 PHASE 0 — Unified Engine Skeleton (No Logic, No Plans)
#
# PURPOSE:
# - Self-contained engine
# - PX-driven (no OC series)
# - No window dependency
# - No route slicing dependency
# - No execution mutation
# - Returns enter=False
# - Emits structured signal container
# ======================================================================================================

from typing import Dict, Any
import time

from engines.stoploss_engine import StopLossEngine, ParentState
from engines.math.dynamic_stake_v7 import get_form_adjustment
from engines.bias.engine import compute_bias
from engines.indicators.opportunities import ensure_schema as ensure_opp_schema
from engines.micro_scalper_v7.direction_engine import compute_msc_decision
from engines.bus_route import build_bus_route_tick

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: import time
# 🧩 ACTION: ADD — session token bootstrap (standalone compatibility)
# 📆 PATCHED: 2026-03-14 — unified standalone execution support
#
# PURPOSE
# -------
# Allow UnifiedEngine to run standalone with Betfair API access.
#
# Mirrors BUS / odds writer behaviour:
#   • Uses SESSION_TOKEN env var if present
#   • Falls back to BETFAIR_SESSION_TOKEN
#   • Prompts user when running standalone
#
# LIVE MODE
# ---------
# When launched by BUS the token already exists and no prompt occurs.
#
# STANDALONE MODE
# ---------------
# Prompts once so drift / odds / match surfaces can function.
# ======================================================================================================

import os

SESSION_TOKEN = (
    os.getenv("SESSION_TOKEN")
    or os.getenv("BETFAIR_SESSION_TOKEN")
)

if __name__ == "__main__":
    if not SESSION_TOKEN:
        SESSION_TOKEN = input("🔐 Enter Betfair session token: ").strip()
        os.environ["SESSION_TOKEN"] = SESSION_TOKEN

class UnifiedEngine:
    """
    Unified Engine — Phase 0 (Signal Surface Only)

    Responsibilities (Phase 0):
    - Build internal market map (from bets table)
    - Track PX anchors (market + parent)
    - Aggregate signal surfaces (empty initially)
    - Print V7 report block
    - Return structured signal summary to BUS
    - Never emit plans (enter=False)

    Non-Responsibilities:
    - No stake logic
    - No routing
    - No BankState mutation
    - No OC series
    - No window gating
    """

    ENGINE_NAME = "UNIFIED"
    LANE_ID = 7

    # --------------------------------------------------------------------------------------------------
    # INITIALISATION
    # --------------------------------------------------------------------------------------------------

    def __init__(self):
        self._boot_ts = time.time()
        self._last_tick_ts = None
        # canonical runner world container
        self._route_ctx_map = {}

        # Market anchor storage
        self._market_anchor_px = {}      # {marketId: {selectionId: px}}
        self._parent_anchor_px = {}      # {marketId: {selectionId: px}}
        self._parent_anchor_ts = {}      # {marketId: {selectionId: ts}}

        # Phase clock cache
        self._daily_markets = {}         # {marketId: {...}}

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: self._daily_markets =
# 🧩 ADD: runner narrative memory containers
# PURPOSE:
# Enables temporal reasoning about runners across ticks.
# Tracks trend persistence, breakouts, and favourite transitions.
# ======================================================================================================

        # Structural runner memory (narrative tracking)
        self._runner_structure = {}

        # Market structural memory
        self._market_structure = {}

        # Runner breakout counters
        self._runner_breakouts = {}

        # Favourite transition tracking
        self._fav_history = {}

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:__init__
# 🔎 SEARCH: self._fav_history =
# 🧩 ACTION: ADD structural trigger memory
# ======================================================================================================

        self._structural_fired = {}

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:__init__
# 🔎 SEARCH: self._fav_history =
# 🧩 ADD: ensure opportunity schema exists
# ======================================================================================================

        try:
            ensure_opp_schema()
        except Exception:
            pass
    # --------------------------------------------------------------------------------------------------
    # PUBLIC ENTRYPOINT
    # --------------------------------------------------------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: class UnifiedEngine:
# 🧩 ACTION: ADD bucket extraction helper
# 📆 PATCHED: 2026-03-12 — BUS route bucket reader
#
# PURPOSE:
# - Convert BUS route schedule into priority buckets
# - Avoid repeated TTO computation
# ======================================================================================================

    def _get_bus_buckets(self):

        try:
            route = build_bus_route_tick()

        except Exception:
            return {}

        buckets = {
            "5m": [],
            "10m": [],
            "20m": [],
            "40m": [],
            "60m": [],
            "long": [],
        }

        for r in route:

            mid = r.get("marketId")
            sid = r.get("selectionId")
            tto = r.get("tto_seconds")

            if tto is None:
                continue

            key = (mid, sid)

            if tto <= 300:
                buckets["5m"].append(key)

            elif tto <= 600:
                buckets["10m"].append(key)

            elif tto <= 1200:
                buckets["20m"].append(key)

            elif tto <= 2400:
                buckets["40m"].append(key)

            elif tto <= 3600:
                buckets["60m"].append(key)

            else:
                buckets["long"].append(key)

        return buckets
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: def tick(self, ctx:
# 🧩 ACTION: REPLACE ENTIRE METHOD
# 📆 UNIFIED ENGINE v1 — COMPLETE (PRE + STOP + INPLAY + CORRECTIVE)
# ======================================================================================================

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:

        now = time.time()
        tick_delta = None

        if self._last_tick_ts is not None:
            tick_delta = now - self._last_tick_ts

        self._last_tick_ts = now

        # ------------------------------------------------------------------
        # 1️⃣ BUILD REPORT (ALL SIGNALS LIVE HERE)
        # ------------------------------------------------------------------

        report = self._build_v7_report(ctx=ctx, tick_delta=tick_delta)

        route = getattr(self, "_route_ctx_map", {})

        layer2     = report.get("layer2", {})
        timing     = report.get("timing", {})
        volatility = report.get("volatility", {})
        liability  = report.get("liability", {})

        # ------------------------------------------------------------------
        # MARKET TIME LOOKUP (FAST ACCESS)
        # ------------------------------------------------------------------

        market_map = {
            m.get("marketId"): m
            for m in timing.get("markets", [])
            if m.get("marketId")
        }

        runners_moved = volatility.get("runners_moved_last_window", 0)

        plans = []

        emit_plan = plans.append

        # --------------------------------------------------
        # Merge stoploss plans from stop surface
        # --------------------------------------------------

        stop_plans = report.get("stop", {}).get("plans", [])
        plans.extend(stop_plans)

        # ensure memory containers exist
        if not hasattr(self, "_emitted_children"):
            self._emitted_children = set()

        if not hasattr(self, "_inplay_markets"):
            self._inplay_markets = set()

        # ------------------------------------------------------------------
        # 2️⃣ PRE-OFF EXPLORATORY (TOP RANKED)
        # ------------------------------------------------------------------

        candidates = self._select_exploratory_candidates(report)

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:tick
# 🔎 SEARCH: # 2️⃣ PRE-OFF EXPLORATORY (TOP RANKED)
# 🧩 ACTION: REPLACE ENTIRE EXPLORATORY LOOP
# 📆 PATCHED: 2026-03-16 — Top 5 candidates always emit exploratory plans
#
# PURPOSE
# -------
# Remove time gating and allow the highest scoring candidates to always
# generate exploratory parent plans.
#
# DESIGN
# ------
# Layer2 produces ranked candidates.
# Unified emits the top 5 directly.
#
# DIRECTION
# ---------
# Direction must come from the signal layer (candidate / ctx),
# never be hard-coded.
# ======================================================================================================

        # --------------------------------------------------
        # 2️⃣ PRE-OFF EXPLORATORY (TOP 5 ALWAYS TRADE)
        # --------------------------------------------------

        for c in candidates:

            mid = c["marketId"]
            sid = c["selectionId"]
            px  = c.get("px")

            if px is None:
                continue

            try:
                px = float(px)
            except Exception:
                continue

            # --------------------------------------------------
            # Direction comes from signal layer
            # --------------------------------------------------
            direction = c.get("direction")

            if not direction:
                rctx = route.get((mid, sid))
                if rctx:
                    direction = rctx.get("direction")

            if not direction:
                direction = "LAY->BACK"

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:tick
# 🔎 SEARCH: emit_plan({
# 🧩 ADD: persist signal fingerprint for learning system
# 📆 PATCHED: 2026-03-16 — Unified signal telemetry
#
# PURPOSE
# -------
# Persist scoring + signal composition so P&L can later be analysed
# against signal patterns.
# ======================================================================================================

            score = c.get("score", 0)

            signal_vector = []

            if c.get("direction"):
                signal_vector.append("direction")

            if c.get("bias"):
                signal_vector.append("bias")

            if c.get("drift"):
                signal_vector.append("drift")

            if c.get("sweet"):
                signal_vector.append("sweet")

            if c.get("bucket"):
                signal_vector.append("bucket")

            signal_vector_str = "|".join(signal_vector)

            emit_plan({
                "enter": True,
                "engine": "MSC_UNIFIED",
                "bet_type": "EXPLORATORY",
                "role": "PARENT",
                "marketId": mid,
                "selectionId": sid,
                "direction": direction,
                "px": px,
                "target_ticks": 1,

                # 🔑 learning system fields
                "signal_score": score,
                "signal_vector": signal_vector_str,
                "signal_source": "unified_v1",

                "why": "unified_top5_candidate",
            })

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 ANCHOR: inside tick(), immediately AFTER exploratory plan emission loop
# 🧩 ACTION: ADD — Unified Risk Harvest Loop
# 📆 PATCHED: 2026-03-08
#
# PURPOSE
# -------
# Unified must manage its own risk harvesting rather than relying on MSC_RISK.
#
# This loop monitors existing parent anchors and emits CHILD risk hedges when
# price moves away from the anchor entry price.
#
# LOGIC
# -----
# LAY parent  → price drifts up   → BACK hedge
# BACK parent → price steams down → LAY hedge
#
# DATA SOURCE
# -----------
# Uses BUS ctx_map fields:
#
#   ctx["anchor_parent_id"]
#   ctx["anchor_entry_odds"]
#   ctx["legacy_entry_side"]
#   ctx["px"]
#
# No database access required.
#
# OUTPUT
# ------
# Emits:
#
#   bet_type = "RISK"
#   role     = "CHILD"
#
# These plans are later limited by the slot allocator:
#
#   exploratory → 5
#   risk        → 21
#   inplay      → 24
#
# DEBUG NOTES
# -----------
# If risk is not firing, check:
#
#   ctx["anchor_parent_id"]
#   ctx["anchor_entry_odds"]
#   ctx["legacy_entry_side"]
#   ctx["px"]
#
# Risk cannot trigger without an anchor parent.
# ======================================================================================================

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: for (mid, sid), rctx in getattr(self, "_route_ctx_map", {}).items():
# 🧩 ACTION: REPLACE — use cached route map
# ======================================================================================================

        for (mid, sid), rctx in route.items():

            market = market_map.get(mid)
            if not market:
                continue

            tto = market.get("tto_seconds")

            # --------------------------------------------------
            # RISK WINDOW
            # 60min → 2min
            # --------------------------------------------------
            if tto is None or not (120 <= tto <= 3600):
                continue

            anchor_id  = rctx.get("anchor_parent_id")
            anchor_odds = rctx.get("anchor_entry_odds")
            side       = rctx.get("legacy_entry_side")
            px         = rctx.get("px")

            if not anchor_id:
                continue

            if anchor_odds is None or px is None:
                continue

            try:
                anchor_odds = float(anchor_odds)
                px = float(px)
            except Exception:
                continue

            # --------------------------------------------------
            # LAY → BACK risk harvest
            # --------------------------------------------------

            if side == "LAY" and px > anchor_odds:

                emit_plan({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "RISK",
                    "role": "CHILD",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": "BACK",
                    "px": px,
                    "why": "unified_risk_drift_harvest",
                })

            # --------------------------------------------------
            # BACK → LAY risk harvest
            # --------------------------------------------------

            elif side == "BACK" and px < anchor_odds:

                emit_plan({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "RISK",
                    "role": "CHILD",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": "LAY",
                    "px": px,
                    "why": "unified_risk_steam_harvest",
                })

        # ======================================================================================================
        # 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
        # 🔎 ANCHOR: inside tick(), immediately AFTER STOPLOSS section
        # 🧩 ACTION: INSERT — Unified InPlay Engine (arming → detection → ladder emit)
        # 📆 PATCHED: 2026-04-XX — Replace legacy InPlay logic with Unified signal model
        #
        # PURPOSE
        # -------
        # Implement full InPlay trading inside Unified using existing signal surfaces:
        #
        #   timing surface
        #   volatility surface
        #   drift surface
        #   sweet spot surface
        #   layer2 candidate ranking
        #
        # This replaces the legacy MSC_INPLAY engine behaviour.
        #
        # CONTRACT
        # --------
        # - No stake logic
        # - No router mutation
        # - Emits batch ladder plans
        # - Uses Unified signal surfaces only
        #
        # ======================================================================================================

        # ensure in-play memory exists
        if not hasattr(self, "_inplay_state"):
            self._inplay_state = {
                "armed_lay": {},
                "armed_back": {},
                "triggered": {},
                "markets_started": set(),
            }

        mem = self._inplay_state

        # --------------------------------------------------
        # 1️⃣ MARKET-LEVEL INPLAY DETECTION
        # --------------------------------------------------

        moved = volatility.get("runners_moved_last_window", 0)

        for m in timing.get("markets", []):
            mid = m.get("marketId")
            tto = m.get("tto_seconds")

            if mid is None or tto is None:
                continue

            # robust detection
            if tto <= 0 or moved >= 5:
                mem["markets_started"].add(mid)

        # --------------------------------------------------
        # 1️⃣ RUNNER ARMING (DRIFT SIGNAL)
        # --------------------------------------------------

        for c in candidates:

            mid = c["marketId"]
            sid = c["selectionId"]
            px  = c.get("px")

            if px is None:
                continue

            key = (mid, sid)

            rctx = self._route_ctx_map.get(key)
            if not rctx:
                continue

            direction = rctx.get("direction")

            if direction == "LAY->BACK":
                mem["armed_lay"][key] = True

            elif direction == "BACK->LAY":
                mem["armed_back"][key] = True


        # --------------------------------------------------
        # 2️⃣ STRUCTURAL LADDER TRIGGER (ANYTIME)
        # --------------------------------------------------



        for c in candidates:

            mid = c["marketId"]
            sid = c["selectionId"]
            px  = c.get("px")

            if px is None:
                continue

            key = (mid, sid)

            px = float(px)

            fired = self._structural_fired.get(key)

            # ---- SWEET SPOT COLLAPSE ----
            if mem["armed_lay"].get(key) and px >= 7 and fired is None:

                ladder = [7, 8, 9, 10, 11, 12]

                for lvl in ladder:
                    if lvl >= px:
                        emit_plan({
                            "enter": True,
                            "engine": "MSC_UNIFIED",
                            "bet_type": "INPLAY",
                            "role": "PARENT",
                            "marketId": mid,
                            "selectionId": sid,
                            "direction": "LAY->BACK",
                            "px": lvl,
                            "why": "unified_structural_ladder",
                        })

                self._structural_fired[key] = "SWEET"


            # ---- 15-20 COLLAPSE BAND ----
            elif 15 <= px <= 20 and fired is None:

                emit_plan({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "INPLAY",
                    "role": "PARENT",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": "LAY->BACK",
                    "px": px,
                    "why": "unified_structural_15_20",
                })

                self._structural_fired[key] = "HIGH"


            # ---- REBOUND BACKS (5 / 4 / 3) ----
            elif mem["armed_back"].get(key) and px <= 5:

                ladder = [5, 4, 3]

                for lvl in ladder:
                    if lvl <= px:
                        emit_plan({
                            "enter": True,
                            "engine": "MSC_UNIFIED",
                            "bet_type": "INPLAY",
                            "role": "PARENT",
                            "marketId": mid,
                            "selectionId": sid,
                            "direction": "BACK->LAY",
                            "px": lvl,
                            "why": "unified_structural_rebound",
                        })

        # --------------------------------------------------
        # 4️⃣ SECONDARY HARVEST (LOSERS 15-20)
        # --------------------------------------------------

        for c in candidates:

            mid = c["marketId"]
            sid = c["selectionId"]
            px  = c.get("px")

            if px is None:
                continue

            px = float(px)

            if 15 <= px <= 20:

                emit_plan({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "INPLAY",
                    "role": "PARENT",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": "LAY->BACK",
                    "px": px,
                    "why": "unified_inplay_secondary_harvest",
                })        
        
        # ------------------------------------------------------------------
        # 4️⃣ IN-PLAY DETECTION (STRICT RULE)
        # Must be AFTER zero AND 3-runner volatility spike
        # ------------------------------------------------------------------

        moved = volatility.get("runners_moved_last_window", 0)

        for m in timing.get("markets", []):
            mid = m.get("marketId")
            tto = m.get("tto_seconds")

            if tto is None:
                continue

            if tto <= 0 and moved >= 3:
                self._inplay_markets.add(mid)

        # ------------------------------------------------------------------
        # 5️⃣ IN-PLAY LADDER (LOSERS + CONTENDERS)
        # ------------------------------------------------------------------

        for c in candidates:

            mid = c["marketId"]
            sid = c["selectionId"]
            px  = c.get("px")

            if mid not in self._inplay_markets:
                continue

            if not px:
                continue

            px = float(px)

            # Lay losers drifting past sweet spot
            if px >= 7:

                emit_plan({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "INPLAY",
                    "role": "PARENT",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": "LAY->BACK",
                    "px": px,
                    "why": "unified_inplay_lay",
                })

            # Back collapsing contenders
            elif px <= 5:

                emit_plan({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "INPLAY",
                    "role": "PARENT",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": "BACK->LAY",
                    "px": px,
                    "why": "unified_inplay_back",
                })

        # --------------------------------------------------
        # STRUCTURAL BREAKDOWN ENGINE (ANYTIME)
        # --------------------------------------------------
        # Replaces the previous INPLAY race-phase trigger.
        # Detects runner collapse structurally using:
        #   drift surface
        #   rank crossover
        #   sweet-spot exit
        #
        # Fires when a runner drifts out of competitiveness.
        # One structural trigger per runner.

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: sweet_rows =
# 🧩 ACTION: ADD zone set
# ======================================================================================================

        collapse_zones = {"7-10", "15-20"}

        drift_rows = report.get("drift", {}).get("runners", [])
        rank_rows  = report.get("rank", {}).get("runners", [])
        sweet_rows = report.get("sweet_spot", {}).get("runners", [])

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: sweet_rows = report.get("sweet_spot", {}).get("runners", [])
# 🧩 ACTION: ADD — Precompute runner surface maps
# PURPOSE:
# Replace repeated list scans with O(1) lookups
# PERFORMANCE:
# Removes O(n²) behaviour in structural loops
# ======================================================================================================

        drift_map = {(r["marketId"], r["selectionId"]): r for r in drift_rows}
        rank_map  = {(r["marketId"], r["selectionId"]): r for r in rank_rows}
        sweet_map = {(r["marketId"], r["selectionId"]): r for r in sweet_rows}

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: for (mid, sid), rctx in self._route_ctx_map.items():
# 🧩 ACTION: REPLACE
# ======================================================================================================

        for (mid, sid), rctx in route.items():

            market = market_map.get(mid)
            if not market:
                continue

            tto = market.get("tto_seconds")

            # --------------------------------------------------
            # STRUCTURAL ENGINE ACTIVE ONLY ≤5min
            # --------------------------------------------------
            if tto is None or tto > 300:
                continue

            px = rctx.get("px")
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: px = rctx.get("px")
# 🧩 ACTION: ADD single conversion
# ======================================================================================================

            try:
                px = float(px)
            except Exception:
                continue

            if px is None:
                continue

            key = (mid, sid)

            px = float(px)

            fired = self._structural_fired.get(key)

            drift_row = drift_map.get((mid, sid))

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: rank_row = next(
# 🧩 ACTION: REPLACE — O(1) rank lookup
# ======================================================================================================

            rank_row = rank_map.get((mid, sid))

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: sweet_row = next(
# 🧩 ACTION: REPLACE — O(1) sweet lookup
# ======================================================================================================

            sweet_row = sweet_map.get((mid, sid))

            drift_speed = 0
            if drift_row:
                drift_speed = abs(drift_row.get("delta_ticks_per_min") or 0)

            rank_delta = 0
            if rank_row:
                rank_delta = abs(rank_row.get("rank_delta") or 0)

            zone = None
            if sweet_row:
                zone = sweet_row.get("zone")

            # --------------------------------------------------
            # STRUCTURAL DRIFT DETECTION
            # --------------------------------------------------
            # Runner drifting through rank boundaries and leaving
            # competitive zone.

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: zone in collapse_zones
# 🧩 ACTION: REPLACE numeric comparison
# ======================================================================================================

            zone_collapse = (7 <= px <= 10) or (15 <= px <= 20)

            structural_break = (
                drift_speed >= 0.5
                or rank_delta >= 2
                or zone_collapse
            )

# ======================================================================================================
# LATE COLLAPSE DETECTION (MISSED SWEET SPOT)
# ======================================================================================================

            if px >= 13 and px < 15 and fired is None:

                emit_plan({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "INPLAY",
                    "role": "PARENT",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": "LAY->BACK",
                    "px": px,
                    "why": "unified_late_structural_collapse",
                })

                self._structural_fired[key] = "LATE"

# ======================================================================================================
# STRUCTURAL BREAK
# ======================================================================================================

            elif structural_break and fired is None:
                # SWEET SPOT LADDER
                if px >= 7 and px < 15:

                    ladder = [7, 8, 9, 10, 11, 12]

                    for lvl in ladder:
                        if lvl >= px:
                            emit_plan({
                                "enter": True,
                                "engine": "MSC_UNIFIED",
                                "bet_type": "INPLAY",
                                "role": "PARENT",
                                "marketId": mid,
                                "selectionId": sid,
                                "direction": "LAY->BACK",
                                "px": lvl,
                                "why": "unified_structural_breakdown",
                            })

                    self._structural_fired[key] = "SWEET"

                # HIGH COLLAPSE BAND
                elif 15 <= px <= 20:

                    emit_plan({
                        "enter": True,
                        "engine": "MSC_UNIFIED",
                        "bet_type": "INPLAY",
                        "role": "PARENT",
                        "marketId": mid,
                        "selectionId": sid,
                        "direction": "LAY->BACK",
                        "px": px,
                        "why": "unified_structural_15_20",
                    })

                    self._structural_fired[key] = "HIGH"

            # --------------------------------------------------
            # REBOUND BACK LADDER
            # --------------------------------------------------

            if px <= 5:

                ladder = [5, 4, 3]

                for lvl in ladder:
                    if lvl <= px:
                        emit_plan({
                            "enter": True,
                            "engine": "MSC_UNIFIED",
                            "bet_type": "INPLAY",
                            "role": "PARENT",
                            "marketId": mid,
                            "selectionId": sid,
                            "direction": "BACK->LAY",
                            "px": lvl,
                            "why": "unified_structural_rebound",
                        })

        # --------------------------------------------------
        # SLOT ALLOCATION (UNIFIED CAPACITY CONTROL)
        # --------------------------------------------------

        exploratory = [p for p in plans if p.get("bet_type") == "EXPLORATORY"]
        risk        = [p for p in plans if p.get("bet_type") == "RISK"]
        inplay      = [p for p in plans if p.get("bet_type") == "INPLAY"]

        # deterministic caps
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: exploratory = exploratory[:5]
# 🧩 ACTION: REPLACE safe slicing
# ======================================================================================================

        if len(exploratory) > 6:
            exploratory = exploratory[:6]

        if len(risk) > 50:
            risk = risk[:50]

        if len(inplay) > 29:
            inplay = inplay[:29]

        plans = exploratory + risk + inplay

        # ------------------------------------------------------------------
        # RETURN CONTRACT
        # ------------------------------------------------------------------

        if not plans:
            return {
                "enter": False,
                "engine": "MSC_UNIFIED",
                "lane": self.LANE_ID,
                "why": "no_signal",
                "signals": self._build_signal_summary(report),
                "report": report,
            }

        return {
            "enter": True,
            "engine": "MSC_UNIFIED",
            "lane": self.LANE_ID,
            "batch": True,
            "plans": plans,
            "why": "unified_emit",
            "signals": self._build_signal_summary(report),
            "report": report,
        }
    # --------------------------------------------------------------------------------------------------
    # SNAPSHOT READER (UNIFIED RUNTIME AUTHORITY)
    # --------------------------------------------------------------------------------------------------

    def _read_unified_snapshot(self) -> Dict[str, Any]:
        from engines.config_paths import open_auto_db
        import sqlite3

        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        try:
            row = con.execute("""
                SELECT *
                FROM unified_runtime_snapshot
                ORDER BY ts DESC
                LIMIT 1
            """).fetchone()

            if not row:
                return {}

            return dict(row)

        finally:
            con.close()

    # --------------------------------------------------------------------------------------------------
    # MARKET NAME MAP (BETS AUTHORITY)
    # --------------------------------------------------------------------------------------------------

    def _build_name_lookup(self) -> Dict[tuple, Dict[str, str]]:
        """
        Returns:
            {(marketId, selectionId): {
                "market_name": str,
                "runner_name": str,
                "event_name": str
            }}
        """

        from engines.config_paths import open_bets_db
        import sqlite3

        con = open_bets_db(rw=False)
        con.row_factory = sqlite3.Row

        out = {}

        try:
            rows = con.execute("""
                SELECT
                    marketId,
                    selectionId,
                    horse_name,
                    event_name,
                    marketStartTime
                FROM bets
                WHERE substr(marketStartTime,1,10) = date('now','utc')
            """).fetchall()

            for r in rows:
                mid = str(r["marketId"])
                sid = str(r["selectionId"])

                out[(mid, sid)] = {
                    "market_name": f"{r['horse_name']} @ {r['event_name']}",
                    "runner_name": r["horse_name"],
                    "event_name": r["event_name"],
                }

        finally:
            con.close()

        return out

    # --------------------------------------------------------------------------------------------------
    # BUILD RUNTIME CTX
    # --------------------------------------------------------------------------------------------------

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: def _build_runtime_ctx_map(self)
# 🧩 ACTION: REPLACE — reuse BUS world instead of rebuilding snapshot
# 📆 PATCHED: 2026-03-07 — Unified engine consumes BUS ctx_map
#
# PURPOSE
# -------
# Remove duplicate BusRouteSnapshot construction.
#
# Unified must consume the same world used by BUS:
#     BusRouteSnapshot → ctx_map
#
# This prevents:
#     • duplicate route builds
#     • duplicate odds refresh
#     • inconsistent world state
#
# CONTRACT
# --------
# BUS injects ctx_map into Unified via ctx["_route_ctx_map"].
# If unavailable (standalone mode), fallback to empty.
# ======================================================================================================

    def _build_runtime_ctx_map(self, ctx):

        route_ctx = ctx.get("_route_ctx_map")

        if isinstance(route_ctx, dict):
            return route_ctx

        # fallback only for standalone reporter mode
        return {}

    # --------------------------------------------------------------------------------------------------
    # WORLD READER (BUS ROUTE SNAPSHOT AUTHORITY)
    # --------------------------------------------------------------------------------------------------

    def _read_route_world(self):
        """
        Read full runner world from BUS snapshot surface.

        This is the canonical WORLD surface:
        - contains all runners currently known to BUS
        - independent of BUS stop / execution window
        """

        from engines.config_paths import connect_db
        import sqlite3

        con = connect_db(ro=True)
        con.row_factory = sqlite3.Row

        try:

            rows = con.execute("""
                SELECT
                    marketId,
                    selectionId,
                    px,
                    back,
                    lay,
                    band
                FROM bus_route_runtime_snapshot
                WHERE ts = (
                    SELECT MAX(ts)
                    FROM bus_route_runtime_snapshot
                )
            """).fetchall()

        finally:
            con.close()

        world = {}

        for r in rows:

            key = (str(r["marketId"]), str(r["selectionId"]))

            world[key] = {
                "marketId": str(r["marketId"]),
                "selectionId": str(r["selectionId"]),
                "px": r["px"],
                "back": r["back"],
                "lay": r["lay"],
                "band": r["band"],
            }

        return world

    # --------------------------------------------------------------------------------------------------
    # V7 REPORT BUILDER — SPEC LOCKED
    # --------------------------------------------------------------------------------------------------

    def _build_v7_report(self, ctx: Dict[str, Any], tick_delta: float) -> Dict[str, Any]:

        # 1️⃣ SYSTEM SNAPSHOT (BUS awareness only)
        system_snapshot = self._read_unified_snapshot()

        # 2️⃣ STRUCTURAL WORLD (self-built, authoritative)
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: self._route_ctx_map = self._build_runtime_ctx_map()
# 🧩 ACTION: REPLACE — pass BUS world
# 📆 PATCHED: 2026-XX-XX — Unified uses BUS ctx_map
# ======================================================================================================

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_v7_report
# 🔎 ANCHOR: self._route_ctx_map = self._build_runtime_ctx_map(ctx)
# 🧩 ACTION: REPLACE — restore PX memory for BUS world
# 📆 PATCHED: 2026-03-09
#
# ROOT CAUSE
# ----------
# BUS rebuilds ctx_map every tick, so fields like:
#     ctx["_prev_px"]
#     ctx["_prev_vol_px"]
# no longer persist between ticks.
#
# Older Unified versions implicitly preserved this memory
# because the runtime world was persistent.
#
# Without PX memory:
#     drift surface collapses
#     volatility surface collapses
#     breakout detection fails
#     candidate pools become empty
#
# FIX
# ---
# Store PX history inside Unified and inject previous values
# back into ctx_map before surfaces execute.
# ======================================================================================================

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_v7_report
# 🔎 SEARCH: from engines.bus.bus import BUS
# 🧩 ACTION: REPLACE — snapshot-first world loader
# 📆 PATCHED: 2026-03-14 — standalone + BUS compatibility
#
# PURPOSE
# -------
# Unified must operate purely from snapshots when BUS is not available.
#
# WORLD SOURCES
# -------------
# 1️⃣ ctx["_route_ctx_map"] injected by BUS (live mode)
# 2️⃣ bus_route_runtime_snapshot table (standalone mode)
#
# RESULT
# ------
# Unified report can run independently from snapshots.
# ======================================================================================================

        route_ctx = ctx.get("_route_ctx_map")

        if isinstance(route_ctx, dict) and route_ctx:
            self._route_ctx_map = route_ctx
        else:
            try:
                self._route_ctx_map = self._read_route_world()
            except Exception:
                self._route_ctx_map = {}

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: self._route_ctx_map = self._build_runtime_ctx_map(ctx)
# 🧩 ACTION: ADD local alias
# PURPOSE: reduce attribute lookups
# ======================================================================================================

        route = getattr(self, "_route_ctx_map", {})

        # initialise PX memory store
        if not hasattr(self, "_runner_px_memory"):
            self._runner_px_memory = {}

        for key, rctx in self._route_ctx_map.items():

            px = rctx.get("px")

            if px is None:
                continue

            try:
                px = float(px)
            except Exception:
                continue

            prev = self._runner_px_memory.get(key)

            # restore previous tick memory
            if prev is not None:
                rctx["_prev_px"] = prev
                rctx["_prev_vol_px"] = prev

            # update stored memory
            self._runner_px_memory[key] = px

        report = {
            "world": self._build_world_surface(),

            # ─────────────────────────────────────────
            # LAYER 1 — STRUCTURAL VISIBILITY
            # ─────────────────────────────────────────
            "timing": self._build_timing_surface(),
            "drift": self._build_drift_surface(),
            "rank": self._build_rank_surface(),
            "sweet_spot": self._build_sweet_spot_surface(),
            "volatility": self._build_volatility_surface(),
            "execution": self._build_execution_surface(),
            "liability": self._build_liability_surface(),
            "capital": self._build_capital_surface(system_snapshot),
            "stop": self._build_stop_surface(system_snapshot),
            "classification": self._build_classification_surface(),
            "temporal": self._build_temporal_surface(),

            # ─────────────────────────────────────────
            # LAYER 2 — TRADE SIGNAL INTELLIGENCE
            # ─────────────────────────────────────────
            "layer2": self._build_layer2_surface(report),
        }

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_v7_report
# 🔎 SEARCH: INSERT INTO unified_runtime_snapshot (
# 🧩 ACTION: REPLACE — extend snapshot with Unified signal telemetry
# 📆 PATCHED: 2026-03-14 — dashboard signal wiring fix
#
# PURPOSE
# -------
# Dashboard panels read signal counts directly from unified_runtime_snapshot.
#
# The original snapshot only exported lifecycle fields.
# This patch adds the signal metrics already produced by the Unified report.
#
# SIGNALS EXPORTED
# ----------------
# timing_markets_tracked
# volatility_runners_moved
# drift_runners_tracked
# sweet_spot_runners
# candidate_count
#
# RESULT
# ------
# Dashboard becomes a pure snapshot reader.
# No JSON, no recomputation, no duplicate logic.
# ======================================================================================================

        try:

            timing_surface = report.get("timing", {})
            drift_surface = report.get("drift", {})
            sweet_surface = report.get("sweet_spot", {})
            vol_surface = report.get("volatility", {})
            layer2_surface = report.get("layer2", {})

            markets = timing_surface.get("markets", [])

            current_market_id = None
            current_market_state = None
            next_market_id = None
            delayed_market_id = None

            # --------------------------------------------------
            # lifecycle detection
            # --------------------------------------------------

            active_index = None

            for i, m in enumerate(markets):

                phase = m.get("phase")
                mid = m.get("marketId")

                if phase != "COMPLETE":

                    current_market_id = mid

                    if phase == "PRE":
                        current_market_state = "PRE"
                    elif phase == "LIVE_PHASE":
                        current_market_state = "ACTIVE"
                    else:
                        current_market_state = phase

                    active_index = i
                    break

            if active_index is not None:

                for j in range(active_index + 1, len(markets)):

                    nxt = markets[j]

                    if nxt.get("phase") != "COMPLETE":

                        next_market_id = nxt.get("marketId")

                        if next_market_id == current_market_id:
                            continue

                        break

            for m in markets:

                tto = m.get("tto_seconds")
                off_detected = m.get("off_detected")

                if tto is not None and tto < 0 and not off_detected:
                    delayed_market_id = m.get("marketId")
                    break

            # --------------------------------------------------
            # signal counts (for dashboard)
            # --------------------------------------------------

            timing_markets_tracked = len(markets)
            volatility_runners_moved = vol_surface.get("runners_moved_last_window", 0)
            drift_runners_tracked = len(drift_surface.get("runners", []))
            sweet_spot_runners = len(sweet_surface.get("runners", []))
            candidate_count = len(layer2_surface.get("candidates", []))

            # --------------------------------------------------
            # write snapshot
            # --------------------------------------------------

            from engines.config_paths import open_auto_db
            import time

            con = open_auto_db(rw=True)

            try:

                con.execute(
                    """
                    INSERT INTO unified_runtime_snapshot (
                        ts,
                        current_market_id,
                        current_market_state,
                        next_market_id,
                        delayed_market_id,
                        timing_markets_tracked,
                        volatility_runners_moved,
                        drift_runners_tracked,
                        sweet_spot_runners,
                        candidate_count
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(time.time()),
                        current_market_id,
                        current_market_state,
                        next_market_id,
                        delayed_market_id,
                        timing_markets_tracked,
                        volatility_runners_moved,
                        drift_runners_tracked,
                        sweet_spot_runners,
                        candidate_count,
                    ),
                )

                con.commit()

            finally:
                con.close()

        except Exception:
            pass

        return report

    def _build_world_surface(self) -> Dict[str, Any]:
        from datetime import datetime, timezone

        return {
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "engine_state": "CLASSIFICATION_ONLY",
            "plans_emitted": 0,
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION A — MARKET PHASE + RAW VOLATILITY
    # --------------------------------------------------------------------------------------------------

    def _build_timing_surface(self) -> Dict[str, Any]:

        from engines.config_paths import open_bets_db
        from datetime import datetime, timezone
        import sqlite3

        now = datetime.now(timezone.utc)

        if not hasattr(self, "_market_phase_state"):
            self._market_phase_state = {}

        con = open_bets_db(rw=False)
        con.row_factory = sqlite3.Row

        try:
            rows = con.execute("""
                SELECT DISTINCT marketId, event_name, marketStartTime
                FROM bets
                WHERE substr(marketStartTime,1,10)=date('now','utc')
                ORDER BY datetime(marketStartTime) ASC

            """).fetchall()
        finally:
            con.close()

        markets = []

        for r in rows:

            mid = str(r["marketId"])
            off = datetime.fromisoformat(
                r["marketStartTime"].replace("Z", "+00:00")
            )

            delta = (off - now).total_seconds()

            state = self._market_phase_state.setdefault(mid, {
                "off_detected": False,
                "off_ts": None,
                "collapse_detected": False,
                "collapse_ts": None,
            })

            # ------------------------------
            # Volatility OFF detection
            # ------------------------------
            moved = 0
            for (m, sid), ctx in getattr(self, "_route_ctx_map", {}).items():
                if m != mid:
                    continue
                px = ctx.get("px")
                prev = ctx.get("_prev_px")

                if prev is not None and px is not None:
                    if abs(px - prev) > 0:
                        moved += 1

                ctx["_prev_px"] = px

            if not state["off_detected"] and moved >= 3:
                state["off_detected"] = True
                state["off_ts"] = now

            # ------------------------------
            # Collapse detection
            # ------------------------------
            collapse_count = 0
            total = 0

            for (m, sid), ctx in getattr(self, "_route_ctx_map", {}).items():
                if m != mid:
                    continue
                total += 1
                px = ctx.get("px")
                if px and px >= 1000:
                    collapse_count += 1

            if total > 0 and collapse_count >= max(2, total // 2):
                if not state["collapse_detected"]:
                    state["collapse_detected"] = True
                    state["collapse_ts"] = now

            # ------------------------------
            # Phase logic
            # ------------------------------
            if state["collapse_detected"]:
                phase = "COMPLETE"
            elif state["off_detected"]:
                phase = "LIVE_PHASE"
            elif delta <= 300:
                phase = "LIVE_PHASE"
            else:
                phase = "PRE"

            live_duration = 0
            if state["off_detected"] and state["off_ts"]:
                live_duration = int((now - state["off_ts"]).total_seconds())

            markets.append({
                "marketId": mid,
                "event_name": r["event_name"],
                "scheduled_off": off.isoformat(),
                "tto_seconds": int(delta),
                "post_zero_elapsed": abs(int(delta)) if delta <= 0 else 0,
                "phase": phase,
                "off_detected": state["off_detected"],
                "live_duration_seconds": live_duration,
                "collapse_detected": state["collapse_detected"],
            })

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_timing_surface
# 🔎 SEARCH: markets.append({
# 🧩 ADD: cleanup structural memory for completed markets
# ======================================================================================================

            if phase == "COMPLETE":

                for key in list(self._runner_structure.keys()):
                    if key[0] == mid:
                        self._runner_structure.pop(key, None)
                        self._runner_breakouts.pop(key, None)
                        self._fav_history.pop(key, None)

                # --- NEW: clear PX memory ---
                if hasattr(self, "_runner_px_memory"):
                    for key in list(self._runner_px_memory.keys()):
                        if key[0] == mid:
                            self._runner_px_memory.pop(key, None)

                # --- NEW: clear structural candidates ---
                if hasattr(self, "_structural_candidates"):
                    for key in list(self._structural_candidates.keys()):
                        if key[0] == mid:
                            self._structural_candidates.pop(key, None)

        return {"markets": markets}

    # --------------------------------------------------------------------------------------------------
    # RAW VOLATILITY PER-TICK EXPOSURE (MONITOR-AUTHORITATIVE)
    # --------------------------------------------------------------------------------------------------

    def _build_volatility_surface(self) -> Dict[str, Any]:

        moved = 0

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            px = ctx.get("px")
            prev = ctx.get("_prev_vol_px")

            if prev is not None and px is not None:
                if abs(px - prev) > 0:
                    moved += 1

            ctx["_prev_vol_px"] = px

        if moved >= 5:
            energy = "SPIKE"
        elif moved >= 3:
            energy = "BUILDING"
        else:
            energy = "LOW"

        return {
            "runners_moved_last_window": moved,
            "impulse_detected": moved >= 3,
            "structural_energy": energy,
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION B — DRIFT SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_drift_surface(self) -> Dict[str, Any]:

        from tools.betfair_runner_trend_surface import get_runner_trend

        runners = []

        for (mid, sid), ctx in self._route_ctx_map.items():

            trend = get_runner_trend(mid, sid)
            if not trend:
                continue

            runners.append({
                "marketId": mid,
                "selectionId": sid,
                "px": ctx.get("px"),
                "delta_ticks": trend.get("ticks_moved"),
                "delta_ticks_per_min": trend.get("ticks_per_min"),
                "drift_direction": trend.get("direction"),
            })

        return {"runners": runners}

    # --------------------------------------------------------------------------------------------------
    # SECTION C — RANK / CROSSOVER SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_rank_surface(self) -> Dict[str, Any]:

        from engines.market_monitor.monitor import get_crossover_signal

        runners = []

        for (mid, sid), ctx in self._route_ctx_map.items():

            xo = get_crossover_signal(mid, sid)

            runners.append({
                "marketId": mid,
                "selectionId": sid,
                "previous_rank": xo.get("rank_prev"),
                "current_rank": xo.get("rank_now"),
                "rank_delta": xo.get("rank_delta"),
                "crossed_over": xo.get("crossed_over_recent"),
            })

        return {"runners": runners}

    # --------------------------------------------------------------------------------------------------
    # SECTION D — SWEET SPOT SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_sweet_spot_surface(self) -> Dict[str, Any]:

        runners = []

        for (mid, sid), ctx in self._route_ctx_map.items():

            px = ctx.get("px")
            if px is None:
                continue

            if 4 <= px <= 7:
                zone = "4-7"
            elif 7 < px <= 10:
                zone = "7-10"
            elif px == 12:
                zone = "12"
            elif 15 <= px <= 20:
                zone = "15-20"
            else:
                zone = "OUT"

            runners.append({
                "marketId": mid,
                "selectionId": sid,
                "px": px,
                "zone": zone,
            })

        return {"runners": runners}


    # --------------------------------------------------------------------------------------------------
    # SECTION E — EXECUTION SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_execution_surface(self) -> Dict[str, Any]:

        from tools.betfair_match_surface import get_direction_confidence

        parent_matched = 0
        child_matched = 0
        direction_conf_total = 0
        direction_conf_count = 0

        for (mid, sid), ctx in self._route_ctx_map.items():

            if ctx.get("anchor_parent_id"):
                parent_matched += 1

            # Direction confidence must be per-runner
            try:
                conf = get_direction_confidence(mid, sid)
                if conf is not None:
                    direction_conf_total += conf
                    direction_conf_count += 1
            except Exception:
                pass

        avg_conf = (
            round(direction_conf_total / direction_conf_count, 2)
            if direction_conf_count > 0
            else 0
        )

        return {
            "parent_matched_count": parent_matched,
            "child_matched_count": child_matched,
            "direction_confidence": avg_conf,
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION F — LIABILITY SURFACE (EXCHANGE TRUTH)
    # --------------------------------------------------------------------------------------------------

    def _build_liability_surface(self) -> Dict[str, Any]:

        try:
            from tools.betfair_match_surface import compute_true_market_liability
            liability = compute_true_market_liability([])
        except Exception:
            liability = 0.0

        if liability > 1000:
            imbalance = "HIGH"
        elif liability > 200:
            imbalance = "MODERATE"
        elif liability > 0:
            imbalance = "LOW"
        else:
            imbalance = "NONE"

        return {
            "worst_case_liability": liability,
            "most_exposed_runner": None,
            "directional_bias": "NEUTRAL",
            "imbalance_level": imbalance,
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION G — CAPITAL SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_capital_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:

        return {
            "total_pot": snap.get("total_pot"),
            "total_floor": snap.get("total_floor"),
            "total_reserved": snap.get("total_reserved"),
            "headroom": snap.get("headroom"),
            "utilisation_pct": snap.get("utilisation_pct"),
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION H — STOP SYSTEM (UNIFIED SNAPSHOT AUTHORITY)
    # --------------------------------------------------------------------------------------------------

    def _build_stop_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:

        from engines.stoploss_engine import StopLossEngine, ParentState
        from engines.price_math import calculate_tick_distance
        import time

        if not hasattr(self, "_shadow_stop_engine"):
            self._shadow_stop_engine = StopLossEngine()

        if not hasattr(self, "_shadow_stop_stats"):
            self._shadow_stop_stats = {
                "total_trades": 0,
                "total_stops": 0,
                "by_engine": {},
                "by_runner": {},
                "recent": [],
            }

        stats = self._shadow_stop_stats

        stop_plans = []

        now = time.time()

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            parent_id = ctx.get("anchor_parent_id")
            entry_odds = ctx.get("anchor_entry_odds")
            entry_side = ctx.get("legacy_entry_side")
            entry_stake = ctx.get("anchor_entry_stake")
            px = ctx.get("px")

            if not parent_id or not entry_odds or not px:
                continue

            parent = ParentState(
                parent_id=parent_id,
                entry_side=entry_side,
                entry_odds=entry_odds,
                entry_stake=entry_stake,
            )

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 ANCHOR: inside _build_stop_surface(), after ParentState creation
# 🧩 ACTION: Evaluate StopLossEngine to generate stop event
# 📆 PATCHED: 2026-XX-XX — activate stoploss engine evaluation
#
# PURPOSE
# -------
# The StopLossEngine evaluates whether a parent trade has reached its stop
# threshold based on current market price.
#
# Without this call the variable `event` is undefined and the entire
# stoploss execution block never runs.
#
# CONTRACT
# --------
# Input
#   parent      → ParentState object
#   current_px  → current market price
#
# Output
#   event → None or dict containing stop classification
#
# If event != None
#   Unified emits STOPLOSS parent plan.
# ======================================================================================================

            event = self._shadow_stop_engine.evaluate(
                parent=parent,
                current_px=float(px)
            )

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 ANCHOR: inside _build_stop_surface() immediately after stop engine evaluation
# 🧩 ACTION: emit stoploss parent + cancel hedge child on match
# 📆 PATCHED: stoploss baseline (final)
# ======================================================================================================

            if event:

                # --------------------------------------------------
                # Emit STOPLOSS parent
                # --------------------------------------------------

                stop_plans.append({
                    "enter": True,
                    "engine": ctx.get("anchor_engine"),
                    "bet_type": "STOPLOSS",
                    "role": "PARENT",
                    "exit_kind": "STOPLOSS",
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": (
                        "BACK->LAY"
                        if parent.entry_side == "BACK"
                        else "LAY->BACK"
                    ),
                    "px": px,
                    "size": parent.entry_stake,
                    "why": "unified_global_stoploss",
                })

                # --------------------------------------------------
                # Register stoploss watch (for child cancellation)
                # --------------------------------------------------

                if not hasattr(self, "_stoploss_watch"):
                    self._stoploss_watch = {}

                self._stoploss_watch[(mid, sid)] = {
                    "parent_id": parent.parent_id,
                    "child_cancelled": False,
                }

                # --------------------------------------------------
                # Statistics
                # --------------------------------------------------

                stats["total_stops"] += 1

                engine = ctx.get("anchor_engine") or "UNKNOWN"
                runner_key = f"{mid}:{sid}"

                stats["by_engine"][engine] = stats["by_engine"].get(engine, 0) + 1
                stats["by_runner"][runner_key] = stats["by_runner"].get(runner_key, 0) + 1

                stats["recent"].append({
                    "ts": now,
                    "engine": engine,
                    "marketId": mid,
                    "selectionId": sid,
                    "classification": event.get("classification"),
                })

                stats["recent"] = stats["recent"][-10:]

        # --------------------------------------------------
        # Stoploss match monitor → cancel hedge child
        # --------------------------------------------------

        if hasattr(self, "_stoploss_watch"):

            for (mid, sid), state in list(self._stoploss_watch.items()):

                if state["child_cancelled"]:
                    continue

                rctx = self._route_ctx_map.get((mid, sid))
                if not rctx:
                    continue

                if rctx.get("entry_status") == "MATCHED":

                    try:
                        from engines.live.live_router import cancel_child_for_parent
                        cancel_child_for_parent(state["parent_id"])
                        state["child_cancelled"] = True
                    except Exception:
                        pass

        stop_rate = (
            round(stats["total_stops"] / stats["total_trades"], 2)
            if stats["total_trades"] > 0 else 0
        )

        return {
            "total_trades_checked": stats["total_trades"],
            "total_stops_triggered": stats["total_stops"],
            "stop_rate": stop_rate,
            "by_engine": stats["by_engine"],
            "by_runner": stats["by_runner"],
            "recent_stop_events": stats["recent"],
            "plans": stop_plans,
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION I — CLASSIFICATION SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_classification_surface(self) -> Dict[str, Any]:

        buckets = {
            "PRIMED": 0,
            "BUILDING": 0,
            "CONTEXT_ACTIVE": 0,
            "INACTIVE": 0,
        }

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            px = ctx.get("px")
            direction = ctx.get("direction")

            if direction and px:
                buckets["PRIMED"] += 1
            elif px:
                buckets["CONTEXT_ACTIVE"] += 1
            else:
                buckets["INACTIVE"] += 1

        return buckets

    # --------------------------------------------------------------------------------------------------
    # SECTION J — TEMPORAL SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_temporal_surface(self) -> Dict[str, Any]:

        if not hasattr(self, "_trade_memory"):
            self._trade_memory = {}

        trades = []

        now = time.time()

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            pid = ctx.get("anchor_parent_id")
            if not pid:
                continue

            key = (mid, sid, pid)

            mem = self._trade_memory.setdefault(key, {
                "entry_px": ctx.get("anchor_entry_odds"),
                "entry_ts": now,
                "mfe": 0,
                "mae": 0,
            })

            entry_px = mem["entry_px"]
            current_px = ctx.get("px")

            if current_px and entry_px:
                delta = current_px - entry_px
                mem["mfe"] = max(mem["mfe"], delta)
                mem["mae"] = min(mem["mae"], delta)

            duration = int(now - mem["entry_ts"])

            trades.append({
                "marketId": mid,
                "selectionId": sid,
                "entry_odds": entry_px,
                "current_odds": current_px,
                "delta_ticks": None,
                "duration_seconds": duration,
                "MFE": mem["mfe"],
                "MAE": mem["mae"],
                "status": "OPEN",
            })

        return {"active_parents": trades}

    # --------------------------------------------------------------------------------------------------
    # SIGNAL SUMMARY (BUS Y TABLE)
    # --------------------------------------------------------------------------------------------------

    def _build_signal_summary(self, report: Dict[str, Any]) -> Dict[str, Any]:
        """
        Converts full report into compact Y-style signal table for BUS.
        Phase 0: empty counts.
        """

        return {
            "timing_active": 0,
            "drift_count": 0,
            "crossover_count": 0,
            "impulse_count": 0,
            "sweet_spot_count": 0,
            "classification_primed": 0,
            "system_imbalance": 0,
            "corrective_pressure": 0,
        }

    # ================================================================================================
    # STRUCTURAL RUNNER MEMORY
    # ================================================================================================

    def _update_runner_structure(self):

        if not hasattr(self, "_runner_structure"):
            self._runner_structure = {}

        for (mid, sid), ctx in self._route_ctx_map.items():

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_update_runner_structure
# 🔎 ANCHOR: immediately after "for (mid, sid), ctx in self._route_ctx_map.items():"
# 🧩 ACTION: ADD — restore persistent PX memory (runner history)
# 📆 PATCHED: 2026-03-09 — restore Unified runner memory after BUS world refactor
#
# ROOT CAUSE
# ----------
# When Unified was refactored to consume BUS ctx_map, runner PX memory was lost.
# BUS recreates ctx every tick, so fields like _prev_px and _prev_vol_px no longer persist.
#
# RESULT
# ------
# All structural surfaces became empty:
#   • drift surface
#   • volatility surface
#   • breakout detection
#   • candidate pools
#
# FIX
# ---
# Persist PX memory inside Unified instead of ctx.
# This restores:
#   • drift calculations
#   • volatility detection
#   • breakout tracking
#   • candidate generation
#
# PERFORMANCE
# -----------
# O(runners) dictionary lookups only.
# No DB calls.
# No route rebuild.
# ======================================================================================================

            mem_px = getattr(self, "_runner_px_memory", None)
            if mem_px is None:
                self._runner_px_memory = {}
                mem_px = self._runner_px_memory

            key = (mid, sid)

            px = ctx.get("px")
            if px is None:
                continue

            try:
                px = float(px)
            except Exception:
                continue

            prev = mem_px.get(key)

            # restore previous px memory into ctx for existing surfaces
            if prev is not None:
                ctx["_prev_px"] = prev
                ctx["_prev_vol_px"] = prev

            # update stored memory
            mem_px[key] = px

            px = ctx.get("px")
            if px is None:
                continue

            key = (mid, sid)

            mem = self._runner_structure.setdefault(key, {
                "anchor_px": px,
                "high_seen": px,
                "low_seen": px,
                "last_px": px,
                "trend": None,
            })

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_update_runner_structure
# 🔎 SEARCH: if px > mem["high_seen"]:
# 🧩 FIX: breakout detection must occur BEFORE updating highs/lows
# ======================================================================================================

            br = self._runner_breakouts.setdefault(key, {"up": 0, "down": 0})

            if px >= mem["high_seen"]:
                br["up"] += 1

            if px <= mem["low_seen"]:
                br["down"] += 1

            if px > mem["high_seen"]:
                mem["high_seen"] = px

            if px < mem["low_seen"]:
                mem["low_seen"] = px

            prev = mem["last_px"]

            if prev is not None:

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_update_runner_structure
# 🔎 SEARCH: if prev is not None:
# 🧩 ADD: trend persistence tracking
# PURPOSE:
# Measures how long a drift/steam direction persists.
# ======================================================================================================

                trend_mem = mem.setdefault("trend_mem", {"dir": None, "duration": 0})

                direction = "DRIFT" if px > prev else "STEAM" if px < prev else None

                if direction == trend_mem["dir"]:
                    trend_mem["duration"] += 1
                else:
                    trend_mem["dir"] = direction
                    trend_mem["duration"] = 1

                if px > prev:
                    mem["trend"] = "DRIFT"

                elif px < prev:
                    mem["trend"] = "STEAM"

            mem["last_px"] = px

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_update_runner_structure
# 🔎 SEARCH: mem["last_px"] = px
# 🧩 ADD: favourite transition detection
# PURPOSE:
# Detect when runners move into or out of favourite leadership.
# ======================================================================================================

            fav_state = ctx.get("is_fav")

            hist = self._fav_history.setdefault(key, {"prev": fav_state, "flips": 0})

            if hist["prev"] is not None and fav_state != hist["prev"]:
                hist["flips"] += 1

            hist["prev"] = fav_state

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_update_runner_structure
# 🔎 SEARCH: mem = self._runner_structure.setdefault
# 🧩 ADD: anchor distance tracking
# PURPOSE:
# Enables anchor-based graph interpretation of price movement.
# ======================================================================================================

            mem["distance_from_anchor"] = px - mem["anchor_px"]
            mem["distance_from_high"] = px - mem["high_seen"]
            mem["distance_from_low"] = px - mem["low_seen"]


    # ================================================================================================
    # BUILD STRUCTURAL POOLS
    # ================================================================================================

    # ======================================================================================================
    # 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_candidate_pools
    # 🔎 SEARCH: def _build_candidate_pools(self, report):
    # 🧩 ACTION: REPLACE ENTIRE METHOD
    # 📆 PATCHED: 2026-03-13 — Drift strength + trend validation
    #
    # PURPOSE
    # -------
    # Introduce proper drift classification and trend validation.
    #
    # NEW SIGNAL RULES
    # ----------------
    # Drift = short-term movement strength
    # Trend = structural movement from anchor
    #
    # Drift must align with trend to create a valid signal.
    #
    # Drift strength scale:
    #   <0.1   → ignore
    #   <0.25  → 1
    #   <0.5   → 2
    #   <1     → 3
    #   <2     → 4
    #   >=2    → 5
    # ======================================================================================================

    def _build_candidate_pools(self, report):

        pools = {
            "crossover": [],
            "drift": [],
            "sweet": [],
            "breakout": [],
            "direction": [],
            "volatility": [],
            "momentum": [],
            "favourite": [],
        }

        drift_rows = report.get("drift", {}).get("runners", [])
        rank_rows  = report.get("rank", {}).get("runners", [])
        sweet_rows = report.get("sweet_spot", {}).get("runners", [])
        layer2     = report.get("layer2", {})

        # --------------------------------------------------
        # CROSSOVERS
        # --------------------------------------------------

        for r in rank_rows:

            if r.get("crossed_over"):

                pools["crossover"].append({
                    "marketId": r["marketId"],
                    "selectionId": r["selectionId"],
                    "score": abs(r.get("rank_delta", 0)) + 5
                })

        # --------------------------------------------------
        # DRIFT (validated against structural trend)
        # --------------------------------------------------

        for r in drift_rows:

            mid = r["marketId"]
            sid = r["selectionId"]

            speed = abs(r.get("delta_ticks_per_min") or 0)

            if speed < 0.1:
                continue

            # Drift strength classification
            if speed < 0.25:
                drift_strength = 1
            elif speed < 0.5:
                drift_strength = 2
            elif speed < 1:
                drift_strength = 3
            elif speed < 2:
                drift_strength = 4
            else:
                drift_strength = 5

            mem = self._runner_structure.get((mid, sid))
            if not mem:
                continue

            dist = mem.get("distance_from_anchor")

            if dist is None:
                continue

            drift_dir = "DRIFT" if r.get("delta_ticks_per_min", 0) > 0 else "STEAM"
            trend_dir = "DRIFT" if dist > 0 else "STEAM"

            # Reject conflicting signals
            if drift_dir != trend_dir:
                continue

            pools["drift"].append({
                "marketId": mid,
                "selectionId": sid,
                "score": drift_strength
            })

        # --------------------------------------------------
        # SWEET SPOT
        # --------------------------------------------------

        for r in sweet_rows:

            zone = r.get("zone")

            if zone == "4-7":
                score = 3
            elif zone == "7-10":
                score = 2
            else:
                continue

            pools["sweet"].append({
                "marketId": r["marketId"],
                "selectionId": r["selectionId"],
                "score": score
            })

        # --------------------------------------------------
        # STRUCTURAL BREAKOUT
        # --------------------------------------------------

        for key, mem in getattr(self, "_runner_structure", {}).items():

            mid, sid = key
            px = mem.get("last_px")

            if px is None:
                continue

            if px >= mem.get("high_seen"):
                pools["breakout"].append({
                    "marketId": mid,
                    "selectionId": sid,
                    "score": 4
                })

            elif px <= mem.get("low_seen"):
                pools["breakout"].append({
                    "marketId": mid,
                    "selectionId": sid,
                    "score": 4
                })

            trend_mem = mem.get("trend_mem")

            if trend_mem and trend_mem["duration"] >= 3:

                pools["momentum"].append({
                    "marketId": mid,
                    "selectionId": sid,
                    "score": trend_mem["duration"]
                })

            fav = self._fav_history.get(key)

            if fav and fav.get("flips", 0) > 0:

                pools["favourite"].append({
                    "marketId": mid,
                    "selectionId": sid,
                    "score": fav["flips"] * 2
                })

        # --------------------------------------------------
        # DIRECTION ENGINE
        # --------------------------------------------------

        for row in layer2.get("direction_calls", []):

            pools["direction"].append({
                "marketId": row["marketId"],
                "selectionId": row["selectionId"],
                "score": row.get("win_prob", 0)
            })

        return pools
    # ================================================================================================
    # FINAL CANDIDATE SELECTION
    # ================================================================================================

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: def _select_exploratory_candidates(self, report):
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-09 — Unified candidate hierarchy + structural loop
#
# PURPOSE
# -------
# Correct candidate selection doctrine.
#
# STRUCTURAL SIGNAL HIERARCHY
# ---------------------------
# 1️⃣ crossover
# 2️⃣ direction
# 3️⃣ breakout
# 4️⃣ drift
#
# OVERLAYS (NOT candidate sources)
# --------------------------------
# sweet_spot
# momentum
# favourite
#
# STRUCTURAL LOOP
# ---------------
# Once a runner becomes a candidate it remains tracked
# for the lifetime of the market unless the market completes.
#
# RULES
# -----
# • Max exploratory candidates = 5
# • Structural signals only create candidates
# • Overlay signals only influence later scoring
# • Candidates persist via _structural_candidates
#
# PERFORMANCE
# -----------
# O(runners) per tick.
# No DB access.
# ======================================================================================================

    def _select_exploratory_candidates(self, report):

        # --------------------------------------------------
        # Ensure structural candidate memory exists
        # --------------------------------------------------
        if not hasattr(self, "_structural_candidates"):
            self._structural_candidates = {}

        self._update_runner_structure()

        pools = self._build_candidate_pools(report)

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: def _select_exploratory_candidates
# 🧩 ACTION: PRIORITISE BUS buckets
# 📆 PATCHED: 2026-03-12 — Unified candidate priority by route buckets
#
# PURPOSE:
# - Always trade closest markets first
# - Prevent far markets starving near markets
# - Allow structural trades >60m
# ======================================================================================================

        buckets = self._get_bus_buckets()

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: buckets = self._get_bus_buckets()
# 🧩 ACTION: ADD candidate source list
# 📆 PATCHED: 2026-03-12 — fix candidate source reference
#
# PURPOSE:
# Candidate ordering must operate on the structural candidate list
# produced by Layer 2 intelligence.
# ======================================================================================================

        candidates = report.get("layer2", {}).get("candidates", [])

        priority_order = [
            "5m",
            "10m",
            "20m",
            "40m",
            "60m",
            "long",
        ]

        ordered = []

        for bucket in priority_order:

            for key in buckets.get(bucket, []):

                mid, sid = key

                for r in candidates:

                    if r["marketId"] == mid and r["selectionId"] == sid:
                        ordered.append(r)

        # remove duplicates while preserving order
        seen = set()
        final = []

        for r in ordered:

            k = (r["marketId"], r["selectionId"])

            if k in seen:
                continue

            final.append(r)
            seen.add(k)

            if len(final) >= 5:
                break

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_select_exploratory_candidates
# 🔎 SEARCH: return final
# 🧩 ACTION: configurable long-bucket expansion
# 📆 PATCHED: 2026-03-16 — exploratory capacity + long-market toggle
#
# PURPOSE
# -------
# Allow exploratory capacity to include additional long-distance markets.
#
# DESIGN
# ------
# BASE_EXPLORATORY = 5
# LONG_BUCKET_EXTRA = configurable toggle
#
# RESULT
# ------
# total exploratory candidates =
#     BASE_EXPLORATORY + LONG_BUCKET_EXTRA
#
# This allows early anchors to exist without interfering with the core
# top-ranked candidates.
# ======================================================================================================

        BASE_EXPLORATORY = 5
        LONG_BUCKET_EXTRA = 1   # ← toggle here

        # --------------------------------------------------
        # Core top candidates
        # --------------------------------------------------

        core = final[:BASE_EXPLORATORY]

        # --------------------------------------------------
        # Long bucket candidates
        # --------------------------------------------------

        long_candidates = [
            r for r in candidates
            if (r["marketId"], r["selectionId"]) in buckets.get("long", [])
        ]

        long_candidates.sort(key=lambda x: x["score"], reverse=True)

        extra = []

        for r in long_candidates:

            k = (r["marketId"], r["selectionId"])

            if k in seen:
                continue

            extra.append(r)

            if len(extra) >= LONG_BUCKET_EXTRA:
                break

        return core + extra


    # --------------------------------------------------------------------------------------------------
    # LAYER 2 — TRADE SIGNAL INTELLIGENCE
    # --------------------------------------------------------------------------------------------------

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: def _build_layer2_surface(self)
# 🧩 ACTION: REPLACE FUNCTION SIGNATURE
# 📆 PATCHED: 2026-03-16 — fix undefined report reference
#
# ROOT CAUSE
# ----------
# _build_layer2_surface referenced the variable `report`
# but the function did not receive it as an argument.
#
# RESULT
# ------
# tick() crashes with:
#     tick_error:name 'report' is not defined
#
# FIX
# ---
# Pass report into the function so timing surfaces can be read.
# ======================================================================================================

    def _build_layer2_surface(self, report) -> Dict[str, Any]:

        drift = self._build_drift_surface().get("runners", [])
        sweet = self._build_sweet_spot_surface().get("runners", [])
        rank  = self._build_rank_surface().get("runners", [])

# ------------------------------------------------------------------
# Build fast lookup maps (required for O(1) access)
# ------------------------------------------------------------------

        sweet_map = {(r["marketId"], r["selectionId"]): r for r in sweet}
        rank_map  = {(r["marketId"], r["selectionId"]): r for r in rank}

        candidates = []

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_layer2_surface
# 🔎 SEARCH: candidates = []
# 🧩 ADD: confidence scoring
# PURPOSE:
# Produces a unified confidence metric combining bias, form, and direction engines.
# ======================================================================================================

        confidence_rows = []

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            try:
                bias_out = compute_bias(ctx, None)
                form_val = get_form_adjustment(mid, int(sid))
                decision = compute_msc_decision(ctx)

                conf = (
                    abs(bias_out.conf) +
                    abs(form_val - 1.0) * 5 +
                    decision.get("win_prob", 0)
                )

                confidence_rows.append({
                    "marketId": mid,
                    "selectionId": sid,
                    "confidence": round(conf, 3)
                })

            except Exception:
                continue

        for r in drift:

            mid = r["marketId"]
            sid = r["selectionId"]

            score = 0

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_layer2_surface
# 🔎 SEARCH: score = 0
# 🧩 ADD: bucket priority weighting
# 📆 PATCHED: 2026-03-16 — market proximity scoring
#
# PURPOSE
# -------
# Replace old window gating with score weighting.
#
# Closer races receive higher priority in candidate ranking.
# ======================================================================================================

            # --------------------------------------------------
            # Bucket priority weighting
            # --------------------------------------------------

            bucket_score = 0

            market = next(
                (m for m in report.get("timing", {}).get("markets", [])
                 if m.get("marketId") == mid),
                None
            )

            if market:
                tto = market.get("tto_seconds")

                if tto is not None:

                    if tto <= 300:
                        bucket_score = 4      # 5m

                    elif tto <= 600:
                        bucket_score = 3      # 10m

                    elif tto <= 1200:
                        bucket_score = 5      # 20m sweet spot

                    elif tto <= 2400:
                        bucket_score = 2      # 40m

                    elif tto <= 3600:
                        bucket_score = 1      # 60m

                    else:
                        bucket_score = 0.5    # long

            score += bucket_score

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: sweet_row = next(
# 🧩 ACTION: REPLACE — use sweet_map lookup
# ======================================================================================================

            sweet_row = sweet_map.get((mid, sid))

            if sweet_row:
                zone = sweet_row.get("zone")
                if zone == "4-7":
                    score += 2
                elif zone == "7-10":
                    score += 1.5
                elif zone == "15-20":
                    score += 1

            dv = r.get("delta_ticks_per_min")
            if dv:
                score += min(abs(dv) * 4, 4)

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: rank_row = next(
# 🧩 ACTION: REPLACE — use rank_map lookup
# ======================================================================================================

            rank_row = rank_map.get((mid, sid))

            if rank_row and rank_row.get("rank_delta"):
                score += 2

            if score > 0:
                candidates.append({
                    "marketId": r["marketId"],
                    "selectionId": r["selectionId"],
                    "px": r.get("px"),
                    "score": round(score, 2),
                })

        candidates.sort(key=lambda x: x["score"], reverse=True)

        sweet_summary = {
            "4-7": sum(1 for s in sweet if s["zone"] == "4-7"),
            "7-10": sum(1 for s in sweet if s["zone"] == "7-10"),
            "15-20": sum(1 for s in sweet if s["zone"] == "15-20"),
            "OUT": sum(1 for s in sweet if s["zone"] == "OUT"),
        }

        accel = [r for r in drift if r.get("delta_ticks_per_min")]
        fast = [r for r in accel if abs(r.get("delta_ticks_per_min", 0)) > 0.5]

        crossovers = [r for r in rank if r.get("rank_delta")]

        # -----------------------------
        # FORM
        # -----------------------------
        form_rows = []

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            try:
                form_val = get_form_adjustment(
                    marketId=mid,
                    selectionId=int(sid)
                )
            except Exception:
                form_val = 1.0

            form_rows.append({
                "marketId": mid,
                "selectionId": sid,
                "form": round(form_val, 3),
            })

        form_rows.sort(key=lambda x: x["form"], reverse=True)

        strong_form = [r for r in form_rows if r["form"] >= 1.05][:5]
        weak_form   = [r for r in form_rows if r["form"] <= 0.90][:5]

        # -----------------------------
        # BIAS ENGINE
        # -----------------------------
        bias_rows = []

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            try:
                bias_out = compute_bias(ctx, None)
                if bias_out.dir != "FLAT":
                    bias_rows.append({
                        "marketId": mid,
                        "selectionId": sid,
                        "dir": bias_out.dir,
                        "conf": round(bias_out.conf, 2),
                    })
            except Exception:
                continue

        # -----------------------------
        # DIRECTION ENGINE
        # -----------------------------
        direction_rows = []

        for (mid, sid), ctx in getattr(self, "_route_ctx_map", {}).items():

            try:
                decision = compute_msc_decision({
                    **ctx,
                    "marketId": mid,
                    "selectionId": sid,
                })

                if decision.get("direction"):
                    direction_rows.append({
                        "marketId": mid,
                        "selectionId": sid,
                        "dir": decision["direction"],
                        "mode": decision["mode"],
                        "win_prob": round(decision["win_prob"], 2),
                    })
            except Exception:
                continue

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py:_build_layer2_surface
# 🔎 SEARCH: return {
# 🧩 FIX: expose confidence rows for debugging
# ======================================================================================================

        confidence_rows.sort(key=lambda x: x["confidence"], reverse=True)

        return {
            "candidates": candidates,
            "sweet_summary": sweet_summary,
            "fast_movers": fast[:5],
            "crossovers": crossovers[:5],
            "form_strong": strong_form,
            "form_weak": weak_form,
            "bias_calls": bias_rows[:5],
            "direction_calls": direction_rows[:5],
            "confidence_rows": confidence_rows[:5],
        }

    # --------------------------------------------------------------------------------------------------
    # REPORT PRINTER (V7 BLOCK — SNAPSHOT DRIVEN, FULL VISUAL)
    # --------------------------------------------------------------------------------------------------

    def print_v7_report(self, report: Dict[str, Any]) -> None:

        RESET = "\033[0m"
        RED   = "\033[91m"
        BOLD  = "\033[1m"

        world  = report.get("world", {})
        timing = report.get("timing", {})
        drift  = report.get("drift", {})
        rank   = report.get("rank", {})
        sweet  = report.get("sweet_spot", {})
        vol    = report.get("volatility", {})
        exec_s = report.get("execution", {})
        liab   = report.get("liability", {})
        cap    = report.get("capital", {})
        stop   = report.get("stop", {})
        cls    = report.get("classification", {})
        temp   = report.get("temporal", {})

        print()
        print("══════════════════════════════════════════════════════════════")
        print(f"{BOLD}{RED}UNIFIED ENGINE V7 REPORT{RESET}")
        print("══════════════════════════════════════════════════════════════")

        # WORLD
        print("\n[WORLD]")
        print(f"  Timestamp UTC   : {world.get('timestamp_utc')}")
        print(f"  Engine State    : {world.get('engine_state')}")
        print(f"  Plans Emitted   : {world.get('plans_emitted')}")

        # TIMING
        print("\n[TIMING]")
        for m in timing.get("markets", []):
            print(
                f"  {m.get('event_name')} | "
                f"TTO={m.get('tto_seconds')}s | "
                f"Phase={m.get('phase')} | "
                f"OffDetected={m.get('off_detected')} | "
                f"LiveDur={m.get('live_duration_seconds')}s | "
                f"Collapse={m.get('collapse_detected')}"
            )

        # DRIFT
        print("\n[DRIFT SURFACE]")
        for r in drift.get("runners", []):
            print(
                f"  {r['marketId']}:{r['selectionId']} | "
                f"Δ={r.get('delta_ticks')} | "
                f"Δ/min={r.get('delta_ticks_per_min')} | "
                f"Dir={r.get('drift_direction')}"
            )

        # RANK
        print("\n[RANK / CROSSOVER]")
        for r in rank.get("runners", []):
            print(
                f"  {r['marketId']}:{r['selectionId']} | "
                f"Δrank={r.get('rank_delta')} | "
                f"Crossed={r.get('crossed_over')}"
            )

        # SWEET SPOT
        print("\n[SWEET SPOT]")
        for r in sweet.get("runners", []):
            print(
                f"  {r['marketId']}:{r['selectionId']} | "
                f"Px={r.get('px')} | "
                f"Zone={r.get('zone')}"
            )

        # VOLATILITY
        print("\n[VOLATILITY]")
        print(f"  Runners Moved     : {vol.get('runners_moved_last_window')}")
        print(f"  Impulse Detected  : {vol.get('impulse_detected')}")
        print(f"  Structural Energy : {vol.get('structural_energy')}")

        # EXECUTION
        print("\n[EXECUTION]")
        print(f"  Parents Matched    : {exec_s.get('parent_matched_count')}")
        print(f"  Children Matched   : {exec_s.get('child_matched_count')}")
        print(f"  Direction Conf Avg : {exec_s.get('direction_confidence')}")

        # LIABILITY
        print("\n[LIABILITY]")
        print(f"  Worst Case Exposure : {liab.get('worst_case_liability')}")
        print(f"  Imbalance Level     : {liab.get('imbalance_level')}")
        print(f"  Directional Bias    : {liab.get('directional_bias')}")

        # CAPITAL
        print("\n[CAPITAL]")
        print(f"  Pot          : {cap.get('total_pot')}")
        print(f"  Floor        : {cap.get('total_floor')}")
        print(f"  Reserved     : {cap.get('total_reserved')}")
        print(f"  Headroom     : {cap.get('headroom')}")
        print(f"  Utilisation% : {cap.get('utilisation_pct')}")

        # STOP
        print("\n[STOP SURFACE]")
        print(f"  Trades Checked : {stop.get('total_trades_checked')}")
        print(f"  Stops Triggered: {stop.get('total_stops_triggered')}")
        print(f"  Stop Rate      : {stop.get('stop_rate')}")

        if stop.get("by_engine"):
            print("  By Engine:")
            for k, v in stop.get("by_engine", {}).items():
                print(f"    {k}: {v}")

        if stop.get("recent_stop_events"):
            print("  Recent Stops:")
            for e in stop.get("recent_stop_events", []):
                print(
                    f"    {e.get('engine')} "
                    f"{e.get('marketId')}:{e.get('selectionId')} "
                    f"{e.get('classification')}"
                )

        # CLASSIFICATION
        print("\n[CLASSIFICATION BUCKETS]")
        for k, v in cls.items():
            print(f"  {k}: {v}")

        # TEMPORAL
        print("\n[TEMPORAL TRADE SURFACE]")
        for t in temp.get("active_parents", []):
            print(
                f"  {t['marketId']}:{t['selectionId']} | "
                f"Entry={t.get('entry_odds')} | "
                f"Current={t.get('current_odds')} | "
                f"Dur={t.get('duration_seconds')}s | "
                f"MFE={t.get('MFE')} | "
                f"MAE={t.get('MAE')}"
            )

        # ─────────────────────────────────────────
        # LAYER 2 — TRADE SIGNAL INTELLIGENCE
        # ─────────────────────────────────────────

        layer2 = report.get("layer2", {})

        print("\n══════════════════════════════════════════════════════════════")
        print("LAYER 2 — TRADE SIGNAL INTELLIGENCE")
        print("══════════════════════════════════════════════════════════════")

        print("\n[TRADE CANDIDATES — RANKED]")
        for c in layer2.get("candidates", []):
            print(
                f"  {c['marketId']}:{c['selectionId']} | "
                f"Px={c.get('px')} | "
                f"Score={c.get('score')}"
            )

        print("\n[SWEET SPOT SUMMARY]")
        for k, v in layer2.get("sweet_summary", {}).items():
            print(f"  {k}: {v}")

        print("\n[FAST DRIFT MOVERS]")
        for r in layer2.get("fast_movers", []):
            print(
                f"  {r['marketId']}:{r['selectionId']} | "
                f"Δ/min={r.get('delta_ticks_per_min')}"
            )

        print("\n[RANK CROSSOVERS]")
        for r in layer2.get("crossovers", []):
            print(
                f"  {r['marketId']}:{r['selectionId']} | "
                f"Δrank={r.get('rank_delta')}"
            )

        print("\n[FORM ENGINE]")
        print("  Top Form Runners:")
        for r in layer2.get("form_strong", []):
            print(
                f"    {r['marketId']}:{r['selectionId']} | "
                f"Form={r.get('form')}"
            )

        print("  Weak Form Runners:")
        for r in layer2.get("form_weak", []):
            print(
                f"    {r['marketId']}:{r['selectionId']} | "
                f"Form={r.get('form')}"
            )

        # --------------------------------------------------
        # BIAS ENGINE
        # --------------------------------------------------
        print("\n[BIAS ENGINE]")
        for r in layer2.get("bias_calls", []):
            print(
                f"  {r['marketId']}:{r['selectionId']} | "
                f"Dir={r.get('dir')} | "
                f"Conf={r.get('conf')}"
            )

        # --------------------------------------------------
        # DIRECTION ENGINE
        # --------------------------------------------------
        print("\n[DIRECTION ENGINE]")
        for r in layer2.get("direction_calls", []):
            print(
                f"  {r['marketId']}:{r['selectionId']} | "
                f"Dir={r.get('dir')} | "
                f"Mode={r.get('mode')} | "
                f"WinProb={r.get('win_prob')}"
            )

        print("\n══════════════════════════════════════════════════════════════\n")

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🧩 ACTION: ADD — Unified Report Loop (Observability Only)
# 📆 PATCHED: 2026-04-XX — Star loop reporter
#
# PURPOSE:
# - Periodically print latest unified snapshot
# - Pure read-only
# - No engine mutation
# - Mirrors BankState reporter pattern
# ======================================================================================================

import threading
import time

_UNIFIED_REPORT_THREAD = None


def _unified_report_loop(interval_s: int = 5):
    """
    Periodically prints the formatted V7 Unified Report.
    Uses engine report builder — NOT raw DB dump.
    """

    # ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: engine = UnifiedEngine()
# 🧩 ACTION: REPLACE reporter world source
# 📆 PATCHED: 2026-03-12 — reporter reads BUS world
#
# PURPOSE
# -------
# The reporter must inspect the live BUS execution world rather than creating
# a new UnifiedEngine instance.
#
# RESULT
# ------
# Reports now reflect the actual running engine state.
# ======================================================================================================

    from engines.bus.bus import BUS

    engine = BUS.engines.get("MSC_UNIFIED")

    report = engine._build_v7_report(
        ctx={
            "_route_ctx_map": BUS.get_runner_ctx_snapshot(),
            "_route_snapshot": BUS.get_route_snapshot(),
        },
        tick_delta=None,
    )

    time.sleep(max(1, int(interval_s)))


def start_unified_reporter(interval_s: int = 5):
    """
    Safe singleton starter.
    """
    global _UNIFIED_REPORT_THREAD

    try:
        if _UNIFIED_REPORT_THREAD and _UNIFIED_REPORT_THREAD.is_alive():
            return
    except Exception:
        pass

    t = threading.Thread(
        target=_unified_report_loop,
        args=(interval_s,),
        name="UnifiedReporter",
        daemon=True,
    )
    _UNIFIED_REPORT_THREAD = t
    t.start()

    print(f"[UNIFIED] reporter started (interval={interval_s}s)")

# ======================================================================================================
# 📍 STANDALONE RUNNER
# 🧩 PURPOSE:
# - Allow unified engine to run independently
# - Prints V7 report every 5 seconds
# - No BUS required
# ======================================================================================================

if __name__ == "__main__":

    print("\n[UNIFIED] Standalone mode starting...\n")

    engine = UnifiedEngine()

    while True:
        try:
            # 🔴 LOAD WORLD SNAPSHOT FIRST
            engine._route_ctx_map = engine._read_route_world()

            report = engine._build_v7_report(
                ctx={"_route_ctx_map": engine._route_ctx_map},
                tick_delta=None
            )

            engine.print_v7_report(report)

        except Exception as e:
            print(f"[UNIFIED][ERR] standalone failed: {e}")

        time.sleep(5)