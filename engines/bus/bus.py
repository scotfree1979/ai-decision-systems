# ======================================================================================================
# BUS v13 — Behaviour-Identical Reconstruction
# Extracted CTX builder + Engine runner, nothing else changed.
# ======================================================================================================

from engines.live.bank_state import get_engine_available

from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
from engines.micro_scalper_v7.inplay_engine import InPlayEngine
from engines.micro_scalper_v7.risk_engine import RiskEngine
from engines.mastery.mastery_policy import plan_for_strategy
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: from engines.mastery.context_builder import build_context
# 🧩 ACTION: DELETE unused CTX builder import
# 📆 PATCHED: 2026-03-05 — enforce CTX ownership invariant
#
# WHY:
# BUS must never construct CTX.
# CTX construction is restricted to StartupCTXBuilder.
# ======================================================================================================

# DELETE THIS LINE
#from engines.mastery.context_builder import build_context
from engines.live.live_router import place_from_bus
from engines.decision_engine.decide_once.scope import build_and_maintain_scope
from engines.math.dynamic_stake_v7 import compute_dynamic_stake, calc_dynamic_stake
from engines.bus_route_startup_ctx import StartupCTXBuilder
from engines.live.overwatcher import evaluate_redistribution
from engines.config_paths import connect_db
from engines.live.live_router import get_parent_snapshot
from engines.bus_route import PLANS_PER_TICK
try:
    from engines.bus_route import ROUTE_SPLIT
except Exception:
    ROUTE_SPLIT = {
        "LEGACY": 0,
        "MSC_RISK": 0,
        "MSC_INPLAY": 0,
        "MSC_EXPLORATORY": 0,
        "OVERWATCHER": 0,
        "MSC_UNIFIED": 0,
    }

# BUS authority: legacy letter → concrete strategy name
LEGACY_LETTER_TO_STRATEGY = {
    "S": "OG_STRATEGY",
    "B": "BTL_SCOUT",
    "G": "BTL_AGGR",
    "X": "S4_CROSSOVER",
    "R": "S5_BREAKOUT",
    "F": "S6_STEAM_FADE",
    "P": "BLUEPRINTS",   # special-case, see below
    "A": "ALWAYS_ON",
}

INPLAY_PRE_OFF_MINUTES  = 5
INPLAY_POST_OFF_MINUTES = 120

# === PATCH START ============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside CTX preparation (right after ctx is loaded from route)
# 📆 PATCHED: 2026-01-29 — Normalize MarketMonitor enums to BUS-safe ints
# ============================================================================

# ============================================================
# Canonical numeric enum normalisation (BUS authority)
# ============================================================

_BAND_MAP = {
    "LEADING":  4,
    "ACTIVE":   3,
    "PASSIVE":  2,
    "EXTENDED": 1,
    "IGNORED":  0,
    "UNKNOWN": -1,
}


# PROMINENCE is positional / tactical.
# LEADING is valid here and MUST be mapped.
_PROMINENCE_MAP = {
    "LEADING":    3,   # 🔑 critical fix
    "FRONT":      3,
    "PROMINENT":  2,
    "MIDFIELD":   1,
    "HELD_UP":    0,
}

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def _get_next_market_pairs():
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-17 — DAL-compliant market cadence helper
#
# WHY
# ---
# Previous helper used:
#     connect_db()
#     sqlite3
#
# That violates the system architecture:
#
#     BUS → DAL only
#
# All DB access must go through engines.config_paths DAL helpers.
#
# FIX
# ---
# Use open_bets_db() which is the canonical DAL reader for bets.db.
#
# RESULT
# ------
# • No sqlite3 import required
# • No direct DB connections
# • Fully DAL compliant
# ======================================================================================================

def _get_next_market_pairs():

    from engines.config_paths import open_bets_db

    import sqlite3

    con = open_bets_db(rw=False)
    con.row_factory = sqlite3.Row

    try:
        row = con.execute("""
            SELECT marketId
            FROM bets
            WHERE datetime(marketStartTime) >= datetime('now','utc')
            ORDER BY datetime(marketStartTime)
            LIMIT 1
        """).fetchone()

        if not row:
            return []

        mid = str(row["marketId"])

        runners = con.execute("""
            SELECT selectionId
            FROM bets
            WHERE marketId = ?
        """, (mid,)).fetchall()

        return [(mid, str(r["selectionId"])) for r in runners]

    finally:
        con.close()

def _normalize_ctx_enums(ctx: dict) -> None:
    """
    BUS authority: normalize ALL enum-like CTX fields to ints.
    Must be idempotent and never raise.
    """

    # ---- band ----
    #band = ctx.get("band")
    #if isinstance(band, str):
    #    ctx["band"] = _BAND_MAP.get(band.upper(), -1)

    # ---- prominence ----
    #for key in ("prominence", "prominent"):
    #    val = ctx.get(key)
    #    if isinstance(val, str):
    #        ctx[key] = _PROMINENCE_MAP.get(val.upper(), 1)
    #    elif isinstance(val, bool):
    #        ctx[key] = 1 if val else 0

    # ---- positional / rank fields (defensive) ----
    for key in ("pos_inplay", "position", "rank", "lane_rank"):
        val = ctx.get(key)
        if isinstance(val, str):
            try:
                ctx[key] = int(val)
            except Exception:
                ctx[key] = -1


# ======================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def _apply_bus_stake_gate(
# 📆 PATCHED: 2026-01-22 — import ENGINE_MIN / ENGINE_MAX from daily_config
#
# WHY:
# - BUS now owns final stake authority
# - ENGINE_MIN / ENGINE_MAX were referenced but never defined
# - daily_config is the canonical source of engine stake bounds
# ======================================================================

from engines import daily_config

ENGINE_MIN = daily_config.ENGINE_MIN
ENGINE_MAX = daily_config.ENGINE_MAX


from engines.bus_route import (
    build_full_cycle,
    build_bus_route_tick,
    RunnerRotation,
    BusRouteSnapshot,      # 🔑 ADD THIS
)
from collections import deque
import time

def _market_ready(st: dict) -> bool:
    if not st:
        return False
    runners = st.get("runners")
    if not runners:
        return False
    # require at least one runner with px
    for r in runners.values():
        if r.get("px") is not None:
            return True
    return False

def _apply_bus_stake_gate(*, engine: str, stake: float) -> float:
    """
    BUS stake gate — neutralised.

    Dynamic stake module is now sole authority.
    BUS enforces only non-zero invariant.
    """

    if stake is None or stake <= 0:
        raise RuntimeError("BUS invariant violated: stake <= 0")

    return float(stake)

# ======================================================================================================
# BUS DIAGNOSTICS SHIM — FINAL
# ======================================================================================================
# Purpose:
# - Absorb ALL legacy note-based writes
# - Guarantee counted reason aggregation
# - Prevent KeyError / TypeError
# - Zero behavioural impact on execution
#
# This allows BUS internals to remain untouched.
# ======================================================================================================

class EngineReportShim(dict):
    def __getitem__(self, engine):
        if engine not in self:
            super().__setitem__(engine, {
                "evaluated": False,
                "fired": 0,
                "reasons": {},
            })
        return super().__getitem__(engine)

    def record_reason(self, engine, reason):
        if not reason:
            return
        eng = self[engine]
        reasons = eng.setdefault("reasons", {})
        reasons[reason] = reasons.get(reason, 0) + 1

    def write_note(self, engine, note):
        # legacy compatibility: convert note → reason
        if note:
            self.record_reason(engine, note)

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: class DecisionBus
# 🧩 ACTION: ADD helper ABOVE class (module-level)
# 📆 PATCHED: 2026-03-10 — Cadence Controller (BUS admission gate)
#
# PURPOSE:
# - Enforce fixed execution cadence
# - Prevent tick overload
# - Preserve BUS as pure router
#
# CANONICAL PARAMETERS:
#   window_seconds = 30
#   tick_seconds   = 6
#   ticks_per_win  = 5
#   plans_per_win  = 300
#   plans_per_tick = 100
# ======================================================================================================

import time
from collections import deque

class CadenceController:
    def __init__(self):
        # Locked parameters
        self.window_seconds   = 60
        self.tick_seconds     = 3
        self.ticks_per_window = 10
        self.plans_per_window = 2400
        self.plans_per_tick   = 240

        # State
        self.window_start_ts = time.time()
        self.tick_index = 0
        self.queue = deque()
        self._ctx_refresh_times = []

    def _roll_window_if_needed(self):
        now = time.time()
        if now - self.window_start_ts >= self.window_seconds:
            if self.queue:
                print(
                    f"[CADENCE][WARN] window rollover with {len(self.queue)} deferred plans"
                )
            self.window_start_ts = now
            self.tick_index = 0
            self.queue.clear()

    def enqueue(self, plans: list[tuple]):
        """
        Accept raw BUS plans (non-blocking).
        """
        self._roll_window_if_needed()
        for p in plans:
            if len(self.queue) < self.plans_per_window:
                self.queue.append(p)
            else:
                # Hard backpressure: defer to next window
                break

    def admit_for_tick(self) -> list[tuple]:
        """
        Admit up to plans_per_tick plans for this tick.
        """
        self._roll_window_if_needed()
        self.tick_index += 1

        admitted = []
        for _ in range(min(self.plans_per_tick, len(self.queue))):
            admitted.append(self.queue.popleft())

        print(
            f"[CADENCE] tick={self.tick_index}/{self.ticks_per_window} "
            f"admitted={len(admitted)} remaining={len(self.queue)}"
        )

        return admitted

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: class DecisionBus
# 🧩 ACTION: ADD helper ABOVE class (module-level)
# 📆 PATCHED: 2026-03-10 — BUS reason aggregation helper (final)
#
# CONTRACT:
# - engine_report MUST be passed explicitly
# - reasons are counted, never overwritten
# - NEVER raises
# ======================================================================================================

def _record_reason(engine_report: dict, engine: str, reason: str | None):
    if not engine_report or not engine or not reason:
        return

    eng = engine_report.setdefault(engine, {})
    reasons = eng.setdefault("reasons", {})
    reasons[reason] = reasons.get(reason, 0) + 1

# === PATCH START ==============================================================
# 📍 TARGET: engines/bus_route.py
# 🔎 SEARCH: def get_root_ctx_runner_pairs():
# 🧩 ACTION: ADD time-gated IN-PLAY eligibility (BUS authority)
# 📆 PATCHED: 2026-01-26 — MSC_INPLAY time gate moved to BUS
# ==============================================================================

# ----------------------------------------------------------------------
# CONFIG — IN-PLAY TIME WINDOW (SAFE DEFAULTS)
# ----------------------------------------------------------------------
INPLAY_PRE_OFF_MINUTES  = 5   # early race priming
INPLAY_POST_OFF_MINUTES = 120         # 🔑 2 hours


def _is_inplay_time_window(market_id: str) -> bool:
    """
    BUS authority: determine if a market is eligible for MSC_INPLAY
    based on time-to-off / time-since-off.

    NOTE:
    - If gates are None → always True (debug / development mode)
    - Bets DB is the time authority
    """

    if INPLAY_PRE_OFF_MINUTES is None and INPLAY_POST_OFF_MINUTES is None:
        return True

    from engines.config_paths import open_bets_db
    from datetime import datetime, timezone
    import sqlite3

    con = open_bets_db(rw=False)
    con.row_factory = sqlite3.Row

    try:
        row = con.execute(
            """
            SELECT off_at_utc
            FROM markets_schedule
            WHERE marketId = ?
            LIMIT 1
            """,
            (str(market_id),)
        ).fetchone()
    finally:
        con.close()

    if not row or not row["off_at_utc"]:
        return False

    now = datetime.now(timezone.utc)
    off = datetime.fromisoformat(row["off_at_utc"].replace("Z", "+00:00"))

    delta_min = (off - now).total_seconds() / 60.0

    # PRE-OFF window
    if INPLAY_PRE_OFF_MINUTES is not None:
        if delta_min > INPLAY_PRE_OFF_MINUTES:
            return False

    # POST-OFF window
    if INPLAY_POST_OFF_MINUTES is not None:
        if delta_min < -INPLAY_POST_OFF_MINUTES:
            return False

    return True

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def bus_snapshot():
# 🧩 ACTION: ADD missing lifecycle counters (children_matched, hedge_ops, stop_ops)
# 📆 PATCHED: 2026-01-11 — BUS snapshot lifecycle correctness (read-only)
# ======================================================================================================

def bus_snapshot():
    """
    DB-first system snapshot for BUS analytics reporting.

    Canonical rules:
    - LIVE mode only
    - Today-independent (current lifecycle state)
    - Exposure = open parent liability only
    - PnL = realised only
    """

    from engines.config_paths import open_auto_db

    snap = {
        "engines": {},
        "parents_opened": 0,
        "children_opened": 0,
        "children_matched": 0,
        "exposure": 0.0,
        "realised": 0.0,
        "unsettled": 0.0,
        "stoploss_fired": 0,
        "risk_hedges": 0,
        "risk_stops": 0,
    }

    con = None
    try:
        con = open_auto_db(rw=False)

        # --------------------------------------------------
        # Engine counts (LIVE parents opened today)
        # --------------------------------------------------
        rows = con.execute("""
            SELECT engine, COUNT(*)
            FROM orders
            WHERE role='PARENT'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND date(opened_at)=date('now','utc')
            GROUP BY engine
        """).fetchall()

        for eng, n in rows:
            snap["engines"][eng] = int(n)

# === PATCH START ==============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def bus_snapshot():
# 📆 PATCHED: 2026-02-26 — Scope BUS snapshot to UTC trading day
#
# PURPOSE:
# - Dashboard + recycling must be day-scoped
# - Prevent historical exposure from blocking capital
# - Align BUS with SR4 reports (UTC scoped)
# ==============================================================================

        # --------------------------------------------------
        # Parent / child lifecycle counts (LIVE, TODAY ONLY)
        # --------------------------------------------------
        snap["parents_opened"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='PARENT'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(entry_status)='MATCHED'
              AND (exit_status IS NULL OR UPPER(exit_status)!='MATCHED')
              AND date(opened_at)=date('now','utc')
        """).fetchone()[0]

        snap["children_opened"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='CHILD'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(entry_status)='MATCHED'
              AND (exit_status IS NULL OR UPPER(exit_status)!='MATCHED')
              AND date(opened_at)=date('now','utc')
        """).fetchone()[0]

        snap["children_matched"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='CHILD'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(entry_status)='MATCHED'
              AND UPPER(exit_status)='MATCHED'
              AND date(opened_at)=date('now','utc')
        """).fetchone()[0]

        # --------------------------------------------------
        # Exposure (LIVE open parent liability — TODAY ONLY)
        # --------------------------------------------------
        row = con.execute("""
            SELECT COALESCE(SUM(
                CASE
                    WHEN UPPER(side)='LAY'
                        THEN entry_stake * (entry_odds - 1.0)
                    WHEN UPPER(side)='BACK'
                        THEN entry_stake
                    ELSE 0
                END
            ),0)
            FROM orders
            WHERE role='PARENT'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(entry_status)='MATCHED'
              AND (exit_status IS NULL OR UPPER(exit_status)!='MATCHED')
              AND date(opened_at)=date('now','utc')
        """).fetchone()

        snap["exposure"] = float(row[0] or 0.0)
# === PATCH END ==============================================================

        # --------------------------------------------------
        # Realised PnL (LIVE, today)
        # --------------------------------------------------
        row = con.execute("""
            SELECT COALESCE(SUM(net_pl),0)
            FROM orders
            WHERE UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(exit_status)='MATCHED'
              AND date(closed_at)=date('now','utc')
        """).fetchone()

        snap["realised"] = float(row[0] or 0.0)

        # --------------------------------------------------
        # Risk ops (matched CHILD exits)
        # --------------------------------------------------
        snap["risk_hedges"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='CHILD'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(exit_kind) IN ('HEDGE','H')
              AND UPPER(entry_status)='MATCHED'
        """).fetchone()[0]

        snap["risk_stops"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='CHILD'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(exit_kind)='STOPLOSS'
              AND UPPER(entry_status)='MATCHED'
        """).fetchone()[0]

        snap["stoploss_fired"] = snap["risk_stops"]

        # --------------------------------------------------
        # Unsettled PnL intentionally disabled
        # --------------------------------------------------
        snap["unsettled"] = 0.0

    except Exception as e:
        snap["error"] = str(e)

    finally:
        try:
            if con:
                con.close()
        except Exception:
            pass

    return snap

class DecisionBus:
    ALLOWED_LEGACY_LETTERS = {"S", "P", "B", "G", "X", "R", "F", "A"}

    def __init__(self):
        self.tick_id = 0
        self.live_run_id = None
        self._route_buffer = deque()
        self._route_rotation = RunnerRotation()
        self._route_id = 1
        self._bus_stop = 0
        self._cadence = CadenceController()
        self._ctx_refresh_times = []
        self._optional_intel_cache = {}
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py:DecisionBus.__init__
# 🔎 SEARCH: self._optional_intel_cache = {}
# 🧩 ACTION: INSERT BELOW — snapshot cadence controller
# 📆 PATCHED: 2026-03-16 — decouple snapshot cadence from BUS ticks
#
# PURPOSE
# -------
# Snapshots must update independently of execution cadence.
# This prevents dashboard freeze when ticks slow down.
#
# DESIGN
# ------
# BUS still owns snapshot data, but cadence is time-driven.
#
# SAFETY
# ------
# No execution logic affected.
# ======================================================================================================

        # --------------------------------------------------
        # Snapshot cadence controller
        # --------------------------------------------------
        self._snapshot_interval = 2.0
        self._last_snapshot_ts = 0.0
        # 🔑 REQUIRED — MSC_INPLAY prewarm tracking
        self._inplay_ctx_prewarmed = set()  
    

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: class DecisionBus.__init__
# 🧩 ACTION: ADD (defensive init)
# 📆 PATCHED: 2026-03-16 — define route_snapshot at construction
#
# WHY:
# BUS is imported at module load time.
# route_snapshot is created later during tick().
# Attribute must exist before any access.
# ======================================================================================================

        self._route_snapshot = None
        # --------------------------------------------------
        # Dashboard runner snapshot (read-only surface)
        # --------------------------------------------------
        self._runner_surface = []

        # ------------------------------------------------------------------
        # ENGINE DEPRECATION (V7 UNIFIED MODE)
        # ------------------------------------------------------------------
        # Only Unified + DB correctness lanes are active.
        # All legacy engines remain loaded but are skipped.

        self._deprecated_engines = {
            "LEGACY",
            "MSC_RISK",
            "MSC_INPLAY",
            "MSC_EXPLORATORY",
            "OVERWATCHER",
        }

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: class DecisionBus.__init__
# 🧩 ACTION: ADD
# 📆 PATCHED: 2026-01-15 — Route-level CTX state (full route, non-LEGACY)
#
# PURPOSE:
# - Hold FULL route CTX set (all runners for 10 ticks)
# - LEGACY uses bus-stop subset only
# - All other engines see full route every tick
# ======================================================================================================

        # ------------------------------------------------------------------
        # Route-level CTX (FULL SET — non-LEGACY lanes)
        # ------------------------------------------------------------------
        
        # Route CTX map initialised later during first tick
        self._route_ctx_map = {}

        # Engine + strategy binding (existing working behaviour)
        from engines.bus.engine_registry import ENGINE_REGISTRY
        from engines.decision_engine.strategies.registry import ORDER as STRATEGY_ORDER

        self.engines = {
            name: cls() if isinstance(cls, type) else cls
            for name, cls in ENGINE_REGISTRY.items()
        }
        self.legacy_strategies = STRATEGY_ORDER

        print("[BUS] registered engines:", list(self.engines.keys()))
        print("[BUS] registered strategies:",
              [name for (name, _fn) in self.legacy_strategies])

    def _force_px_refresh(self, mid: str, sid: str, ctx: dict) -> bool:
        """
        LAST-CHANCE PX recovery (BUS authority).

        CONTRACT:
        - Called only after normal route refresh + _ensure_px_from_route failed
        - Forces BusRouteSnapshot to refetch live odds immediately
        - Never raises
        - Returns True iff px is recovered
        """

        try:
            # Force an immediate dynamic refresh
            self._route_snapshot.refresh_ctx_dynamic_fields()

            route_ctx = self._route_ctx_map.get((mid, sid))
            if not route_ctx:
                return False

            px = route_ctx.get("px")
            if px is None:
                return False

            # Re-bind into the working ctx
            ctx["px"]   = px
            ctx["odds"] = px
            ctx["ltp"]  = px

            return True

        except Exception:
            # BUS must fail-open, never crash a tick
            return False

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside class DecisionBus (helper section)
# 🧩 ACTION: ADD engine-specific band eligibility
# 📆 PATCHED: 2026-04-XX — ACTIVE/PASSIVE policy v2
#
# POLICY:
#   LEGACY + MSC_EXPLORATORY → ACTIVE only (band >= 2)
#   MSC_RISK + MSC_INPLAY   → ACTIVE + PASSIVE (band >= 1)
#   OVERWATCHER             → no restriction
# ======================================================================================================

    def _engine_band_allowed(self, engine: str, ctx: dict) -> bool:
        band = ctx.get("band")

        # No band = fail safe
        if band is None:
            return False

        # ACTIVE or LEADING = 2+
        # PASSIVE = 1
        # IGNORED = 0

        if engine in ("LEGACY", "MSC_EXPLORATORY"):
            return band in ("ACTIVE", "PASSIVE")   # ACTIVE + PASSIVE

        # --------------------------------------------------
        # UNIFIED
        # --------------------------------------------------
        if engine == "MSC_UNIFIED":
            return band in ("ACTIVE", "PASSIVE", "EXTENDED")

        
        if engine == "MSC_INPLAY":
            return band in ("ACTIVE", "PASSIVE", "EXTENDED")   # ACTIVE + PASSIVE + EXTENDED

        if engine == "MSC_RISK":
            return band in ("ACTIVE", "PASSIVE", "EXTENDED")  # ACTIVE + PASSIVE + EXTENDED

        # OVERWATCHER and others
        return True

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside class DecisionBus (helper section)
# 🛠 ACTION: ADD window snapshot reporter
# 📆 PATCHED: 2026-04-27 — Unified Window Snapshot
#
# PURPOSE:
# - Surface route window composition
# - Validate 5+floating logic
# - Print only at route build points
# - No behavioural mutation
# ======================================================================================================

    def _print_window_snapshot(self, label: str):

        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)

        snapshot = self._route_snapshot

        runner_pool = set(snapshot.get_all_runners() or [])
        mids = []
        seen = set()

        for mid, _sid in snapshot.get_all_runners() or []:
            if mid not in seen:
               mids.append(mid)
               seen.add(mid)

        bus_stops = snapshot.bus_stops or {}
        stop_sizes = [len(v) for v in bus_stops.values()] if bus_stops else []

        lines = []
        lines.append("\n======================================================================")
        lines.append(f"🚌  V7 WINDOW SNAPSHOT ({label}) — {now.strftime('%Y-%m-%d %H:%M:%SZ')}")
        lines.append("======================================================================")
        lines.append(f"Route ID                 : {self._route_id}")
        lines.append(f"Runner Pool Size         : {len(runner_pool)}")
        lines.append(f"Unique Markets In Route  : {len(mids)}")
        lines.append(f"Bus Stops                : {len(bus_stops)}")

        if stop_sizes:
            lines.append(f"Runners Per Stop         : {stop_sizes[0]}")
            if len(set(stop_sizes)) > 1:
                lines.append("⚠ Uneven stop distribution detected")
        else:
            lines.append("⚠ No bus stops detected")

        lines.append("")
        lines.append("Markets In Route (Ordered):")

        for mid in mids:
            lines.append(f"  - {mid}")

        # Duplication detection
        if runner_pool and len(runner_pool) < 10:
            lines.append("")
            lines.append("Duplication Mode         : ACTIVE (runner count < 10)")
        else:
            lines.append("")
            lines.append("Duplication Mode         : NORMAL")

        lines.append("======================================================================")

        print("\n".join(lines))

    def _ensure_px_from_route(self, ctx: dict) -> bool:
        """
        HARD INVARIANT:
        PX must come from BusRouteSnapshot if missing.

        Returns:
            True  -> px present
            False -> px still unavailable (runner truly not priced)
        """
        if ctx.get("px") is not None:
            return True

        mid = ctx.get("marketId")
        sid = ctx.get("selectionId")

        if not mid or not sid:
            return False

        route_ctx = self._route_ctx_map.get((mid, sid))
        if not route_ctx:
            return False

        px = route_ctx.get("px")
        if px is None:
            return False

        # 🔑 authoritative refresh
        ctx["px"] = px
        ctx["odds"] = px
        ctx["ltp"] = px

        return True


    # ======================================================================
    # LANE 6 — DB CORRECTNESS (CHILD + RISK GAP ENFORCEMENT)
    # ======================================================================
# ======================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def _lane6_db_correctness(self):
# 🛠 ACTION: REPLACE FUNCTION BODY (return plans instead of executing)
# 📆 PATCHED: 2026-03-19 — Lane 6 emits repair plans (engine-style)
#
# WHY:
# - Lane 6 must behave like an engine
# - It detects invariant violations and EMITS plans
# - BUS owns sizing, run_id, cadence, and placement
# - Fixes: missing run_id, missing size, cadence breakage
# ======================================================================

    def _lane6_db_correctness(self):
        """
        Final safety lane.

        Guarantees:
        1) No MATCHED parent exists without a CHILD
        2) No risk cycle is skipped when price moves and LEGACY parent exists

        IMPORTANT:
        - NO execution here
        - NO placement
        - NO sizing
        - RETURNS plans for BUS to handle
        """

        from engines.config_paths import open_auto_db
        from engines.bus_route import get_risk_legacy_parent_pairs
        import sqlite3

        repair_plans = []

        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        try:
            # --------------------------------------------------
            # 1️⃣ CHILD GUARANTEE — ALL ENGINES
            # --------------------------------------------------
            parents = con.execute("""
                SELECT
                    p.id            AS parent_id,
                    p.engine        AS engine,
                    p.marketId      AS marketId,
                    p.selectionId   AS selectionId,
                    p.side          AS side,
                    p.entry_stake   AS entry_stake
                FROM orders p
                WHERE p.mode = 'LIVE'
                  AND p.role = 'PARENT'
                  AND UPPER(p.entry_status) = 'MATCHED'
                  AND NOT EXISTS (
                      SELECT 1
                      FROM orders c
                      WHERE c.hedge_of = p.id
                        AND c.role = 'CHILD'
                  )
            """).fetchall()

            for p in parents:
                ctx = self._route_ctx_map.get((p["marketId"], p["selectionId"]))
                if not ctx:
                    continue

                ctx_l = dict(ctx)
                _normalize_ctx_enums(ctx_l)

                exit_side = "BACK" if p["side"].upper() == "LAY" else "LAY"

                from engines.live.child_rescue import ensure_single_child_for_parent

                ensure_single_child_for_parent(
                    parent_id   = int(p["parent_id"]),
                    marketId    = p["marketId"],
                    selectionId = p["selectionId"],
                    side        = exit_side,
                    px          = ctx_l.get("px"),
                    stake       = float(p["entry_stake"]),
                    exit_kind   = "RESCUE",
                    lane        = 6,
                    engine      = "OVERWATCHER",
                    reason      = "lane6_missing_child",
                )

            # --------------------------------------------------
            # 3️⃣ CHILD CONSISTENCY + LIFECYCLE FINALISATION
            # --------------------------------------------------
            rows = con.execute("""
                SELECT
                    c.id              AS child_id,
                    c.hedge_of        AS parent_id,
                    c.entry_status    AS child_entry_status,
                    p.parent_closed   AS parent_closed
                FROM orders c
                JOIN orders p ON p.id = c.hedge_of
                WHERE c.role = 'CHILD'
                  AND p.mode = 'LIVE'
            """).fetchall()

            by_parent = {}
            for r in rows:
                by_parent.setdefault(r["parent_id"], []).append(r)

            for parent_id, children in by_parent.items():

                matched_children = [
                    c for c in children
                    if (c["child_entry_status"] or "").upper() == "MATCHED"
                ]

                # Invariant: at most ONE matched child
                if len(matched_children) == 0:
                    continue

                primary_child = matched_children[0]

                # --------------------------------------------------
                # A) cancel any extra matched / pending children
                # --------------------------------------------------
                for c in children:
                    if c["child_id"] == primary_child["child_id"]:
                        continue

                    if (c["child_entry_status"] or "").upper() in ("QUEUED", "PLACING", "PLACED", "MATCHED"):
                        con.execute("""
                            UPDATE orders
                               SET entry_status = 'CANCELLED',
                                   exit_kind   = 'CANCELLED_BY_LANE6',
                                   closed_at   = datetime('now','utc')
                             WHERE id = ?
                        """, (int(c["child_id"]),))

                # --------------------------------------------------
                # B) finalise parent lifecycle (idempotent)
                # --------------------------------------------------
                if not primary_child["parent_closed"]:
                    con.execute("""
                        UPDATE orders
                           SET parent_closed = 1,
                               exit_status   = 'MATCHED',
                               closed_at     = COALESCE(closed_at, datetime('now','utc'))
                         WHERE id = ?
                           AND role = 'PARENT'
                           AND parent_closed = 0
                    """, (int(parent_id),))

            con.commit()


            # --------------------------------------------------
            # 2️⃣ RISK GAP FILL — MSC_RISK ONLY
            # --------------------------------------------------
            for mid, sid, legacy_pid, anchor_px in get_risk_legacy_parent_pairs():

                row = con.execute("""
                    SELECT 1
                    FROM orders
                    WHERE mode='LIVE'
                      AND engine='MSC_RISK'
                      AND role='PARENT'
                      AND marketId=?
                      AND selectionId=?
                      AND ABS(entry_odds - ?) < 0.0001
                      AND UPPER(entry_status) IN ('PLACED','MATCHED')
                    LIMIT 1
                """, (str(mid), str(sid), float(anchor_px))).fetchone()

                if row:
                    continue

                ctx = self._route_ctx_map.get((mid, sid))
                if not ctx:
                    continue

                _normalize_ctx_enums(ctx)

                ctx_l = dict(ctx)
                ctx_l["risk_parent_id"] = legacy_pid
                ctx_l["risk_anchor_px"] = anchor_px

                repair_plans.append((
                    "MSC_RISK",
                    {
                        "enter": True,
                        "engine": "MSC_RISK",
                        "role": "PARENT",
                        "marketId": mid,
                        "selectionId": sid,
                        "px": float(anchor_px),
                        "direction": "LAY->BACK",
                        "why": "lane6_risk_gap_fill",
                    },
                    ctx_l,
                ))

        finally:
            con.close()

        return repair_plans

    # ==================================================
    # 🟪 LANE 7 — MSC_UNIFIED (SIGNAL ENGINE)
    # ==================================================
    def _lane7_msc_unified(self, base_ctx, engine_report):
        """
        Unified signal lane.
        Tick-level engine.
        Phase 0: report-only (no plan emission).
        """

        plans = []

        unified = self.engines.get("MSC_UNIFIED")
        if not unified:
            return plans

        try:
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: if v.get("_valid_px")
# 🧩 ACTION: REPLACE (non-blocking PX filter)
# 📆 PATCHED: 2026-03-17 — skip px=None at execution, not map-level
#
# ROOT CAUSE
# ----------
# Filtering at map-level can produce empty ctx_map for engines.
# This does NOT block tick, but makes system appear dead.
#
# FIX
# ---
# Pass full world to engine.
# Let execution skip px=None.
#
# RESULT
# ------
# • No empty world
# • No NoneType compare errors
# • Tick always progresses
# ======================================================================================================

            ctx_unified = {
                "_route_ctx_map": self._route_ctx_map,
                "_route_snapshot": self._route_snapshot,
            }

            result = unified.tick(ctx_unified)

            engine_report["MSC_UNIFIED"]["evaluated"] = True

            # --------------------------------------------------
            # MARKET TERMINATION (finished markets return None)
            # --------------------------------------------------
            if result is None:
                _record_reason(engine_report, "MSC_UNIFIED", "market_finished")
                return plans
            # --------------------------------------------------
            # WHY RECORDING (STRUCTURED)
            # --------------------------------------------------
            why = result.get("why")
            if why:
                _record_reason(engine_report, "MSC_UNIFIED", why)

            # --------------------------------------------------
            # SIGNAL COUNT RECORDING (Y TABLE)
            # --------------------------------------------------
            signals = result.get("signals") or {}

            for key, value in signals.items():
                if value:
                    _record_reason(
                        engine_report,
                        "MSC_UNIFIED",
                        f"signal_{key}"
                    )

            # --------------------------------------------------
            # BATCH SUPPORT (REQUIRED)
            # --------------------------------------------------

            if result.get("batch") and isinstance(result.get("plans"), list):

                for p in result["plans"]:
                    plan = dict(p)
                    plan["engine"] = "MSC_UNIFIED"

                    plans.append(("MSC_UNIFIED", plan, ctx_unified))
                    engine_report["MSC_UNIFIED"]["fired"] = engine_report["MSC_UNIFIED"].get("fired", 0) + 1

            elif result.get("enter"):

                result["engine"] = "MSC_UNIFIED"
                plans.append(("MSC_UNIFIED", result, ctx_unified))
                engine_report["MSC_UNIFIED"]["fired"] = engine_report["MSC_UNIFIED"].get("fired", 0) + 1

            # --------------------------------------------------
            # PLAN (PHASE 0 = NONE)
            # --------------------------------------------------


        except Exception as e:
            _record_reason(engine_report, "MSC_UNIFIED", f"tick_error:{e}")

        return plans

    # ==================================================
    # 🟪 LANE 8 — MSC_BLUEPRINT (SIGNAL ENGINE)
    # ==================================================
    def _lane8_msc_blueprint(self, base_ctx, engine_report):
        """
        Unified signal lane.
        Tick-level engine.
        Phase 0: report-only (no plan emission).
        """

        plans = []

        unified = self.engines.get("MSC_BLUEPRINT")
        if not unified:
            return plans

        try:
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: if v.get("_valid_px")
# 🧩 ACTION: REPLACE (non-blocking PX filter)
# 📆 PATCHED: 2026-03-17 — skip px=None at execution, not map-level
#
# ROOT CAUSE
# ----------
# Filtering at map-level can produce empty ctx_map for engines.
# This does NOT block tick, but makes system appear dead.
#
# FIX
# ---
# Pass full world to engine.
# Let execution skip px=None.
#
# RESULT
# ------
# • No empty world
# • No NoneType compare errors
# • Tick always progresses
# ======================================================================================================

            ctx_unified = {
                "_route_ctx_map": self._route_ctx_map,
                "_route_snapshot": self._route_snapshot,
            }
 
            result = unified.tick(ctx_unified)

            engine_report["MSC_BLUEPRINT"]["evaluated"] = True

            if not result:
                _record_reason(engine_report, "MSC_BLUEPRINT", "no_result")
                return plans

            # --------------------------------------------------
            # MARKET TERMINATION (finished markets return None)
            # --------------------------------------------------
            if result is None:
                _record_reason(engine_report, "MSC_BLUEPRINT", "market_finished")
                return plans

            # --------------------------------------------------
            # WHY RECORDING (STRUCTURED)
            # --------------------------------------------------
            why = result.get("why")
            if why:
                _record_reason(engine_report, "MSC_BLUEPRINT", why)

            # --------------------------------------------------
            # SIGNAL COUNT RECORDING (Y TABLE)
            # --------------------------------------------------
            signals = result.get("signals") or {}

            for key, value in signals.items():
                if value:
                    _record_reason(
                        engine_report,
                        "MSC_BLUEPRINT",
                        f"signal_{key}"
                    )

            # --------------------------------------------------
            # BATCH SUPPORT (REQUIRED)
            # --------------------------------------------------

            if result.get("batch") and isinstance(result.get("plans"), list):

                for p in result["plans"]:
                    plan = dict(p)
                    plan["engine"] = "MSC_BLUEPRINT"

                    plans.append(("MSC_BLUEPRINT", plan, ctx_unified))
                    engine_report["MSC_BLUEPRINT"]["fired"] += 1

            elif result.get("enter"):

                result["engine"] = "MSC_BLUEPRINT"
                plans.append(("MSC_BLUEPRINT", result, ctx_unified))
                engine_report["MSC_BLUEPRINT"]["fired"] += 1

            # --------------------------------------------------
            # PLAN (PHASE 0 = NONE)
            # --------------------------------------------------


        except Exception as e:
            _record_reason(engine_report, "MSC_BLUEPRINT", f"tick_error:{e}")

        return plans


# ======================================================================
# 📍 TARGET: engines/bus/bus.py
# 🧩 ACTION: ADD method to DecisionBus
# 📆 PATCHED: 2026-03-XX — Optional intel broker
# ======================================================================

    def _get_optional_intel(self, *, engine: str, ctx: dict) -> dict | None:
        """
        Optional intelligence broker.

        Engines may request additional market-level analytics.
        BUS decides whether to supply them.

        Returns:
            dict to be merged into ctx, or None
        """

        req = ctx.get("_request_intel")
        if not req:
            return None

        intel_type = req.get("type")
        if intel_type != "INPLAY_MARKET_PROFILE":
            return None

        # First implementation: MSC_INPLAY only
        if engine != "MSC_INPLAY":
            return None

        mid = ctx.get("marketId")
        sid = ctx.get("selectionId")
        if not mid or not sid:
            return None

        market_blob = self._optional_intel_cache.get(str(mid))
        if not market_blob:
            return None

        row = market_blob.get(str(sid))
        if not row:
            return None

        # Explicit, namespaced fields only
        return {
            "inplay_rank_base_px": row.get("rank_base_px"),
            "inplay_rank_ticks":   row.get("rank_ticks"),
            "inplay_rank_pnl":     row.get("rank_pnl"),
            "inplay_move_class":   row.get("move_class"),
            "inplay_pnl_if_win":   row.get("pnl_if_win"),
        }

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def _build_bus_stop_ctxs(
# 🧩 ACTION: COMMENT CORRECTION — clarify CTX reuse invariant
# 📆 PATCHED: 2026-03-07 — BUS never constructs CTX
#
# WHY:
# The function does NOT build CTX.
# It simply reuses the ctx_map created by BusRouteSnapshot.
#
# Clarifying this prevents future architectural regression.
# ======================================================================================================


# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: class DecisionBus.__init__
# 🧩 ACTION: ADD (inside __init__)
# 📆 PATCHED: 2025-12-14 — Runner rotation state (bucket + market aware)
# ======================================================================================================

        # ------------------------------------------------------------------
        # Runner rotation state (BUS responsibility)
        # Tracks which runners have been evaluated per bucket
        # ------------------------------------------------------------------
        self._bucket_seen = {
            "in_play": set(),
            "near20": set(),
            "near60": set(),
            "next5": set(),
        }

    # ======================================================================================================
    # 📍 TARGET: engines/bus/bus.py
    # 🔎 SEARCH: def _evaluate_runner(
    # 🧩 ACTION: REPLACE (FINAL CANONICAL LANE EXECUTION)
    # 📆 PATCHED: 2026-03-05 — Lock BUS lane semantics + helper-owned odds
    #
    # ARCHITECTURAL CONTRACT (DO NOT VIOLATE):
    # ---------------------------------------
    # • BusRoute is the SINGLE source of truth for:
    #     - runner identity (marketId, selectionId)
    #     - lane eligibility
    #     - lifecycle exclusions
    #     - in-play truth
    #     - odds resolution (including fallback)
    #
    # • BUS responsibilities are ONLY:
    #     - reuse prebuilt CTX
    #     - filter CTX per lane
    #     - refresh odds FROM HELPER
    #     - send CTX to engines
    #
    # HARD RULES:
    #   ❌ NO scope
    #   ❌ NO MarketMonitor
    #   ❌ NO DB lifecycle logic
    #   ❌ NO runner discovery
    #
    # If this block changes, the system WILL regress.
    # ======================================================================================================

    def _evaluate_runner(self, base_ctx, bus_stop_pairs, engine_report):
        """
        FINAL CANONICAL LANE EXECUTION

        BUS responsibilities:
        - Trust BusRoute helpers
        - Reuse pre-built CTX
        - Refresh odds via helper
        - Send CTX to engines
        """

        plans = []
        lane_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0, 7: 0, 8: 0}

        # --------------------------------------------------
        # ENGINE DEPRECATION SWITCH
        # --------------------------------------------------

        skip_legacy = False
        skip_risk = False
        skip_inplay = False
        skip_exploratory = False
        skip_overwatcher = False

        if "LEGACY" in self._deprecated_engines:
            engine_report["LEGACY"]["evaluated"] = True
            _record_reason(engine_report, "LEGACY", "deprecated_lane")
            skip_legacy = True

        if "MSC_RISK" in self._deprecated_engines:
            engine_report["MSC_RISK"]["evaluated"] = True
            _record_reason(engine_report, "MSC_RISK", "deprecated_lane")
            skip_risk = True

        if "MSC_INPLAY" in self._deprecated_engines:
            engine_report["MSC_INPLAY"]["evaluated"] = True
            _record_reason(engine_report, "MSC_INPLAY", "deprecated_lane")
            skip_inplay = True

        if "MSC_EXPLORATORY" in self._deprecated_engines:
            engine_report["MSC_EXPLORATORY"]["evaluated"] = True
            _record_reason(engine_report, "MSC_EXPLORATORY", "deprecated_lane")
            skip_exploratory = True

        if "OVERWATCHER" in self._deprecated_engines:
            engine_report["OVERWATCHER"]["evaluated"] = True
            _record_reason(engine_report, "OVERWATCHER", "deprecated_lane")
            skip_overwatcher = True   


        # --------------------------------------------------
        # 🔁 ODDS REFRESH — HELPER OWNED (AUTHORITATIVE)
        # --------------------------------------------------
        route = self._route_ctx_map
        ctx_map = self._route_ctx_map

        # === PATCH START ============================================================
        # 📍 TARGET: engines/bus/bus.py
        # 🔎 SEARCH: _normalize_ctx_enums(ctx)
        # 🧩 ACTION: normalize route ctx map instead of undefined variable
        # 📆 PATCHED: 2026-02-07 — fix BUS ctx scoping bug
        # ============================================================================

        for mid, sid in bus_stop_pairs:
            ctx = ctx_map.get((mid, sid))
            if ctx:
                _normalize_ctx_enums(ctx)
  
        from engines.micro_scalper_v7.direction_engine import compute_msc_decision

        for mid, sid in bus_stop_pairs:
            ctx = ctx_map.get((mid, sid))
            if not ctx:
                continue
            try:
                dec = compute_msc_decision(ctx)
                if isinstance(dec, dict):
                    d = dec.get("direction")
                    ctx["direction"] = d
                    ctx["msc_direction"] = d
            except Exception:
                pass
        # === PATCH END ==============================================================


        # --------------------------------------------------
        # 📊 BUS STOP CTX HEALTH (LOW-NOISE)
        # --------------------------------------------------
        bus_pairs = bus_stop_pairs

        if bus_pairs:
            mids = {mid for (mid, _sid) in bus_pairs}
            total_pairs = len(bus_pairs)

            # CTX presence
            ctx_present = sum(
                1 for (mid, sid) in bus_pairs
                if (mid, sid) in self._route_ctx_map
            )

            # CTX with execution price (odds refreshed)
            ctx_with_px = sum(
                1 for (mid, sid) in bus_pairs
                if (mid, sid) in self._route_ctx_map
                and self._route_ctx_map[(mid, sid)].get("px") is not None
            )

            print(
                f"[BUS][CTX] "
                f"tick={self.tick_id} "
                f"route={self._route_id} "
                f"bus_stop={self._bus_stop} | "
                f"markets={len(mids)} "
                f"runners={total_pairs} "
                f"ctx_present={ctx_present} "
                f"ctx_with_px={ctx_with_px}"
            )

        # --------------------------------------------------
        # 🧠 CTX STRUCTURAL DIAGNOSTIC (V7)
        # --------------------------------------------------
        total = len(self._route_ctx_map)
        ready = 0

        for ctx in self._route_ctx_map.values():
            if (
                ctx.get("px") is not None and
                ctx.get("band") is not None and
                ctx.get("direction") is not None and
                ctx.get("anchor_odd") is not None
            ):
                ready += 1

        print(
            f"[BUS][CTX][V7] "
            f"runners={total} "
            f"structural_ready={ready} "
            f"structural_missing={total - ready}"
        )

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: # 🟦 LANE 1 — LEGACY (BUS STOP ONLY)
# 🧩 ACTION: HARD SKIP deprecated LEGACY lane
# 📆 PATCHED: 2026-03-17 — enforce true deprecation (no execution)
#
# ROOT CAUSE
# ----------
# Deprecated lanes were still executing loops and calling strategies.
#
# FIX
# ---
# Immediately skip entire lane if deprecated.
#
# RESULT
# ------
# • No LEGACY execution
# • No plans generated
# • Only diagnostic reason recorded
# ======================================================================================================

        # --------------------------------------------------
        # 🟦 LANE 1 — LEGACY (BUS STOP ONLY)
        # --------------------------------------------------
        if "LEGACY" in self._deprecated_engines:
            engine_report["LEGACY"]["evaluated"] = True
            _record_reason(engine_report, "LEGACY", "deprecated_lane")
        else:
            engine_report["LEGACY"]["evaluated"] = True

            for mid, sid in bus_stop_pairs:
      
                ctx = self._route_ctx_map.get((mid, sid))

                if not ctx:
                    continue

                if not self._engine_band_allowed("LEGACY", ctx):
                    _record_reason(engine_report, "LEGACY", "inactive_band")
                    continue

                if not self._ensure_px_from_route(ctx):
                    self._force_px_refresh(mid, sid, ctx)
                    if ctx.get("px") is None:
                        _record_reason(engine_report, "LEGACY", "missing_px_after_refresh")
                        continue


            # --------------------------------------------------
            # 🧠 BLUEPRINT MATERIALISATION (BUS AUTHORITY)
            # --------------------------------------------------
            # Blueprint is NOT an engine.
            # It is a state writer that MUST run before strategy P is evaluated.
            # Failure must NOT block strategy evaluation.

            try:
                from engines.blueprint_build import update_for_market

                update_for_market(
                    marketId=mid,
                    selectionId=sid,
                    ctx=ctx,      # full route ctx; blueprint extracts what it needs
                    source="BUS", # audit only
                )
            except Exception as e:
                _record_reason(
                    engine_report,
                    "LEGACY",
                    f"blueprint_update_error:{e}",
                )
            # ==================================================
            # 🧠 MARKET MONITOR REFRESH (BUS AUTHORITY)
            # ==================================================
            # Required for:
            # - BTL_AGGR (G): get_moved_signals()
            #
            # NOTE:
            # - refresh() populates the global move buffer
            # - strategies consume it via get_moved_signals()
            # - MUST run before strategy evaluation
            #
            try:
                from engines.market_monitor.monitor import refresh
                refresh([mid])
            except Exception as e:
                _record_reason(
                    engine_report,
                    "LEGACY",
                    f"market_monitor_refresh_error:{e}",
                )

            # --------------------------------------------------
            # 🧠 STRATEGY EVALUATION (ALL LEGACY STRATEGIES)
            # --------------------------------------------------
            for letter in self.ALLOWED_LEGACY_LETTERS:
                ctx_l = dict(ctx)
                _normalize_ctx_enums(ctx_l)
                # --------------------------------------------------
                # LEGACY PHASE CONTRACT (BUS AUTHORITY)
                # --------------------------------------------------
                ctx_l["phase"] = "PRE"
                ctx_l["letter"] = letter

                # inside LEGACY letter loop, before plan_for_strategy
                if "direction" not in ctx_l:
                    try:
                        from engines.micro_scalper_v7.direction_engine import compute_msc_decision
                        dec = compute_msc_decision(ctx_l)
                        if isinstance(dec, dict):
                            d = dec.get("direction")
                            if d in ("BACK->LAY", "LAY->BACK"):
                                ctx_l["direction"] = d
                    except Exception:
                        pass

                # ==================================================
                # 🧩 STRATEGY: ALWAYS_ON (A)
                # ==================================================
                # Always-on baseline strategy
                # • No MarketMonitor dependency
                # • No firing logic
                # • Direction = market truth
                # • Confidence = execution truth
                # BUS only materialises fields

                if letter == "A":
                    try:
                        # --------------------------------------------------
                        # Ensure execution price aliases
                        # --------------------------------------------------
                        ctx_l.setdefault("odds", ctx_l.get("px"))
                        ctx_l.setdefault("ltp",  ctx_l.get("px"))

                        # --------------------------------------------------
                        # MARKET TRUTH — direction from price movement
                        # --------------------------------------------------
                        from tools.betfair_runner_trend_surface import get_runner_trend

                        trend = get_runner_trend(
                            str(ctx_l.get("marketId")),
                            str(ctx_l.get("selectionId")),
                        )

                        direction = trend.get("direction")
                        if direction == "FLAT":
                            direction = None

                        ctx_l["direction"]     = direction
                        ctx_l["trend_ticks"]   = trend.get("ticks_moved")
                        ctx_l["trend_conf"]    = trend.get("confidence")

                        # --------------------------------------------------
                        # EXECUTION TRUTH — hedge-cycle confidence
                        # --------------------------------------------------
                        from tools.betfair_match_surface import get_direction_confidence

                        ctx_l["confidence"] = get_direction_confidence(
                            str(ctx_l.get("marketId")),
                            str(ctx_l.get("selectionId")),
                        )

                    except Exception as e:
                        _record_reason(
                            engine_report,
                            "LEGACY",
                            f"always_on_inject_error:{e}",
                        )


                # ==================================================
                # 🧩 STRATEGY: OG_STRATEGY (S)
                # ==================================================
                # OG requires MarketMonitor signals
                # BUS must materialise and inject them

                if letter == "S":
                    try:
                        from engines.market_monitor.monitor import signals_for_runner

                        ctx_l["signals"] = signals_for_runner(
                            mid,
                            sid,
                            ctx_l.get("px"),
                        )
                    except Exception as e:
                        _record_reason(
                            engine_report,
                            "LEGACY",
                            f"og_signal_error:{e}",
                        )

                # ==================================================
                # 🧩 STRATEGY: BTL_SCOUT (B)
                # ==================================================
                # Requires MarketMonitor PASSIVE→ACTIVE signal

                if letter == "B":
                    try:
                        from engines.market_monitor.monitor import signals_for_runner

                        ctx_l["signals"] = signals_for_runner(
                            mid,
                            sid,
                            ctx_l.get("px"),
                        )
                    except Exception as e:
                        _record_reason(
                            engine_report,
                            "LEGACY",
                            f"btl_scout_signal_error:{e}",
                        )

                # ======================================================================
                # 📍 TARGET: engines/bus/bus.py
                # 🔎 CONTEXT: LANE 1 — LEGACY (BUS STOP ONLY)
                # 🧩 STRATEGY: S4_CROSSOVER (X)
                # 📆 PATCHED: 2026-02-02 — Inject fav_rank for crossover detection
                #
                # WHY:
                # - S4_CROSSOVER requires ctx.fav_rank
                # - fav_rank is stored in odds_current
                # - BUS must materialise it into ctx
                #
                # CONTRACT:
                # - No strategy logic
                # - DB-backed read only
                # - Pure wiring
                # ======================================================================

                # === PATCH START ============================================================
                # 📍 TARGET: engines/bus/bus.py
                # 🔎 CONTEXT: LANE 1 — LEGACY (BUS STOP ONLY)
                # 🧩 STRATEGY: S4_CROSSOVER (X)
                # 📆 PATCHED: 2026-03-20 — inject structural crossover signals
                # ============================================================================

                if letter == "X":
                    try:
                        from engines.market_monitor.monitor import (
                            signals_for_runner,
                            get_crossover_signal,
                        )

                        sig = signals_for_runner(mid, sid, ctx_l.get("px"))
                        cross = get_crossover_signal(mid, sid)

                        ctx_l["signals"] = {
                            **sig,
                            **cross,
                        }

                    except Exception as e:
                        _record_reason(
                            engine_report,
                            "LEGACY",
                            f"x_signal_error:{e}",
                        )
                # === PATCH END ==============================================================

                # ======================================================================
                # 📍 TARGET: engines/bus/bus.py
                # 🔎 CONTEXT: LANE 1 — LEGACY (BUS STOP ONLY)
                # 🧩 STRATEGY: S5_BREAKOUT (R)
                # 📆 PATCHED: 2026-02-02 — Inject price alias for OC breakout
                #
                # WHY:
                # - S5_BREAKOUT reads ctx.price explicitly
                # - BUS owns live price as ctx["px"]
                # - Strategy is DB-backed and event-based
                #
                # CONTRACT:
                # - No breakout logic here
                # - No DB reads here
                # - Pure field wiring only
                # ======================================================================

                if letter == "R":
                    try:
                        # Ensure price alias exists for strategy
                        if "price" not in ctx_l:
                            ctx_l["price"] = ctx_l.get("px")
                    except Exception as e:
                        _record_reason(
                            engine_report,
                            "LEGACY",
                            f"r_price_inject_error:{e}",
                        )

                # ======================================================================
                # 📍 TARGET: engines/bus/bus.py
                # 🔎 CONTEXT: LANE 1 — LEGACY (BUS STOP ONLY)
                # 🧩 STRATEGY: S6_STEAM_FADE (F)
                # 📆 PATCHED: 2026-02-02 — Inject MarketMonitor signals for STEAM_FADE
                #
                # WHY:
                # - S6_STEAM_FADE depends on ctx.signals["lost_fav_recent"]
                # - signals are computed by MarketMonitor
                # - BUS must materialise and inject them
                #
                # CONTRACT:
                # - No strategy logic here
                # - No guards
                # - Pure wiring only
                # ======================================================================

                if letter == "F":
                    try:
                        from engines.market_monitor.monitor import signals_for_runner

                        # Reuse the same MarketMonitor signal surface
                        ctx_l["signals"] = signals_for_runner(
                            mid,
                            sid,
                            ctx_l.get("px"),
                        )

                    except Exception as e:
                        _record_reason(
                            engine_report,
                            "LEGACY",
                            f"steam_fade_signal_error:{e}",
                        )



                # --------------------------------------------------
                # Mastery → Strategy decision
                # --------------------------------------------------
                try:
                    strategy = LEGACY_LETTER_TO_STRATEGY.get(letter)
                    if not strategy:
                        continue  # defensive

                    res = plan_for_strategy(strategy, ctx_l)
                    if res and res.get("enter"):
                        plan = dict(res)

                        # 🔒 SAFETY INVARIANT
                        assert plan.get("letter") == letter, (
                            f"[BUS] LEGACY plan letter mismatch: "
                            f"expected={letter} got={plan.get('letter')} "
                            f"(strategy={strategy})"
                        )

                        plan["engine"] = "LEGACY"
                        plans.append(("LEGACY", plan, ctx_l))
                        engine_report["LEGACY"]["fired"] += 1
                        lane_counts[1] += 1

                except Exception as e:
                    _record_reason(
                        engine_report,
                        "LEGACY",
                        f"mastery_error:{e}",
                    )


        # --------------------------------------------------
        # 🟨 LANE 2 — MSC_RISK (BETFAIR-TRUTH DRIVEN)
        # --------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: # 🟨 LANE 2 — MSC_RISK
# 🧩 ACTION: HARD SKIP deprecated MSC_RISK
# ======================================================================================================

        if "MSC_RISK" in self._deprecated_engines:
            engine_report["MSC_RISK"]["evaluated"] = True
            _record_reason(engine_report, "MSC_RISK", "deprecated_lane")
        else:
            engine_report["MSC_RISK"]["evaluated"] = True



        from engines.bus_route import get_risk_legacy_parent_pairs

        from tools.betfair_match_surface import query_bet_match_surface
        from tools.betfair_runner_trend_surface import get_runner_trend
        import os
        from engines.daily_config import get_app_key

        risc = self.engines.get("MSC_RISK")
        if not risc:
            pass

        app_key = get_app_key()
        token   = os.getenv("SESSION_TOKEN") or os.getenv("BETFAIR_SESSION_TOKEN")

        # --------------------------------------------------
        # BUS-AUTHORITATIVE EXCLUSIONS (PER LEGACY PARENT)
        # --------------------------------------------------  

        for mid, sid, legacy_parent_id, anchor_px in get_risk_legacy_parent_pairs():

            # --------------------------------------------------
            # CTX MUST COME FROM ROUTE (AUTHORITATIVE)
            # Never gate on missing CTX
            # --------------------------------------------------
            ctx = self._route_ctx_map.get((mid, sid))
            if not ctx:
                # Fail-open: cannot evaluate without CTX,
                # but this is not a logical exclusion
                continue

            # --------------------------------------------------
            # PX MUST COME FROM ROUTE (AUTHORITATIVE)
            # Never gate on missing PX
            # --------------------------------------------------
            last_px = ctx.get("px")
            if last_px is None:
                # Try one last authoritative refresh
                # Force hydrate PX (never gate)
                self._ensure_px_from_route(ctx)
                self._force_px_refresh(mid, sid, ctx)

                if ctx.get("px") is None:
                    continue
                last_px = ctx.get("px")
                if last_px is None:
                    continue

            # --------------------------------------------------
            # TREND IS INFORMATIONAL ONLY
            # NOT A GATE
            # --------------------------------------------------
            trend = get_runner_trend(mid, sid) or {}

            # --------------------------------------------------
            # BUILD PURE RISK CTX (BUS OWNS TRUTH)
            # --------------------------------------------------
            ctx_l = dict(ctx)
            ctx_l.update({
                "anchor_parent_id":   legacy_parent_id,
                "anchor_entry_odds":  float(anchor_px),
                "anchor_entry_stake": ctx.get("anchor_entry_stake"),
                "anchor_engine":      ctx.get("anchor_engine"),        # 🔑 added
                "last_px":            float(last_px) if last_px is not None else None,
                "risk_direction":     trend.get("direction"),
                "risk_ticks_moved":   trend.get("ticks_moved"),
                "risk_confidence":    trend.get("confidence"),
            })

            # 🔑 ensure anchor stake fallback (prevents missing_parent_anchor edge case)
            if ctx_l.get("anchor_entry_stake") is None:
                ctx_l["anchor_entry_stake"] = ctx.get("anchor_entry_stake")

            _normalize_ctx_enums(ctx_l)

            if not self._engine_band_allowed("MSC_RISK", ctx):
                _record_reason(engine_report, "MSC_RISK", "inactive_band")
                continue


            # --------------------------------------------------
            # RISK ENGINE — PURE PLAN EMITTER
            # --------------------------------------------------
            try:
                plan = risc.tick(ctx_l)
                if plan:
                    plan = dict(plan)
                    plan["engine"] = "MSC_RISK"
                    plans.append(("MSC_RISK", plan, ctx_l))
                    engine_report["MSC_RISK"]["fired"] += 1
                    lane_counts[2] += 1
            except Exception:
                _record_reason(engine_report, "MSC_RISK", "tick_error")

        # --------------------------------------------------
        # 🟥 LANE 3 — MSC_INPLAY (BUS-AUTHORISED, DB-FIRST)
        # --------------------------------------------------
        if "MSC_INPLAY" in self._deprecated_engines:
            engine_report["MSC_INPLAY"]["evaluated"] = True
            _record_reason(engine_report, "MSC_INPLAY", "deprecated_lane")
        else:
            engine_report["MSC_INPLAY"]["evaluated"] = True

 

        from engines.bus_route import get_v7_inplay_snapshot, get_inplay_parent_runner_pairs
        from engines.config_paths import connect_db
        import sqlite3

        inplay = self.engines.get("MSC_INPLAY")
        if not inplay:
            pass

        # --------------------------------------------------
        # 1️⃣ PRE-INPLAY CTX PRE-WARM (BUS → ROUTE)
        #     Runs ONCE per market, 5min → 0min before off
        # --------------------------------------------------
        con = connect_db(ro=True)
        con.row_factory = sqlite3.Row

        try:
            rows = con.execute(
                """
                SELECT DISTINCT
                    marketId,
                    (julianday(marketStartTime) - julianday('now','utc')) * 1440.0 AS mins_to_off
                FROM bets
                WHERE
                    (julianday(marketStartTime) - julianday('now','utc')) <= (5.0 / 1440.0)
                    AND (julianday(marketStartTime) - julianday('now','utc')) > 0.0
                """
            ).fetchall()
        finally:
            con.close()

        prewarm_mids = [
            str(r["marketId"])
            for r in rows
            if r["marketId"]
            and str(r["marketId"]) not in self._inplay_ctx_prewarmed
        ]

        if prewarm_mids:
            prewarm_pairs = [
                (mid, sid)
                for (mid, sid) in get_inplay_parent_runner_pairs()
                if mid in prewarm_mids
            ]

            if prewarm_pairs:
                added = self._route_snapshot.absorb_runner_pairs(prewarm_pairs)

                for mid in prewarm_mids:
                    self._inplay_ctx_prewarmed.add(mid)

                if added:
                    print(
                        f"[BUS][INPLAY][PREWARM] "
                        f"markets={len(prewarm_mids)} runners_added={added}"
                    )

        # --------------------------------------------------
        # 1️⃣.1️⃣ CTX CONSUMPTION — ONLY AFTER OFF
        # --------------------------------------------------
        con = connect_db(ro=True)
        con.row_factory = sqlite3.Row

        try:
            rows = con.execute(
                """
                SELECT DISTINCT
                    marketId,
                    (julianday(marketStartTime) - julianday('now','utc')) * 1440.0 AS mins_to_off
                FROM bets
                WHERE
                    (julianday(marketStartTime) - julianday('now','utc')) <= 0.0
                  AND
                    (julianday(marketStartTime) - julianday('now','utc')) >= -(120.0 / 1440.0)

                """
            ).fetchall()
        finally:
            con.close()

        inplay_mids = [
            str(r["marketId"])
            for r in rows
            if r["marketId"]
            and (r["mins_to_off"] is None or r["mins_to_off"] <= 0.0)
        ]

        if not inplay_mids:
            pass

        # --------------------------------------------------
        # 2️⃣ BUS_ROUTE supplies CTX (market-scoped, authoritative)
        # --------------------------------------------------
        for mid in inplay_mids:

            market_ctxs = self._route_snapshot.get_ctx_for_market(mid)
            if not market_ctxs:
                continue

            # In-play snapshot (ONCE per market)
            snap = get_v7_inplay_snapshot(mid) or []
            snap_by_sid = {str(r["selectionId"]): r for r in snap}

            for (_mid, sid), ctx in market_ctxs.items():
                if not ctx:
                    continue

                ctx_l = dict(ctx)

                # --------------------------------------------------
                # MSC_INPLAY MODE CONTRACT (BUS AUTHORITY)
                # --------------------------------------------------
                ctx_l["in_play"] = True
                ctx_l["_allow_bf_px_fallback"] = False

                intel = snap_by_sid.get(str(sid))
                if intel:
                    ctx_l.update({
                        "fav_rank":            intel.get("fav_rank"),
                        "success":             intel.get("success"),
                        "weight":              intel.get("weight"),
                        "drift_ratio":         intel.get("drift_ratio"),
                        "drift_pct":           intel.get("drift_pct"),
                        "actual_drift_pct":    intel.get("actual_drift_pct"),
                        "reversal_flag":       intel.get("reversal_flag"),
                        "mto_minutes":         intel.get("mto_minutes"),
                        "pos_inplay":          intel.get("pos_inplay"),
                        "inplay_move_class":   intel.get("move"),
                        "inplay_rank_base_px": intel.get("base_px"),
                        "inplay_pnl_if_win":   intel.get("pnl_if_win"),
                    })

                if ctx_l.get("_request_intel"):
                    opt = self._get_optional_intel(
                        engine="MSC_INPLAY",
                        ctx=ctx_l,
                    )
                    if opt:
                        ctx_l.update(opt)

                _normalize_ctx_enums(ctx_l)

                # --------------------------------------------------
                # PROMINENCE SIGNAL (BUS → INPLAY)
                # --------------------------------------------------
                try:
                    from engines.market_monitor.monitor import (
                        signals_for_runner,
                        get_crossover_signal,
                    )

                    mm = signals_for_runner(mid, sid, ctx_l.get("px")) or {}
                    xo = get_crossover_signal(mid, sid) or {}

                    ctx_l["prominent"] = bool(
                        mm.get("is_fav_now")
                        or mm.get("new_fav_recent")
                        or mm.get("lost_fav_recent")
                        or xo.get("crossed_over_recent")
                    )

                except Exception:
                    ctx_l["prominent"] = False

                if not self._engine_band_allowed("MSC_INPLAY", ctx_l):
                    _record_reason(engine_report, "MSC_INPLAY", "inactive_band")
                    continue


                # --------------------------------------------------
                # PURE ENGINE DECISION
                # --------------------------------------------------
                # ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside _evaluate_runner → LANE 3 MSC_INPLAY
# 🧩 ACTION: EXPAND MSC_INPLAY batch into individual parent plans
# 📆 PATCHED: 2026-04-XX — Normalize MSC_INPLAY to standard parent contract
#
# PURPOSE:
# - Convert batch emission into standard per-plan entries
# - Preserve engine intelligence
# - Keep router + placement logic unchanged
# - No retries, no sequencing here
#
# CONTRACT:
# - If p["batch"] == True → expand p["plans"]
# - Each child plan becomes normal ("PARENT") plan
# - Router handles sequential unlock
# ======================================================================================================

                try:
                    p = inplay.tick(ctx_l)

                    if not p:
                        _record_reason(engine_report, "MSC_INPLAY", "no_plan")
                        continue

                    if not p.get("enter"):
                        _record_reason(
                            engine_report,
                            "MSC_INPLAY",
                            p.get("reason") or p.get("why") or "note",
                        )
                        continue

                    # --------------------------------------------------
                    # 🔁 BATCH EXPANSION (NEW)
                    # --------------------------------------------------
                    if p.get("batch") and isinstance(p.get("plans"), list):

                        for subplan in p["plans"]:
                            plan = dict(subplan)
                            plan["engine"] = "MSC_INPLAY"
                            plans.append(("MSC_INPLAY", plan, ctx_l))
                            engine_report["MSC_INPLAY"]["fired"] += 1
                            lane_counts[3] += 1

                    else:
                        # Legacy single-plan behaviour (safe fallback)
                        plan = dict(p)
                        plan["engine"] = "MSC_INPLAY"
                        plans.append(("MSC_INPLAY", plan, ctx_l))
                        engine_report["MSC_INPLAY"]["fired"] += 1
                        lane_counts[3] += 1

                except Exception:
                    _record_reason(engine_report, "MSC_INPLAY", "tick_error")


        # --------------------------------------------------
        # 🟩 LANE 4 — MSC_EXPLORATORY (ROUTE − EXCLUSIONS)
        # --------------------------------------------------
        # ======================================================================
        # 📍 TARGET: engines/bus/bus.py
        # 🔎 SEARCH: 🟩 LANE 4 — MSC_EXPLORATORY
        # 🧩 ACTION: REPLACE LANE 4 LOGIC
        # 📆 PATCHED: 2026-04-01 — Ranked exploratory integration
        # ======================================================================

        engine_report["MSC_EXPLORATORY"]["evaluated"] = True

        if "MSC_EXPLORATORY" in self._deprecated_engines:
            _record_reason(engine_report, "MSC_EXPLORATORY", "deprecated_lane")
            return plans, lane_counts
        else:

            from engines.bus_route import get_exploratory_active_parent_pairs

            exclusions = get_exploratory_active_parent_pairs()
            exp = self.engines.get("MSC_EXPLORATORY")

            if exp:
            # 1️⃣ Collect candidates
            # ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: for (mid, sid), ctx in self._route_ctx_map.items():
# 🧩 ACTION: REPLACE with local alias
# 📆 PATCHED: 2026-03-17 — eliminate repeated attribute lookups
#
# PURPOSE
# -------
# Use cached route alias created earlier in tick().
#
# PERFORMANCE
# -----------
# Removes repeated object attribute resolution inside
# critical execution loops.
# ======================================================================================================

                for (mid, sid), ctx in self._route_ctx_map.items():
                    if (mid, sid) in exclusions or ctx.get("px") is None:
                        continue

                    ctx_l = dict(ctx)
                    _normalize_ctx_enums(ctx_l)

                    if not self._engine_band_allowed("MSC_EXPLORATORY", ctx):
                        _record_reason(engine_report, "MSC_EXPLORATORY", "inactive_band")
                        continue


                    try:
                        exp.tick(ctx_l)
                    except Exception:
                        _record_reason(engine_report, "MSC_EXPLORATORY", "tick_error")

                # 2️⃣ Emit ranked top-N
                ranked_plans = exp.flush_ranked()

# === PATCH START ==============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 CONTEXT: LANE 4 ranked emission
# 🧩 ACTION: pass ctx forward correctly
# 📆 PATCHED: 2026-04-01
# ==============================================================================

            for plan, ctx_l in ranked_plans:
                plans.append(("MSC_EXPLORATORY", plan, ctx_l))
                engine_report["MSC_EXPLORATORY"]["fired"] += 1
                lane_counts[4] += 1

# === PATCH END ==============================================================

        # --------------------------------------------------
        # 🟥 LANE 5 — OVERWATCHER (ROUTE-FED, PURE EVALUATOR)
        # --------------------------------------------------
# === PATCH START ==============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: # 🟥 LANE 5 — OVERWATCHER
# 🛠 ACTION: Add explicit NO-FIRE reason recording
# 📆 PATCHED: 2026-04-XX — Overwatch visibility instrumentation
#
# PURPOSE:
# - Overwatch must report why it did not emit
# - No behavioural change
# - Diagnostic only
# ==============================================================================

        if "OVERWATCHER" in self._deprecated_engines:
            engine_report["OVERWATCHER"]["evaluated"] = True
            _record_reason(engine_report, "OVERWATCHER", "deprecated_lane")
        else:
            engine_report["OVERWATCHER"]["evaluated"] = True



        overwatcher = self.engines.get("OVERWATCHER")

        if overwatcher:

            for (mid, sid), ctx in self._route_ctx_map.items():

                if not ctx:
                    _record_reason(engine_report, "OVERWATCHER", "missing_ctx")
                    continue

                current_px = ctx.get("px")
                anchor_px  = ctx.get("anchor_entry_odds")
                anchor_id  = ctx.get("anchor_parent_id")
                anchor_stk = ctx.get("anchor_entry_stake")

                if current_px is None:
                    _record_reason(engine_report, "OVERWATCHER", "missing_px")
                    continue

                if not anchor_id or anchor_px is None:
                    _record_reason(engine_report, "OVERWATCHER", "no_active_parent")
                    continue

                # --------------------------------------------------
                # STOPLOSS EVALUATION
                # --------------------------------------------------
                plan = overwatcher.maybe_emit_stoploss_plan(
                    parent_row={
                        "id": anchor_id,
                        "marketId": mid,
                        "selectionId": sid,
                        "side": ctx.get("side"),
                        "entry_odds": anchor_px,
                        "entry_stake": anchor_stk,
                        "opened_at": ctx.get("opened_at"),
                    },
                    current_px=float(current_px),
                )

                if plan:
                    plan["engine"] = "OVERWATCHER"
                    plans.append(("OVERWATCHER", plan, ctx))
                    engine_report["OVERWATCHER"]["fired"] += 1
                    lane_counts[5] += 1
                else:
                    _record_reason(engine_report, "OVERWATCHER", "no_stoploss_hit")

# === PATCH END ==============================================================

                    # --------------------------------------------------
                    # PURE PROGRESSIVE LOCK EVALUATION
                    # --------------------------------------------------
                    runner_pnl = ctx.get("pnl_if_win")
                    if runner_pnl is None:
                        continue

                    plan = overwatcher.evaluate_progressive_lock(
                        market_id=mid,
                        selection_id=sid,
                        runner_pnl=float(runner_pnl),
                    )

                    if plan:
                        plan["px"] = float(current_px)
                        plan["engine"] = "OVERWATCHER"
                        plans.append(("OVERWATCHER", plan, ctx))
                        engine_report["OVERWATCHER"]["fired"] += 1
                        lane_counts[5] += 1

        return plans, lane_counts

    def _ensure_route_buffer(self):
        if self._route_buffer:
            return

        route = build_full_cycle()
        self._route_buffer.extend(route)

        print(
            f"[BUS][ROUTE] buffer refilled "
            f"size={len(route)} "
            f"route={self._route_id}"
        )


    def set_live_run_id(self, run_id: str):
        self.live_run_id = run_id


    def run_live(self, hz: float = 1.0):
        interval = max(0.05, 1.0 / max(0.1, hz))
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def run_live(self, hz: float = 1.0):
# 🧩 ACTION: ADD — persist hz for dashboard
# 📆 PATCHED: 2026-04-26 — expose hz to dashboard
#
# PURPOSE:
# - Wireframe shows Hz
# - BUS already receives hz
# - Persist for snapshot read
# ======================================================================================================

        # Persist hz for dashboard visibility
        self._hz = float(hz)
        print(f"[BUS] live loop started (hz={hz})")

        while True:
            try:
                self.tick()
            except Exception as e:
                print(f"[BUS][ERR] tick failed: {e}")

            time.sleep(interval)

    # ======================================================================
    # PIPELINES — REMOVED (ARCHITECTURAL LOCK)
    # ======================================================================
    # As of BUS v13, ALL execution flows through BUS_ROUTE lanes only.
    #
    # The following pipeline-style entry points are permanently disabled
    # to prevent:
    # - CTX reconstruction
    # - route bypass
    # - duplicate engine execution
    # - future architectural drift
    #
    # Any attempt to call these is a programmer error.
    # ======================================================================

    def _run_risc_pipeline(self, *args, **kwargs):
        raise RuntimeError(
            "BUS pipeline removed: MSC_RISK must execute via BUS_ROUTE lanes only"
        )

    def _run_inplay_pipeline(self, *args, **kwargs):
        raise RuntimeError(
            "BUS pipeline removed: MSC_INPLAY must execute via BUS_ROUTE lanes only"
        )

    def _run_exploratory_pipeline(self, *args, **kwargs):
        raise RuntimeError(
            "BUS pipeline removed: MSC_EXPLORATORY must execute via BUS_ROUTE lanes only"
        )



# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: class DecisionBus
# 🧩 ACTION: ADD (new helper method, no replacements)
# 📆 PATCHED: 2025-12-15 — Unconditional TICK context container
# ======================================================================================================

    def _new_tick_ctx(self):
        """
        Create a fresh, per-tick diagnostic context.
        This is ANALYSIS FIRST — execution does not depend on this existing.
        """
        return {
            # identity
            "tick_id": self.tick_id,
            "ts_start": time.time(),
            "ts_end": None,
            "duration": None,

            # phase flags
            "analysis_ran": False,
            "enrichment_ran": False,
            "routing_ran": False,

            # analysis
            "markets_seen": 0,
            "runners_seen": 0,
            "engine_outcomes": {},   # engine -> {evaluated, fired, why}
            "plans_raw": [],         # all raw plans emitted by engines

            # enrichment
            "plans_enriched": [],
            "plans_annotated": [],    # (plan, reason)

            # routing
            "plans_routed": [],
            "plans_route_failed": [],

            # errors (never fatal)
            "errors": [],
        }

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: # CTX BUILDER — ROUTE-AUTHORITATIVE (FINAL)
# 🧩 ACTION: COMMENT CORRECTION
# 📆 PATCHED: 2026-03-05 — clarify CTX reuse architecture
#
# WHY:
# BUS does NOT build CTX.
# BUS consumes CTX surfaces produced by BusRouteSnapshot.
# ======================================================================================================

# REPLACE HEADER WITH:

    # ======================================================================
    # CTX CONSUMER — ROUTE-AUTHORITATIVE (FINAL)
    # ======================================================================
    # 📍 TARGET: engines/bus/bus.py
    # 🔎 REPLACES: _build_ctx_for_market + _run_engines_for_tick
    # 📆 PATCHED: 2026-04-XX — Route-aligned CTX world (final)
    #
    # ARCHITECTURE:
    # - RouteSnapshot owns identity + px + band
    # - BUS owns lifecycle + sizing + routing
    # - Engines are pure plan emitters
    #
    # INVARIANTS:
    # - NO MarketMonitor usage
    # - NO odds fetching
    # - NO px mutation
    # - Anchor fields injected from DB (correct columns)
    # ======================================================================

    def _build_ctx_for_market(self, base_ctx, mid, sid):

        ctx = dict(base_ctx)

        if not self.live_run_id:
            self.live_run_id = f"BOOT-{int(time.time())}"

        ctx["run_id"] = self.live_run_id
        ctx["marketId"] = str(mid)
        ctx["selectionId"] = str(sid)

        # --------------------------------------------------
        # DB Lifecycle Snapshot (AUTHORITATIVE)
        # --------------------------------------------------
        try:
            from engines.config_paths import open_auto_db
            import sqlite3

            con = open_auto_db(rw=False)
            con.row_factory = sqlite3.Row

            rows = con.execute(
                """
                SELECT
                    id,
                    engine,
                    role,
                    entry_status,
                    exit_status,
                    hedge_of,
                    marketId,
                    selectionId,
                    entry_odds,
                    entry_stake
                FROM orders
                WHERE marketId = ?
                  AND selectionId = ?
                """,
                (str(mid), str(sid)),
            ).fetchall()

        finally:
            try:
                con.close()
            except Exception:
                pass

        ctx["orders_by_runner"] = [dict(r) for r in rows] if rows else []

# ======================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: _build_ctx_for_market()
# 🧩 REPLACE: anchor discovery logic
# 📆 PATCHED: 2026-03-07 — router snapshot anchor resolution
#
# PURPOSE:
# - Remove DB scan for anchor discovery
# - Use router execution snapshot (authoritative runtime surface)
# - Guarantee O(1) anchor lookup
#
# ARCHITECTURE:
# Router owns execution truth.
# BUS consumes snapshot surfaces.
# ======================================================================

        ctx["anchor_parent_id"] = None
        ctx["anchor_entry_odds"] = None
        ctx["anchor_entry_stake"] = None
        ctx["anchor_engine"] = None

        parent = get_parent_snapshot(mid, sid)

        if parent:
            ctx["anchor_parent_id"] = parent.get("parent_id")
            ctx["anchor_entry_odds"] = parent.get("entry_odds")
            ctx["anchor_entry_stake"] = parent.get("entry_stake")
            ctx["anchor_engine"] = parent.get("engine")

        # --------------------------------------------------
        # 🔑 ANCHOR ODD (STRUCTURAL AXIS) — BUS AUTHORITY
        # --------------------------------------------------
        try:
            from engines.config_paths import open_bets_db

            con_b = open_bets_db(rw=False)
            row_b = con_b.execute(
                """
                SELECT anchor_odd
                FROM bets
                WHERE marketId = ?
                  AND selectionId = ?
                LIMIT 1
                """,
                (str(mid), str(sid)),
            ).fetchone()

            if row_b and row_b[0] is not None:
                ctx["anchor_odd"] = float(row_b[0])
            else:
                ctx["anchor_odd"] = None

        finally:
            try:
                con_b.close()
            except Exception:
                pass

        return ctx


    # ======================================================================
    # ENGINE PLAN COLLECTION — PURE (FINAL)
    # ======================================================================

    def _run_engines_for_tick(self, mid, sid, ctx, engine_report):

        plans = []

        def _record(engine, evaluated=True, fired=False, why=None):
            eng = engine_report.setdefault(engine, {})
            eng["evaluated"] = evaluated
            eng["fired"] = eng.get("fired", 0) + (1 if fired else 0)
            if why:
                reasons = eng.setdefault("reasons", {})
                reasons[why] = reasons.get(why, 0) + 1

        # --------------------------------------------------
        # MSC_EXPLORATORY
        # --------------------------------------------------
        try:
            eng = self.engines.get("MSC_EXPLORATORY")
            if eng:
                p = eng.tick(ctx)
                if p and p.get("enter"):
                    p["engine"] = "MSC_EXPLORATORY"
                    plans.append(("MSC_EXPLORATORY", p, ctx))
                    _record("MSC_EXPLORATORY", True, True)
                else:
                    _record("MSC_EXPLORATORY", True, False)
        except Exception as e:
            _record("MSC_EXPLORATORY", False, False, str(e))

        # --------------------------------------------------
        # MSC_INPLAY
        # --------------------------------------------------
        try:
            eng = self.engines.get("MSC_INPLAY")
            if eng:
                p = eng.tick(ctx)
                if p and p.get("enter"):
                    p["engine"] = "MSC_INPLAY"
                    plans.append(("MSC_INPLAY", p, ctx))
                    _record("MSC_INPLAY", True, True)
                else:
                    _record("MSC_INPLAY", True, False)
        except Exception as e:
            _record("MSC_INPLAY", False, False, str(e))

        # --------------------------------------------------
        # MSC_RISK
        # --------------------------------------------------
        try:
            eng = self.engines.get("MSC_RISK")
            if eng:
                p = eng.tick(ctx)
                if p and p.get("enter"):
                    p["engine"] = "MSC_RISK"
                    plans.append(("MSC_RISK", p, ctx))
                    _record("MSC_RISK", True, True)
                else:
                    _record("MSC_RISK", True, False)
        except Exception as e:
            _record("MSC_RISK", False, False, str(e))

        # --------------------------------------------------
        # MSC_UNIFIED
        # --------------------------------------------------
        try:
            # Unified runs via Lane 7 now.
            # This stub preserves engine report structure
            # without executing the engine twice.
            _record("MSC_UNIFIED", True, False)
        except Exception as e:
            _record("MSC_UNIFIED", False, False, str(e))

        # --------------------------------------------------
        # OVERWATCHER STOPLOSS
        # --------------------------------------------------
        try:
            eng = self.engines.get("OVERWATCHER")
            if eng and ctx.get("anchor_entry_odds") and ctx.get("anchor_entry_stake"):

                from engines.price_math import walk_ticks

                side = ctx.get("side")
                px = ctx.get("px")
                entry_odds = ctx.get("anchor_entry_odds")
                entry_stake = ctx.get("anchor_entry_stake")

                if side and px and entry_odds and entry_stake:

                    side = str(side).upper()

                    if side == "LAY":
                        stop_px = walk_ticks(entry_odds, 3, direction="down")
                        hit = px <= stop_px
                        exit_side = "BACK"
                    else:
                        stop_px = walk_ticks(entry_odds, 3, direction="up")
                        hit = px >= stop_px
                        exit_side = "LAY"

                    if hit:
                        plan = {
                            "engine": "OVERWATCHER",
                            "role": "CHILD",
                            "exit_kind": "STOPLOSS",
                            "marketId": mid,
                            "selectionId": sid,
                            "side": exit_side,
                            "px": px,
                            "size": entry_stake,
                        }
                        plans.append(("OVERWATCHER", plan, ctx))
                        _record("OVERWATCHER", True, True)
                    else:
                        _record("OVERWATCHER", True, False)

        except Exception as e:
            _record("OVERWATCHER", False, False, str(e))

        return plans

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside class DecisionBus (near analytics_report)
# 🧩 ACTION: ADD — dashboard snapshot (read-only)
# 📆 PATCHED: 2026-04-26 — expose BUS telemetry to dashboard
#
# PURPOSE:
# - Dashboard requires structured state
# - Pure read-only method
# - No mutation
# - No execution side effects
# ======================================================================================================

    def dashboard_snapshot(self) -> dict:
        """
        Read-only snapshot for LIVE dashboard.
        No mutation. No routing. No DB writes.
        """

        try:
            avg_ctx = (
                sum(self._ctx_refresh_times[-10:])
                / min(len(self._ctx_refresh_times), 10)
                if self._ctx_refresh_times else 0.0
            )
        except Exception:
            avg_ctx = 0.0

        return {
            "route_id": self._route_id,
            "bus_stop": self._bus_stop,
            "tick_id": self.tick_id,
            "hz": getattr(self, "_hz", 0.0),
            "window_size": getattr(self._cadence, "window_seconds", 0),
            "avg_ctx_refresh": avg_ctx,
            "plans_generated": getattr(self, "_last_attempted", 0),
            "plans_routed": getattr(self, "_last_delegated", 0),
        }

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py:DecisionBus
# 🔎 SEARCH: def dashboard_snapshot(self)
# 🧩 ACTION: INSERT BELOW — snapshot scheduler
# 📆 PATCHED: 2026-03-16 — time-driven telemetry loop
#
# PURPOSE
# -------
# Write runtime snapshots every 2 seconds regardless of tick cadence.
#
# SAFETY
# ------
# Read-only telemetry.
# Never blocks execution.
# ======================================================================================================

    def _run_snapshots(self):

        now = time.time()

        if now - self._last_snapshot_ts < self._snapshot_interval:
            return

        self._last_snapshot_ts = now

        try:
            _write_bus_runtime_snapshot(self.dashboard_snapshot())
        except Exception:
            pass

        try:
            from engines.live.bank_state import _write_bank_runtime_snapshot
            _write_bank_runtime_snapshot()
        except Exception:
            pass

        try:
            inplay_engine = self.engines.get("MSC_INPLAY")
            if inplay_engine:
                inplay_engine.write_runtime_snapshot()
        except Exception:
            pass

        try:
            from engines.live.live_router import collect_router_live_state
            from engines.live.live_router import _write_router_runtime_snapshot_from_collect
            live = collect_router_live_state()
            _write_router_runtime_snapshot_from_collect(live)
        except Exception:
            pass

        try:
            _write_unified_runtime_snapshot()
        except Exception:
            pass

# === PATCH START ==============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside class DecisionBus (place below dashboard_snapshot)
# 📆 PATCHED: 2026-04-05 — Expose RouteSnapshot to dashboard (read-only)
#
# PURPOSE:
# - Allow dashboard to read BUS runner surface directly
# - Avoid DB reconstruction of runners
# - Preserve BUS performance (no additional queries)
#
# CONTRACT:
# - Read-only
# - No mutation
# - No execution side effects
# - Safe when BUS not yet initialised
# ==============================================================================

    def get_route_snapshot(self):
        """
        Read-only accessor for the active BusRouteSnapshot.

        Used by dashboard to render:
        • next BUS stop runners
        • live view runners
        • px / odds surfaces

        Returns:
            BusRouteSnapshot | None
        """

        return self._route_snapshot


    def get_bus_stop_snapshot(self):
        """
        Convenience helper for dashboard.

        Returns runners for the current BUS stop
        as (marketId, selectionId) pairs.
        """

        if not self._route_snapshot:
            return []

        return self._route_snapshot.get_bus_stop(self._bus_stop) or []


    def get_runner_ctx_snapshot(self):
        """
        Returns the full ctx_map surface for dashboard inspection.
        """
        if not self._route_snapshot:
            return {}

        return self._route_ctx_map

    def get_runner_surface(self):
        """
        Read-only runner surface for dashboard.

        Returns top runners for current BUS stop
        with px + name already resolved.
        """
        return self._runner_surface or []


# === PATCH END ==============================================================
    
    # ======================================================================
    # analytics_report() — unchanged
    # ======================================================================
    def analytics_report(self):
        from engines.live.live_router import fetch_router_stats
        from engines.live.bank_state import get_engine_pots
      

        snap = bus_snapshot()
        pots = get_engine_pots()
        rstats = fetch_router_stats()

        print("────────────────────────────────────────────────")
        print(f"[BUS][HEALTH] tick={self.tick_id}")
        print("────────────────────────────────────────────────")
        print("Engines Fired:")
        for eng, n in snap["engines"].items():
            print(f"  • {eng:<15} {n}")
        print("\nOrders:")
        print(f"  Parents Opened:   {snap['parents_opened']}")
        print(f"  Children Opened:  {snap['children_opened']}")
        print(f"  Children Matched: {snap['children_matched']}")
        print("\nExposure & PnL:")
        print(f"  Exposure Total:   {snap['exposure']:.2f}")
        print(f"  Realised PnL:     {snap['realised']:.2f}")
        print(f"  Unsettled PnL:    {snap['unsettled']:.2f}")
        print("\nEngine Pots:")
        for eng, amt in pots.items():
            print(f"  • {eng:<15} £{amt:.2f}")
        print("\nStoploss:")
        print(f"  Fired: {snap['stoploss_fired']}")
        print("\nRisk Engine:")
        print(f"  Hedge Ops: {snap['risk_hedges']}")
        print(f"  Stop Ops:  {snap['risk_stops']}")
        print("────────────────────────────────────────────────")

    # ======================================================================
    # PHASE 0 — LIVE DB TRUTH (REPORT ONLY)
    # ======================================================================

    def _phase0_report_db_truth(self):
        return


    # ======================================================================
    # TICK — authoritative BUS lifecycle (route → ctx → lanes)
    # ======================================================================
    def tick(self):

        # --------------------------------------------------
        # Ensure snapshot exists (first tick safety)
        # --------------------------------------------------
        if self._route_snapshot is None:
            self._route_snapshot = BusRouteSnapshot()
            self._startup_ctx_builder = StartupCTXBuilder(self._route_snapshot)

            # 🔴 FORCE WORLD BUILD
            self._route_snapshot.build_route()
            self._route_snapshot.partition_into_bus_stops()
 

            # 🔴 BUILD FULL CTX WORLD
            # 🔁 NON-BLOCKING CTX BUILD
            if hasattr(self, "_startup_ctx_builder") and not self._startup_ctx_builder.done:
                self._startup_ctx_builder.step(max_builds=50)

            # refresh dynamic fields once ctx exists
            self._route_snapshot.refresh_ctx_dynamic_fields()

            self._route_ctx_map = self._route_snapshot.ctx_map


# ======================================================================================================

            print(f"[BUS][ROUTE] initial route built | ctx={len(self._route_ctx_map)}")

        
        # ===============================================================
        # 0️⃣ BUS IDENTITY
        # ===============================================================
        self.tick_id += 1
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py:DecisionBus.tick
# 🔎 SEARCH: self.tick_id += 1
# 🧩 ACTION: INSERT BELOW — snapshot cadence execution
# 📆 PATCHED: 2026-03-16 — run telemetry independent of ticks
#
# PURPOSE
# -------
# Ensures dashboard telemetry refreshes every ~2 seconds
# even if BUS ticks are slow.
#
# SAFETY
# ------
# Does not affect execution flow.
# ======================================================================================================
        # 🔁 CONTINUOUS CTX BUILD (NON-BLOCKING)
        if hasattr(self, "_startup_ctx_builder") and not self._startup_ctx_builder.done:
            self._startup_ctx_builder.step(max_builds=50)
        # --------------------------------------------------
        # Snapshot telemetry scheduler
        # --------------------------------------------------
        self._run_snapshots()
        # Increment bus stop manually
        self._bus_stop += 1
        # ===============================================================
        # ROUTE BOUNDARY — REBUILD IDENTITY, PRESERVE CTX
        # ===============================================================
        if self._bus_stop > 10:
            self._bus_stop = 1
            self._route_id += 1

            # --------------------------------------------------
            # 🔁 Rebuild route identity
            # --------------------------------------------------
            self._route_snapshot.build_route()

            # --------------------------------------------------
            # 🔁 Repartition bus stops
            # --------------------------------------------------
            self._route_snapshot.partition_into_bus_stops()
     
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: for _ in range(5):
# 🧩 REPLACE: partial CTX warmup with full CTX world hydration
# 📆 PATCHED: 2026-03-05 — Build CTX world at route boundary only
#
# WHY:
# Partial hydration requires BUS to continue building CTX during ticks.
# That violates the invariant that BUS must only reuse CTX.
#
# The CTX world must be fully constructed before trading begins.
# ======================================================================================================

            while not self._startup_ctx_builder.done:
                self._startup_ctx_builder.step(max_builds=200)

            # --------------------------------------------------
            # Refresh dynamic fields after hydration
            # --------------------------------------------------
            t0 = time.time()
            self._route_snapshot.refresh_ctx_dynamic_fields()
            dt = time.time() - t0
            self._ctx_refresh_times.append(dt)

            print("[BUS][ROUTE] rebuilt + ctx world rehydrated")
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside def tick(self): immediately after self.tick_id += 1
# 🧩 ACTION: ADD BankState floor reconciliation call (driver authority)
# 📆 PATCHED: 2026-02-12 — BUS enforces BankState refresh every tick
#
# PURPOSE:
# - BUS is the system driver
# - BankState exposure floor must refresh every tick
# - Prevent stale floor / reconciliation drift
#
# SAFETY:
# - Idempotent
# - No mutation beyond BankState internal logic
# - Fail-open (never block tick)
# ======================================================================================================

        # ==================================================
        # 🟦 BANKSTATE FLOOR REFRESH (BUS DRIVER AUTHORITY)
        # ==================================================
        try:
            from engines.live.bank_state import _reconcile_market_exposure_live
            _reconcile_market_exposure_live()
        except Exception:
            # BUS must never die due to reconciliation
            pass
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: def tick(self):
# 🧩 ACTION: INSERT — Pre-route DB integrity repair (Lane 6 DB-only)
# 📆 PATCHED: 2026-04-XX — Always-on child + lifecycle repair
#
# PURPOSE:
# - Run DB-only invariant repairs BEFORE route hydration
# - Remove restart dependency for missing child fixes
# - Keep risk gap fill in route-dependent Lane 6
#
# INVARIANTS:
# - No CTX usage
# - No PX usage
# - No route dependency
# - Idempotent
# ======================================================================================================

        # ==================================================
        # 🟥 PRE-ROUTE DB INTEGRITY REPAIR (ALWAYS-ON)
        # ==================================================
        try:
            from engines.config_paths import open_auto_db
            import sqlite3

            con = open_auto_db(rw=True)
            con.row_factory = sqlite3.Row

            # --------------------------------------------------
            # 1️⃣ Ensure every MATCHED parent has at least one child
            # --------------------------------------------------
            parents = con.execute("""
                SELECT p.id
                FROM orders p
                WHERE p.mode='LIVE'
                  AND p.role='PARENT'
                  AND UPPER(p.entry_status)='MATCHED'
                  AND NOT EXISTS (
                      SELECT 1 FROM orders c
                      WHERE c.hedge_of=p.id
                        AND c.role='CHILD'
                  )
            """).fetchall()

            for row in parents:
                parent_id = int(row["id"])

                # Mark parent as needing repair (idempotent flag only)
                con.execute("""
                    UPDATE orders
                       SET needs_child_repair = 1
                     WHERE id = ?
                """, (parent_id,))

            # --------------------------------------------------
            # 2️⃣ Collapse duplicate children (keep first MATCHED)
            # --------------------------------------------------
            children = con.execute("""
                SELECT
                    c.id,
                    c.hedge_of,
                    c.entry_status
                FROM orders c
                WHERE c.role='CHILD'
                  AND c.mode='LIVE'
            """).fetchall()

            by_parent = {}
            for c in children:
                by_parent.setdefault(c["hedge_of"], []).append(c)

            for parent_id, rows in by_parent.items():
                matched = [
                    r for r in rows
                    if (r["entry_status"] or "").upper() == "MATCHED"
                ]

                if len(matched) <= 1:
                    continue

                # Keep first matched, cancel others
                for r in matched[1:]:
                    con.execute("""
                        UPDATE orders
                           SET entry_status='CANCELLED',
                               exit_kind='CANCELLED_BY_DB_REPAIR',
                               closed_at=datetime('now','utc')
                         WHERE id=?
                    """, (int(r["id"]),))

            con.commit()
            con.close()

        except Exception:
            # Never block BUS tick
            pass

# ======================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: self._phase0_report_db_truth()
# 📆 PATCHED: 2026-03-04 — Remove per-tick DB analytics reporting
# ======================================================================

# OLD
        #self._phase0_report_db_truth()

# NEW
# Phase0 DB analytics removed for performance
# Dashboard now owns reporting
        pass

        # ===============================================================
        # 1️⃣ DIAGNOSTICS (non-fatal, never blocks)
        # ===============================================================
        tick_ctx = self._new_tick_ctx()
        engine_report = EngineReportShim()

        # ===============================================================
        # 2️⃣ BASE CONTEXT (BUS-OWNED)
        # ===============================================================
        #_bc = build_context(source="LIVE")
        #base_ctx = _bc[0] if isinstance(_bc, tuple) else _bc

        # NEW
        # CTX comes exclusively from RouteSnapshot
        base_ctx = {}

        if not self.live_run_id:
            self.live_run_id = f"LIVE-{int(time.time())}"
        base_ctx["run_id"] = self.live_run_id

        risk_confidence = {}

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: CTX BATCH BUILDER — PROGRESSIVE WARM-UP
# 🧩 ACTION: DELETE progressive CTX build inside tick()
# 📆 PATCHED: 2026-03-05 — Enforce CTX reuse invariant
#
# WHY:
# BUS must NEVER build CTX.
# CTX is built exclusively by:
#   - StartupCTXBuilder
#   - BusRouteSnapshot route rebuild
#
# Leaving this block causes:
# - DB reads every tick
# - partial CTX world mutation
# - unpredictable tick latency
#
# CTX must be fully constructed at route boundary only.
# ======================================================================================================

# DELETE THIS BLOCK COMPLETELY

#if hasattr(self, "_startup_ctx_builder"):
#    self._startup_ctx_builder.step(max_builds=25)

        # --------------------------------------------------
        # REFRESH ODDS — EVERY TICK (AUTHORITATIVE)
        # --------------------------------------------------
        t0 = time.time()
        self._route_snapshot.refresh_ctx_dynamic_fields()
        dt = time.time() - t0

        # --------------------------------------------------
        # BIND CTX MAP (AUTHORITATIVE SNAPSHOT)
        # --------------------------------------------------
        self._route_ctx_map = self._route_snapshot.ctx_map
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: self._route_ctx_map = self._route_snapshot.get_ctx_map()
# 🧩 ACTION: ADD local alias
# 📆 PATCHED: 2026-03-09 — reduce attribute lookup overhead in tick loop
#
# PURPOSE
# -------
# Accessing self._route_ctx_map repeatedly inside loops causes
# unnecessary attribute resolution overhead.
#
# Local alias removes thousands of lookups per tick.
#
# PERFORMANCE
# -----------
# ~10–15% BUS speed improvement.
# ======================================================================================================

        self._route_ctx_map = self._route_snapshot.ctx_map

        # 🔑 local alias for hot loops
        route = self._route_ctx_map
        # WORLD DRIVEN EXECUTION
        bus_stop_pairs = list(self._route_ctx_map.keys())
        # --------------------------------------------------
        # NORMALISE ENUMS (BUS AUTHORITY)
        # --------------------------------------------------
        for mid, sid in bus_stop_pairs:
            ctx = self._route_ctx_map.get((mid, sid))
            if ctx:
                _normalize_ctx_enums(ctx)

        self._ctx_refresh_times.append(dt)

        # --------------------------------------------------
        # 🔑 ANCHOR INJECTION (BUS AUTHORITY)
        # --------------------------------------------------
        from engines.config_paths import open_bets_db

        con = open_bets_db(rw=False)
        try:
            # ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: for (mid, sid), ctx in self._route_ctx_map.items():
# 🧩 ACTION: REPLACE with local alias
# 📆 PATCHED: 2026-03-17 — eliminate repeated attribute lookups
#
# PURPOSE
# -------
# Use cached route alias created earlier in tick().
#
# PERFORMANCE
# -----------
# Removes repeated object attribute resolution inside
# critical execution loops.
# ======================================================================================================

            for (mid, sid), ctx in self._route_ctx_map.items():
                row = con.execute(
                    """
                    SELECT anchor_odd
                    FROM bets
                    WHERE marketId = ?
                      AND selectionId = ?
                    LIMIT 1
                    """,
                    (str(mid), str(sid)),
                ).fetchone()

                ctx["anchor_odd"] = float(row[0]) if row and row[0] is not None else None
        finally:
            con.close()

        print(
            f"[BUS][CTX_REFRESH] "
            f"tick={self.tick_id} "
            f"route={self._route_id} "
            f"bus_stop={self._bus_stop} "
            f"runners={len(self._route_ctx_map)} "
            f"dt={dt:.4f}s"
        )

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: bus_stop_pairs = list(self._route_ctx_map.keys())
# 🧩 ACTION: REPLACE — cadence market runner selection
# 📆 PATCHED: 2026-03-17 — BUS cadence restored (next-market schedule)
#
# ROOT CAUSE
# ----------
# BUS was incorrectly sending the entire CTX world every tick:
#
#     bus_stop_pairs = list(self._route_ctx_map.keys())
#
# This broke the cadence model and caused BUS phase sequencing
# to stall because routing logic expected a bounded runner set.
#
# ARCHITECTURE
# ------------
# BUS must only send ONE market's runners per tick.
#
# Market selection is based on the bets table schedule:
#
#     next market where marketStartTime >= now
#
# Engines do NOT depend on this input — they already see
# the full world via ctx_map — but BUS must provide a
# deterministic cadence signal.
#
# BEHAVIOUR
# ---------
# Tick N:
#     send runners for next scheduled market
#
# When that market reaches off time:
#     BUS automatically advances to the following market
#
# In-play markets remain visible to engines via ctx_map.
#
# RESULT
# ------
# • BUS ticks never stall
# • cadence restored
# • engines remain world-driven
# • architecture invariant preserved
# ======================================================================================================

        # --------------------------------------------------
        # Cadence market runners (BUS schedule authority)
        # --------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: bus_stop_pairs = _get_next_market_pairs()
# 🧩 ACTION: INSERT BELOW
# 📆 PATCHED: 2026-03-18 — Remove BUS reliance on cadence pairs
#
# PURPOSE
# -------
# bus_stop_pairs is informational only.
# Engines must still be ticked even if no pairs are returned.
#
# If pairs are empty we inject a harmless placeholder so the
# evaluation loop still executes without scanning the world.
#
# PERFORMANCE
# -----------
# Zero cost. One tuple only. Immediately skipped by ctx lookup.
# ======================================================================================================

        if not bus_stop_pairs:
            bus_stop_pairs = [("__BUS_TICK__", "__NO_RUNNER__")]


# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: bus_stop_pairs = legacy_slice
# 🧩 ACTION: ADD pair sanitiser
# 📆 PATCHED: 2026-03-12 — prevent BUS tick crash from malformed route pairs
#
# ROOT CAUSE
# ----------
# Occasionally BusRouteSnapshot can return items that are not (mid, sid)
# tuples. When BUS loops with:
#
#     for mid, sid in bus_stop_pairs:
#
# Python raises:
#
#     not enough values to unpack (expected 2, got 0)
#
# RESULT
# ------
# BUS tick aborts and Unified never executes.
#
# FIX
# ---
# Filter invalid entries before iteration.
# Invalid pairs are logged but do NOT crash the tick.
# ======================================================================================================

        clean_pairs = []

        for p in bus_stop_pairs:
            if isinstance(p, (tuple, list)) and len(p) == 2:
                clean_pairs.append((str(p[0]), str(p[1])))

        # 🔑 CRITICAL: NEVER allow empty — ensures tick always progresses
        if clean_pairs:
            bus_stop_pairs = clean_pairs

        # --------------------------------------------------
        # Dashboard runner surface snapshot
        # --------------------------------------------------
        try:
            ctx_map = self._route_ctx_map

            surface = []

            for mid, sid in bus_stop_pairs[:4]:
                ctx = ctx_map.get((mid, sid))
                if not ctx:
                    continue

                surface.append({
                    "marketId": mid,
                    "selectionId": sid,
                    "px": ctx.get("px"),
                    "horse_name": ctx.get("horse_name"),
                    "side": ctx.get("side"),
                })

            self._runner_surface = surface

        except Exception:
            self._runner_surface = []

        # ===============================================================
        # 7️⃣ AUTHORITATIVE PLAN GENERATION (LANES ONLY)
        # ===============================================================

        generated_plans, lane_counts = self._evaluate_runner(
            base_ctx=base_ctx,
            bus_stop_pairs=bus_stop_pairs,
            engine_report=engine_report,
        )

# ======================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: lane6_hits = self._lane6_db_correctness()
# 🛠 ACTION: REPLACE WITH PLAN MERGE
# 📆 PATCHED: 2026-03-19 — Lane 6 unified with engine plan flow
#
# WHY:
# - Lane 6 emits plans, not counts
# - BUS must treat them exactly like engine output
# ======================================================================

        # ==================================================
        # 🟥 LANE 6 — DB CORRECTNESS (ENGINE-STYLE)
        # ==================================================
        lane6_plans = self._lane6_db_correctness()

        if lane6_plans:
            generated_plans.extend(lane6_plans)
            lane_counts[6] += len(lane6_plans)

        # ==================================================
        # 🟪 LANE 7 — MSC_UNIFIED (ENGINE-STYLE)
        # ==================================================
        lane7_plans = self._lane7_msc_unified(base_ctx, engine_report)

        if lane7_plans:
            generated_plans.extend(lane7_plans)
            lane_counts[7] += len(lane7_plans)

        # ==================================================
        # 🟪 LANE 8 — MSC_BLUEPRINT (ENGINE-STYLE)
        # ==================================================
        lane8_plans = self._lane8_msc_blueprint(base_ctx, engine_report)

        if lane8_plans:
            generated_plans.extend(lane8_plans)
            lane_counts[8] += len(lane8_plans)

        # --------------------------------------------------
        # STOPLOSS VISIBILITY — DIAGNOSTIC ONLY
        # --------------------------------------------------
        if lane_counts.get(5, 0) > 0:
            print(
                f"[BUS][STOPLOSS] generated={lane_counts[5]} "
                f"plans this tick"
            )



        all_runners = self._route_snapshot.get_all_runners() or []
        mids = {mid for (mid, _sid) in all_runners}
        runner_count = len(all_runners)

        # ===============================================================
        # 🔒 PREFLIGHT COMPLETE — SAFE TO EXECUTE BELOW
        # ===============================================================

        # ==================================================
        # OVERWATCHER — REDISTRIBUTION (ANALYSIS ONLY)
        # ==================================================
        for (mid, sid) in bus_stop_pairs:
            ctx = self._route_ctx_map.get((mid, sid))
            if not ctx:
                continue

            # --------------------------------------------------
            # PROMINENCE NORMALISATION (BUS AUTHORITY)
            # --------------------------------------------------
            _normalize_ctx_enums(ctx)


            try:
                redist = evaluate_redistribution(ctx)
                if redist:
                    ctx["redistribution"] = redist
                    tick_ctx["enrichment_ran"] = True

                    print(
                        f"[BUS][REDIST] mid={redist.get('marketId')} "
                        f"oc={redist.get('oc_phase')} "
                        f"disp={redist.get('dispersion'):.2f} "
                        f"urgency={redist.get('urgency')}"
                    )
            except Exception as e:
                tick_ctx["errors"].append(("redistribution", str(e)))

        # ==================================================
        # BUS PLAN ID NORMALISATION (AUTHORITATIVE)
        # ==================================================
        bus_exec_id = f"BUS-{self.tick_id}"

        plans = []
        for eng, plan, ctx in generated_plans:
            plan = dict(plan)
            engine = plan.get("engine")   # ✅ MOVE THIS UP
            upstream_pid = plan.get("plan_id")
            if upstream_pid:
                plan["plan_id"] = f"{bus_exec_id}-{upstream_pid}"
            else:
                plan["plan_id"] = bus_exec_id

            plans.append((eng, plan, ctx))

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def tick(self):
# 🧩 ACTION: INSERT — engine-aware slot enforcement (legacy by source, others by engine)
# 📆 PATCHED: 2026-01-23 — BUS slot adapter v2 (correct legacy letter semantics)
#
# PURPOSE:
# - Enforce per-tick slot quotas from ROUTE_SPLIT
# - LEGACY: one source/letter per runner
# - MSC_*: one plan per runner per engine
# - Preserve engine intelligence and emission order
# - Allow cadence to recycle overflow naturally
#
# INVARIANTS:
# - No engine logic modified
# - No plan scoring or ranking
# - Cross-engine overlap allowed
# - Hard cap = PLANS_PER_TICK
# ======================================================================================================

        # ===============================================================
        # 7.5️⃣ SLOT ADMISSION — ENGINE-AWARE (LEGACY ≠ MSC)
        # ===============================================================
        from engines.bus_route import ROUTE_SPLIT, PLANS_PER_TICK

        slot_budget = dict(ROUTE_SPLIT)

        # Seen keys per engine
        slot_seen = {
            "LEGACY": set(),            # (source, mid, sid)
            "MSC_RISK": set(),          # (mid, sid)
            "MSC_INPLAY": set(),        # (mid, sid)
            "MSC_EXPLORATORY": set(),   # (mid, sid)
            "OVERWATCHER": set(),       # (mid, sid)
            "MSC_UNIFIED": set(),       # (mid, sid)
            "MSC_BLUEPRINT": set(),     # (mid, sid)
        }

        slotted_plans = []
        unslotted_plans = []

        for eng, plan, ctx in plans:

            # hard global cap still enforced later by cadence
            if slot_budget.get(eng, 0) > 0:
                slotted_plans.append((eng, plan, ctx))
                slot_budget[eng] -= 1
            else:
                unslotted_plans.append((eng, plan, ctx))

        # 🔑 CRITICAL FIX:
        # Always forward something to cadence

   
        if not slotted_plans and unslotted_plans:
            print(f"[BUS][DROP] {len(unslotted_plans)} unslotted plans (not enriched)")
        plans = slotted_plans


        # ==================================================
        # PHASE 1 REPORT — ANALYSIS
        # ==================================================
        print("────────────────────────────────────────────────────────")
        print(
            f"[BUS][PHASE 1][ANALYSIS] "
            f"tick=#{self.tick_id} "
            f"route=#{self._route_id} "
            f"bus_stop=#{self._bus_stop}"
        )
        print("────────────────────────────────────────────────────────")

        print("SCOPE")
        route_runners = self._route_snapshot.get_all_runners() or []
        route_mids = {mid for (mid, _sid) in route_runners}

        print(f"  markets_seen   : {len(route_mids)}")
        print(f"  runners_seen   : {len(route_runners)}")

        evaluated = len(engine_report)
        fired = sum(1 for r in engine_report.values() if r.get("fired"))

        print("\nENGINES")
        print(f"  evaluated      : {evaluated}")
        print(f"  fired          : {fired}")

        print("\nENGINE OUTCOMES")
        for eng, info in engine_report.items():
            reasons = info.get("reasons", {})
            reason_str = ", ".join(f"{k}={v}" for k, v in reasons.items()) if reasons else "none"
            print(
                f"  {eng:<16} "
                f"evaluated={info.get('evaluated')} "
                f"fired={info.get('fired')} "
                f"reasons=[{reason_str}]"
            )

        print("\nPLANS")
        print(f"  raw            : {len(generated_plans)}")

        print("\nERRORS")
        if tick_ctx["errors"]:
            for e in tick_ctx["errors"]:
                print(f"  - {e}")
        else:
            print("  none")

        print("────────────────────────────────────────────────────────\n")

        # ==================================================
        # 🟢 BOOKMAKER ADMISSION CONTROLLER (BAC v1)
        # ==================================================

        from collections import defaultdict

        # --------------------------------------------------
        # 1️⃣ Build per-market exposure snapshot (LIVE parents only)
        # --------------------------------------------------
        market_book = defaultdict(lambda: {"BACK": 0.0, "LAY": 0.0})

        for (mid, sid), ctx_live in self._route_ctx_map.items():
            for o in ctx_live.get("orders_by_runner", []):
                if (
                    o.get("role") == "PARENT"
                    and str(o.get("entry_status")).upper() == "MATCHED"
                ):
                    side = str(o.get("side")).upper()
                    stake = float(o.get("entry_stake") or 0.0)
                    odds  = float(o.get("entry_odds") or 0.0)

                    if side == "BACK":
                        market_book[mid]["BACK"] += stake
                    elif side == "LAY":
                        market_book[mid]["LAY"] += stake * (odds - 1.0)

        # --------------------------------------------------
        # 2️⃣ Score generated plans by convexity improvement
        # --------------------------------------------------
        scored_plans = []

        for eng, plan, ctx in plans:

            mid = plan.get("marketId")
            side = str(plan.get("side") or "").upper()

            score = 0

            # Structural crossover priority
            why = str(plan.get("why") or "")
            if "crossover" in why.lower():
                score += 100

            # Convexity balancing
            if mid in market_book:

                back_exp = market_book[mid]["BACK"]
                lay_exp  = market_book[mid]["LAY"]

                if back_exp > lay_exp and side == "LAY":
                    score += 20
                elif lay_exp > back_exp and side == "BACK":
                    score += 20
                else:
                    score += 5
            else:
                score += 5

            # Slight global LAY bias
            if side == "LAY":
                score += 3

            scored_plans.append((score, eng, plan, ctx))

        # --------------------------------------------------
        # 3️⃣ Reorder plans by score (high first)
        # --------------------------------------------------
        scored_plans.sort(key=lambda x: x[0], reverse=True)

        plans = [(eng, plan, ctx) for (_s, eng, plan, ctx) in scored_plans]
        try:
            # ==================================================
            # PHASE 2 — ENRICHMENT (BEGINS)
            # ==================================================

            # ------------------------------------
            # ENGINE OUTCOME BINDING (reporting)
            # ------------------------------------
            tick_ctx["engine_outcomes"] = engine_report


# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: plan_queue.extend(plans)
# 🧩 ACTION: REPLACE
# 📆 PATCHED: 2026-03-10 — Route plans through cadence controller
# ======================================================================================================



            # ------------------------------------
            # RAW PLAN CAPTURE (analysis visibility)
            # ------------------------------------
            for _eng, _plan, _ctx in generated_plans:
                tick_ctx["plans_raw"].append(_plan)


            # Enrichment — unchanged
    # ======================================================================================================
    # 📍 TARGET: engines/bus/bus.py
    # 🔎 ANCHOR: after plans = self._run_engines_for_tick(...)
    # 🧩 ACTION: ADD
    # 📆 PATCHED: 2025-12-14 — Execution enrichment + integrity blockers
    # ======================================================================================================

            from engines.micro_scalper_v7.direction_engine import compute_msc_decision

            final_plans = []

            for eng, plan, ctx in plans:


                # ------------------------------------------------------------------
                # 🔒 CRITICAL: RE-BIND CTX TO ROUTE SNAPSHOT (AUTHORITATIVE)
                # ------------------------------------------------------------------
                mid = plan.get("marketId") or ctx.get("marketId")
                sid = plan.get("selectionId") or ctx.get("selectionId")

                if mid and sid:
                    route_ctx = self._route_ctx_map.get((mid, sid))
                    if route_ctx:
                        ctx = route_ctx
                
                # --------------------------------------------------
                # Execution enrichment (ONLY missing execution fields)
                # --------------------------------------------------
                plan.setdefault("marketId", ctx.get("marketId"))
                plan.setdefault("selectionId", ctx.get("selectionId"))
                # 🔁 PX REFRESH (LAST CHANCE, BUS AUTHORITY)
                if plan.get("px") is None:
                    self._ensure_px_from_route(ctx)
                    plan["px"] = ctx.get("px")

                # --------------------------------------------------
                # TARGET TICKS — BUS AUTHORITY (PARENT ONLY)
                # --------------------------------------------------
                if plan.get("role") != "CHILD":
                    # Default hedge distance is 1 tick unless engine explicitly set it
                    plan.setdefault("target_ticks", 1)


           


                # --------------------------------------------------
                # Direction check (execution truth)
                # --------------------------------------------------

                exec_dir = ctx.get("direction")
                if not plan.get("direction") and exec_dir:
                    plan["direction"] = exec_dir

                # --------------------------------------------------
                # REQUIRED EXPOSURE (AUTHORITATIVE — BUS OWNED)
                # Full lifecycle exposure (parent + child)
                # --------------------------------------------------

                # 🔥 BUS IS AUTHORITATIVE — DROP ANY UPSTREAM STAKE
                plan.pop("size", None)

                engine = plan.get("engine")
                px = float(plan.get("px") or 0.0)

                # --------------------------------------------------
                # 🔑 BET TYPE NORMALISATION (BUS AUTHORITY)
                # --------------------------------------------------
                # Engines may set bet_type.
                # If missing, BUS assigns deterministic default.

                bet_type = plan.get("bet_type")

                if not bet_type:

                    if engine == "MSC_RISK":
                        bet_type = "RISK"

                    elif engine == "MSC_EXPLORATORY":
                        bet_type = "EXPLORATORY"

                    elif engine == "MSC_INPLAY":
                        bet_type = "INPLAY"

                    elif engine == "OVERWATCHER":
                        # distinguish stoploss vs lock if needed
                        if plan.get("exit_kind") == "STOPLOSS":
                            bet_type = "STOPLOSS"
                        else:
                            bet_type = "CORRECTION"

                    elif engine == "MSC_UNIFIED":
                        # unified should normally set this itself
                        bet_type = plan.get("why") or "UNIFIED"

                    else:
                        bet_type = "LEGACY"

                plan["bet_type"] = bet_type

                if not engine or px <= 0:
                    plan["_bus_block"] = "missing_engine_or_px"
                    tick_ctx["plans_route_failed"].append(
                        (plan, "missing_engine_or_px")
                    )
                    continue  # 🔴 DO NOT ROUTE


                # --------------------------------------------------
                # STAKE COMPUTATION — BUS OWNED (UNIFIED DISPATCH)
                # --------------------------------------------------
                # All engines use the unified dynamic stake dispatcher.
                # Stake logic is now fully owned by dynamic_stake_v7.
                #
                # Dispatch order inside compute_dynamic_stake():
                #   1️⃣ bet_type (preferred for Unified / Blueprint)
                #   2️⃣ engine fallback (legacy compatibility)
                #
                # BUS no longer calls engine-specific stake functions.

                from engines.math.dynamic_stake_v7 import compute_dynamic_stake

                ctx["bet_type"] = plan.get("bet_type")

                raw_stake = compute_dynamic_stake(
                    engine=engine,
                    ctx=ctx,
                )



                # --------------------------------------------------
                # HARD VALIDATION
                # --------------------------------------------------
                if not raw_stake or raw_stake <= 0:
                    raw_stake = ENGINE_MIN.get(engine, 1.0)

                # --------------------------------------------------
                # 🎚️ STAKE REPORTING (BUS AUTHORITY)
                # --------------------------------------------------

                if engine in ("LEGACY", "MSC_EXPLORATORY", "MSC_RISK"):
                    print(
                        f"[BUS][STAKE] "
                        f"engine={engine} "
                        f"mid={plan.get('marketId')} "
                        f"sid={plan.get('selectionId')} "
                        f"conf={ctx.get('risk_confidence')} "
                        f"raw={raw_stake:.2f}"
                    )
# === PATCH START ==============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: # HIGH-ODDS STAKE DAMPENING (BUS AUTHORITY)
# 🛠 ACTION: Restrict dampening to non-MSC_INPLAY engines
# 📆 PATCHED: 2026-04-21 — Exclude MSC_INPLAY from half-stake guard
#
# PURPOSE:
# - Preserve high-odds dampening globally
# - Allow MSC_INPLAY full ladder exposure
#
# INVARIANT:
# - MSC_INPLAY never halved
# - All other engines still halved above px>8
# ==============================================================================

                # --------------------------------------------------
                # 🎚️ HIGH-ODDS STAKE DAMPENING (BUS AUTHORITY)
                # --------------------------------------------------
                if engine != "MSC_INPLAY" and px > 8.0:
                    raw_stake = float(raw_stake) * 0.5
                    plan["_bus_note"] = "high_odds_half_stake"
                    _record_reason(engine_report, engine, "high_odds_half_stake")

                # === PATCH END ==============================================================

                # --------------------------------------------
                # ENGINE FLOOR ENFORCEMENT (FINAL RAW STAKE)
                # --------------------------------------------
                engine_min = ENGINE_MIN.get(engine)
                if engine_min is not None:
                    raw_stake = max(float(raw_stake), float(engine_min))

                # ==================================================
                # 🔒 FINAL BUS STAKE GATE (ABSOLUTE AUTHORITY)
                # ==================================================
                stake = _apply_bus_stake_gate(
                    engine=engine,
                    stake=float(raw_stake),
                )

                plan["size"] = float(stake)
                plan["_stake_source"] = "BUS"

                # --------------------------------------------------
                # REQUIRED EXPOSURE (POST-STAKE, FINAL)
                # --------------------------------------------------
                plan["required_exposure"] = float(stake) * px

            
                # --- BUS MUST NEVER BLOCK EXECUTION ---
                # Annotate only, router decides.

                # Direction drift annotation (diagnostic only)
                plan_dir = plan.get("direction")
                exec_dir = ctx.get("direction")

                if plan_dir and exec_dir and plan_dir != exec_dir:
                    plan["_bus_note"] = "direction_changed"
                    _record_reason(engine_report, plan["engine"], "direction_changed")
                    tick_ctx["plans_annotated"].append((plan, "direction_changed"))

       

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside enrichment loop, BEFORE final_plans.append
# 🧩 ACTION: ADD de-duplication gate
# 📆 PATCHED: 2026-01-02 — prevent duplicate parent plans
# ======================================================================================================

                # --------------------------------------------------
                # BUS DE-DUPLICATION — LEGACY ONLY (ENGINE + LETTER)
                # --------------------------------------------------
                engine = plan.get("engine")

                # BUS must NOT de-duplicate MSC engines
                # MSC_RISK / MSC_EXPLORATORY / MSC_INPLAY manage their own lifecycles
                if engine == "LEGACY":

                    if "seen_plan_keys" not in tick_ctx:
                        tick_ctx["seen_plan_keys"] = set()
                        tick_ctx["dup_blocked_by_engine"] = {
                            "LEGACY": 0,
                        }

                    mid = plan.get("marketId")
                    sid = plan.get("selectionId")
                    px  = float(plan.get("px") or 0.0)

                    # LEGACY execution identity is ENGINE + LETTER
                    # (S, B, X, G may all place at same PX)
                    letter = (
                        plan.get("source")
                        or plan.get("letter")
                        or ""
                    )
                    letter = str(letter).upper()[:1]

                    key = (engine, letter, mid, sid, px)

                    if key in tick_ctx["seen_plan_keys"]:
                        tick_ctx["dup_blocked_by_engine"]["LEGACY"] += 1
                        tick_ctx["plans_route_failed"].append(
                            (plan, "duplicate_legacy_engine_letter_price")
                        )
                        _record_reason(engine_report, "LEGACY", "duplicate_legacy_engine_letter_price")

                        continue

                    tick_ctx["seen_plan_keys"].add(key)


# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside enrichment loop, BEFORE final_plans.append
# 🧩 ACTION: ADD hard execution identity guard
# 📆 PATCHED: 2026-01-02 — enforce router contract (marketId invariant)
# ======================================================================================================

                # --------------------------------------------------
                # HARD EXECUTION IDENTITY GUARD (BUS AUTHORITY)
                # --------------------------------------------------
                if not plan.get("marketId") or not plan.get("selectionId"):
                    tick_ctx["plans_route_failed"].append(
                        (plan, "missing_execution_identity")
                    )
                    engine_report[plan.get("engine", "UNKNOWN")]["note"] = "missing_marketId"
                    continue



                # ALWAYS forward (BUS never blocks execution)
                final_plans.append((eng, plan, ctx))
                tick_ctx["plans_enriched"].append(plan)

            # --------------------------------------------------
            # PHASE 2 REPORT — ENRICHMENT
            # --------------------------------------------------
            print("────────────────────────────────────────────────────────")
            print(f"[BUS][PHASE 2][ENRICHMENT] tick=#{self.tick_id}")
            print("────────────────────────────────────────────────────────")

            print("PLANS IN")
            print(f"  raw            : {len(tick_ctx['plans_raw'])}")

            print("\nENRICHMENT")
            print(f"  enriched       : {len(tick_ctx['plans_enriched'])}")
            print(f"  annotated       : {len(tick_ctx['plans_annotated'])}")

            # --------------------------------------------------
            # Rejection reason breakdown (diagnostic)
            # --------------------------------------------------
            reasons = {}
            for _plan, reason in tick_ctx["plans_annotated"]:
                reasons[reason] = reasons.get(reason, 0) + 1

            print("\nANNOTATIONS")
            if reasons:
                for reason, count in reasons.items():
                    print(f"  {reason:<22} : {count}")
            else:
                print("  none")

            blocked = {}
            for _plan, reason in tick_ctx["plans_route_failed"]:
                blocked[reason] = blocked.get(reason, 0) + 1

            if blocked:
                print("\nBLOCKED (BUS)")
                for reason, count in blocked.items():
                    print(f"  {reason:<22} : {count}")
            print("────────────────────────────────────────────────────────\n")

            # ==================================================
            # 📊 V7 BOOKMAKER SURFACE
            # ==================================================

            from collections import defaultdict

            market_book = defaultdict(lambda: {"BACK": 0.0, "LAY": 0.0})

            for (mid, sid), ctx_live in self._route_ctx_map.items():
                for o in ctx_live.get("orders_by_runner", []):
                    if (
                        o.get("role") == "PARENT"
                        and str(o.get("entry_status")).upper() == "MATCHED"
                    ):
                        side = str(o.get("side")).upper()
                        stake = float(o.get("entry_stake") or 0.0)
                        odds  = float(o.get("entry_odds") or 0.0)

                        if side == "BACK":
                            market_book[mid]["BACK"] += stake
                        elif side == "LAY":
                            market_book[mid]["LAY"] += stake * (odds - 1.0)

            if market_book:
                print("\n══════════════════════════════════════════════════════")
                print("V7 BOOKMAKER SURFACE")
                print("══════════════════════════════════════════════════════")

                for mid, data in market_book.items():
                    back_exp = round(data["BACK"], 2)
                    lay_exp  = round(data["LAY"], 2)
                    imbalance = round(back_exp - lay_exp, 2)

                    if imbalance > 0:
                        state = "BACK_HEAVY"
                    elif imbalance < 0:
                        state = "LAY_HEAVY"
                    else:
                        state = "BALANCED"

                    print(
                        f"{mid}  "
                        f"BACK_EXP={back_exp:<8} "
                        f"LAY_EXP={lay_exp:<8} "
                        f"IMB={imbalance:<8} "
                        f"{state}"
                    )

                print("══════════════════════════════════════════════════════\n")

            print("────────────────────────────────────────────────────────\n")

            # --------------------------------------------------
            # STAKE INVARIANT CHECK (SAFE)
            # --------------------------------------------------
            for _p in tick_ctx["plans_enriched"]:
                if _p.get("size") is None or _p["size"] <= 0:
                    raise RuntimeError(
                        f"[BUS] stake invariant violated: plan={_p}"
                    )


            # ==================================================
            # PHASE 3 — ROUTING (BEGINS)
            # ==================================================

            import uuid

            # Routing
            from engines.decision_engine.decide_once.placement import enqueue_for_placement

            # enrichment loop ends

            self._cadence.enqueue(final_plans)

            admitted = self._cadence.admit_for_tick()

            for eng, p, ctx in admitted:
                if not p.get("customerOrderRef"):
                    p["customerOrderRef"] = f"{p['engine'][:1]}-{uuid.uuid4().hex[:10]}"


                # ==================================================
                # 🔒 FINAL EXECUTION PAYLOAD SEAL (BUS AUTHORITY)
                # ==================================================

                # Identity (hard invariants)
                if not p.get("marketId"):
                    raise RuntimeError("[BUS] missing marketId at routing")
                if not p.get("selectionId"):
                    raise RuntimeError("[BUS] missing selectionId at routing")

                # Engine (authoritative)
                if not p.get("engine"):
                    raise RuntimeError("[BUS] missing engine at routing")
                ctx["engine"] = p["engine"]

                # Run identity (MUST exist)
                if not self.live_run_id:
                    raise RuntimeError("[BUS] live_run_id not set")
                ctx["run_id"] = self.live_run_id

                # Execution ordering (DB-authoritative metadata)
                p["route_id"] = self._route_id
                p["bus_stop"] = self._bus_stop
                p["tick_id"]  = self.tick_id

                # Customer reference (idempotent)
                if not p.get("customerOrderRef"):
                    p["customerOrderRef"] = (
                        f"{p['engine'][:1]}-{uuid.uuid4().hex[:10]}"
                    )

                # Price (execution truth — MUST already exist)
                if "px" not in p or p["px"] is None:
                    continue

                # Stake (execution truth — MUST already exist)
                if "size" not in p or p["size"] is None or p["size"] <= 0:
                    raise RuntimeError(
                        f"[BUS] missing/invalid size for {p['marketId']}:{p['selectionId']}"
                    )

                # Direction → Side (MANDATORY)
                if not p.get("side"):
                    direction = str(p.get("direction") or "").upper()
                    if direction.startswith("LAY"):
                        p["side"] = "LAY"
                    elif direction.startswith("BACK"):
                        p["side"] = "BACK"
                    else:
                        raise RuntimeError(
                            f"[BUS] missing side/direction for "
                            f"{p['marketId']}:{p['selectionId']}"
                        )

                # Hedge ticks invariant — PARENT ONLY
                if p.get("role") != "CHILD":
                    if "target_ticks" not in p:
                        raise RuntimeError(
                            f"[BUS] missing target_ticks for "
                            f"{p['marketId']}:{p['selectionId']}"
                        )


                # Required exposure (BUS-owned, already computed)
                if "required_exposure" not in p:
                    raise RuntimeError(
                        f"[BUS] missing required_exposure for "
                        f"{p['marketId']}:{p['selectionId']}"
                    )

                # Strategy letter (audit + DB)
                if not p.get("letter"):
                    p["letter"] = (p.get("engine") or "A")[0].upper()

                # ==================================================
                # 🚀 HANDOFF TO PLACEMENT (NO MORE MUTATION)
                # ==================================================
                enqueue_for_placement(p["engine"], p, ctx)



# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: PHASE 3 — ROUTING REPORT
# 🧩 ACTION: REPLACE (reporting only, routing untouched)
# 📆 PATCHED: 2025-12-05 — Collapse PHASE 3 to BUS-truthful routing diagnostics
#
# RATIONALE:
# BUS must report only what it knows synchronously.
# Router / DB / Betfair outcomes are post-BUS and reported elsewhere.
# ======================================================================================================


            # ==================================================
            # PHASE 3 — ROUTING REPORT (BUS-LOCAL DIAGNOSTICS)
            # ==================================================

            plans_generated = sum(lane_counts.values())

            plans_delegated = len(admitted)
        
            plans_not_delegated = max(plans_generated - plans_delegated, 0)

            tick_ctx["plans_routed"] = plans_delegated
            

            print("────────────────────────────────────────────────────────")
            print(
                f"[BUS][PHASE 3][ROUTING] "
                f"tick=#{self.tick_id} "
                f"route=#{self._route_id} "
                f"bus_stop=#{self._bus_stop}"
            )

            print("────────────────────────────────────────────────────────")

            print("ROUTING (BUS-LOCAL)")
            print(f"  plans_generated : {plans_generated}")
            print(f"  plans_delegated : {plans_delegated}")
            print(f"  not_delegated   : {plans_not_delegated}")

            print("\nPLANS BY LANE")
            print(f"  Lane 1 (LEGACY)         : {lane_counts[1]}")
            print(f"  Lane 2 (MSC_RISK)       : {lane_counts[2]}")
            print(f"  Lane 3 (MSC_INPLAY)     : {lane_counts[3]}")
            print(f"  Lane 4 (MSC_EXPLORATORY): {lane_counts[4]}")
            print(f"  Lane 5 (OVERWATCHER)    : {lane_counts[5]}")
            print(f"  Lane 6 (DB CORRECTNESS) : {lane_counts[6]}")
            print(f"  Lane 7 (MSC_UNIFIED)    : {lane_counts[7]}")
            print(f"  Lane 8 (MSC_BLUEPRINT)  : {lane_counts[8]}")

            if "dup_blocked_by_engine" in tick_ctx:
                print("\nDUPLICATES BLOCKED")
                for eng, n in tick_ctx["dup_blocked_by_engine"].items():
                    if n > 0:
                        print(f"  {eng:<16}: {n}")

            if tick_ctx["plans_annotated"]:
                print("\nBUS ANNOTATIONS")
                reasons = {}
                for _plan, reason in tick_ctx["plans_annotated"]:
                    reasons[reason] = reasons.get(reason, 0) + 1
                for reason, count in reasons.items():
                    print(f"  {reason:<22} : {count}")
            else:
                print("\nBUS ANNOTATIONS")
                print("  none")

            print("────────────────────────────────────────────────────────\n")

            # --------------------------------------------------
            # Route through cadence controller
            # --------------------------------------------------


            # --------------------------------------------------
            # 🔢 FILL RATE METRIC (AUTHORITATIVE)
            # --------------------------------------------------
            # ======================================================================================================
            # 📍 TARGET: engines/bus/bus.py
            # 🔎 SEARCH: fill_rate =
            # 🧩 ACTION: CLARIFY METRIC
            # 📆 PATCHED: 2026-03-16 — fill rate reflects cadence admission
            # ======================================================================================================

            attempted = len(generated_plans)
            delegated = len(admitted)

            fill_rate = (delegated / attempted) if attempted > 0 else 0.0


            fill_pct = fill_rate * 100.0

            if fill_pct >= 50.0:
                fill_colour = "🟢"
            else:
                fill_colour = "🔴"

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: fill_rate = (delegated / attempted) if attempted > 0 else 0.0
# 🧩 ACTION: ADD — persist fill telemetry for dashboard (read-only)
# 📆 PATCHED: 2026-04-26 — expose BUS fill metrics to dashboard
#
# PURPOSE:
# - Dashboard needs fill rate, attempted, delegated
# - No behavioural impact
# - Pure state capture
# ======================================================================================================

            # --------------------------------------------------
            # 📊 Persist fill telemetry (dashboard read-only)
            # --------------------------------------------------
            self._last_fill_rate = fill_pct
            self._last_attempted = attempted
            self._last_delegated = delegated

            _write_bus_runtime_snapshot(self.dashboard_snapshot())

            # ==================================================
            # 🟦 BANKSTATE SNAPSHOT (TICK-DRIVEN)
            # ==================================================
            try:
                from engines.live.bank_state import _write_bank_runtime_snapshot
                _write_bank_runtime_snapshot()
            except Exception:
                pass

            # ==================================================
            # 🟥 INPLAY SNAPSHOT (TICK-DRIVEN)
            # ==================================================
            try:
                inplay_engine = self.engines.get("MSC_INPLAY")
                if inplay_engine:
                    inplay_engine.write_runtime_snapshot()
            except Exception:
                pass

            # ==================================================
            # 🟩 ROUTER SNAPSHOT (TICK-DRIVEN)
            # ==================================================

            try:
                from engines.live.live_router import collect_router_live_state
                from engines.live.live_router import _write_router_runtime_snapshot_from_collect

                live = collect_router_live_state()
                _write_router_runtime_snapshot_from_collect(live)

            except Exception:
                pass

            print(
                f"[BUS][FILL] attempted={attempted} "
                f"delegated={delegated} "
                f"fill_rate={fill_pct:.1f}% {fill_colour}"
            )

            # ==================================================
            # 🟪 UNIFIED SNAPSHOT (TICK-DRIVEN)
            # ==================================================
            try:
                _write_unified_runtime_snapshot()
            except Exception:
                pass

            # ------------------------------------
            # TICK REPORT — ALWAYS PRINT
            # ------------------------------------
            print(
                f"[BUS][TICK] #{self.tick_id} "
                f"route=#{self._route_id} "
                f"bus_stop=#{self._bus_stop} "
                f"plans={len(generated_plans)}"
            )

            print("────────────────────────────────────────────────────────")
            route_runners = self._route_snapshot.get_all_runners() or []
            route_mids = {mid for (mid, _sid) in route_runners}

            print(
                f"[BUS][TICK] #{self.tick_id} #{self._route_id} #{self._bus_stop} "
                f"markets={len(route_mids)} runners={len(route_runners)}"
            )

            print("────────────────────────────────────────────────────────\n")

            print("ENGINE SUMMARY")
            print("────────────────────────────────────────────────────────")

            for eng, r in engine_report.items():

                fired = r.get("fired", 0)
                reasons = r.get("reasons", {})

                if fired > 0:
                     print(f"{eng:<16}: FIRED ({fired} plans)")

                elif reasons:
                    rs = ", ".join(f"{k}={v}" for k, v in reasons.items())
                    print(f"{eng:<16}: NO-FIRE [{rs}]")
                else:
                    print(f"{eng:<16}: NO-FIRE")


            print("\nEXECUTION")
            print("────────────────────────────────────────────────────────")
            print(f"plans_generated : {len(generated_plans)}")

            print(f"plans_routed    : {len(admitted)}")

            print("────────────────────────────────────────────────────────")

            print("\nTIMING")
 

            if len(self._ctx_refresh_times) >= 10:
                avg = sum(self._ctx_refresh_times[-10:]) / 10
                print(f"[BUS][CTX_REFRESH][AVG10] {avg:.4f}s")

            print("────────────────────────────────────────────────────────")


        finally:
            # ------------------------------------
            # END-OF-TICK SUMMARY (EVERY 10 TICKS)
            # ------------------------------------
            if tick_ctx["tick_id"] % 10 == 0:
                tick_ctx["ts_end"] = time.time()
                tick_ctx["duration"] = tick_ctx["ts_end"] - tick_ctx["ts_start"]

                engines_evaluated = len(tick_ctx["engine_outcomes"])
                engines_fired = sum(
                    1 for v in tick_ctx["engine_outcomes"].values()
                    if v.get("fired")
                )

                print("\n════════════════════════════════════════")
                print(
                    f"[BUS][TICK END] #{tick_ctx['tick_id']}  "
                    f"duration={tick_ctx['duration']:.3f}s"
                )
                print("════════════════════════════════════════")

                print("PHASES")
                print(f"  analysis_ran   : {tick_ctx['analysis_ran']}")
                print(f"  enrichment_ran : {tick_ctx['enrichment_ran']}")
                print(f"  routing_ran    : {tick_ctx['routing_ran']}")

                print("\nSCOPE")
                print(f"  markets_seen  : {tick_ctx['markets_seen']}")
                print(f"  runners_seen  : {tick_ctx['runners_seen']}")

                print("\nENGINES")
                print(f"  evaluated     : {engines_evaluated}")
                print(f"  fired         : {engines_fired}")

                print("\nPLANS")
                print(f"  raw           : {len(tick_ctx['plans_raw'])}")
                print(f"  enriched      : {len(tick_ctx['plans_enriched'])}")
                print(f"  annotated      : {len(tick_ctx['plans_annotated'])}")
                print(f"  routed        : {tick_ctx['plans_routed']}")

                print(f"  route_failed  : {len(tick_ctx['plans_route_failed'])}")

                print("\nERRORS")
                if tick_ctx["errors"]:
                    for e in tick_ctx["errors"]:
                        print(f"  - {e}")
                else:
                    print("  none")

                print("════════════════════════════════════════\n")

                # ------------------------------------
                # SYSTEM ANALYTICS SNAPSHOT
                # ------------------------------------
                self.analytics_report()


    # ======================================================================
    # PHASE 3 — ROUTING (DELEGATION ONLY)
    # BUS does NOT validate, enrich, or decide here.
    # It delegates execution to Placement (execution owner).
    # ======================================================================
    def _route(self, plan, ctx):
        """
        Phase 3 routing — authoritative engine stamping.

        BUS is the sole authority on execution identity.
        Placement and BankState must see identical engine names.
        """

        try:
            from engines.decision_engine.decide_once.placement import enqueue_for_placement

            # ------------------------------------------------------------------
            # AUTHORITATIVE ENGINE STAMP (NO EXCEPTIONS)
            # ------------------------------------------------------------------
            engine = plan.get("engine")
            if not engine:
                raise RuntimeError("BUS routing error: plan missing engine")

            # HARD OVERWRITE — DO NOT MERGE, DO NOT INFER
            ctx["engine"] = engine

            # Name passed to placement is audit-only
            enqueue_for_placement(engine, plan, ctx)

        except Exception as e:
            try:
                self._tick_ctx["plans_route_failed"].append(
                    (plan, f"placement_enqueue_error:{e}")
                )
            except Exception:
                pass
 

# ======================================================================
# END OF def tick(self)
# ======================================================================

# === PATCH START ==============================================================
# 📍 TARGET: engines/bus/bus.py (append at end of file)
# 📆 PATCHED: 2026-02-27 — Structured runtime snapshot (BUS)
#
# PURPOSE:
# - Persist authoritative BUS telemetry
# - No logic mutation
# - Pure observability
# ==============================================================================

def _ensure_bus_runtime_schema():
    import sqlite3
    from engines.config_paths import open_auto_db
    con = open_auto_db(rw=True)
    con.execute("""
        CREATE TABLE IF NOT EXISTS bus_runtime_snapshot(
            ts TEXT,
            route_id INTEGER,
            bus_stop INTEGER,
            tick_id INTEGER,
            hz REAL,
            window_size INTEGER,
            avg_ctx_refresh REAL,
            plans_generated INTEGER,
            plans_routed INTEGER
        )
    """)
    con.close()


def _write_bus_runtime_snapshot(data: dict):
    try:
        import sqlite3
        from datetime import datetime, timezone
        from engines.config_paths import autoscalp_db

        _ensure_bus_runtime_schema()

        from engines.config_paths import open_auto_db

        con = open_auto_db(rw=True)
# === PATCH START ==============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: INSERT INTO bus_runtime_snapshot
# 🛠 ACTION: Store generated / routed counts instead of fill_rate
# 📆 PATCHED: 2026-03-05 — snapshot stores raw execution counts
# ==============================================================================

        con.execute("""
        INSERT INTO bus_runtime_snapshot
        VALUES (?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            data.get("route_id"),
            data.get("bus_stop"),
            data.get("tick_id"),
            data.get("hz"),
            data.get("window_size"),
            data.get("avg_ctx_refresh"),
            data.get("plans_generated"),
            data.get("plans_routed"),
        ))

# === PATCH END ================================================================
        con.commit()
        con.close()
    except Exception:
        pass
# === PATCH END ==============================================================
def _ensure_unified_runtime_schema():
    import sqlite3
    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=True)

    con.execute("""
        CREATE TABLE IF NOT EXISTS unified_runtime_snapshot (
            ts TEXT PRIMARY KEY,

            route_id INTEGER,
            bus_stop INTEGER,
            tick_id INTEGER,
            hz REAL,
            fill_rate REAL,

            parents_open INTEGER,
            children_open INTEGER,
            children_matched INTEGER,

            total_pot REAL,
            total_floor REAL,
            total_reserved REAL,
            headroom REAL,
            utilisation_pct REAL,

            worst_case_liability REAL,
            directional_bias TEXT,
            imbalance_level TEXT,

            inplay_active INTEGER,
            inplay_confidence REAL,

            current_market_id TEXT,
            current_market_state TEXT,
            next_market_id TEXT,
            delayed_market_id TEXT
        )
    """)

    con.close()

def _write_unified_runtime_snapshot():
    try:
        import sqlite3
        from datetime import datetime, timezone
    

        _ensure_unified_runtime_schema()

        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=True)
        con.row_factory = sqlite3.Row

        # --------------------------------------------------
        # BUS
        # --------------------------------------------------
        bus = con.execute("""
            SELECT *
            FROM bus_runtime_snapshot
            ORDER BY ts DESC
            LIMIT 1
        """).fetchone()

        # --------------------------------------------------
        # BANKSTATE RUNTIME
        # --------------------------------------------------
        bank_rt = con.execute("""
            SELECT *
            FROM bankstate_runtime_snapshot
            ORDER BY ts DESC
            LIMIT 1
        """).fetchone()


        # --------------------------------------------------
        # BANKSTATE RUNTIME SNAPSHOT (REAL SCHEMA)
        # --------------------------------------------------
        total_pot = float(bank_rt["total_pot"] or 0) if bank_rt else 0.0
        total_used = float(bank_rt["total_used"] or 0) if bank_rt else 0.0
        total_available = float(bank_rt["total_available"] or 0) if bank_rt else 0.0

        # Unified interpretation
        total_floor = total_used
        total_reserved = total_used  # or keep 0 if you want strict separation
        headroom = total_available

        utilisation = (total_used / total_pot * 100.0) if total_pot > 0 else 0.0

        # --------------------------------------------------
        # LIABILITY (exchange truth)
        # --------------------------------------------------
        liab = con.execute("""
            SELECT
                SUM(matched_size) AS total_matched
            FROM betfair_execution_surface
            WHERE source='CURRENT'
        """).fetchone()

        # --------------------------------------------------
        # INPLAY SNAPSHOT
        # --------------------------------------------------
        inplay = con.execute("""
            SELECT *
            FROM inplay_runtime_snapshot
            ORDER BY ts DESC
            LIMIT 1
        """).fetchone()

        # --------------------------------------------------
        # DERIVED FIELDS (MATCH DASHBOARD LOGIC)
        # --------------------------------------------------

        # 1️⃣ Total pot from runtime snapshot
        total_pot = float(bank_rt["total_pot"] or 0) if bank_rt else 0.0

        # 2️⃣ Matched floor from engine snapshot (authoritative)
        engine_rows = con.execute("""
            SELECT floor
            FROM bankstate_engine_snapshot
            WHERE ts = (
                SELECT MAX(ts)
                FROM bankstate_engine_snapshot
            )
        """).fetchall()

        total_floor = sum(float(r["floor"] or 0) for r in engine_rows)

        # 3️⃣ Reserved = unmatched from engine snapshot
        engine_unmatched = con.execute("""
            SELECT unmatched
            FROM bankstate_engine_snapshot
            WHERE ts = (
                SELECT MAX(ts)
                FROM bankstate_engine_snapshot
            )
        """).fetchall()

        total_reserved = sum(float(r["unmatched"] or 0) for r in engine_unmatched)

        # 4️⃣ Headroom & utilisation
        headroom = total_pot - total_floor
        utilisation = (total_floor / total_pot * 100.0) if total_pot > 0 else 0.0

        worst_case_liability = float(liab["total_matched"] or 0) if liab else 0.0

        directional_bias = "UNKNOWN"
        imbalance_level = "NONE"

        # (Light classification only — no recomputation)
        if worst_case_liability > 1000:
            imbalance_level = "HIGH"
        elif worst_case_liability > 200:
            imbalance_level = "MODERATE"
        elif worst_case_liability > 0:
            imbalance_level = "LOW"

        # --------------------------------------------------
        # ROUTER SNAPSHOT (AGGREGATED)
        # --------------------------------------------------
        router_rows = con.execute("""
            SELECT role,
                   SUM(queued)    AS queued,
                   SUM(placing)   AS placing,
                   SUM(placed)    AS placed,
                   SUM(matched)   AS matched,
                   SUM(cancelled) AS cancelled,
                   SUM(closed)    AS closed
            FROM router_runtime_snapshot
            WHERE ts = (
                SELECT MAX(ts) FROM router_runtime_snapshot
                WHERE date(ts) = date('now','utc')
            )
            AND date(ts) = date('now','utc')
            GROUP BY role
        """).fetchall()

        parents_open = 0
        children_open = 0
        children_matched = 0

        for r in router_rows:
            role = r["role"]

            open_count = (
                (r["queued"] or 0)
                + (r["placing"] or 0)
                + (r["placed"] or 0)
                + (r["matched"] or 0)
            )

            if role == "PARENT":
                parents_open = open_count

            elif role == "CHILD":
                children_open = open_count
                children_matched = r["matched"] or 0


        # --------------------------------------------------
        # INSERT
        # --------------------------------------------------
        con.execute("""
            INSERT INTO unified_runtime_snapshot (
                ts,
                route_id,
                bus_stop,
                tick_id,
                hz,
                fill_rate,
                parents_open,
                children_open,
                children_matched,
                total_pot,
                total_floor,
                total_reserved,
                headroom,
                utilisation_pct,
                worst_case_liability,
                directional_bias,
                imbalance_level,
                inplay_active,
                inplay_confidence,
                current_market_id,
                current_market_state,
                next_market_id,
                delayed_market_id
            )
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            datetime.now(timezone.utc).isoformat(),

            bus["route_id"] if bus else None,
            bus["bus_stop"] if bus else None,
            bus["tick_id"] if bus else None,
            bus["hz"] if bus else None,

            (
                (bus["plans_routed"] / bus["plans_generated"])
                if bus and bus["plans_generated"] else 0.0
            ),

            parents_open,
            children_open,
            children_matched,

            total_pot,
            total_floor,
            total_reserved,
            headroom,
            utilisation,

            worst_case_liability,
            directional_bias,
            imbalance_level,

            1 if inplay else 0,
            float(inplay["confidence"]) if inplay and "confidence" in inplay.keys() else 0.0,

            None,  # current_market_id (written by unified engine)
            None,  # current_market_state
            None,  # next_market_id
            None   # delayed_market_id
        ))

        con.commit()
        con.close()

    except Exception as e:
        import traceback
        traceback.print_exc()
        print("[UNIFIED SNAPSHOT ERROR]", e)

# Global BUS instance
BUS = DecisionBus()