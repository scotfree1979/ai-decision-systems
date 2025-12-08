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
from engines.decision_engine.decide_once.scope import read_scope_window
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

# === PATCH END ================================================================


    # ----------------------------------------------------------------------
    # BUS TICK
    # ----------------------------------------------------------------------
    def tick(self):

        self.tick_id += 1

        scope = read_scope_window(ahead_min=90)
        markets = scope.get("markets", [])
        if not markets:
            return

        # Build base context once per tick
        base_ctx, _ = build_context(source="LIVE")  # CTXv7

        for mid in markets:
            mids = str(mid)
            sids = scope["active_sids"].get(mids, [])
            for sid in sids:
                sid = str(sid)

                # --- Build runner CTX --------------------------------------------------
                ctx = dict(base_ctx)
                ctx["marketId"] = mids
                ctx["selectionId"] = sid

                # ==========================================================================
                # 1) STOPLOSS FIRST (highest priority)
                # ==========================================================================
                sl_plan = pull_stoploss_for(mids, sid)
                if sl_plan:
                    sl_plan["engine"] = "OVERWATCHER"
                    sl_plan["marketId"] = mids
                    sl_plan["selectionId"] = sid
                    self._route(sl_plan, ctx)
                    continue

                # ==========================================================================
                # 2) IN-PLAY ENGINE
                # ==========================================================================
                if ctx.get("oc_phase", 0) >= 7:
                    plan = self.inplay.tick(ctx)
                    if plan and plan.get("enter"):
                        plan["engine"] = "MSC_INPLAY"
                        self._route(plan, ctx)
                    continue

                # ==========================================================================
                # 3) EXPLORATORY ENGINE — must inherit legacy plan letters
                # ==========================================================================
                expl_plan = self.expl.tick(ctx)
                if expl_plan and expl_plan.get("enter"):
                    # Exploratory uses the legacy family letter
                    expl_plan["engine"] = "MSC_EXPLORATORY"
                    self._route(expl_plan, ctx)
                    continue

                # ==========================================================================
                # 4) LEGACY — direction-first
                # ==========================================================================
                # Ask MSC for direction
                msc_dir = self.expl.direction_only(ctx)
                if msc_dir:
                    ctx["direction"] = msc_dir

                leg_plan = plan_for_strategy("OG_STRATEGY", ctx)
                if leg_plan and leg_plan.get("enter"):
                    leg_plan["engine"] = "LEGACY"
                    self._route(leg_plan, ctx)

                # ==========================================================================
                # 5) RISK ENGINE (LEGACY ONLY)
                # ==========================================================================
                if leg_plan and leg_plan.get("enter"):
                    ctx["parent_engine"] = "LEGACY"
                    risk_plan = self.risk.tick(ctx)
                    if risk_plan and risk_plan.get("enter"):
                        risk_plan["engine"] = "MSC_RISK"
                        self._route(risk_plan, ctx)

        # --- Optional analytics output -----------------------------------------
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

        # Attach budget
        plan["budget"] = get_engine_budget(engine)

# === PATCH START ============================================================
# 📍 TARGET: DecisionBus._route
# 📆 PATCHED: 2026-02-14 — BUS Invariant Guard v1
# ============================================================================

        # BUS invariant checks
        if plan.get("engine") is None:
            print(f"[BUS][VIOLATION] missing engine: {plan}")
            return
        if plan.get("px") in (None, 0):
            print(f"[BUS][VIOLATION] missing px: {plan}")
            return
        if plan.get("size") in (None, 0):
            print(f"[BUS][VIOLATION] missing size: {plan}")
            return
        if not plan.get("direction"):
            print(f"[BUS][VIOLATION] missing direction: {plan}")
            return

# === PATCH END ================================================================

# === PATCH START ============================================================
# 📍 TARGET: DecisionBus._route
# 🔎 BEFORE router call
# ============================================================================

        pot = plan.get("budget", 0)
        if plan["size"] > pot:
            print(f"[BUS][BLOCK] {plan['engine']} ran out of pot. size={plan['size']} pot={pot}")
            return

# === PATCH END ================================================================

        try:
            place_from_bus(plan, ctx)
        except Exception as e:
            print(f"[BUS][ERR] router failed mid={plan.get('marketId')} sid={plan.get('selectionId')}: {e}")
# === PATCH START ============================================================
# 📍 TARGET: DecisionBus._route
# 📆 PATCHED: 2026-02-14 — engine trace logging
# ============================================================================

        print(f"[BUS][ROUTE] {plan['engine']:<15} mid={plan.get('marketId')} "
              f"sid={plan.get('selectionId')} dir={plan.get('direction')} "
              f"px={plan.get('px')} size={plan.get('size')}")
# === PATCH END ================================================================


# Global BUS instance
BUS = DecisionBus()

# === PATCH END ================================================================
