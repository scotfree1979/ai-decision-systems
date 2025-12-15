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

# === PATCH START ============================================================
# 📍 TARGET: engines/mastery_v7/live_router_bridge.py
# 🔎 SEARCH: def _force_place(event: dict):
# 📆 PATCHED: 2025-12-12 — hard safety filter for live routing
# PURPOSE:
#   • Prevent telemetry from reaching router
#   • Prevent sid=None crashes
#   • Keep BUS ticking under all conditions
# ============================================================================

def _force_place(event: dict):
    """
    Force-place ONLY executable trade events through live_router.
    Telemetry events are explicitly ignored.
    """

    if not isinstance(event, dict):
        return

    etype = (event.get("type") or "").lower()

    # --- BLOCK telemetry / non-trade events -------------------------------
    if etype in (
        "liability_signal",
        "probability_risk_signal",
        "market_risk",
        "cashout_tick",
        "loss_cut_signal",
    ):
        return

    mid = event.get("marketId")
    sid = event.get("selectionId")

    # --- HARD REQUIRE identifiers -----------------------------------------
    if not mid or not sid:
        return

    odds = event.get("odds_now") or event.get("odds")
    stake = event.get("stake")

    if not odds or not stake:
        return

    side = event.get("side")
    if not side:
        return

    try:
        live_router.place_parent_and_hedge(
            market_id=mid,
            selection_id=sid,
            side=side,
            entry_odds=float(odds),
            stake=float(stake),
            source=str(event.get("type") or "V7_BRIDGE"),
        )
    except Exception:
        # swallow — NEVER kill the bridge thread
        return

# === PATCH END ==============================================================

# === PATCH START ============================================================
# 📍 TARGET: engines/mastery_v7/live_router_bridge.py
# 🔎 SEARCH: def handle_mastery_event(event):
# 📆 PATCHED: 2025-12-12 — restrict bridge to executable trade events
# ============================================================================

def handle_mastery_event(event):
    """
    Route ONLY executable Overwatcher trade decisions.
    All telemetry events are ignored.
    """

    t = (event.get("type") or "").lower()

    # --- ONLY real trade actions ------------------------------------------
    if t in (
        "stop_loss_triggered",
        "stop_loss_breached",
        "legacy_boundary_exit",
        "msc_trailing_positive",
        "msc_trailing_negative",
        "market_end_exit",
        "micro_lay",
    ):
        _force_place(event)

    # Everything else is telemetry → ignore

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

# === PATCH START ============================================================
# 📍 TARGET: engines/mastery_v7/live_router_bridge.py
# 🔎 SEARCH: def send_plan_through_bridge(plan: dict):
# 📆 PATCHED: 2025-12-12 — guard invalid plans
# ============================================================================

def send_plan_through_bridge(plan: dict):
    if not isinstance(plan, dict):
        return

    if not plan.get("marketId") or not plan.get("selectionId"):
        return

    if not plan.get("side") or not plan.get("odds") or not plan.get("stake"):
        return

    try:
        live_router.place_parent_and_hedge(
            market_id=plan.get("marketId"),
            selection_id=plan.get("selectionId"),
            side=plan.get("side"),
            entry_odds=float(plan.get("odds")),
            stake=float(plan.get("stake")),
            source=str(plan.get("reason") or "BRAIN_BRIDGE"),
        )
    except Exception:
        return

# === PATCH END ==============================================================


