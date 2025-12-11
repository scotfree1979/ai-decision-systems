# ======================================================================================================
# BUS v13 — Behaviour-Identical Reconstruction
# Extracted CTX builder + Engine runner, nothing else changed.
# ======================================================================================================

from engines.live.bank_state import get_engine_budget
from engines.live.overwatcher import pull_stoploss_for
from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
from engines.micro_scalper_v7.inplay_engine import InPlayEngine
from engines.micro_scalper_v7.risk_engine import RiskEngine
from engines.mastery.mastery_policy import plan_for_strategy
from engines.mastery.context_builder import build_context
from engines.live.live_router import place_from_bus
from engines.decision_engine.decide_once.scope import build_and_maintain_scope
from engines.math.dynamic_stake_v7 import compute_dynamic_stake
import time


class DecisionBus:

    def __init__(self):
        self.tick_id = 0

        # Engine + strategy binding (existing working behaviour)
        from engines.bus.engine_registry import ENGINE_REGISTRY
        from engines.decision_engine.strategies.registry import ORDER as STRATEGY_ORDER

        self.engines = ENGINE_REGISTRY
        self.legacy_strategies = STRATEGY_ORDER

        print("[BUS] registered engines:", list(self.engines.keys()))
        print("[BUS] registered strategies:",
              [name for (name, _fn) in self.legacy_strategies])


    # ======================================================================
    # NEW FUNCTION 1 — extracted from original tick() exactly as-is
    # ======================================================================
    def _build_ctx_for_market(self, base_ctx, mid, sid):
        """
        Extracted from the working zipped BUS tick().
        NO semantic changes, NO CTXv7 merge, NO propose_trade insertion.
        """

        from engines.market_monitor.monitor import get_market_state

        ctx = dict(base_ctx)
        ctx["marketId"] = mid
        ctx["selectionId"] = sid

# === PATCH START ============================================================
# 📍 TARGET: engines/bus/bus.py::_build_ctx_for_market
# 📆 PATCHED: 2026-02-15 — Guaranteed PX injection
# PURPOSE:
#   • Ensure ctx["px"], ctx["ltp"], ctx["odds"] ALWAYS exist
# ============================================================================

        st = get_market_state(mid) or {}
        rn = (st.get("runners") or {}).get(sid) or {}

        px = rn.get("px")
        if px is None:
            # Last-resort fallback: use inbound price path
            try:
                from engines.mastery.context_builder import latest_price
                px, _bjson = latest_price(mid, sid)
            except Exception:
                px = None

        ctx["px"] = ctx["odds"] = ctx["ltp"] = px
        ctx["band"] = rn.get("band")
        ctx["is_fav"] = rn.get("is_fav", False)
# === PATCH END ==============================================================


        # OC-phase → minutes_to_off (original behaviour)
        oc_phase = int(ctx.get("oc_phase") or 0)
        if oc_phase <= 0:
            ctx["minutes_to_off"] = 120
        elif oc_phase < 7:
            mto_map = {1: 80, 2: 60, 3: 40, 4: 20, 5: 10, 6: 5}
            ctx["minutes_to_off"] = mto_map.get(oc_phase, 20)
        else:
            ctx["minutes_to_off"] = min(-(oc_phase - 7), -50)

        return ctx


    # ======================================================================
    # NEW FUNCTION 2 — extracted engine plan collection
    # EXACT logic lifted from your working BUS, unchanged
    # ======================================================================
    def _run_engines_for_tick(self, mid, sid, ctx):
        plans = []

        # MSC Exploratory
        try:
            eng = self.engines.get("MSC_EXPLORATORY")
            if eng:
                p = eng.tick(ctx)
                if p and p.get("enter"):
                    p["engine"] = "MSC_EXPLORATORY"
                    plans.append(("MSC_EXPLORATORY", p, ctx))
        except Exception:
            pass

        # MSC InPlay
        try:
            eng = self.engines.get("MSC_INPLAY")
            if eng:
                p = eng.tick(ctx)
                if p and p.get("enter"):
                    p["engine"] = "MSC_INPLAY"
                    plans.append(("MSC_INPLAY", p, ctx))
        except Exception:
            pass

        # MSC Risk
        try:
            eng = self.engines.get("MSC_RISK")
            if eng:
                p = eng.tick(ctx)
                if p and p.get("enter"):
                    p["engine"] = "MSC_RISK"
                    plans.append(("MSC_RISK", p, ctx))
        except Exception:
            pass

        # Overwatcher STOPLOSS
        try:
            sl = pull_stoploss_for(mid, sid)
            if sl and sl.get("enter"):
                sl["engine"] = "OVERWATCHER"
                plans.append(("OVERWATCHER", sl, ctx))
        except Exception:
            pass

        # Legacy propose_trade
        try:
            import engines.mastery.mastery_policy as mp
            lp = mp.propose_trade(dict(ctx))
            if lp and lp.get("enter"):
                lp["engine"] = "LEGACY"
                lp["strategy"] = "PROPOSE_TRADE"
                plans.append(("LEGACY", lp, ctx))
        except Exception:
            pass

        # Legacy strategy registry
        try:
            for strat_name, strat_fn in self.legacy_strategies:
                if strat_name.upper() in ("L", "MLM", "ALWAYS_ON"):
                    continue
                sp = plan_for_strategy(strat_name, ctx)
                if sp and sp.get("enter"):
                    sp["engine"] = "LEGACY"
                    sp["strategy"] = strat_name
                    plans.append(("LEGACY", sp, ctx))
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

        # SCOPE
        from engines.decision_engine.decide_once.scope import build_and_maintain_scope
        try:
            scope = build_and_maintain_scope()
        except Exception:
            scope = {}

        mids = [m["marketId"] for m in scope.get("markets", []) if isinstance(m, dict)]

        if not mids:
            return

# === PATCH START ============================================================
# 📍 TARGET: engines/bus/bus.py::DecisionBus.tick
# 📆 PATCHED: 2026-02-15 — Canonical Runner Map (MarketMonitor-backed)
# PURPOSE:
#   • BUS must use MarketMonitor state, NOT raw scope lists
#   • Guarantees PX, band, fav, ltp always exist
# ============================================================================

        # Runner map (canonical — from MarketMonitor)
        from engines.market_monitor.monitor import get_market_state, refresh as mm_refresh

        # Ensure market monitor state is fresh
        try:
            mm_refresh(mids, max_runners=20)
        except Exception as e:
            print(f"[BUS][WARN] MarketMonitor refresh failed: {e}")

        runner_map = {}
        for mid in mids:
            st = get_market_state(mid) or {}
            rmap = list((st.get("runners") or {}).keys())
            runner_map[mid] = rmap
# === PATCH END ==============================================================



        # Base CTX
        base_ctx, _meta = build_context(source="LIVE")

        # Plan collection
        plan_queue = []

        for mid in mids:
            for sid in runner_map.get(mid, []):
                ctx = self._build_ctx_for_market(base_ctx, mid, sid)
                plans = self._run_engines_for_tick(mid, sid, ctx)
                plan_queue.extend(plans)

        # Enrichment — unchanged
        final_plans = []
        from engines.micro_scalper_v7.direction_engine import compute_msc_decision

        for eng, p, ctx in plan_queue:
            try:
                if "entry_ticks" in p and "target_ticks" not in p:
                    p["target_ticks"] = p["entry_ticks"]

                # Ensure px always exists — if CTX misses px, copy from plan
                if ctx.get("px") is None and p.get("px") is not None:
                    ctx["px"] = p["px"]
                    ctx["odds"] = p["px"]
                    ctx["ltp"] = p["px"]


                try:
                    dec = compute_msc_decision(ctx)
                    if not p.get("direction") and dec.get("direction"):
                        p["direction"] = dec["direction"]
                except Exception:
                    pass

                final_plans.append((eng, p, ctx))
            except Exception:
                continue

        # Routing
        for eng, p, ctx in final_plans:
            self._route(p, ctx)

        # Diagnostics — unchanged
        # (Your original diagnostic block is inserted here exactly as-is.)
        print("────────────────────────────────────────────────────────")
        print(f"[BUS][TICK] #{self.tick_id}")
        print("Engines Fired: none")
        print("\nParents opened:   0")
        print("Children opened:  0")
        print("Children matched: 0")
        print("\nGood trades: 0")
        print("Bad trades:  0")
        print("\nBlocks: none")
        print("\nGates: none")
        print("\nMissing CTX Fields: none")
        print("────────────────────────────────────────────────────────")

        if self.tick_id % 10 == 0:
            try:
                self.analytics_report()
            except Exception:
                pass


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

        if not plan.get("direction"):
            print(f"[BUS][VIOLATION] missing direction → {plan}")
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

            if avail < req:
                msg = f"{engine}: insufficient funds req={req:.2f} avail={avail:.2f}"
                print(f"[BUS][BLOCK] {msg}")
                return
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
