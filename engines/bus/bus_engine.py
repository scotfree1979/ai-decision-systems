# ======================================================================
# engines/bus/bus_engine.py
# Unified Tick Bus v1.0
# ======================================================================

from __future__ import annotations
import time
from typing import Optional, Dict, Any, List
from engines.live.live_router import place_parent_and_hedge
from engines.live.live_router import _orders_insert_parent_queued
from engines.mastery import event_sink
from engines.live import bank_state

# ENGINE → POT mapping (canonical)
ENGINE_POT = {
    "MSC_EXPLORATORY": "MSC_EXPLORATORY",
    "MSC_RISK":        "MSC_RISK",
    "MSC_INPLAY":      "MSC_INPLAY",
    "LEGACY":          "LEGACY",
    "OVERWATCHER":     "OVERWATCHER",
}

# RISK Switches
RISK_ACTIVE_FOR = {
    "LEGACY": True,
    "MSC_EXPLORATORY": False,
    "MSC_RISK": False,
    "MSC_INPLAY": False,
}

# STOPLOSS direct channel (populated by Overwatcher)
STOPLOSS_EVENTS: List[dict] = []


class TickBus:

    def __init__(self):
        # Queues from engines
        self.msc_queue: List[dict] = []
        self.legacy_queue: List[dict] = []
        self.inplay_queue: List[dict] = []
        self.risk_queue: List[dict] = []

    # ==============================================================
    # ADD PLAN SOURCES (called externally)
    # ==============================================================

    def push_msc_exploratory(self, plan: dict):
        self._tag(plan, "MSC_EXPLORATORY")
        self.msc_queue.append(plan)

    def push_msc_risk(self, plan: dict):
        self._tag(plan, "MSC_RISK")
        self.risk_queue.append(plan)

    def push_msc_inplay(self, plan: dict):
        self._tag(plan, "MSC_INPLAY")
        self.inplay_queue.append(plan)

    def push_legacy(self, plan: dict):
        self._tag(plan, "LEGACY")
        self.legacy_queue.append(plan)

    def push_stoploss(self, ev: dict):
        ev["engine"] = "OVERWATCHER"
        STOPLOSS_EVENTS.append(ev)

    # ==============================================================
    # INTERNAL TAGGING
    # ==============================================================

    def _tag(self, plan: dict, engine: str):
        plan["engine"] = engine
        plan["pot"] = ENGINE_POT[engine]

    # ==============================================================
    # MAIN ENTRY — called once per runner per tick
    # ==============================================================

    def tick(self, ctx: dict) -> Optional[int]:
        """
        ctx contains:
            marketId, selectionId, oc_phase, odds, size
        """

        # PRIORITY 1 → STOPLOSS
        if STOPLOSS_EVENTS:
            ev = STOPLOSS_EVENTS.pop(0)
            return self._route_stoploss(ev, ctx)

        # PRIORITY 2 → RISK (legacy-only)
        if self.risk_queue:
            p = self.risk_queue.pop(0)
            engine = p["engine"]
            if RISK_ACTIVE_FOR.get(engine, False):
                return self._route(p, ctx)

        # PRIORITY 3 → INPLAY MSC
        if self.inplay_queue:
            return self._route(self.inplay_queue.pop(0), ctx)

        # PRIORITY 4 → EXPLORATORY MSC
        if self.msc_queue:
            return self._route(self.msc_queue.pop(0), ctx)

        # PRIORITY 5 → LEGACY (single output per runner)
        if self.legacy_queue:
            return self._route(self.legacy_queue.pop(0), ctx)

        return None

    # ==============================================================
    # ROUTING
    # ==============================================================

    def _route_stoploss(self, ev: dict, ctx: dict) -> Optional[int]:
        """
        STOPLOSS child placement bypasses all engines.
        """
        mid = str(ev["marketId"])
        sid = str(ev["selectionId"])

        direction = "BACK" if ev["entry_side"] == "LAY" else "LAY"

        plan = {
            "engine": "OVERWATCHER",
            "pot": "OVERWATCHER",
            "enter": True,
            "direction": direction,
            "px": float(ev["current_odds"]),
            "size": float(ev["entry_stake"]),
            "marketId": mid,
            "selectionId": sid,
            "customerOrderRef": f"W-{int(time.time())}"
        }

        return self._direct_router(plan, ctx)

    def _route(self, plan: dict, ctx: dict) -> Optional[int]:
        """
        Generic routing for MSC + Legacy.
        """
        if not plan or not plan.get("enter"):
            return None

        mid = str(plan.get("marketId") or ctx["marketId"])
        sid = str(plan.get("selectionId") or ctx["selectionId"])

        plan["marketId"] = mid
        plan["selectionId"] = sid

        return self._direct_router(plan, ctx)

    # ==============================================================
    # DIRECT ROUTER PATH (placement.py removed)
    # ==============================================================

    def _direct_router(self, plan: dict, ctx: dict) -> Optional[int]:
        """
        This replaces placement.place_from_plan()
        → Bus sends directly into LiveRouter.
        """

        from engines.live.live_router import place_parent_and_hedge

        cor = plan.get("customerOrderRef")
        if not cor:
            cor = f"{plan['engine'][0]}-{int(time.time())}"
            plan["customerOrderRef"] = cor

        # Preclaim parent
        parent_id = _orders_insert_parent_queued(
            run_id=ctx.get("run_id"),
            market_id=plan["marketId"],
            selection_id=plan["selectionId"],
            side="LAY" if plan["direction"].startswith("LAY") else "BACK",
            entry_odds=plan["px"],
            entry_stake=plan["size"],
            cor=cor,
            source=plan["engine"]
        )

        # Send to router
        result = place_parent_and_hedge(
            _name=plan["engine"],
            _plan=plan,
            _ctx=ctx
        )

        # Mastery learns passively
        try:
            event_sink.emit("bus_plan_fired", {
                "engine": plan["engine"],
                "marketId": plan["marketId"],
                "selectionId": plan["selectionId"],
                "direction": plan["direction"],
                "px": plan["px"],
                "size": plan["size"]
            })
        except Exception:
            pass

        return result
