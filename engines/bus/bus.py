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
from engines.market_monitor.phase_clock import MarketPhaseClock
from engines.live.overwatcher import evaluate_redistribution
from engines.risk.risk_price_helper_v2 import get_legacy_parent_odds_snapshot

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
#   plans_per_tick = 60
# ======================================================================================================

import time
from collections import deque

class CadenceController:
    def __init__(self):
        # Locked parameters
        self.window_seconds   = 30
        self.tick_seconds     = 6
        self.ticks_per_window = 5
        self.plans_per_window = 300
        self.plans_per_tick   = 60

        # State
        self.window_start_ts = time.time()
        self.tick_index = 0
        self.queue = deque()

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
        ctx = self._route_ctx_map.get((mid, sid))

        if not ctx:
            continue
        ctxs[(mid, sid)] = ctx

    return ctxs


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

        # --------------------------------------------------
        # Parent / child lifecycle counts (LIVE, open only)
        # --------------------------------------------------
        snap["parents_opened"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='PARENT'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(entry_status)='MATCHED'
              AND (exit_status IS NULL OR UPPER(exit_status)!='MATCHED')
        """).fetchone()[0]

        snap["children_opened"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='CHILD'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(entry_status)='MATCHED'
              AND (exit_status IS NULL OR UPPER(exit_status)!='MATCHED')
        """).fetchone()[0]

        # --------------------------------------------------
        # Children fully matched (completed lifecycle)
        # --------------------------------------------------
        snap["children_matched"] = con.execute("""
            SELECT COUNT(*)
            FROM orders
            WHERE role='CHILD'
              AND UPPER(COALESCE(mode,''))='LIVE'
              AND UPPER(entry_status)='MATCHED'
              AND UPPER(exit_status)='MATCHED'
        """).fetchone()[0]

        # --------------------------------------------------
        # Exposure (LIVE open parent liability)
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
        """).fetchone()

        snap["exposure"] = float(row[0] or 0.0)

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
    ALLOWED_LEGACY_LETTERS = {"S", "P", "B", "G", "X", "R", "F"}

    def __init__(self):
        self.tick_id = 0
        self.live_run_id = None
        self._route_buffer = deque()
        self._route_rotation = RunnerRotation()
        self._route_id = 1
        self._bus_stop = 0
        self._cadence = CadenceController()
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

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: class DecisionBus
# 🧩 ACTION: ADD method (bind lifecycle gate to BUS instance)
# 📆 PATCHED: 2026-03-18 — Fix missing _engine_ctx_allowed binding
#
# WHY:
# - _engine_ctx_allowed was defined at module scope
# - BUS calls it as an instance method (self._engine_ctx_allowed)
# - This caused a runtime AttributeError during live ticks
#
# CONTRACT:
# - Behaviour unchanged
# - DB-first lifecycle gate preserved
# - MSC + LEGACY semantics untouched
# ======================================================================================================

    def _engine_ctx_allowed(self, engine: str, ctx: dict) -> bool:
        """
        DB-authoritative per-engine CTX gate.

        Rules:
        - LEGACY always allowed
        - Other engines:
            • no matched parent → allow
            • matched parent + matched child → allow
            • matched parent + unmatched child → block
        """

        if engine == "LEGACY":
            return True

        mid = ctx.get("marketId")
        sid = ctx.get("selectionId")

        if not mid or not sid:
            return True

        try:
            from engines.config_paths import open_auto_db

            con = open_auto_db(rw=False)

            row = con.execute(
                """
                SELECT
                    p.id,
                    p.entry_status,
                    c.exit_status
                FROM orders p
                LEFT JOIN orders c
                  ON c.hedge_of = p.id
                WHERE p.engine = ?
                  AND p.marketId = ?
                  AND p.selectionId = ?
                  AND p.role = 'PARENT'
                  AND UPPER(p.entry_status) = 'MATCHED'
                LIMIT 1
                """,
                (engine, mid, sid),
            ).fetchone()

            # No matched parent → allow
            if row is None:
                return True

            _pid, _parent_status, child_exit_status = row

            # Parent matched, child not matched → block
            if not child_exit_status or str(child_exit_status).upper() != "MATCHED":
                return False

            # Parent + child matched → allow
            return True

        except Exception:
            # BUS must fail-open, never deadlock
            return True

        finally:
            try:
                con.close()
            except Exception:
                pass


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
        lane_counts = {1: 0, 2: 0, 3: 0, 4: 0}

        # --------------------------------------------------
        # 🔁 ODDS REFRESH — HELPER OWNED (AUTHORITATIVE)
        # --------------------------------------------------
        from engines.bus_route import get_runner_odds_map

        all_pairs = list(self._route_ctx_map.keys())
        odds_map = get_runner_odds_map(all_pairs)

        for (mid, sid), odds in odds_map.items():
            ctx = self._route_ctx_map.get((mid, sid))
            if not ctx:
                continue

            ctx["px"]   = odds["px"]
            ctx["odds"] = odds["px"]
            ctx["back"] = odds.get("back")
            ctx["lay"]  = odds.get("lay")

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
        # 🟦 LANE 1 — LEGACY (BUS STOP ONLY)
        # --------------------------------------------------
        engine_report["LEGACY"]["evaluated"] = True

        for mid, sid in bus_stop_pairs:
            ctx = self._route_ctx_map.get((mid, sid))
            if not ctx or ctx.get("px") is None:
                continue

            for letter in self.ALLOWED_LEGACY_LETTERS:
                ctx_l = dict(ctx)
                ctx_l["letter"] = letter

                try:
                    res = plan_for_strategy(letter, ctx_l)
                    if res and res.get("enter"):
                        plan = dict(res)
                        plan["engine"] = "LEGACY"
                        plans.append(("LEGACY", plan, ctx_l))
                        engine_report["LEGACY"]["fired"] += 1
                        lane_counts[1] += 1
                except Exception as e:
                    _record_reason(engine_report, "LEGACY", f"mastery_error:{e}")

        # --------------------------------------------------
        # 🟨 LANE 2 — MSC_RISK (HELPER-DRIVEN)
        # --------------------------------------------------
        engine_report["MSC_RISK"]["evaluated"] = True

        from engines.bus_route import get_risk_legacy_parent_pairs

        risc = self.engines.get("MSC_RISK")
        if risc:
            for mid, sid in get_risk_legacy_parent_pairs():
                ctx = self._route_ctx_map.get((mid, sid))
                if not ctx or ctx.get("px") is None:
                    continue

                try:
                    r = risc.tick(ctx)
                    if r and r.get("enter"):
                        plan = dict(r)
                        plan["engine"] = "MSC_RISK"
                        plans.append(("MSC_RISK", plan, ctx))
                        engine_report["MSC_RISK"]["fired"] += 1
                        lane_counts[2] += 1
                except Exception:
                    _record_reason(engine_report, "MSC_RISK", "tick_error")

        # --------------------------------------------------
        # 🟥 LANE 3 — MSC_INPLAY (HELPER-DRIVEN)
        # --------------------------------------------------
        engine_report["MSC_INPLAY"]["evaluated"] = True

        from engines.bus_route import get_v7_inplay_snapshot

        inplay = self.engines.get("MSC_INPLAY")
        if inplay:
            seen_markets = set()
            inplay_pairs = set()

            for mid, _ in self._route_ctx_map.keys():
                if mid in seen_markets:
                    continue
                seen_markets.add(mid)

                for r in get_v7_inplay_snapshot(mid) or []:
                    inplay_pairs.add((mid, str(r["selectionId"])))

            for mid, sid in inplay_pairs:
                ctx = self._route_ctx_map.get((mid, sid))
                if not ctx or ctx.get("px") is None:
                    continue

                try:
                    r = inplay.tick(ctx)
                    if r and r.get("enter"):
                        plan = dict(r)
                        plan["engine"] = "MSC_INPLAY"
                        plans.append(("MSC_INPLAY", plan, ctx))
                        engine_report["MSC_INPLAY"]["fired"] += 1
                        lane_counts[3] += 1
                except Exception:
                    _record_reason(engine_report, "MSC_INPLAY", "tick_error")

        # --------------------------------------------------
        # 🟩 LANE 4 — MSC_EXPLORATORY (ROUTE − EXCLUSIONS)
        # --------------------------------------------------
        engine_report["MSC_EXPLORATORY"]["evaluated"] = True

        from engines.bus_route import get_exploratory_active_parent_pairs

        exclusions = get_exploratory_active_parent_pairs()

        exp = self.engines.get("MSC_EXPLORATORY")
        if exp:
            for (mid, sid), ctx in self._route_ctx_map.items():
                if (mid, sid) in exclusions or ctx.get("px") is None:
                    continue

                try:
                    r = exp.tick(ctx)
                    if r and r.get("enter"):
                        plan = dict(r)
                        plan["engine"] = "MSC_EXPLORATORY"
                        plans.append(("MSC_EXPLORATORY", plan, ctx))
                        engine_report["MSC_EXPLORATORY"]["fired"] += 1
                        lane_counts[4] += 1
                except Exception:
                    _record_reason(engine_report, "MSC_EXPLORATORY", "tick_error")

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
    # CTX BUILDER — MarketMonitor authoritative (Scope provides MIDs only)
    # ======================================================================
    def _build_ctx_for_market(self, base_ctx, mid, sid):
        """
        Build a per-runner execution context.

        Architectural contract:
        - Scope provides marketIds ONLY
        - MarketMonitor provides runners + prices
        - BUS joins them here
        """

        from engines.market_monitor.monitor import get_market_state

        def _refresh_ctx_odds(self, ctx):
            st = get_market_state(ctx["marketId"]) or {}
            runners = st.get("runners") or {}
            rn = runners.get(ctx["selectionId"])

            if not rn:
                return  # runner vanished; engine will no-op safely

            px = rn.get("px")
            if px is None:
                return

            ctx["px"] = px
            ctx["odds"] = px
            ctx["ltp"] = px

        ctx = dict(base_ctx)
        if not self.live_run_id:
            self.live_run_id = f"BOOT-{int(time.time())}"
        ctx["run_id"] = self.live_run_id


        ctx["marketId"] = mid
        ctx["selectionId"] = sid

        # --------------------------------------------------
        # RUNNER ORDERS SNAPSHOT (for lifecycle engines)
        # --------------------------------------------------
        try:
            from engines.config_paths import open_auto_db
            con = open_auto_db(rw=False)
            con.row_factory = None

            rows = con.execute("""
                SELECT
                    id,
                    engine,
                    role,
                    entry_status,
                    exit_status,
                    hedge_of,
                    marketId,
                    selectionId
                FROM orders
                WHERE marketId = ?
                  AND selectionId = ?
            """, (mid, sid)).fetchall()

            ctx["orders_by_runner"] = [
                {
                    "id": r[0],
                    "engine": r[1],
                    "role": r[2],
                    "entry_status": r[3],
                    "exit_status": r[4],
                    "hedge_of": r[5],
                    "marketId": r[6],
                    "selectionId": r[7],
                }
                for r in rows
            ]

        except Exception:
            # Fail-safe: lifecycle engines will no-op
            ctx["orders_by_runner"] = []

        # ------------------------------------------------------------------
        # RISC CONTRACT INJECTION (AUTHORITATIVE — BUS RESPONSIBILITY)
        # ------------------------------------------------------------------
        ctx["legacy_parent_id"] = None
        ctx["legacy_entry_side"] = None
        ctx["legacy_entry_odds"] = None
        ctx["legacy_entry_stake"] = None

        for o in ctx.get("orders_by_runner", []):
            if (
                o.get("engine") == "LEGACY"
                and o.get("role") == "PARENT"
                and o.get("entry_status") == "MATCHED"
            ):
                ctx["legacy_parent_id"] = o["id"]
                ctx["legacy_entry_side"] = o.get("side")
                ctx["legacy_entry_odds"] = o.get("entry_odds")
                ctx["legacy_entry_stake"] = o.get("entry_stake")
                break

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: def _build_ctx_for_market(self, base_ctx, mid, sid):
# 🧩 ACTION: REMOVE phase clock dependency entirely
# 📆 PATCHED: 2026-03-18 — BUS trusts route helper; remove OC/phase logic
#
# WHY:
# - Runner identity + lifecycle are owned by BusRouteSnapshot
# - Temporal correctness is upstream (Scope + MarketMonitor + inbound OC cache)
# - BUS only requires fresh odds + DB lifecycle truth
#
# EFFECT:
# - Removes MarketPhaseClock dependency
# - Eliminates None unpack crash
# - Simplifies BUS responsibility to routing only
# ======================================================================================================

        # ❌ REMOVED:
        # MarketPhaseClock.get()
        # oc_phase
        # minutes_to_off
        # phase
        # in_play
        # tto_window


    # ======================================================================
    # NEW FUNCTION 2 — extracted engine plan collection
    # EXACT logic lifted from your working BUS, unchanged
    # ======================================================================
    def _run_engines_for_tick(self, mid, sid, ctx, engine_report):

        plans = []

        # --------------------------------------------------
        # Helper — engine decision surface capture ONLY
        # (no behavioural impact)
        # --------------------------------------------------
        def _record(engine, evaluated=True, fired=False, why=None):
            eng = engine_report.setdefault(engine, {})
            eng["evaluated"] = evaluated
            eng["fired"] = eng.get("fired", 0) + (1 if fired else 0)

            if why:
                reasons = eng.setdefault("reasons", {})
                reasons[why] = reasons.get(why, 0) + 1
        # ============================
        # MSC Exploratory
        # ============================
        try:
            eng = self.engines.get("MSC_EXPLORATORY")
            if eng:
                p = eng.tick(ctx)

                if p is None:
                    _record("MSC_EXPLORATORY", evaluated=True, fired=False, why="no_plan")
                elif p.get("enter"):
                    p["engine"] = "MSC_EXPLORATORY"
                    _record("MSC_EXPLORATORY", evaluated=True, fired=True)
                    plans.append(("MSC_EXPLORATORY", p, ctx))
                else:
                    _record(
                        "MSC_EXPLORATORY",
                        evaluated=True,
                        fired=False,
                        why=p.get("reason") or p.get("why") or "note",
                    )
        except Exception as e:
            _record("MSC_EXPLORATORY", evaluated=False, fired=False, why=str(e))

        # ============================
        # MSC In-Play
        # ============================
# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: inside _run_engines_for_tick → MSC In-Play section
# 🧩 ACTION: REPLACE in_play gate logic
# 📆 PATCHED: 2026-01-02 — fix MSC_INPLAY scope contract
# ======================================================================================================

        try:
            eng = self.engines.get("MSC_INPLAY")
            if eng:

                # Correct authority: MarketPhaseClock via ctx
                if not ctx.get("in_play", False):
                    _record(
                        "MSC_INPLAY",
                        evaluated=True,
                        fired=False,
                        why="not_in_play",
                    )
                else:
                    p = eng.tick(ctx)

                    if p is None:
                        _record(
                            "MSC_INPLAY",
                            evaluated=True,
                            fired=False,
                            why="no_plan",
                        )
                    elif p.get("enter"):
                        p["engine"] = "MSC_INPLAY"
                        _record("MSC_INPLAY", evaluated=True, fired=True)
                        plans.append(("MSC_INPLAY", p, ctx))
                    else:
                        _record(
                            "MSC_INPLAY",
                            evaluated=True,
                            fired=False,
                            why=p.get("reason") or p.get("why") or "note",
                        )
        except Exception as e:
            _record("MSC_INPLAY", evaluated=False, fired=False, why=str(e))

        # ✅ RETURN MUST BE HERE (same indent as `plans = []`)
        return plans
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
    # TICK — authoritative BUS lifecycle (route → ctx → lanes)
    # ======================================================================
    def tick(self):
        # ===============================================================
        # 0️⃣ BUS IDENTITY
        # ===============================================================
        self.tick_id += 1

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

        # ===============================================================
        # 3️⃣ ROUTE / BUS STOP ADVANCEMENT
        # ===============================================================
        self._bus_stop += 1
        if self._bus_stop > 10:
            self._bus_stop = 1
            self._route_id += 1

        # ===============================================================
        # 3️⃣ ROUTE / BUS CTX V7
        # ===============================================================
        if self._bus_stop == 1 or not self._route_ctx_map:
            from engines.bus_route import build_route_ctx_map
            self._route_ctx_map = build_route_ctx_map()


        # ===============================================================
        # 4️⃣ ROUTE SNAPSHOT (AUTHORITATIVE, ONCE PER ROUTE)
        # ===============================================================
        if self._bus_stop == 1 or self._route_snapshot is None:
            from engines.bus_route import BusRouteSnapshot

            self._route_snapshot = BusRouteSnapshot()
            self._route_snapshot.build_route()
            self._route_snapshot.partition_into_bus_stops()

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: def tick(self):
# 🧩 ACTION: REPLACE CTX BUILD SECTION (route-authoritative)
# 📆 PATCHED: 2026-03-16 — Enforce route-owned runner identity for CTX
#
# WHY:
# - BUS must NEVER recompute or rediscover (marketId, selectionId)
# - BusRouteSnapshot is the sole authority for runner membership
# - CTX is built ONLY for runners already selected by the route helper
#
# EFFECT:
# - Fixes broken CTX → engine pipeline
# - Prevents scope / monitor re-entry
# - Restores correct plan generation flow
# ======================================================================================================

        # ===============================================================
        # 5️⃣ BUILD ROUTE-LEVEL CTX MAP (AUTHORITATIVE)
        # ===============================================================
        #
        # IMPORTANT:
        # - Runner identity comes ONLY from BusRouteSnapshot
        # - BUS does NOT filter, discover, or recompute mids/sids
        # - CTX is keyed once per route and reused across ticks
        #
        # ===============================================================

        # Initialise map if missing or new route
        if not self._route_ctx_map or self._bus_stop == 1:

            self._route_ctx_map = {}

            for mid, sid in self._route_snapshot.get_all_runners():
                ctx = self._build_ctx_for_market(base_ctx, mid, sid)
                if ctx:
                    self._route_ctx_map[(mid, sid)] = ctx

        # ===============================================================
        # 6️⃣ LEGACY BUS STOP SLICE (ROUTE-PROVIDED)
        # ===============================================================
        #
        # NOTE:
        # - bus stop membership is precomputed by the route helper
        # - BUS does NOT alter or filter this list
        #
        # ===============================================================

        legacy_slice = self._route_snapshot.get_bus_stop(self._bus_stop) or []
        bus_stop_pairs = legacy_slice


        # ===============================================================
        # 7️⃣ AUTHORITATIVE PLAN GENERATION (LANES ONLY)
        # ===============================================================
        # ======================================================================================================
        # 📍 TARGET: engines/bus/bus.py
        # 🔎 SEARCH: generated_plans = self._evaluate_runner(
        # 🧩 ACTION: REPLACE
        # 📆 PATCHED: 2026-03-16 — bind returned lane counts
        # ======================================================================================================

        generated_plans, lane_counts = self._evaluate_runner(
            base_ctx=base_ctx,
            bus_stop_pairs=legacy_slice,
            engine_report=engine_report,
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

            upstream_pid = plan.get("plan_id")
            if upstream_pid:
                plan["plan_id"] = f"{bus_exec_id}-{upstream_pid}"
            else:
                plan["plan_id"] = bus_exec_id

            plans.append((eng, plan, ctx))

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
        print(f"  markets_seen   : {len(mids)}")
        print(f"  runners_seen   : {runner_count}")

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

            # Feed ALL generated plans into cadence controller
            self._cadence.enqueue(plans)

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


                # --------------------------------------------------
                # Execution enrichment (ONLY missing execution fields)
                # --------------------------------------------------
                plan.setdefault("marketId", ctx.get("marketId"))
                plan.setdefault("selectionId", ctx.get("selectionId"))
                plan.setdefault("px", ctx.get("px"))

                # --------------------------------------------------
                # Direction check (execution truth)
                # --------------------------------------------------
                dec = None
                try:
                    dec = compute_msc_decision(ctx)
                    ctx["msc_decision"] = dec
                except Exception:
                    dec = None

                exec_dir = dec.get("direction") if isinstance(dec, dict) else None
                plan_dir = plan.get("direction")

                # Inject direction if missing
                if not plan_dir and exec_dir:
                    plan["direction"] = exec_dir
                    plan_dir = exec_dir

                # ------------------------------------------------------------------
                # STAKE ENRICHMENT (ENGINE-AWARE — SINGLE AUTHORITY)
                #
                # Rules:
                # - MSC_RISK computes stake mechanically inside the engine
                # - BUS must respect RISK stake and never override it
                # - All other engines use dynamic stake
                # ------------------------------------------------------------------
                engine = plan.get("engine")

                if engine == "MSC_RISK":
                    # RISK owns stake calculation
                    if not plan.get("size") or float(plan.get("size") or 0) <= 0:
                        plan["_bus_block"] = "risk_plan_missing_size"
                        tick_ctx["plans_route_failed"].append(
                            (plan, "risk_plan_missing_size")
                        )
                        _record_reason(engine, "risk_plan_missing_size")
                        continue  # 🔴 DO NOT ROUTE
                    plan["_stake_source"] = "risk_engine"

                else:
                    # All other engines use dynamic stake
                    from engines.live.bank_state import get_engine_available
                    from engines.math.dynamic_stake_v7 import compute_dynamic_stake

                    if "size" not in plan or plan.get("size") in (None, 0):

                        px = float(plan.get("px") or 0.0)

                        # Guard: cannot compute stake without these
                        if not engine or px <= 0:
                            plan["_bus_block"] = "dynamic_stake_missing_inputs"
                            tick_ctx["plans_route_failed"].append(
                                (plan, "dynamic_stake_missing_inputs")
                            )
                            _record_reason(engine_report, engine, "dynamic_stake_zero")
                            continue  # 🔴 DO NOT ROUTE

                        try:
                            pot = get_engine_available(engine)

                            stake, stake_meta = compute_dynamic_stake(
                                engine=engine,
                                px=px,
                                pot=pot,
                                ctx=ctx,
                            )

                            # Hard guarantee — size must be valid
                            if not stake or stake <= 0:
                                plan["_bus_block"] = "dynamic_stake_zero"
                                tick_ctx["plans_route_failed"].append(
                                    (plan, "dynamic_stake_zero")
                                )
                                _record_reason(engine_report, engine, "dynamic_stake_zero")
                                continue  # 🔴 DO NOT ROUTE

                            plan["size"] = float(stake)
                            plan["_stake_source"] = "dynamic"
                            plan["_stake_meta"] = stake_meta

                        except Exception as e:
                            plan["_bus_block"] = "dynamic_stake_error"
                            tick_ctx["plans_route_failed"].append(
                                (plan, f"dynamic_stake_error:{e}")
                            )
                            _record_reason(engine, "dynamic_stake_error")
                            continue  # 🔴 DO NOT ROUTE


            
                # --- BUS MUST NEVER BLOCK EXECUTION ---
                # Annotate only, router decides.

                # Direction drift annotation (diagnostic only)
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
            # PHASE 3 — ROUTING (BEGINS)
            # ==================================================

            import uuid

            # Routing
            from engines.decision_engine.decide_once.placement import enqueue_for_placement

            admitted = self._cadence.admit_for_tick()

            for eng, p, ctx in admitted:
                if not p.get("customerOrderRef"):
                    p["customerOrderRef"] = f"{p['engine'][:1]}-{uuid.uuid4().hex[:10]}"

                ctx["engine"] = p["engine"]
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
            print(f"[BUS][TICK] #{self.tick_id} #{self._route_id} #{self._bus_stop} markets={len(mids)} runners={runner_count}")
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
