# ======================================================================================================
# BUS v13 — Behaviour-Identical Reconstruction
# Extracted CTX builder + Engine runner, nothing else changed.
# ======================================================================================================

from engines.live.bank_state import get_engine_available
from engines.live.overwatcher import pull_stoploss_for
from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
from engines.micro_scalper_v7.inplay_engine import InPlayEngine
from engines.micro_scalper_v7.risk_engine import RiskEngine
from engines.mastery.mastery_policy import plan_for_strategy
from engines.mastery.context_builder import build_context
from engines.live.live_router import place_from_bus
from engines.decision_engine.decide_once.scope import build_and_maintain_scope
from engines.math.dynamic_stake_v7 import compute_dynamic_stake
from engines.market_monitor.phase_clock import MarketPhaseClock

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


class DecisionBus:

    def __init__(self):
        self.tick_id = 0

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


    def run_live(self, hz: float = 1.0):
        interval = max(0.05, 1.0 / max(0.1, hz))
        print(f"[BUS] live loop started (hz={hz})")

        while True:
            try:
                self.tick()
            except Exception as e:
                print(f"[BUS][ERR] tick failed: {e}")

            time.sleep(interval)

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
            "plans_rejected": [],    # (plan, reason)

            # routing
            "plans_routed": [],
            "plans_route_failed": [],

            # errors (never fatal)
            "errors": [],
        }


# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: class DecisionBus
# 🧩 ACTION: ADD (new helper method)
# 📆 PATCHED: 2025-12-14 — BUS budget pre-check helper
# ======================================================================================================

    def _has_budget(self, plan, ctx) -> bool:
        from engines.live.bank_state import get_engine_available

        engine = plan.get("engine")
        if not engine:
            return False

        try:
            avail = get_engine_available(engine)
            size = float(plan.get("size") or 0)
            px = float(plan.get("px") or 0)
            direction = plan.get("direction")

            if size <= 0 or px <= 0:
                return False

            if direction == "BACK->LAY":
                liab = size
            else:
                liab = size * max(px - 1.0, 0.0)

            # Conservative pre-check (router re-validates)
            return avail >= (liab * 2)

        except Exception:
            return False

            # --------------------------------------------------
            # RUNNER COUNT (DIAGNOSTIC — REAL, NOT DERIVED)
            # --------------------------------------------------
            try:
                st = get_market_state(mid) or {}
                tick_ctx["runners_seen"] = len(
                    [
                        r for r in (st.get("runners") or {}).values()
                        if r.get("band") in ("ACTIVE", "PASSIVE")
                    ]
                )
            except Exception:
                tick_ctx["runners_seen"] = 0


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

        ctx = dict(base_ctx)
        ctx["marketId"] = mid
        ctx["selectionId"] = sid

        # --------------------------------------------------
        # MARKET MONITOR — SINGLE SOURCE OF RUNNER TRUTH
        # --------------------------------------------------
        st = get_market_state(mid) or {}
        runners = st.get("runners") or {}

        rn = runners.get(sid)
        if not rn:
            # Runner vanished or market not hydrated yet
            return None

        px = rn.get("px")

        # Hard guarantee: ctx px must reflect live monitor state
        ctx["px"] = ctx["odds"] = ctx["ltp"] = px
        ctx["band"] = rn.get("band")
        ctx["is_fav"] = rn.get("is_fav", False)

        # --------------------------------------------------
        # AUTHORITATIVE CLOCK (TIME DIMENSION)
        # --------------------------------------------------
        try:
            phase = MarketPhaseClock.get(mid)

            ctx["oc_phase"] = phase.oc_phase
            ctx["minutes_to_off"] = phase.minutes_to_off
            ctx["phase"] = phase.phase
            ctx["in_play"] = phase.in_play

        except Exception:
            # Safe fallback — should not normally occur
            ctx["oc_phase"] = 0.0
            ctx["minutes_to_off"] = 999.0
            ctx["phase"] = "PRE"
            ctx["in_play"] = False

        # --------------------------------------------------
        # DERIVE TTO WINDOW (BUS-LOCAL, NOT STORED)
        # --------------------------------------------------
        mto = ctx["minutes_to_off"]
        if mto <= 0:
            ctx["tto_window"] = "INP"
        elif mto <= 5:
            ctx["tto_window"] = "S3"
        elif mto <= 10:
            ctx["tto_window"] = "S2"
        elif mto <= 20:
            ctx["tto_window"] = "S1"
        elif mto <= 60:
            ctx["tto_window"] = "60"
        elif mto <= 120:
            ctx["tto_window"] = "120"
        else:
            ctx["tto_window"] = "120+"

        return ctx

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
            engine_report.setdefault(engine, {})
            engine_report[engine].update({
                "evaluated": evaluated,
                "fired": fired,
                "why": why,
            })

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
                        why=p.get("reason") or p.get("why") or "blocked",
                    )
        except Exception as e:
            _record("MSC_EXPLORATORY", evaluated=False, fired=False, why=str(e))

        # ============================
        # MSC In-Play
        # ============================
        try:
            eng = self.engines.get("MSC_INPLAY")
            if eng:
                p = eng.tick(ctx)

                if p is None:
                    _record("MSC_INPLAY", evaluated=True, fired=False, why="no_plan")
                elif p.get("enter"):
                    p["engine"] = "MSC_INPLAY"
                    _record("MSC_INPLAY", evaluated=True, fired=True)
                    plans.append(("MSC_INPLAY", p, ctx))
                else:
                    _record(
                        "MSC_INPLAY",
                        evaluated=True,
                        fired=False,
                        why=p.get("reason") or p.get("why") or "blocked",
                    )
        except Exception as e:
            _record("MSC_INPLAY", evaluated=False, fired=False, why=str(e))

        # ============================
        # Legacy propose_trade
        # ============================
        try:
            import engines.mastery.mastery_policy as mp
            lp = mp.propose_trade(dict(ctx))

            if lp is None:
                _record("LEGACY", evaluated=True, fired=False, why="no_plan")
            elif lp.get("enter"):
                lp["engine"] = "LEGACY"
                lp["strategy"] = "PROPOSE_TRADE"
                _record("LEGACY", evaluated=True, fired=True)
                plans.append(("LEGACY", lp, ctx))
            else:
                _record(
                    "LEGACY",
                    evaluated=True,
                    fired=False,
                    why=lp.get("reason") or lp.get("why") or "blocked",
                )
        except Exception as e:
            _record("LEGACY", evaluated=False, fired=False, why=str(e))

        # ============================
        # Legacy strategy registry
        # ============================
        try:
            for strat_name, strat_fn in self.legacy_strategies:
                if strat_name.upper() in ("L", "MLM", "ALWAYS_ON"):
                    continue

                sp = plan_for_strategy(strat_name, ctx)

                if sp is None:
                    _record("LEGACY", evaluated=True, fired=False, why="no_plan")
                elif sp.get("enter"):
                    sp["engine"] = "LEGACY"
                    sp["strategy"] = strat_name
                    _record("LEGACY", evaluated=True, fired=True)
                    plans.append(("LEGACY", sp, ctx))
                else:
                    _record(
                        "LEGACY",
                        evaluated=True,
                        fired=False,
                        why=sp.get("reason") or sp.get("why") or "blocked",
                    )
        except Exception as e:
            _record("LEGACY", evaluated=False, fired=False, why=str(e))

        return plans

# ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: def _run_engines_for_tick(self, mid, sid, ctx, engine_report):
# 🧩 ACTION: ADD (capture engine decision outcomes, no replacement)
# 📆 PATCHED: 2025-12-15 — Engine decision surface capture
# ======================================================================================================

        def _record(engine, evaluated=True, fired=False, why=None):
            engine_report.setdefault(engine, {})
            engine_report[engine].update({
                "evaluated": evaluated,
                "fired": fired,
                "why": why,
            })



    # ======================================================================
    # analytics_report() — unchanged
    # ======================================================================
    def analytics_report(self):
        from engines.live.live_router import fetch_router_stats
        from engines.live.bank_state import get_engine_pots
        from engines.analytics.snapshot import bus_snapshot

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
    # TICK — rewritten only to call the two new helper functions
    # ======================================================================
    def tick(self):
        self.tick_id += 1
        tick_ctx = self._new_tick_ctx()
     


        try:
            # ==================================================
            # PHASE A — ANALYSIS (UNCONDITIONAL)
            # ==================================================
            tick_ctx["analysis_ran"] = True

            scope = build_and_maintain_scope() or {}
            markets = scope.get("markets") or []
            mids = [m["marketId"] for m in markets if isinstance(m, dict)]
            tick_ctx["markets_seen"] = len(mids)

            base_ctx_raw = build_context(source="LIVE")
            base_ctx = base_ctx_raw[0] if isinstance(base_ctx_raw, tuple) else base_ctx_raw

            # MarketMonitor refresh is advisory — never fatal
            try:
                import engines.market_monitor.monitor as monitor
                if mids:
                    monitor.refresh(mids, max_runners=20)
            except Exception as e:
                tick_ctx["errors"].append(("market_monitor_refresh", str(e)))

            # ------------------------------------
            # PER-TICK ENGINE REPORT (RESET EACH TICK)
            # ------------------------------------
            engine_report = {
                "LEGACY":          {"fired": 0, "blocked": None},
                "MSC_EXPLORATORY": {"fired": 0, "blocked": None},
                "MSC_INPLAY":      {"fired": 0, "blocked": None},
                "MSC_RISK":        {"fired": 0, "blocked": None},
                "OVERWATCHER":     {"fired": 0, "blocked": None},
            }


            # SCOPE
            
            scope = build_and_maintain_scope()
          

            mids = [m["marketId"] for m in scope.get("markets", []) if isinstance(m, dict)]
            if not mids:
                tick_ctx["errors"].append(("analysis", "no_markets_in_scope"))



            # ------------------------------------
            # BASE CTX (MUST COME FIRST)
            # ------------------------------------
            _bc = build_context(source="LIVE")
            if isinstance(_bc, tuple):
                base_ctx, _meta = _bc
            else:
                base_ctx, _meta = _bc, {}


            # ------------------------------------
            # PLAN QUEUE (MUST EXIST BEFORE USE)
            # ------------------------------------
            plan_queue = []

            # ------------------------------------
            # MARKET MONITOR — ANALYSE, THEN READ
            # ------------------------------------
            import engines.market_monitor.monitor as monitor

            try:
                # 🔑 ACTIVE STEP: tell Monitor to analyse these markets
                monitor.refresh(mids, max_runners=20)
            except Exception as e:
                print(f"[BUS][WARN] MarketMonitor refresh failed: {e}")

    # ======================================================================================================
    # 📍 TARGET: engines/bus/bus.py
    # 🔎 SEARCH START:
    #     for mid in mids:
    # 🔎 SEARCH END:
    #     plan_queue.extend(plans)
    # 🧩 ACTION: REPLACE ENTIRE BLOCK
    # 📆 PATCHED: 2025-12-14 — One runner per tick (bucket + market aware)
    # ======================================================================================================

            from engines.market_monitor.monitor import get_market_state

         
            bucketed = scope.get("buckets")
            if not bucketed:
                # Fallback: treat all markets as near20 if buckets not provided
                bucketed = {
                    "near20": [m["marketId"] for m in scope.get("markets", []) if isinstance(m, dict)]
                }

            selected = None

            # Priority order (locked)
            for bucket_name in ("in_play", "near20", "near60", "next5"):

                mids_in_bucket = bucketed.get(bucket_name) or []
                if not mids_in_bucket:
                    continue

                candidates = []
                for mid in mids_in_bucket:
                    st = get_market_state(mid) or {}
                    for sid, r in (st.get("runners") or {}).items():
                        if r.get("band") in ("ACTIVE", "PASSIVE"):
                            tick_ctx["runners_seen"] += 1
                            candidates.append((mid, sid))



                if not candidates:
                    continue

                seen = self._bucket_seen[bucket_name]

                # Reset once all candidates seen
                if len(seen) >= len(candidates):
                    seen.clear()

                for mid, sid in candidates:
                    key = (mid, sid)
                    if key not in seen:
                        selected = (bucket_name, mid, sid)
                        seen.add(key)
                        break

                if selected:
                    break

    # ======================================================================================================
    # 📍 TARGET: engines/bus/bus.py
    # 🔎 ANCHOR: if not selected:
    # 🧩 ACTION: REPLACE ENTIRE BLOCK
    # 📆 PATCHED: 2025-12-15 — Prevent silent tick on no_runnable_runners
    # ======================================================================================================

            if not selected:
                tick_ctx["errors"].append(("analysis", "no_runnable_runners"))
                # do NOT return — allow final TICK report


            bucket_name, mid, sid = selected

            ctx = self._build_ctx_for_market(base_ctx, mid, sid)
    # ======================================================================================================
    # 📍 TARGET: engines/bus/bus.py
    # 🔎 ANCHOR: if not ctx:
    # 🧩 ACTION: REPLACE ENTIRE BLOCK
    # 📆 PATCHED: 2025-12-15 — Prevent silent tick on ctx_build_failed
    # ======================================================================================================

            if not ctx:
                tick_ctx["errors"].append(("analysis", "ctx_build_failed"))
                # do NOT return — allow final TICK report


            plans = self._run_engines_for_tick(mid, sid, ctx, engine_report)
            # --------------------------------------------------
            # PHASE 1 REPORT — ANALYSIS
            # --------------------------------------------------
            print("────────────────────────────────────────────────────────")
            print(f"[BUS][PHASE 1][ANALYSIS] tick=#{self.tick_id}")
            print("────────────────────────────────────────────────────────")

            print("SCOPE")
            print(f"  markets_seen   : {tick_ctx['markets_seen']}")
            print(f"  runners_seen   : {tick_ctx['runners_seen']}")

            evaluated = len(engine_report)
            fired = sum(1 for r in engine_report.values() if r.get("fired"))

            print("\nENGINES")
            print(f"  evaluated      : {evaluated}")
            print(f"  fired          : {fired}")

            print("\nENGINE OUTCOMES")
            for eng, info in engine_report.items():
                print(
                    f"  {eng:<16} "
                    f"evaluated={info.get('evaluated')} "
                    f"fired={info.get('fired')} "
                    f"why={info.get('why')}"
                )

            print("\nPLANS")
            print(f"  raw            : {len(plans)}")

            print("\nERRORS")
            if tick_ctx["errors"]:
                for e in tick_ctx["errors"]:
                    print(f"  - {e}")
            else:
                print("  none")

            print("────────────────────────────────────────────────────────\n")

            # ==================================================
            # PHASE 2 — ENRICHMENT (BEGINS)
            # ==================================================

            # ------------------------------------
            # ENGINE OUTCOME BINDING (reporting)
            # ------------------------------------
            tick_ctx["engine_outcomes"] = engine_report

            plan_queue.extend(plans)

            # ------------------------------------
            # RAW PLAN CAPTURE (analysis visibility)
            # ------------------------------------
            for _eng, _plan, _ctx in plans:
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
                except Exception:
                    dec = None

                exec_dir = dec.get("direction") if isinstance(dec, dict) else None
                plan_dir = plan.get("direction")

                # Inject direction if missing
                if not plan_dir and exec_dir:
                    plan["direction"] = exec_dir
                    plan_dir = exec_dir

                # Blocker 1 — direction invalidated
                if plan_dir and exec_dir and plan_dir != exec_dir:
                    engine_report[plan["engine"]]["blocked"] = "direction_changed"
                    tick_ctx["plans_rejected"].append((plan, "direction_changed"))
                    continue

                # Blocker 2 — budget pre-check (BUS-level only)
                if not self._has_budget(plan, ctx):
                    engine_report[plan["engine"]]["blocked"] = "insufficient_budget"
                    tick_ctx["plans_rejected"].append((plan, "insufficient_budget"))
                    continue

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
            print(f"  rejected       : {len(tick_ctx['plans_rejected'])}")

            # --------------------------------------------------
            # Rejection reason breakdown (diagnostic)
            # --------------------------------------------------
            reasons = {}
            for _plan, reason in tick_ctx["plans_rejected"]:
                reasons[reason] = reasons.get(reason, 0) + 1

            print("\nREJECTIONS")
            if reasons:
                for reason, count in reasons.items():
                    print(f"  {reason:<22} : {count}")
            else:
                print("  none")

            print("────────────────────────────────────────────────────────\n")

            # ==================================================
            # PHASE 3 — ROUTING (BEGINS)
            # ==================================================
            from engines.live.overwatcher import pull_stoploss_for

            sl_plan = pull_stoploss_for(mid, sid)
            if sl_plan:
                plans.append(sl_plan)


            # Routing
            for eng, p, ctx in final_plans:

                # --------------------------------------------------
                # 🔥 NORMALISE ROUTING IDENTITY (CRITICAL)
                # --------------------------------------------------
                if "marketId" not in p or p.get("marketId") is None:
                    p["marketId"] = ctx.get("marketId")

                if "selectionId" not in p or p.get("selectionId") is None:
                    p["selectionId"] = ctx.get("selectionId")

                # --------------------------------------------------
                # Route the plan
                # --------------------------------------------------
                self._route(p, ctx)



            # ==================================================
            # PHASE 3 — ROUTING REPORT (ROUTER REALITY)
            # DB-SOURCED — AUTHORITATIVE
            # ==================================================
            tick_ctx["routing_ran"] = True

            from engines.config_paths import auto_conn_live
            import sqlite3

            con = auto_conn_live(rw=False)
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            attempted = cur.execute("""
                SELECT COUNT(*)
                FROM orders
                WHERE mode='LIVE'
                  AND role='PARENT'
                  AND opened_at >= datetime(?, 'unixepoch')
            """, (tick_ctx["ts_start"],)).fetchone()[0]

            parents = cur.execute("""
                SELECT
                    entry_status,
                    COUNT(*) AS n
                FROM orders
                WHERE mode='LIVE'
                  AND role='PARENT'
                  AND opened_at >= datetime(?, 'unixepoch')
                GROUP BY entry_status
            """, (tick_ctx["ts_start"],)).fetchall()

            failures = cur.execute("""
                SELECT
                    COALESCE(error, 'unknown') AS reason,
                    COUNT(*) AS n
                FROM orders
                WHERE mode='LIVE'
                  AND role='PARENT'
                  AND entry_status='failed'
                  AND opened_at >= datetime(?, 'unixepoch')
                GROUP BY error
            """, (tick_ctx["ts_start"],)).fetchall()

            con.close()

            # Persist into tick_ctx for 10-tick rollup
            tick_ctx["plans_routed"] = sum(
                r["n"] for r in parents if r["entry_status"] != "failed"
            )
            tick_ctx["plans_route_failed"] = [
                (f["reason"], f["n"]) for f in failures
            ]

            print("────────────────────────────────────────────────────────")
            print(f"[BUS][PHASE 3][ROUTING] tick=#{self.tick_id}")
            print("────────────────────────────────────────────────────────")

            print("ROUTING ATTEMPTS")
            print(f"  attempted      : {attempted}")

            print("\nROUTER STATES (PARENTS)")
            if parents:
                for r in parents:
                    print(f"  {r['entry_status']:<10} : {r['n']}")
            else:
                print("  none")

            print("\nROUTER FAILURES")
            if failures:
                for f in failures:
                    print(f"  {f['reason']:<22} : {f['n']}")
            else:
                print("  none")

            print("────────────────────────────────────────────────────────\n")



            # ------------------------------------
            # TICK REPORT — ALWAYS PRINT
            # ------------------------------------
            runner_count = sum(
                1 for mid in mids
                for r in (get_market_state(mid) or {}).get("runners", {}).values()
                if r.get("band") in ("ACTIVE", "PASSIVE")
            )

            print("────────────────────────────────────────────────────────")
            print(f"[BUS][TICK] #{self.tick_id}   markets={len(mids)} runners={runner_count}")
            print("────────────────────────────────────────────────────────\n")

            print("ENGINE SUMMARY")
            print("────────────────────────────────────────────────────────")
            for eng, r in engine_report.items():
                if r["fired"] > 0:
                    print(f"{eng:<16}: FIRED    ({r['fired']} plans)")
                else:
                    print(f"{eng:<16}: BLOCKED  reason={r['blocked']}")

            print("\nEXECUTION")
            print("────────────────────────────────────────────────────────")
            print(f"plans_generated : {len(plan_queue)}")
            print(f"plans_routed    : {len(final_plans)}")

            try:
                from engines.analytics.snapshot import bus_snapshot
                snap = bus_snapshot()
                print(f"exposure        : {snap['exposure']:.2f}")
            except Exception:
                print("exposure        : unavailable")

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
                print(f"  rejected      : {len(tick_ctx['plans_rejected'])}")
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
    # It delegates to the Live Router and records observable outcomes only.
    # ======================================================================
    def _route(self, plan, ctx):
        """
        Phase 3 routing + reporting anchor.
        Execution is delegated based on engine type.
        """

        engine = (plan.get("engine") or "").upper()
        ptype  = (plan.get("type") or "").upper()
        try:
            # --------------------------------------------------
            # OVERWATCHER STOPLOSS → CHILD-ONLY execution
            # --------------------------------------------------
            if engine == "OVERWATCHER" and ptype == "STOPLOSS":
                from engines.live.live_router import place_from_bus
                place_from_bus(plan, ctx)
                return
            # --------------------------------------------------
            # MSC engines → direct router path
            # --------------------------------------------------
            if engine.startswith("MSC_"):
                from engines.live.live_router import place_from_bus
                place_from_bus(plan, ctx)

            # --------------------------------------------------
            # Legacy engines → legacy placement pipeline
            # --------------------------------------------------
            else:
                from engines.live.live_router import place_legacy_from_bus
                place_legacy_from_bus(plan, ctx)

        except Exception as e:
            # Routing errors must never stop the tick
            try:
                self._tick_ctx["plans_route_failed"].append(
                    (plan, f"router_error:{e}")
                )
            except Exception:
                pass





# ======================================================================
# END OF def tick(self)
# ======================================================================


# Global BUS instance
BUS = DecisionBus()
