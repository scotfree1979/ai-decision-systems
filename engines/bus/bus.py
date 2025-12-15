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

        # ============================
        # MSC Exploratory
        # ============================
        try:
            eng = self.engines.get("MSC_EXPLORATORY")
            if eng:
                p = eng.tick(ctx)

                if p is None:
                    engine_report["MSC_EXPLORATORY"]["blocked"] = "no_plan"
                elif p.get("enter"):
                    p["engine"] = "MSC_EXPLORATORY"
                    engine_report["MSC_EXPLORATORY"]["fired"] += 1
                    engine_report["MSC_EXPLORATORY"]["blocked"] = None
                    plans.append(("MSC_EXPLORATORY", p, ctx))
                else:
                    engine_report["MSC_EXPLORATORY"]["blocked"] = (
                        p.get("reason") or p.get("why") or "blocked"
                    )
        except Exception:
            pass

        # ============================
        # MSC In-Play
        # ============================
        try:
            eng = self.engines.get("MSC_INPLAY")
            if eng:
                p = eng.tick(ctx)

                if p is None:
                    engine_report["MSC_INPLAY"]["blocked"] = "no_plan"
                elif p.get("enter"):
                    p["engine"] = "MSC_INPLAY"
                    engine_report["MSC_INPLAY"]["fired"] += 1
                    engine_report["MSC_INPLAY"]["blocked"] = None
                    plans.append(("MSC_INPLAY", p, ctx))
                else:
                    engine_report["MSC_INPLAY"]["blocked"] = (
                        p.get("reason") or p.get("why") or "blocked"
                    )
        except Exception:
            pass



        # ============================
        # Legacy propose_trade
        # ============================
        try:
            import engines.mastery.mastery_policy as mp
            lp = mp.propose_trade(dict(ctx))

            if lp is None:
                engine_report["LEGACY"]["blocked"] = "no_plan"
            elif lp.get("enter"):
                lp["engine"] = "LEGACY"
                lp["strategy"] = "PROPOSE_TRADE"
                engine_report["LEGACY"]["fired"] += 1
                engine_report["LEGACY"]["blocked"] = None
                plans.append(("LEGACY", lp, ctx))
            else:
                engine_report["LEGACY"]["blocked"] = (
                    lp.get("reason") or lp.get("why") or "blocked"
                )
        except Exception:
            pass

        # ============================
        # Legacy strategy registry
        # ============================
        try:
            for strat_name, strat_fn in self.legacy_strategies:
                if strat_name.upper() in ("L", "MLM", "ALWAYS_ON"):
                    continue

                sp = plan_for_strategy(strat_name, ctx)

                if sp is None:
                    engine_report["LEGACY"]["blocked"] = "no_plan"
                elif sp.get("enter"):
                    sp["engine"] = "LEGACY"
                    sp["strategy"] = strat_name
                    engine_report["LEGACY"]["fired"] += 1
                    engine_report["LEGACY"]["blocked"] = None
                    plans.append(("LEGACY", sp, ctx))
                else:
                    engine_report["LEGACY"]["blocked"] = (
                        sp.get("reason") or sp.get("why") or "blocked"
                    )
        except Exception:
            pass

        return plans


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
        tick_ts = time.time()

        tick_record = {
            "tick_id": self.tick_id,
            "ts": tick_ts,
            "mode": "LIVE",
            "markets_seen": 0,
            "markets_ready": 0,
            "runners_seen": 0,
            "plans_generated": 0,
            "plans_routed": 0,
            "reason": "OK",
        }


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
            tick_record["reason"] = "no_markets_in_scope"
            self._persist_tick(tick_record)
            self._print_tick(tick_record)
            return


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

        if not selected:
            tick_record["reason"] = "no_runnable_runners"
            self._persist_tick(tick_record)
            self._print_tick(tick_record)
            return

        bucket_name, mid, sid = selected

        ctx = self._build_ctx_for_market(base_ctx, mid, sid)
        if not ctx:
            tick_record["reason"] = "ctx_build_failed"
            self._persist_tick(tick_record)
            self._print_tick(tick_record)
            return

        plans = self._run_engines_for_tick(mid, sid, ctx, engine_report)
        plan_queue.extend(plans)



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
                continue

            # Blocker 2 — budget pre-check (BUS-level only)
            if not self._has_budget(plan, ctx):
                engine_report[plan["engine"]]["blocked"] = "insufficient_budget"
                continue

            final_plans.append((eng, plan, ctx))


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

        # ------------------------------------
        # FINALISE TICK RECORD  ✅ THIS IS THE PLACE
        # ------------------------------------
        tick_record["plans_generated"] = len(plan_queue)
        tick_record["plans_routed"] = len(final_plans)

        self._persist_tick(tick_record)
        self._print_tick(tick_record)

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



    # ======================================================================
    # ROUTING — unchanged
    # ======================================================================
    def _route(self, plan, ctx):
        engine = plan.get("engine")
        if not engine:
            print(f"[BUS][DROP] missing engine → {plan}")
            return

        from engines.live.bank_state import get_engine_pot, get_engine_available

        plan["budget"] = get_engine_pot(engine)

        try:
            dyn = compute_dynamic_stake(ctx, engine)
            plan["size"] = float(dyn)
        except Exception as e:
            print(f"[BUS][DYN-STAKE-ERR] {engine}: {e}")
            return

        if not plan.get("px"):
            px = plan.get("px") or ctx.get("px") or ctx.get("ltp") or ctx.get("odds")
            try:
                plan["px"] = float(px)
            except Exception:
                print(f"[BUS][VIOLATION] missing px → {plan}")
                return

        if not plan.get("size"):
            print(f"[BUS][VIOLATION] missing size → {plan}")
            return


        try:
            avail = get_engine_available(engine)
            size = float(plan["size"])
            px = float(plan["px"])
            dirn = plan["direction"]

            if dirn == "BACK->LAY":
                liab = size
            else:
                liab = size * max(px - 1.0, 0.0)

            req = liab * 2


        except Exception as e:
            print(f"[BUS][LIAB-ERR] {e}")
            return

        try:
            place_from_bus(plan, ctx)
        except Exception as e:
            print(f"[BUS][ERR] router failed mid={plan.get('marketId')} sid={plan.get('selectionId')}: {e}")
            return

        print(f"[BUS][ROUTE] {engine:<15} mid={plan.get('marketId')} "
              f"sid={plan.get('selectionId')} dir={plan.get('direction')} "
              f"px={plan.get('px')} size={plan.get('size')}")


# Global BUS instance
BUS = DecisionBus()
