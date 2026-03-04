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

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        Called once per BUS tick.
        Must always return a dict.
        Must never return None.
        """

        now = time.time()
        tick_delta = None

        if self._last_tick_ts is not None:
            tick_delta = now - self._last_tick_ts

        self._last_tick_ts = now


        # ----------------------------------------------------------------------------------------------
        # PHASE 0: BUILD REPORT SURFACE (empty scaffolding for now)
        # ----------------------------------------------------------------------------------------------

        full_report = self._build_v7_report(
            ctx=ctx,
            tick_delta=tick_delta,
        )

        signal_summary = self._build_signal_summary(full_report)

        # ----------------------------------------------------------------------------------------------
        # RETURN STRUCTURE — NO PLAN
        # ----------------------------------------------------------------------------------------------

        return {
            "enter": False,
            "engine": self.ENGINE_NAME,
            "lane": self.LANE_ID,
            "why": "classification_only",
            "signals": signal_summary,
            "report": full_report,
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

            # Structural authority panels
            "timing": self._build_timing_surface(),
            "drift": self._build_drift_surface(),
            "rank": self._build_rank_surface(),
            "sweet_spot": self._build_sweet_spot_surface(),
            "volatility": self._build_volatility_surface(),

            # Execution / capital authority panels
            "execution": self._build_execution_surface(),
            "liability": self._build_liability_surface(),
            "capital": self._build_capital_surface(system_snapshot),
            "stop": self._build_stop_surface(system_snapshot),

            # Internal classification & trade memory
            "classification": self._build_classification_surface(),
            "temporal": self._build_temporal_surface(),
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

        con = open_bets_db(rw=False)
        con.row_factory = sqlite3.Row
        now = datetime.now(timezone.utc)

        try:
            rows = con.execute("""
                SELECT marketId, event_name, marketStartTime
                FROM bets
                WHERE substr(marketStartTime,1,10)=date('now','utc')
                ORDER BY datetime(marketStartTime) ASC
                LIMIT 5
            """).fetchall()
        finally:
            con.close()

        markets = []

        for r in rows:
            off = datetime.fromisoformat(
                r["marketStartTime"].replace("Z", "+00:00")
            )

            delta = (off - now).total_seconds()

            markets.append({
                "marketId": r["marketId"],
                "event_name": r["event_name"],
                "scheduled_off": off.isoformat(),
                "tto_seconds": int(delta),
                "post_zero_elapsed": abs(int(delta)) if delta <= 0 else 0,
                "phase": "LIVE_PHASE" if delta <= 300 else "PRE",
            })

        return {"markets": markets}

    # --------------------------------------------------------------------------------------------------
    # RAW VOLATILITY PER-TICK EXPOSURE (MONITOR-AUTHORITATIVE)
    # --------------------------------------------------------------------------------------------------

    def _build_volatility_surface(self) -> Dict[str, Any]:

        from tools.betfair_runner_trend_surface import get_runner_trend

        moved = 0

        for (mid, sid), ctx in self._route_ctx_map.items():
            trend = get_runner_trend(mid, sid)
            if trend and abs(trend.get("ticks_moved") or 0) >= 1:
                moved += 1

        impulse = moved >= 3

        return {
            "runners_moved_last_window": moved,
            "impulse_detected": impulse,
            "structural_energy": "SPIKE" if impulse else "LOW",
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

        from engines.live.bank_state import _compute_market_floor_from_betfair_surface

        floor_rows = _compute_market_floor_from_betfair_surface()

        if not floor_rows:
            return {
                "worst_case_liability": 0.0,
                "most_exposed_runner": None,
                "directional_bias": "NONE",
                "imbalance_level": "NONE",
            }

        # Worst market exposure
        worst = max(
            floor_rows,
            key=lambda r: float(r.get("true_market_exposure") or 0.0)
        )

        worst_case = float(worst.get("true_market_exposure") or 0.0)

        # Imbalance classification (simple exposure tiering)
        if worst_case > 1000:
            imbalance = "HIGH"
        elif worst_case > 200:
            imbalance = "MODERATE"
        elif worst_case > 0:
            imbalance = "LOW"
        else:
            imbalance = "NONE"

        return {
            "worst_case_liability": worst_case,
            "most_exposed_runner": worst.get("marketId"),
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
        """
        Unified Stop System — Snapshot Compatible.

        Proof-of-capability mode.
        No execution.
        No mutation.

        Evaluates:
        - Trailing breach (BASE_MIN_TRAIL_TICKS)
        - Positive vs Negative classification
        - Engine distribution
        """

        from engines.config_paths import open_auto_db, autoscalp_db
        from engines.price_math import calculate_tick_distance
        import sqlite3

        BASE_MIN_TRAIL_TICKS = 10

        # --------------------------------------------------
        # 1️⃣ LOAD LIVE MATCHED PARENTS
        # --------------------------------------------------
        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        try:
            parents = con.execute("""
                SELECT
                    id,
                    engine,
                    marketId,
                    selectionId,
                    side,
                    entry_odds
                FROM orders
                WHERE mode='LIVE'
                  AND role='PARENT'
                  AND UPPER(entry_status)='MATCHED'
                  AND (exit_status IS NULL OR UPPER(exit_status)!='MATCHED')
            """).fetchall()
        finally:
            con.close()

        if not parents:
            return {
                "parents_reviewed": 0,
                "stops_triggered": 0,
                "by_engine": {},
                "rows": [],
            }

        # --------------------------------------------------
        # 2️⃣ LOAD CURRENT PX FROM SNAPSHOT WORLD
        # --------------------------------------------------
        px_map = {}

        from engines.config_paths import open_auto_db

        con = open_auto_db(rw=False)

        try:
            rows = con.execute("""
                SELECT marketId, selectionId, px
                FROM inplay_runtime_snapshot
                WHERE ts = (
                    SELECT MAX(ts)
                    FROM inplay_runtime_snapshot
                )
            """).fetchall()

            for marketId, selectionId, px in rows:
                if px is not None:
                    px_map[(str(marketId), str(selectionId))] = float(px)
        finally:
            con.close()

        # --------------------------------------------------
        # 3️⃣ STOP EVALUATION
        # --------------------------------------------------
        stops_triggered = 0
        by_engine = {}
        rows_out = []

        for p in parents:

            mid = str(p["marketId"])
            sid = str(p["selectionId"])
            engine = p["engine"]
            entry = float(p["entry_odds"])
            side = str(p["side"]).upper()

            current_px = px_map.get((mid, sid))

            if current_px is None:
                continue

            try:
                ticks = abs(calculate_tick_distance(entry, current_px))
            except Exception:
                continue

            if ticks < BASE_MIN_TRAIL_TICKS:
                continue

            # Classification logic
            is_positive = (
                current_px < entry if side == "LAY"
                else current_px > entry
            )

            classification = "TS-POS" if is_positive else "TS-NEG"

            stops_triggered += 1
            by_engine[engine] = by_engine.get(engine, 0) + 1

            rows_out.append({
                "parent_id": p["id"],
                "engine": engine,
                "marketId": mid,
                "selectionId": sid,
                "entry_odds": entry,
                "current_odds": current_px,
                "ticks_moved": ticks,
                "classification": classification,
            })

        return {
            "parents_reviewed": len(parents),
            "stops_triggered": stops_triggered,
            "by_engine": by_engine,
            "rows": rows_out,
            "snapshot_route": snap.get("route_id"),
            "snapshot_tick": snap.get("tick_id"),
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

        for (mid, sid), ctx in self._route_ctx_map.items():
            if ctx.get("direction"):
                buckets["PRIMED"] += 1
            else:
                buckets["INACTIVE"] += 1

        return buckets

    # --------------------------------------------------------------------------------------------------
    # SECTION J — TEMPORAL SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_temporal_surface(self) -> Dict[str, Any]:

        trades = []

        for (mid, sid), ctx in self._route_ctx_map.items():

            if not ctx.get("anchor_parent_id"):
                continue

            trades.append({
                "marketId": mid,
                "selectionId": sid,
                "entry_odds": ctx.get("anchor_entry_odds"),
                "current_odds": ctx.get("px"),
                "delta_ticks": None,
                "duration_seconds": None,
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
    # REPORT PRINTER (V7 BLOCK — SNAPSHOT DRIVEN, FULL VISUAL)
    # --------------------------------------------------------------------------------------------------

    def print_v7_report(self, report: Dict[str, Any]) -> None:

        RESET = "\033[0m"
        CYAN  = "\033[96m"
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
        print(f"{BOLD}{CYAN}UNIFIED ENGINE V7 REPORT{RESET}")
        print("══════════════════════════════════════════════════════════════")

        # WORLD
        print("\n[WORLD]")
        print(f"  Timestamp UTC  : {world.get('timestamp_utc')}")
        print(f"  Engine State   : {world.get('engine_state')}")
        print(f"  Plans Emitted  : {world.get('plans_emitted')}")

        # TIMING
        print("\n[TIMING]")
        for m in timing.get("markets", []):
            print(f"  {m.get('event_name')} | TTO={m.get('tto_seconds')}s | Phase={m.get('phase')}")

        # DRIFT
        print("\n[DRIFT SURFACE]")
        for r in drift.get("runners", []):
            print(f"  {r['marketId']}:{r['selectionId']} Δ={r.get('delta_ticks')} dir={r.get('drift_direction')}")

        # RANK
        print("\n[RANK / CROSSOVER]")
        for r in rank.get("runners", []):
            print(f"  {r['marketId']}:{r['selectionId']} Δrank={r.get('rank_delta')} crossed={r.get('crossed_over')}")

        # SWEET SPOT
        print("\n[SWEET SPOT]")
        for r in sweet.get("runners", []):
            print(f"  {r['marketId']}:{r['selectionId']} zone={r.get('zone')}")

        # VOLATILITY
        print("\n[VOLATILITY]")
        print(f"  Moved: {vol.get('runners_moved_last_window')}")
        print(f"  Impulse: {vol.get('impulse_detected')}")
        print(f"  Energy: {vol.get('structural_energy')}")

        # EXECUTION
        print("\n[EXECUTION]")
        print(f"  Parents Matched : {exec_s.get('parent_matched_count')}")
        print(f"  Children Matched: {exec_s.get('child_matched_count')}")
        print(f"  Direction Conf  : {exec_s.get('direction_confidence')}")

        # LIABILITY
        print("\n[LIABILITY]")
        print(f"  Worst Case : {liab.get('worst_case_liability')}")
        print(f"  Exposed    : {liab.get('most_exposed_runner')}")
        print(f"  Bias       : {liab.get('directional_bias')}")

        # CAPITAL
        print("\n[CAPITAL]")
        print(f"  Pot       : {cap.get('total_pot')}")
        print(f"  Floor     : {cap.get('total_floor')}")
        print(f"  Reserved  : {cap.get('total_reserved')}")
        print(f"  Headroom  : {cap.get('headroom')}")
        print(f"  Utilisation: {cap.get('utilisation_pct')}")

        # STOP
        print("\n[STOP SURFACE]")
        print(f"  Reviewed : {stop.get('parents_reviewed')}")
        print(f"  Triggered: {stop.get('stops_triggered')}")

        # CLASSIFICATION
        print("\n[CLASSIFICATION BUCKETS]")
        for k, v in cls.items():
            print(f"  {k}: {v}")

        # TEMPORAL
        print("\n[TEMPORAL TRADE SURFACE]")
        for t in temp.get("active_parents", []):
            print(f"  {t['marketId']}:{t['selectionId']} entry={t.get('entry_odds')} current={t.get('current_odds')}")

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