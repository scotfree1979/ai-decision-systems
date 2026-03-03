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
        from engines.config_paths import autoscalp_db
        import sqlite3

        con = sqlite3.connect(autoscalp_db())
        con.row_factory = sqlite3.Row

        row = con.execute("""
            SELECT *
            FROM unified_runtime_snapshot
            ORDER BY ts DESC
            LIMIT 1
        """).fetchone()

        con.close()

        if not row:
            return {}

        return dict(row)

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
    # V7 REPORT BUILDER (SNAPSHOT-DRIVEN)
    # --------------------------------------------------------------------------------------------------

    def _build_v7_report(self, ctx: Dict[str, Any], tick_delta: float) -> Dict[str, Any]:

        snap = self._read_unified_snapshot()

        report = {
            "timing": self._build_timing_surface(snap),
            "drift": self._build_drift_surface(snap),
            "rank": self._build_rank_surface(snap),
            "sweet_spot": self._build_sweet_spot_surface(snap),
            "volatility": self._build_volatility_surface(snap),
            "execution": self._build_execution_surface(snap),
            "liability": self._build_liability_surface(snap),
            "capital": self._build_capital_surface(snap),
            "stop": self._build_stop_surface(snap),
            "classification": self._build_classification_surface(snap),
            "temporal": self._build_temporal_surface(snap),
            "cadence": {
                "tick_delta_sec": tick_delta,
            },
        }

        return report

    # --------------------------------------------------------------------------------------------------
    # SECTION A — MARKET PHASE + RAW VOLATILITY
    # --------------------------------------------------------------------------------------------------

    def _build_timing_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "route_id": snap.get("route_id"),
            "bus_stop": snap.get("bus_stop"),
            "tick_id": snap.get("tick_id"),
            "hz": snap.get("hz"),
        }

    # --------------------------------------------------------------------------------------------------
    # RAW VOLATILITY PER-TICK EXPOSURE (MONITOR-AUTHORITATIVE)
    # --------------------------------------------------------------------------------------------------

    def _build_volatility_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "inplay_active": snap.get("inplay_active", 0),
            "inplay_confidence": snap.get("inplay_confidence", 0.0),
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION B — DRIFT SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_drift_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "directional_bias": snap.get("directional_bias"),
            "imbalance_level": snap.get("imbalance_level"),
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION C — RANK / CROSSOVER SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_rank_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "parents_open": snap.get("parents_open"),
            "children_open": snap.get("children_open"),
            "children_matched": snap.get("children_matched"),
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION D — SWEET SPOT SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_sweet_spot_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "utilisation_pct": snap.get("utilisation_pct"),
            "headroom": snap.get("headroom"),
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION E — EXECUTION SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_execution_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "fill_rate": snap.get("fill_rate"),
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION F — LIABILITY SURFACE (EXCHANGE TRUTH)
    # --------------------------------------------------------------------------------------------------

    def _build_liability_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "worst_case_liability": snap.get("worst_case_liability"),
            "directional_bias": snap.get("directional_bias"),
            "imbalance_level": snap.get("imbalance_level"),
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

        con = sqlite3.connect(autoscalp_db())
        con.row_factory = sqlite3.Row

        try:
            rows = con.execute("""
                SELECT marketId, selectionId, px
                FROM inplay_runtime_snapshot
                WHERE ts = (
                    SELECT MAX(ts)
                    FROM inplay_runtime_snapshot
                )
            """).fetchall()

            for r in rows:
                px_map[(str(r["marketId"]), str(r["selectionId"]))] = float(r["px"])
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

    def _build_classification_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "system_mode": (
                "INPLAY" if snap.get("inplay_active")
                else "BALANCED"
            ),
            "bias": snap.get("directional_bias"),
            "pressure": snap.get("imbalance_level"),
        }

    # --------------------------------------------------------------------------------------------------
    # SECTION J — TEMPORAL SURFACE
    # --------------------------------------------------------------------------------------------------

    def _build_temporal_surface(self, snap: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "route_id": snap.get("route_id"),
            "bus_stop": snap.get("bus_stop"),
            "tick_id": snap.get("tick_id"),
        }

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
        """
        V7 Unified Visual Report.
        Snapshot compatible.
        Phase-0 safe.
        Fully defensive.
        """

        # ANSI colours
        RESET   = "\033[0m"
        GREEN   = "\033[92m"
        RED     = "\033[91m"
        YELLOW  = "\033[93m"
        CYAN    = "\033[96m"
        MAGENTA = "\033[95m"
        BOLD    = "\033[1m"

        timing = report.get("timing", {})
        vol    = report.get("volatility", {})
        liab   = report.get("liability", {})
        cap    = report.get("capital", {})
        stop   = report.get("stop", {})
        exec_s = report.get("execution", {})
        cls    = report.get("classification", {})

        inplay_active = bool(vol.get("inplay_active", 0))

        # --------------------------------------------------
        # Phase colour
        # --------------------------------------------------
        if inplay_active:
            phase_colour = RED
            phase_label  = "IN-PLAY"
        else:
            phase_colour = GREEN
            phase_label  = "PRE-MARKET"

        print()
        print("══════════════════════════════════════════════════════════════")
        print(f"{BOLD}{CYAN}UNIFIED ENGINE V7 REPORT{RESET}")
        print("══════════════════════════════════════════════════════════════")

        # --------------------------------------------------
        # TIMING
        # --------------------------------------------------
        print(f"\n{BOLD}[TIMING]{RESET}")
        print(f"  Route        : {timing.get('route_id')}")
        print(f"  Bus Stop     : {timing.get('bus_stop')}")
        print(f"  Tick         : {timing.get('tick_id')}")
        print(f"  Hz           : {timing.get('hz')}")
        print(f"  Tick Δ       : {report.get('cadence', {}).get('tick_delta_sec')}")
        print(f"  Phase        : {phase_colour}{phase_label}{RESET}")

        if inplay_active:
            print(f"  {RED}>>> LIVE TRADING ZONE ACTIVE <<<{RESET}")

        # --------------------------------------------------
        # EXECUTION
        # --------------------------------------------------
        print(f"\n{BOLD}[EXECUTION]{RESET}")

        fill_rate = exec_s.get("fill_rate") or 0

        if fill_rate >= 75:
            fill_colour = GREEN
        elif fill_rate >= 40:
            fill_colour = YELLOW
        else:
            fill_colour = RED

        print(f"  Fill Rate    : {fill_colour}{fill_rate}%{RESET}")

        # --------------------------------------------------
        # LIABILITY
        # --------------------------------------------------
        print(f"\n{BOLD}[LIABILITY]{RESET}")

        worst      = liab.get("worst_case_liability") or 0
        bias       = liab.get("directional_bias")
        imbalance  = liab.get("imbalance_level")

        if worst > 1000:
            liab_colour = RED
        elif worst > 200:
            liab_colour = YELLOW
        else:
            liab_colour = GREEN

        print(f"  Worst Case   : {liab_colour}{worst}{RESET}")
        print(f"  Bias         : {bias}")
        print(f"  Imbalance    : {imbalance}")

        # Pressure Gauge
        pressure_map = {
            "NONE":      "▁▁▁▁▁",
            "LOW":       "▂▂▁▁▁",
            "MODERATE":  "▃▃▃▁▁",
            "HIGH":      "▅▅▅▅▅",
        }

        gauge = pressure_map.get(imbalance, "▁▁▁▁▁")
        print(f"  Pressure     : {gauge}")

        # --------------------------------------------------
        # CAPITAL
        # --------------------------------------------------
        print(f"\n{BOLD}[CAPITAL]{RESET}")

        util = cap.get("utilisation_pct") or 0

        if util >= 80:
            util_colour = RED
        elif util >= 50:
            util_colour = YELLOW
        else:
            util_colour = GREEN

        print(f"  Total Pot    : {cap.get('total_pot')}")
        print(f"  Floor        : {cap.get('total_floor')}")
        print(f"  Reserved     : {cap.get('total_reserved')}")
        print(f"  Headroom     : {cap.get('headroom')}")
        print(f"  Utilisation  : {util_colour}{util}%{RESET}")

        if util >= 80:
            print(f"  {RED}>>> CAPITAL STRESS WARNING <<<{RESET}")

        # --------------------------------------------------
        # STOP SYSTEM
        # --------------------------------------------------
        print(f"\n{BOLD}[STOP SYSTEM]{RESET}")

        parents_reviewed = stop.get("parents_reviewed") or 0
        stops_triggered  = stop.get("stops_triggered") or 0

        stop_colour = RED if stops_triggered > 0 else GREEN

        print(f"  Parents Reviewed : {parents_reviewed}")
        print(f"  Stops Triggered  : {stop_colour}{stops_triggered}{RESET}")

        # Engine distribution
        by_engine = stop.get("by_engine") or {}
        for eng, count in by_engine.items():
            print(f"    {eng:<18} : {count}")

        # TS classification breakdown
        ts_pos = 0
        ts_neg = 0

        for r in stop.get("rows", []):
            if r.get("classification") == "TS-POS":
                ts_pos += 1
            elif r.get("classification") == "TS-NEG":
                ts_neg += 1

        if ts_pos or ts_neg:
            print(f"  TS-POS        : {GREEN}{ts_pos}{RESET}")
            print(f"  TS-NEG        : {RED}{ts_neg}{RESET}")

        # --------------------------------------------------
        # CLASSIFICATION
        # --------------------------------------------------
        print(f"\n{BOLD}[CLASSIFICATION]{RESET}")
        print(f"  Mode         : {cls.get('system_mode')}")
        print(f"  Bias         : {cls.get('bias')}")
        print(f"  Pressure     : {cls.get('pressure')}")

        print("\n══════════════════════════════════════════════════════════════\n")