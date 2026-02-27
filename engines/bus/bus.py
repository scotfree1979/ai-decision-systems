# ======================================================================================================
# BUS v13 — Behaviour-Identical Reconstruction
# Extracted CTX builder + Engine runner, nothing else changed.
# ======================================================================================================

from engines.live.bank_state import get_engine_available

from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
from engines.micro_scalper_v7.inplay_engine import InPlayEngine
from engines.micro_scalper_v7.risk_engine import RiskEngine
from engines.mastery.mastery_policy import plan_for_strategy
from engines.mastery.context_builder import build_context
from engines.live.live_router import place_from_bus
from engines.decision_engine.decide_once.scope import build_and_maintain_scope
from engines.math.dynamic_stake_v7 import compute_dynamic_stake, calc_dynamic_stake

from engines.live.overwatcher import evaluate_redistribution
from engines.risk.risk_price_helper_v2 import get_legacy_parent_odds_snapshot
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

def _normalize_ctx_enums(ctx: dict) -> None:
    """
    BUS authority: normalize ALL enum-like CTX fields to ints.
    Must be idempotent and never raise.
    """

    # ---- band ----
    band = ctx.get("band")
    if isinstance(band, str):
        ctx["band"] = _BAND_MAP.get(band.upper(), -1)

    # ---- prominence ----
    for key in ("prominence", "prominent"):
        val = ctx.get(key)
        if isinstance(val, str):
            ctx[key] = _PROMINENCE_MAP.get(val.upper(), 1)
        elif isinstance(val, bool):
            ctx[key] = 1 if val else 0

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
        self.plans_per_window = 1200
        self.plans_per_tick   = 120

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



# NEW — minimal helper, lives inside DecisionBus

def _build_bus_stop_ctxs(self, base_ctx, runner_pairs):
    """
    Build CTX ONCE per runner for this bus stop.
    Returns: {(mid, sid): ctx}
    """
    ctxs = {}

    for mid, sid in runner_pairs:
        ctx = dict(self._route_ctx_map.get((mid, sid)))


        if not ctx:
            continue
        ctxs[(mid, sid)] = ctx

    return ctxs

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

    from engines.config_paths import connect_db
    from datetime import datetime, timezone
    import sqlite3

    con = connect_db(ro=True)
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
        
        # Build FULL route CTX map once (AUTHORITATIVE)
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
            return band >= 2   # ACTIVE + PASSIVE

        
        if engine == "MSC_INPLAY":
            return band >= 1   # ACTIVE + PASSIVE + EXTENDED

        if engine == "MSC_RISK":
            return band >= 1  # ACTIVE + PASSIVE + EXTENDED

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

        runner_pool = snapshot.get_all_runners() or []
        mids = sorted({mid for (mid, _sid) in runner_pool})

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


# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: class DecisionBus.__init__
# 🧩 ACTION: ADD
# 📆 PATCHED: 2026-03-10 — Cadence Controller initialisation
# ======================================================================================================



        # ------------------------------------------------------------------
        # Cadence Controller (execution admission gate)
        # ------------------------------------------------------------------
        self._cadence = CadenceController()
        self._route_id = 1          # starts at Route #1
        self._bus_stop = 0          # increments per tick, resets at 10

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



    # 🔑 THIS MUST BE HERE — SAME INDENT AS tick(), _build_ctx_for_market(), etc.
    def _build_bus_stop_ctxs(self, base_ctx, runner_pairs):
        """
        Build CTX ONCE per runner for this bus stop.
        Returns: {(mid, sid): ctx}
        """
        ctxs = {}
        for mid, sid in runner_pairs:
            ctx = self._route_ctx_map.get((mid, sid))            
            if not ctx:
                continue
            ctxs[(mid, sid)] = ctx
        return ctxs


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
    # 📆 PATCHED: 2026-03-18 — Lock BUS lane semantics + helper-owned odds
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
        lane_counts = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0, 6: 0}

        # --------------------------------------------------
        # 🔁 ODDS REFRESH — HELPER OWNED (AUTHORITATIVE)
        # --------------------------------------------------
        self._route_ctx_map = self._route_snapshot.get_ctx_map()

        # ==================================================
        # 🔒 HARD PX INVARIANT (BUS AUTHORITY)
        # Every CTX must have px before lanes execute
        # ==================================================

        missing_px = [
            (mid, sid)
            for (mid, sid), ctx in self._route_ctx_map.items()
            if ctx.get("px") is None
        ]

        if missing_px:
            # One forced refresh pass
            self._route_snapshot.refresh_ctx_dynamic_fields()
            self._route_ctx_map = self._route_snapshot.get_ctx_map()

            # Re-check
            still_missing = [
                (mid, sid)
                for (mid, sid), ctx in self._route_ctx_map.items()
                if ctx.get("px") is None
            ]

            if still_missing:
                print(
                    f"[BUS][PX][WARN] still missing px for {len(still_missing)} runners"
                )
        
        # === PATCH START ============================================================
        # 📍 TARGET: engines/bus/bus.py
        # 🔎 SEARCH: _normalize_ctx_enums(ctx)
        # 🧩 ACTION: normalize route ctx map instead of undefined variable
        # 📆 PATCHED: 2026-02-07 — fix BUS ctx scoping bug
        # ============================================================================

        for ctx in self._route_ctx_map.values():
            _normalize_ctx_enums(ctx)
  
        from engines.micro_scalper_v7.direction_engine import compute_msc_decision

        for (mid, sid), ctx in self._route_ctx_map.items():
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

        # --------------------------------------------------
        # 🟦 LANE 1 — LEGACY (BUS STOP ONLY)
        # --------------------------------------------------
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

        from engines.bus_route import get_exploratory_active_parent_pairs

        exclusions = get_exploratory_active_parent_pairs()
        exp = self.engines.get("MSC_EXPLORATORY")

        if exp:
            # 1️⃣ Collect candidates
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

    # ======================================================================
    # CTX BUILDER — ROUTE-AUTHORITATIVE (FINAL)
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

        # --------------------------------------------------
        # ENGINE-NEUTRAL ANCHOR INJECTION (FINAL)
        # --------------------------------------------------
        ctx["anchor_parent_id"] = None
        ctx["anchor_entry_odds"] = None
        ctx["anchor_entry_stake"] = None
        ctx["anchor_engine"] = None

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

        for o in ctx["orders_by_runner"]:
            if (
                o.get("role") == "PARENT"
                and str(o.get("entry_status")).upper() == "MATCHED"
            ):
                ctx["anchor_parent_id"] = o.get("id")
                ctx["anchor_entry_odds"] = o.get("entry_odds")
                ctx["anchor_entry_stake"] = o.get("entry_stake")
                ctx["anchor_engine"] = o.get("engine")
                break

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
            # Identity
            "route_id": self._route_id,
            "bus_stop": self._bus_stop,
            "tick_id": self.tick_id,
            "hz": getattr(self, "_hz", 0.0),

            # Cadence
            "window_size": getattr(self._cadence, "window_seconds", 0),

            # Context
            "avg_ctx_refresh": avg_ctx,
            "bus_stop_runners":
                self._route_snapshot.get_bus_stop(self._bus_stop)
                if self._route_snapshot else [],

            # Fill telemetry
            "fill_rate": getattr(self, "_last_fill_rate", 0.0),
            "attempted": getattr(self, "_last_attempted", 0),
            "delegated": getattr(self, "_last_delegated", 0),
        }

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

        """
        Phase 0: DB-first live truth.
        Reports ALL markets with live parents, ordered by time-to-off.
        No mutation. No engine logic.
        """
        ordered = []  # 🔒 hard initialise immediately
        from engines.config_paths import connect_db, open_auto_db
        from datetime import datetime, timezone
        import sqlite3

        now = datetime.now(timezone.utc)

        # --------------------------------------------------
        # 1️⃣ Load all LIVE parents
        # --------------------------------------------------
        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        parents = con.execute("""
            SELECT
                id,
                engine,
                marketId,
                selectionId,
                entry_odds
            FROM orders
            WHERE mode='LIVE'
              AND role='PARENT'
              AND UPPER(entry_status)='MATCHED'
              AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
              AND date(COALESCE(opened_at, datetime('now','utc'))) = date('now','utc')

        """).fetchall()

        if not parents:
            con.close()
            return  # nothing to report

        # --------------------------------------------------
        # 2️⃣ Group parents by market
        # --------------------------------------------------
        by_market = {}
        for p in parents:
            by_market.setdefault(p["marketId"], []).append(p)

        # --------------------------------------------------
        # 3️⃣ Resolve market metadata (bets.db)
        # --------------------------------------------------
        bdb = connect_db(ro=True)
        bdb.row_factory = sqlite3.Row

        market_meta = {}
        for mid in by_market.keys():
            row = bdb.execute("""
                SELECT
                    horse_name,
                    event_name,
                    marketStartTime
                FROM bets
                WHERE marketId=?
                LIMIT 1
            """, (str(mid),)).fetchone()

            if not row:
                continue

            off = datetime.fromisoformat(
                row["marketStartTime"].replace("Z", "+00:00")
            )
            secs = (off - now).total_seconds()

            # --------------------------------------------------
            # ⛔ PHASE-0 CUTOFF — FINISHED MARKETS
            # Exclude markets ≥ 6 minutes AFTER off
            # --------------------------------------------------
            if secs <= -360:
                continue


            market_meta[mid] = {
                "horse": row["horse_name"],
                "event": row["event_name"],
                "off": off,
                "secs": secs,
            }


        bdb.close()
        con.close()

        # --------------------------------------------------
        # 4️⃣ Order markets by time-to-off
        # --------------------------------------------------


        if market_meta:
            ordered = sorted(
                market_meta.items(),
                key=lambda x: x[1]["secs"]
            )

        print("\n================ BUS PHASE 0 — LIVE DB STATE =================")
        print(f"[{now.strftime('%H:%M:%S')} UTC] markets={len(ordered)}\n")

        total_parents = 0
        total_children = 0
        missing_children = 0
        engines_missing = set()

        # --------------------------------------------------
        # 5️⃣ Per-market breakdown
        # --------------------------------------------------
        for mid, meta in ordered:
            mins = int(meta["secs"] // 60)
            secs = int(meta["secs"] % 60)

            print(f"MARKET: {meta['horse']} @ {meta['event']}")

            print(f"  off_in: {mins:02d}:{secs:02d}\n")

            # parent rows for this market
            rows = by_market[mid]

            # group by engine → runner → px
            tree = {}
            for r in rows:
                eng = r["engine"]
                sid = r["selectionId"]
                px  = round(float(r["entry_odds"]), 2)
                tree.setdefault(eng, {}).setdefault(sid, {}).setdefault(px, []).append(r["id"])

            # load children map once
            con = open_auto_db(rw=False)
            con.row_factory = sqlite3.Row
            child_rows = con.execute("""
                SELECT hedge_of
                FROM orders
                WHERE role='CHILD'
                  AND UPPER(entry_status) IN ('PLACED','MATCHED')
            """).fetchall()
            con.close()

            has_child = {c["hedge_of"] for c in child_rows}

            for eng, runners in tree.items():
                print(f"  {eng}")
                for sid, pxs in runners.items():
                    for px, pids in pxs.items():
                        parents_n = len(pids)
                        children_n = sum(1 for pid in pids if pid in has_child)

                        total_parents += parents_n
                        total_children += children_n

                        status = "OK"
                        if children_n < parents_n:
                            missing_children += (parents_n - children_n)
                            engines_missing.add(eng)
                            status = "⚠ MISSING CHILD"

                        print(
                            f"    Runner {sid} @ px={px:<4} "
                            f"parents={parents_n}  children={children_n}  {status}"
                        )
                print()

            print("-------------------------------------------------------------\n")

        # --------------------------------------------------
        # 6️⃣ Summary
        # --------------------------------------------------
        print("SUMMARY")
        print(f"  total_markets        : {len(ordered)}")
        print(f"  total_parents        : {total_parents}")
        print(f"  total_children       : {total_children}")
        print(f"  missing_children     : {missing_children}")
        print(f"  engines_affected     : {', '.join(sorted(engines_missing)) or 'none'}")
        print("=============================================================\n")


    # ======================================================================
    # TICK — authoritative BUS lifecycle (route → ctx → lanes)
    # ======================================================================
    def tick(self):
        # ===============================================================
        # 0️⃣ BUS IDENTITY
        # ===============================================================
        self.tick_id += 1
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

        # ==================================================
        # PHASE 0 — LIVE DB TRUTH (READ-ONLY)
        # ==================================================
        self._phase0_report_db_truth()

        # ===============================================================
        # 1️⃣ DIAGNOSTICS (non-fatal, never blocks)
        # ===============================================================
        tick_ctx = self._new_tick_ctx()
        engine_report = EngineReportShim()

        # ===============================================================
        # 2️⃣ BASE CONTEXT (BUS-OWNED)
        # ===============================================================
        _bc = build_context(source="LIVE")
        base_ctx = _bc[0] if isinstance(_bc, tuple) else _bc

        if not self.live_run_id:
            self.live_run_id = f"LIVE-{int(time.time())}"
        base_ctx["run_id"] = self.live_run_id

        # --------------------------------------------------
        # 🧠 RISK CONFIDENCE (READ-ONLY, PHASE 2)
        # --------------------------------------------------
        from engines.shadow_confidence import get as _get_shadow_conf

        risk_confidence = {}

        for (mid, sid), ctx in self._route_ctx_map.items():
            rec = _get_shadow_conf(mid, sid, "MSC_RISK")

            drift = rec.get("DRIFT", 0)
            steam = rec.get("STEAM", 0)

            if drift or steam:
                risk_confidence[(mid, sid)] = {
                    "drift": drift,
                    "steam": steam,
                    "net": drift - steam,
                    "direction": "DRIFT" if drift >= steam else "STEAM",
                }

        # Persist for diagnostics / future use
        tick_ctx["risk_confidence"] = risk_confidence

        # Diagnostic only
        if risk_confidence:
            print(f"[BUS][RISK][CONF] runners={len(risk_confidence)}")

        # --------------------------------------------------
        # 🔑 BIND NUMERIC RISK CONFIDENCE TO CTX (AUTHORITATIVE)
        # --------------------------------------------------
        for (mid, sid), ctx in self._route_ctx_map.items():
            rc = risk_confidence.get((mid, sid))
            if not rc:
                ctx["risk_confidence"] = 0.0
                continue

            # Simple linear confidence score (deterministic)
            # 50 = neutral, >50 = positive edge, <50 = negative edge
            ctx["risk_confidence"] = max(
                0.0,
                min(
                    100.0,
                    50.0 + (rc["net"] * 10.0)
                )
            )


        # ===============================================================
        # 4️⃣ ROUTE SNAPSHOT — BUS-OWNED CONTROL (NO LAZY LOOP)
        # ===============================================================

        from engines.bus_route import BusRouteSnapshot
        from engines.bus_route_startup_ctx import StartupCTXBuilder

        # --------------------------------------------------
        # INIT SNAPSHOT + STARTUP CTX BUILDER (ONCE)
        # --------------------------------------------------
        if self._route_snapshot is None:
            self._route_snapshot = BusRouteSnapshot()
            self._startup_ctx_builder = StartupCTXBuilder(self._route_snapshot)

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside tick(), immediately after snapshot initialisation block
# 🧩 ACTION: ADD — initial route build binding (correct location)
# 📆 PATCHED: 2026-02-12 — Ensure snapshot exists before build
#
# PURPOSE:
# - Guarantee first route is built once snapshot exists
# - Prevent NoneType build_route crash
# - Maintain prebuild-at-7 logic unchanged
#
# INVARIANT:
# - Snapshot must exist before build_route is called
# - Build occurs exactly once at first tick
# ======================================================================================================

        # --------------------------------------------------
        # 🔑 INITIAL ROUTE BUILD (FIRST USE ONLY)
        # --------------------------------------------------
        if not hasattr(self, "_route_initialised"):
            self._route_snapshot.build_route()
            self._route_snapshot.partition_into_bus_stops()
            self._route_snapshot.refresh_ctx_dynamic_fields()
            self._print_window_snapshot("INITIAL BUILD")

            self._route_initialised = True
            print("[BUS][ROUTE] initial route built")
        # --------------------------------------------------
        # DERIVE ROUTE + BUS STOP FROM TICK (AUTHORITATIVE)
        # --------------------------------------------------
        self._route_id = ((self.tick_id - 1) // 10) + 1
        self._bus_stop = ((self.tick_id - 1) % 10) + 1

        # --------------------------------------------------
        # ROUTE BUILD — ONLY AT ROUTE BOUNDARY
        # --------------------------------------------------
        # ======================================================================================================
        # 📍 TARGET: engines/bus/bus.py
        # 🔎 ANCHOR: inside def tick(self): route management section
        # 🧩 ACTION: PRE-BUILD NEXT ROUTE AT BUS STOP 7
        # 📆 PATCHED: 2026-04-12 — Non-blocking route rollover
        #
        # MODEL:
        # - Route runs for 10 bus stops
        # - At bus_stop 7, we build next route in advance
        # - At bus_stop 1, we simply switch snapshot reference
        # - No blocking build at route boundary
        #
        # INVARIANT:
        # - Route build NEVER occurs at bus_stop 1
        # - Route build NEVER blocks first tick of cycle
        # ======================================================================================================

        # --------------------------------------------------
        # PRE-BUILD NEXT ROUTE (DELEGATED TO SNAPSHOT)
        # --------------------------------------------------
        if self._bus_stop == 7:
            try:
                self._route_snapshot.prepare_next_route()
                self._print_window_snapshot("PREBUILD @ STOP 7")
                print("[BUS][ROUTE] prebuild delegated to snapshot")
            except Exception:
                pass

        # --------------------------------------------------
        # ROUTE SWITCH (DELEGATED TO SNAPSHOT)
        # --------------------------------------------------
        if self._bus_stop == 1:
            try:
                self._route_snapshot.activate_next_route()
                print("[BUS][ROUTE] activated prebuilt route")
            except Exception:
                pass


        # --------------------------------------------------
        # CTX BATCH BUILDER — PROGRESSIVE WARM-UP
        # --------------------------------------------------
        if hasattr(self, "_startup_ctx_builder"):
            self._startup_ctx_builder.step(max_builds=25)

        # --------------------------------------------------
        # REFRESH ODDS — EVERY TICK (AUTHORITATIVE)
        # --------------------------------------------------
        dt = self._route_snapshot.refresh_ctx_dynamic_fields()

        # --------------------------------------------------
        # BIND CTX MAP (AUTHORITATIVE SNAPSHOT)
        # --------------------------------------------------
        self._route_ctx_map = self._route_snapshot.get_ctx_map()

        # --------------------------------------------------
        # NORMALISE ENUMS (BUS AUTHORITY)
        # --------------------------------------------------
        for ctx in self._route_ctx_map.values():
            _normalize_ctx_enums(ctx)

        self._ctx_refresh_times.append(dt)

        # --------------------------------------------------
        # 🔑 ANCHOR INJECTION (BUS AUTHORITY)
        # --------------------------------------------------
        from engines.config_paths import open_bets_db

        con = open_bets_db(rw=False)
        try:
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

        # ===============================================================
        # 6️⃣ BUS STOP SLICE (ROUTE-PROVIDED)
        # ===============================================================

        legacy_slice = self._route_snapshot.get_bus_stop(self._bus_stop) or []
        bus_stop_pairs = legacy_slice

        # ===============================================================
        # 7️⃣ AUTHORITATIVE PLAN GENERATION (LANES ONLY)
        # ===============================================================

        generated_plans, lane_counts = self._evaluate_runner(
            base_ctx=base_ctx,
            bus_stop_pairs=legacy_slice,
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

                if not engine or px <= 0:
                    plan["_bus_block"] = "missing_engine_or_px"
                    tick_ctx["plans_route_failed"].append(
                        (plan, "missing_engine_or_px")
                    )
                    continue  # 🔴 DO NOT ROUTE

                # --------------------------------------------------
                # STAKE COMPUTATION — BUS OWNED (ALL ENGINES)
                # --------------------------------------------------

                # --------------------------------------------------
                # MSC_RISK — mechanical sizing (BUS-owned)
                # --------------------------------------------------
                # ======================================================================
                # 📍 TARGET: engines/bus/bus.py
                # 🔎 SEARCH: if engine == "MSC_RISK":
                # 🛠 ACTION: REPLACE BLOCK
                # 📆 PATCHED: 2026-02-11 — Fix legacy_parent_id resolution for MSC_RISK
                #
                # WHY:
                # - legacy_parent_id was referenced as a free variable
                # - Risk parent metrics must be sourced from ctx (Lane 2 injection)
                # - BUS must never depend on undefined variables
                #
                # GUARANTEE:
                # - Risk always resolves parent anchor from ctx
                # - No DB lookup required here
                # - Eliminates name 'legacy_parent_id' is not defined
                # ======================================================================

                if engine == "MSC_RISK":

                    from engines.math.dynamic_stake_v7 import compute_risk_dynamic_stake

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside MSC_RISK stake computation block
# 🧩 ACTION: REPLACE — use engine-neutral anchor fields
# 📆 PATCHED: 2026-02-12 — Risk shadow engine-neutral anchor support
# ======================================================================================================

                    anchor_parent_id   = ctx.get("anchor_parent_id")
                    anchor_entry_odds  = ctx.get("anchor_entry_odds")
                    anchor_entry_stake = ctx.get("anchor_entry_stake")

                    if not anchor_parent_id or not anchor_entry_odds:
                        plan["_bus_block"] = "risk_missing_parent_anchor"
                        tick_ctx["plans_route_failed"].append(
                            (plan, "risk_missing_parent_anchor")
                        )
                        continue


                    # Hard invariant — risk cannot operate without anchor
                    anchor_parent_id   = ctx.get("anchor_parent_id")
                    anchor_entry_odds  = ctx.get("anchor_entry_odds")

                    if not anchor_parent_id or not anchor_entry_odds:
                        plan["_bus_block"] = "risk_missing_parent_anchor"
                        tick_ctx["plans_route_failed"].append(
                           (plan, "risk_missing_parent_anchor")
                        )
                        continue # 🔴 DO NOT ROUTE

                    # Ensure px exists (BUS authority)
                    if not self._ensure_px_from_route(ctx):
                        self._force_px_refresh(mid, sid, ctx)
                        if ctx.get("px") is None:
                            plan["_bus_block"] = "risk_missing_px"
                            tick_ctx["plans_route_failed"].append(
                                (plan, "risk_missing_px")
                            )
                            continue # 🔴 DO NOT ROUTE

                    raw_stake = compute_risk_dynamic_stake(
                        ctx=ctx,
                        engine=engine,
                    )

                # --------------------------------------------------
                # MSC_INPLAY — momentum / position sizing (BUS-owned)
                # --------------------------------------------------
                elif engine == "MSC_INPLAY":

                    from engines.math.dynamic_stake_v7 import compute_inplay_dynamic_stake

                    raw_stake = compute_inplay_dynamic_stake(
                        ctx=ctx,
                        engine=engine,
                    )

                # --------------------------------------------------
                # MSC_EXPLORATORY — conviction-weighted sizing (BUS-owned)
                # --------------------------------------------------
                elif engine == "MSC_EXPLORATORY":

                    from engines.math.dynamic_stake_v7 import compute_exploratory_dynamic_stake

                    raw_stake = compute_exploratory_dynamic_stake(
                        ctx=ctx,
                        engine=engine,
                    )
                # --------------------------------------------------
                # OVERWATCHER — STOPLOSS (BUS-owned, FIXED STAKE)
                # --------------------------------------------------
                elif engine == "OVERWATCHER":

                    from engines.math.dynamic_stake_v7 import compute_overwatch_dynamic_stake

                    raw_stake = compute_overwatch_dynamic_stake(
                        ctx=ctx,
                        engine=engine,
                        plan=plan,
                    )

                # --------------------------------------------------
                # LEGACY + FALLBACK — envelope-based dynamic stake
                # --------------------------------------------------
                else:
                    from engines.math.dynamic_stake_v7 import compute_dynamic_stake

                    raw_stake = compute_dynamic_stake(
                        engine=engine,
                        ctx=ctx,
                    )


                # --------------------------------------------------
                # HARD VALIDATION
                # --------------------------------------------------
                if not raw_stake or raw_stake <= 0:
                    plan["_bus_block"] = "stake_zero"
                    tick_ctx["plans_route_failed"].append(
                        (plan, "stake_zero")
                    )
                    continue  # 🔴 DO NOT ROUTE

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: market_cutoff_5min
# 🧩 ACTION: REPLACE — engine-aware market-time cutoff adapter (v1.4)
# 📆 PATCHED: 2026-01-23 — Engine-specific execution cutoff
#
# RATIONALE:
# - INPLAY must remain unrestricted
# - RISK (shadow bets) may operate closer to off
# - LEGACY + EXPLORATORY must stop earlier
#
# ENGINE RULES:
#   • MSC_INPLAY      → no cutoff
#   • MSC_RISK        → block ≤ 2 minutes
#   • LEGACY          → block ≤ 5 minutes
#   • MSC_EXPLORATORY → block ≤ 5 minutes
#
# BUS is the final execution authority.
# ======================================================================================================

                # --------------------------------------------------
                # ⏱️ MARKET START TIME CUTOFF (ENGINE-AWARE)
                # --------------------------------------------------
                try:
                    from engines.config_paths import open_auto_db

                    con = open_auto_db(rw=False)
                    row = con.execute(
                        """
                        SELECT
                            julianday(b.marketStartTime) - julianday('now','utc')
                        FROM bets b
                        WHERE b.marketId = ?
                        LIMIT 1
                        """,
                        (plan.get("marketId"),),
                    ).fetchone()

                    if row and row[0] is not None:
                        minutes_to_off = float(row[0]) * 1440.0
                        plan["_minutes_to_off"] = round(minutes_to_off, 2)

                        engine = plan.get("engine")

                        # INPLAY — never blocked here
                        if engine == "MSC_INPLAY":
                            pass

                        # RISK — allowed until 2 minutes to off
                        elif engine == "MSC_RISK":
                            if minutes_to_off <= 2.0:
                                plan["_bus_block"] = "market_cutoff_risk_2min"
                                tick_ctx["plans_route_failed"].append(
                                    (plan, "market_cutoff_risk_2min")
                                )
                                _record_reason(
                                    engine_report,
                                    engine,
                                    "market_cutoff_risk_2min",
                                )
                                continue  # 🔴 DO NOT ROUTE

                        # LEGACY + EXPLORATORY — stop at 5 minutes
                        else:
                            if minutes_to_off <= 5.0:
                                plan["_bus_block"] = "market_cutoff_5min"
                                tick_ctx["plans_route_failed"].append(
                                    (plan, "market_cutoff_5min")
                                )
                                _record_reason(
                                    engine_report,
                                    engine,
                                    "market_cutoff_5min",
                                )
                                continue  # 🔴 DO NOT ROUTE

                except Exception:
                    # BUS must fail-open, never deadlock
                    pass
                finally:
                    try:
                        con.close()
                    except Exception:
                        pass
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
# 📆 PATCHED: 2025-12-18 — Collapse PHASE 3 to BUS-truthful routing diagnostics
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
            print(f"  Lane 5 (OVERWATCHDER)   : {lane_counts[5]}")
            print(f"  Lane 6 (DB CORRECTNESS) : {lane_counts[6]}")

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

            print(
                f"[BUS][FILL] attempted={attempted} "
                f"delegated={delegated} "
                f"fill_rate={fill_pct:.1f}% {fill_colour}"
            )

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
                reasons = r.get("reasons", {})
                if r["fired"] > 0:
                    print(f"{eng:<16}: FIRED ({r['fired']} plans)")
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


# Global BUS instance
BUS = DecisionBus()