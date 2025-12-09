# === PATCH START ============================================================
# 📍 TARGET: engines/bus/bus.py
# 📆 PATCHED: 2026-02-13 — BUS FINAL EXECUTION LOOP
# ============================================================================

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
        self.expl = ExploratoryEngine()
        self.inplay = InPlayEngine()
        self.risk = RiskEngine()

# === PATCH START ============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: class DecisionBus:
# 📆 PATCHED: 2026-02-14 — BUS Health Report v1
# ============================================================================

    def analytics_report(self):
        """Print text analytics every 10 ticks."""

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


    # ----------------------------------------------------------------------
    # BUS TICK  (Fully upgraded OC-integrated version)
    # ----------------------------------------------------------------------
# === PATCH START ============================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 SEARCH: def tick(self):
# ⛏️ ACTION: full function replacement
# 📆 PATCHED: 2026-02-18 — BUS v12 (Full MSC/Legacy unification, OddService-backed)
# ============================================================================

    def tick(self):

        self.tick_id += 1

        # ----------------------------------------------------------------------
        # 0) BUILD SCOPE (canonical market order)
        # ----------------------------------------------------------------------
        from engines.decision_engine.decide_once.scope import build_and_maintain_scope
        try:
            scope = build_and_maintain_scope(inplay_window_min=15) or {}
        except Exception:
            scope = {}

        mids = scope.get("markets", [])
        if not mids:
            return

        # Flatten mkt list → list[str]
        mids = [str(m) for m in mids]

        # ----------------------------------------------------------------------
        # 1) FETCH RUNNERS (via MarketMonitor — canonical runner source)
        # ----------------------------------------------------------------------
        from engines.market_monitor.monitor import get_market_state

        runner_map = {}
        for mid in mids:
            st = get_market_state(mid) or {}
            runners = (st.get("runners") or {}).keys()
            runner_map[mid] = list(runners)


        # ----------------------------------------------------------------------
        # 2) BUILD BASE CTX
        # ----------------------------------------------------------------------
        base_ctx, _meta = build_context(source="LIVE")

        # ----------------------------------------------------------------------
        # 3) COLLECT ALL PLANS EVERY TICK (DEMAND FROM ALL ENGINES)
        # ----------------------------------------------------------------------
        # queue = list[("ENGINE_NAME", plan_dict, ctx_dict)]
        plan_queue = []

        from engines.market_monitor.monitor import get_market_state
        from engines.micro_scalper_v7.direction_engine import compute_msc_decision

        for mid in mids:

            sids = runner_map.get(mid, [])
            if not sids:
                continue

            for sid in sids:

                # ---------------------------
                # Build per-runner CTX
                # ---------------------------
                ctx = dict(base_ctx)
                ctx["marketId"] = mid
                ctx["selectionId"] = sid

                # Attach latest runner odds + band + fav
                st = get_market_state(mid) or {}
                rn = (st.get("runners") or {}).get(sid) or {}
                ctx["ltp"] = rn.get("px")
                ctx["odds"] = rn.get("px")
                ctx["band"] = rn.get("band")
                ctx["is_fav"] = rn.get("is_fav", False)

                # ------------------------------------------------------------------
                # Compute OC-phase → minutes_to_off (snap) for compatibility
                # ------------------------------------------------------------------
                oc_phase = int(ctx.get("oc_phase") or 0)
                if oc_phase <= 0:
                    ctx["minutes_to_off"] = 120
                elif oc_phase < 7:
                    # OC 1–6: linearly map to {80,60,40,20,10,5}
                    mto_map = {1: 80, 2: 60, 3: 40, 4: 20, 5: 10, 6: 5}
                    ctx["minutes_to_off"] = mto_map.get(oc_phase, 20)
                else:
                    # OC7+ → negative: -1, -2, … capped to -50
                    offset = min(-(oc_phase - 7), -50)
                    ctx["minutes_to_off"] = offset

                # ================================================================
                # DEMAND PLANS FROM ALL MSC ENGINES
                # ================================================================
                # 3A) MSC Exploratory
                try:
                    p = self.expl.tick(ctx)
                    if p and p.get("enter"):
                        p["engine"] = "MSC_EXPLORATORY"
                        plan_queue.append(("MSC_EXPLORATORY", p, ctx))
                except Exception:
                    pass

                # 3B) MSC InPlay
                try:
                    p = self.inplay.tick(ctx)
                    if p and p.get("enter"):
                        p["engine"] = "MSC_INPLAY"
                        plan_queue.append(("MSC_INPLAY", p, ctx))
                except Exception:
                    pass

                # 3C) MSC Risk
                try:
                    p = self.risk.tick(ctx)
                    if p and p.get("enter"):
                        p["engine"] = "MSC_RISK"
                        plan_queue.append(("MSC_RISK", p, ctx))
                except Exception:
                    pass

                # ================================================================
                # DEMAND STOPLOSS PLAN
                # ================================================================
                try:
                    slp = pull_stoploss_for(mid, sid)
                    if slp and slp.get("enter"):
                        slp["engine"] = "OVERWATCHER"
                        plan_queue.append(("OVERWATCHER", slp, ctx))
                except Exception:
                    pass

                # ================================================================
                # DEMAND LEGACY STRATEGY PLANS
                # ================================================================
                try:
                    from engines.decision_engine.strategies.registry import ORDER as STRATEGY_ORDER
                except Exception:
                    STRATEGY_ORDER = []

                for strat_name, strat_fn in STRATEGY_ORDER:
                    if strat_name.upper() in ("ALWAYS_ON", "MLM", "L"):
                        continue
                    try:
                        lp = plan_for_strategy(strat_name, ctx)
                        if lp and lp.get("enter"):
                            lp["engine"] = "LEGACY"
                            lp["strategy"] = strat_name
                            plan_queue.append(("LEGACY", lp, ctx))
                    except Exception:
                        pass

        # ----------------------------------------------------------------------
        # 4) ENRICH PLANS (MINUTES_TO_OFF, DIRECTION, TICK SNAPPING)
        # ----------------------------------------------------------------------
        final_plans = []

        # ❌ REMOVE this invalid block:
        # if "entry_ticks" in plan and "target_ticks" not in plan:
        #     plan["target_ticks"] = plan["entry_ticks"]

        for eng, p, ctx in plan_queue:
            try:
                # ------------------------------------------------------------------
                # Mirror entry_ticks → target_ticks only if target missing (compat)
                # ------------------------------------------------------------------
                if "entry_ticks" in p and "target_ticks" not in p:
                    p["target_ticks"] = p["entry_ticks"]

                # Minutes-to-off already filled → copy into plan
                p["minutes_to_off"] = ctx.get("minutes_to_off")


                # --------------------------------------------------------------
                # Direction enforcement for MSC + Legacy
                # --------------------------------------------------------------
                try:
                    dec = compute_msc_decision(ctx)
                    msc_dir = dec.get("direction")
                except Exception:
                    msc_dir = None

                if eng != "OVERWATCHER" and eng != "MSC_RISK":
                    # If plan has direction and disagrees with MSC → DROP
                    if p.get("direction") and msc_dir and p["direction"] != msc_dir:
                        continue
                    # If plan missing direction → assign MSC direction
                    if not p.get("direction") and msc_dir:
                        p["direction"] = msc_dir

                # --------------------------------------------------------------
                # Snap ticks
                # --------------------------------------------------------------
                if eng == "MSC_EXPLORATORY":
                    et = int(p.get("entry_ticks") or 2)
                    p["entry_ticks"] = max(1, min(et, 3))

                elif eng == "MSC_RISK":
                    p["entry_ticks"] = 1  # always

                elif eng == "LEGACY":
                    tt = int(p.get("target_ticks") or 4)
                    if tt < 4:
                        tt = 4
                    if tt > 6:
                        tt = 6
                    p["target_ticks"] = tt

                # MSC InPlay untouched

                # --------------------------------------------------------------
                # Prepare for route
                # --------------------------------------------------------------
                final_plans.append((eng, p, ctx))

            except Exception:
                continue

        # ----------------------------------------------------------------------
        # 5) ROUTE ALL VALID PLANS (budget and liability gating inside _route)
        # ----------------------------------------------------------------------
        for eng, p, ctx in final_plans:
            self._route(p, ctx)

        # ----------------------------------------------------------------------
        # 6) Diagnostics (unchanged)
        # ----------------------------------------------------------------------        # ==========================================================================
        # BUS TICK DIAGNOSTICS — unchanged
        # ==========================================================================
        diag = {
            "engines": {},
            "parents_opened": 0,
            "children_opened": 0,
            "children_matched": 0,
            "good": 0,
            "bad": 0,
            "blocks": [],
            "gates": [],
            "missing": [],
        }

        try:
            import sqlite3
            con = _bank_conn(rw=False); con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT id, role, entry_status, exit_status, engine, hedge_of, source
                FROM orders
                WHERE date(opened_at)=date('now')
            """).fetchall()
            con.close()
        except Exception:
            rows = []

        for r in rows:
            role = (r["role"] or "").upper()
            if role == "PARENT":
                diag["parents_opened"] += 1
            elif role == "CHILD":
                diag["children_opened"] += 1

            if role == "CHILD" and str(r["entry_status"]).upper() == "MATCHED":
                diag["children_matched"] += 1

            if role == "CHILD" and r["hedge_of"]:
                letter = (r["source"] or "").upper()
                if letter == "H":
                    diag["good"] += 1
                elif letter == "S":
                    diag["bad"] += 1

        for eng, n in getattr(self, "_tick_engine_fires", {}).items():
            diag["engines"][eng] = n

        for blk in getattr(self, "_tick_block_list", []):
            diag["blocks"].append(blk)

        for g in getattr(self, "_tick_gate_list", []):
            diag["gates"].append(g)

        for m in getattr(self, "_tick_missing_ctx", []):
            diag["missing"].append(m)

        self._tick_engine_fires = {}
        self._tick_block_list = []
        self._tick_gate_list = []
        self._tick_missing_ctx = []

        print("────────────────────────────────────────────────────────")
        print(f"[BUS][TICK] #{self.tick_id}")

        if diag["engines"]:
            print("Engines Fired:")
            for eng, n in diag["engines"].items():
                print(f"  • {eng:<15} {n}")
        else:
            print("Engines Fired: none")

        print(f"\nParents opened:   {diag['parents_opened']}")
        print(f"Children opened:  {diag['children_opened']}")
        print(f"Children matched: {diag['children_matched']}")

        print(f"\nGood trades: {diag['good']}")
        print(f"Bad trades:  {diag['bad']}")

        print("\nBlocks:")
        if diag["blocks"]:
            for b in diag["blocks"]:
                print(f"  - {b}")
        else:
            print("  none")

        print("\nGates:")
        if diag["gates"]:
            for g in diag["gates"]:
                print(f"  - {g}")
        else:
            print("  none")

        print("\nMissing CTX Fields:")
        if diag["missing"]:
            for m in diag["missing"]:
                print(f"  - {m}")
        else:
            print("  none")

        print("────────────────────────────────────────────────────────")

        if self.tick_id % 10 == 0:
            try:
                self.analytics_report()
            except Exception:
                pass

    # ----------------------------------------------------------------------
    # ROUTING ENGINE
    # ----------------------------------------------------------------------

    def _route(self, plan, ctx):
        """Direct pass-through to LiveRouter"""
        engine = plan.get("engine")
        if not engine:
            print(f"[BUS][DROP] missing engine → {plan}")
            return

        # -----------------------------------------------------
        #  TICK DIAGNOSTIC TRACKING (v1)
        # -----------------------------------------------------
        # Initialise per-tick tracking dicts on first call this tick.
        if not hasattr(self, "_tick_engine_fires"):
            self._tick_engine_fires = {}
        if not hasattr(self, "_tick_block_list"):
            self._tick_block_list = []
        if not hasattr(self, "_tick_gate_list"):
            self._tick_gate_list = []
        if not hasattr(self, "_tick_missing_ctx"):
            self._tick_missing_ctx = []

        # Engine fire count (attempt, before validation)
        self._tick_engine_fires[engine] = self._tick_engine_fires.get(engine, 0) + 1

        # Attach budget
        # === PATCH START ============================================================
        # 📍 TARGET: engines/bus/bus.py
        # 🔎 SEARCH: plan["budget"] = get_engine_budget(engine)
        # 📆 PATCHED: 2026-02-14 — correct BankState integration
        # ============================================================================

        from engines.live.bank_state import (
            get_engine_pot,
            get_engine_available,
        )

        # Replace previous line:
        plan["budget"] = get_engine_pot(engine)

        # === PATCH END ==============================================================
        # 2) Dynamic Stake v7 — true sizing
        try:
            dyn = compute_dynamic_stake(ctx, engine)
            plan["size"] = float(dyn)
        except Exception as e:
            print(f"[BUS][DYN-STAKE-ERR] {engine}: {e}")

        # BUS invariant checks
        if plan.get("engine") is None:
            self._tick_missing_ctx.append("missing-engine")
            print(f"[BUS][VIOLATION] missing engine: {plan}")
            return

        if plan.get("px") in (None, 0):
            self._tick_missing_ctx.append(f"{engine}:missing-px")
            print(f"[BUS][VIOLATION] missing px: {plan}")
            return

        if plan.get("size") in (None, 0):
            self._tick_missing_ctx.append(f"{engine}:missing-size")
            print(f"[BUS][VIOLATION] missing size: {plan}")
            return

        if not plan.get("direction"):
            self._tick_missing_ctx.append(f"{engine}:missing-direction")
            print(f"[BUS][VIOLATION] missing direction: {plan}")
            return

        # --------------------------------------------------------------
        # LIABILITY + BUDGET GATING (parent + child worst-case check)
        # --------------------------------------------------------------
        try:
            from engines.live.bank_state import get_engine_available
            from datetime import datetime
            avail = get_engine_available(engine)

            size = float(plan.get("size") or 0.0)
            px   = float(plan.get("px")   or 1.01)   # ensure px >=1.01 to avoid zero-liability errors
            dirn = plan.get("direction") or "BACK->LAY"

            # Parent liability
            if dirn == "BACK->LAY":
                liab_parent = size
            else:
                liab_parent = size * max(0.0, px - 1.0)

            # Child reserve = same as parent liability
            liab_child = liab_parent

            required = liab_parent + liab_child

            if avail < required:
                msg = f"{engine}: insufficient funds req={required:.2f} avail={avail:.2f}"
                self._tick_block_list.append(msg)
                print(f"[BUS][BLOCK] {msg}")

                # ------------------------------------------------------
                # Emit dropped-plan event (not router, not GoalAdapter)
                # ------------------------------------------------------
                try:
                    from engines.mastery.event_sink import emit as mastery_emit
                    mastery_emit("plan_dropped", {
                        "ts": datetime.utcnow().isoformat(),
                        "engine": engine,
                        "marketId": plan.get("marketId"),
                        "selectionId": plan.get("selectionId"),
                        "reason": msg,
                        "plan": plan,
                    })
                except Exception as e:
                    print(f"[BUS][EVENTSYNC-DROP-WARN] {e}")

                return

        except Exception as e:
            print(f"[BUS][LIAB-ERR] {e}")
            return

        # --------------------------------------------------------------
        # ROUTE TO LIVEROUTER (SUCCESS PATH)
        # --------------------------------------------------------------
        try:
            place_from_bus(plan, ctx)

            # ------------------------------------------------------
            # Emit routed-plan event to EventSync
            # ------------------------------------------------------
            try:
                from engines.mastery.event_sink import emit as mastery_emit
                from datetime import datetime
                mastery_emit("plan_routed", {
                    "ts": datetime.utcnow().isoformat(),
                    "engine": engine,
                    "marketId": plan.get("marketId"),
                    "selectionId": plan.get("selectionId"),
                    "plan": plan,
                })
            except Exception as e:
                print(f"[BUS][EVENTSYNC-ROUTE-WARN] {e}")

        except Exception as e:
            print(f"[BUS][ERR] router failed mid={plan.get('marketId')} "
                  f"sid={plan.get('selectionId')}: {e}")
            return

        # --------------------------------------------------------------
        # ENGINE TRACE LOG
        # --------------------------------------------------------------
        print(f"[BUS][ROUTE] {plan['engine']:<15} mid={plan.get('marketId')} "
              f"sid={plan.get('selectionId')} dir={plan.get('direction')} "
              f"px={plan.get('px')} size={plan.get('size')}")




# Global BUS instance
BUS = DecisionBus()

# === PATCH END ================================================================
