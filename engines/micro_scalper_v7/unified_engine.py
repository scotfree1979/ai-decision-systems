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

        # Market anchor storage
        self._market_anchor_px = {}      # {marketId: {selectionId: px}}
        self._parent_anchor_px = {}      # {marketId: {selectionId: px}}
        self._parent_anchor_ts = {}      # {marketId: {selectionId: ts}}

        # Phase clock cache
        self._daily_markets = {}         # {marketId: {...}}


    # --------------------------------------------------------------------------------------------------
    # PUBLIC ENTRYPOINT
    # --------------------------------------------------------------------------------------------------

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

        layer2     = report.get("layer2", {})
        timing     = report.get("timing", {})
        volatility = report.get("volatility", {})
        liability  = report.get("liability", {})

        plans = []

        # ensure memory containers exist
        if not hasattr(self, "_emitted_children"):
            self._emitted_children = set()

        if not hasattr(self, "_inplay_markets"):
            self._inplay_markets = set()

        # ------------------------------------------------------------------
        # 2️⃣ PRE-OFF EXPLORATORY (TOP RANKED)
        # ------------------------------------------------------------------

        candidates = layer2.get("candidates", [])[:15]

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/unified_engine.py
# 🔎 SEARCH: # 2️⃣ PRE-OFF EXPLORATORY (TOP RANKED)
# 🧩 ACTION: ADD time-to-off entry gating for capital efficiency
# 📆 PATCHED: 2026-03-05 — Unified early-drift capital lock prevention
#
# PURPOSE
# -------
# Capture early drifts (18→17→16→…) while preventing capital lock hours before off.
#
# ENTRY LOGIC
# -----------
# Entry allowed only if price exceeds threshold determined by time-to-off.
#
# TTO (seconds)      MIN ENTRY PX
# > 7200  (2h)       block
# > 3600  (1h)       ≥ 18
# > 1800  (30m)      ≥ 14
# > 900   (15m)      ≥ 10
# ≤ 900              unrestricted
#
# Once anchor exists, MSC_RISK manages the full drift lifecycle.
# ======================================================================================================

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

  
            plans.append({
                "enter": True,
                "engine": "MSC_UNIFIED",
                "bet_type": "EXPLORATORY",
                "role": "PARENT",
                "marketId": c["marketId"],
                "selectionId": c["selectionId"],
                "direction": "LAY->BACK",
                "px": c.get("px"),
                "why": "unified_exploratory",
            })

        # ------------------------------------------------------------------
        # 3️⃣ STOP LOSS CHILD EMISSION (ANCHOR-BASED)
        # ------------------------------------------------------------------

        for (mid, sid), rctx in getattr(self, "_route_ctx_map", {}).items():

            pid        = rctx.get("anchor_parent_id")
            entry_px   = rctx.get("anchor_entry_odds")
            entry_side = rctx.get("legacy_entry_side")
            current_px = rctx.get("px")

            if not pid or not entry_px or not current_px:
                continue

            entry_px   = float(entry_px)
            current_px = float(current_px)

            adverse_ticks = 0

            # determine adverse direction
            if entry_side == "BACK":
                if current_px > entry_px:
                    adverse_ticks = current_px - entry_px
            elif entry_side == "LAY":
                if current_px < entry_px:
                    adverse_ticks = entry_px - current_px

            # simple 3-tick stop threshold
            if adverse_ticks >= 3:

                child_key = (mid, sid, pid)

                if child_key in self._emitted_children:
                    continue

                self._emitted_children.add(child_key)

                opposite = "LAY->BACK" if entry_side == "BACK" else "BACK->LAY"

                plans.append({
                    "enter": True,
                    "engine": "MSC_UNIFIED",
                    "bet_type": "STOPLOSS",
                    "role": "CHILD",
                    "exit_kind": "STOP",
                    "parent_id": pid,
                    "marketId": mid,
                    "selectionId": sid,
                    "direction": opposite,
                    "px": current_px,
                    "why": "unified_stop_loss",
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

                plans.append({
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

                plans.append({
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

        # ------------------------------------------------------------------
        # 6️⃣ CORRECTIVE FLATTEN (ONLY IF MARKET LIABILITY EXISTS)
        # ------------------------------------------------------------------

        worst = liability.get("worst_case_liability", 0)

        if worst and worst > 0:

            for (mid, sid), rctx in getattr(self, "_route_ctx_map", {}).items():

                px     = rctx.get("px")
                anchor = rctx.get("anchor_entry_odds")

                if not px or not anchor:
                    continue

                px     = float(px)
                anchor = float(anchor)

                # collapsing toward win
                if px < anchor and px <= 5:

                    plans.append({
                        "enter": True,
                        "engine": "MSC_UNIFIED",
                        "bet_type": "CORRECTION",
                        "role": "CHILD",
                        "exit_kind": "CORRECTIVE",
                        "marketId": mid,
                        "selectionId": sid,
                        "direction": "BACK->LAY",
                        "px": px,
                        "why": "unified_corrective",
                    })

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

    def _build_runtime_ctx_map(self) -> dict:

        from engines.bus_route import BusRouteSnapshot

        snapshot = BusRouteSnapshot()
        snapshot.build_route()
        snapshot.refresh_ctx_dynamic_fields()

        return snapshot.get_ctx_map() or {}

    # --------------------------------------------------------------------------------------------------
    # V7 REPORT BUILDER — SPEC LOCKED
    # --------------------------------------------------------------------------------------------------

    def _build_v7_report(self, ctx: Dict[str, Any], tick_delta: float) -> Dict[str, Any]:

        # 1️⃣ SYSTEM SNAPSHOT (BUS awareness only)
        system_snapshot = self._read_unified_snapshot()

        # 2️⃣ STRUCTURAL WORLD (self-built, authoritative)
        self._route_ctx_map = self._build_runtime_ctx_map()

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
            "layer2": self._build_layer2_surface(),
        }

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
                LIMIT 5
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

            event = self._shadow_stop_engine.evaluate(
                parent=parent,
                mid=mid,
                sid=sid,
                current_odds=px,
                oc_phase=0,
            )

            stats["total_trades"] += 1

            if event:

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

                # Keep last 10
                stats["recent"] = stats["recent"][-10:]

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

    # --------------------------------------------------------------------------------------------------
    # LAYER 2 — TRADE SIGNAL INTELLIGENCE
    # --------------------------------------------------------------------------------------------------

    def _build_layer2_surface(self) -> Dict[str, Any]:

        drift = self._build_drift_surface().get("runners", [])
        sweet = self._build_sweet_spot_surface().get("runners", [])
        rank  = self._build_rank_surface().get("runners", [])

        candidates = []

        for r in drift:

            score = 0

            sweet_row = next(
                (s for s in sweet
                 if s["marketId"] == r["marketId"]
                 and s["selectionId"] == r["selectionId"]),
                None
            )

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

            rank_row = next(
                (rk for rk in rank
                 if rk["marketId"] == r["marketId"]
                 and rk["selectionId"] == r["selectionId"]),
                None
            )

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

        return {
            "candidates": candidates,
            "sweet_summary": sweet_summary,
            "fast_movers": fast[:5],
            "crossovers": crossovers[:5],
            "form_strong": strong_form,
            "form_weak": weak_form,
            "bias_calls": bias_rows[:5],
            "direction_calls": direction_rows[:5],
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

    engine = UnifiedEngine()

    while True:
        try:
            # Build report from latest snapshot
            report = engine._build_v7_report(
                ctx={}, 
                tick_delta=None
            )

            # Print formatted V7 block
            engine.print_v7_report(report)

        except Exception as e:
            print(f"[UNIFIED][ERR] reporter loop failed: {e}")

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
            report = engine._build_v7_report(
                ctx={},
                tick_delta=None
            )

            engine.print_v7_report(report)

        except Exception as e:
            print(f"[UNIFIED][ERR] standalone failed: {e}")

        time.sleep(5)