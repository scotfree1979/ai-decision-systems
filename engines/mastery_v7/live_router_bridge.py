#!/usr/bin/env python3
"""
v7 Live Router Bridge
Forces direct execution of Mastery-generated intents through the existing
live_router.place_parent_and_hedge() even if DecideOnce lanes are idle.
"""

from engines.live import live_router
from engines.mastery import event_sink
from datetime import datetime, timezone


def _safe_float(v, d=0.0):
    try: return float(v)
    except: return d

# === PATCH START ===
# 📍 TARGET: engines/mastery_v7/live_router_bridge.py:_force_place
# 📆 PATCHED: 2025-11-10Z — unify bridge path for stoploss/micro/greenup events
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _force_place(event: dict):
    """
    Force-place any actionable event (stoploss, scalp, greenup, etc.)
    through the same brain bridge used by live pulses.
    Ensures all live-layer decisions share one routing logic.
    """
    try:
        plan = {
            "marketId": event.get("marketId"),
            "selectionId": event.get("selectionId"),
            "side": event.get("side") or ("LAY" if event.get("type") in ("stoploss","stop_loss_breached") else "BACK"),
            "odds": event.get("odds_now") or event.get("odds") or 0,
            "stake": event.get("stake") or 2.0,
            "reason": event.get("type"),
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        print(f"[v7-bridge] forcing placement → {plan['reason']} mid={plan['marketId']} sid={plan['selectionId']} odds={plan['odds']} stake={plan['stake']}")
        live_router.place_parent_and_hedge(
            market_id=plan["marketId"],
            selection_id=plan["selectionId"],
            side=plan["side"],
            entry_odds=float(plan["odds"] or 0),
            stake=float(plan["stake"] or 2.0),
            source=str(plan["reason"] or "BRAIN_BRIDGE")
        )

    except Exception as e:
        print(f"[v7-bridge] force_place warn: {e}")
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/mastery_v7/live_router_bridge.py
# 📆 PATCHED: 2025-11-02Z — unify Overwatcher + Mastery bridge to live_router
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import importlib

# === PATCH START ============================================================
# 📍 TARGET: engines/mastery_v7/live_router_bridge.py
# 🔎 SEARCH: in handle_mastery_event(event)
# 📆 PATCHED: 2025-12-02 — restrict events to Overwatcher-only decisions
# ============================================================================
def handle_mastery_event(event):
    t = (event.get("type") or "").lower()

    # --- NEW FILTER: ONLY react to events produced by Overwatcher ---
    if t in (
        "stop_loss_triggered",
        "stop_loss_breached",
        "legacy_boundary_exit",
        "msc_trailing_positive",
        "msc_trailing_negative",
        "market_end_exit",

        "micro_lay",

        "probability_risk_signal",
        "liability_signal",
    ):
        _force_place(event)

    # --- IGNORE ALL OTHER EVENTS (brain_plan, bridge_pulse, etc.) ---
    # This prevents infinite loops and invalid placements.
# === PATCH END ==============================================================


# ensure event_sink live before subscribing
try:
    # always attach to the live mastery.event_sink (cloud-based)
    from engines.mastery import event_sink as es
except ImportError:
    import importlib
    es = importlib.import_module("engines.mastery.event_sink")


if not hasattr(es, "_subscribers") or es._subscribers is None:
    es._subscribers = []
es.subscribe(handle_mastery_event)
print("[v7-bridge] unified event_sink subscriber active ✅")
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery_v7/live_router_bridge.py
# 📆 PATCHED: 2025-11-09Z — expose callable send_plan_through_bridge() for brain pulses
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def send_plan_through_bridge(plan: dict):
    """
    Public entrypoint for Mastery Brain or other modules to
    force execution of a plan through the live_router bridge.
    """
    try:
        # Reuse the same logic that handle_mastery_event uses
        live_router.place_parent_and_hedge(
            market_id=plan.get("marketId"),
            selection_id=plan.get("selectionId"),
            side=plan.get("side"),
            entry_odds=float(plan.get("odds") or 0),
            stake=float(plan.get("stake") or 2.0),
            source=str(plan.get("reason") or "BRAIN_BRIDGE")
        )

    except Exception as e:
        print(f"[v7-bridge] send_plan_through_bridge warn: {e}")
# === PATCH END ===


