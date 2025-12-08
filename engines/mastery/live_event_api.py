#!/usr/bin/env python3
"""
Unified Live Event API (v7.9.9.1)
=================================

Every live engine (MSC, Overwatcher, LiveRouter, Lanes, Settlements,
BankState, BudgetManager) emits events through THIS module only.

This gives Mastery a complete, end-to-end understanding of LIVE behaviour:

    • Parent lifecycle
    • Child lifecycle (Hedges + Stop-Loss S-children)
    • MSC tick outputs
    • Overwatcher STOPLOSS events
    • Router placements & matches
    • Settlements
    • Bank & Budget changes

All helpers wrap event_sink.emit() with correct field names.
"""

from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from engines.mastery.event_sink import emit


# ============================================================
# Internal convenience
# ============================================================

def _ts() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ============================================================
# PARENT EVENTS
# ============================================================

def emit_parent_queued(
    *, cor: str, marketId: str, selectionId: str,
    side: str, entry_odds: float, entry_stake: float,
    engine: str, strategy: Optional[str] = None
):
    emit("parent_queued", {
        "ts": _ts(),
        "parent_ref": cor,
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "side": side.upper(),
        "entry_odds": float(entry_odds),
        "entry_stake": float(entry_stake),
        "engine": engine.upper(),
        "strategy": strategy,
    })


def emit_parent_placed(
    *, cor: str, bet_id: str, marketId: str, selectionId: str,
    engine: str, strategy: Optional[str] = None
):
    emit("parent_placed", {
        "ts": _ts(),
        "parent_ref": cor,
        "bet_id": str(bet_id),
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "engine": engine.upper(),
        "strategy": strategy,
    })


def emit_parent_matched(
    *, cor: str, marketId: str, selectionId: str,
    entry_odds: float, entry_stake: float,
    engine: str, strategy: Optional[str] = None
):
    emit("parent_matched", {
        "ts": _ts(),
        "parent_ref": cor,
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "entry_odds": float(entry_odds),
        "entry_stake": float(entry_stake),
        "engine": engine.upper(),
        "strategy": strategy,
    })


def emit_parent_exit(
    *, cor: str, marketId: str, selectionId: str,
    exit_kind: str, exit_odds: float, exit_stake: float,
    realized_pnl: float, engine: str
):
    emit("parent_exit", {
        "ts": _ts(),
        "parent_ref": cor,
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "exit_kind": exit_kind.upper(),
        "exit_odds": float(exit_odds),
        "exit_stake": float(exit_stake),
        "realized_pnl": float(realized_pnl),
        "engine": engine.upper(),
    })


# ============================================================
# CHILD EVENTS (HEDGE + STOPLOSS)
# ============================================================

def emit_child_queued(
    *, parent_ref: str, marketId: str, selectionId: str,
    side: str, odds: float, stake: float,
    exit_kind: str
):
    emit("child_queued", {
        "ts": _ts(),
        "parent_ref": parent_ref,
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "side": side.upper(),
        "entry_odds": float(odds),
        "entry_stake": float(stake),
        "exit_kind": exit_kind.upper(),
    })


def emit_child_placed(
    *, parent_ref: str, child_id: int, bet_id: str,
    marketId: str, selectionId: str, exit_kind: str
):
    emit("child_placed", {
        "ts": _ts(),
        "parent_ref": parent_ref,
        "child_id": int(child_id),
        "bet_id": str(bet_id),
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "exit_kind": exit_kind.upper(),
    })


def emit_child_matched(
    *, parent_ref: str, child_id: int,
    marketId: str, selectionId: str,
    exit_kind: str, exit_odds: float, exit_stake: float, realized_pnl: float
):
    emit("child_matched", {
        "ts": _ts(),
        "parent_ref": parent_ref,
        "child_id": int(child_id),
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "exit_kind": exit_kind.upper(),
        "exit_odds": float(exit_odds),
        "exit_stake": float(exit_stake),
        "realized_pnl": float(realized_pnl),
    })


# ============================================================
# STOPLOSS EVENTS (Overwatcher + MSC)
# ============================================================

def emit_stoploss_triggered(ev: Dict[str, Any]):
    """
    Pass-through helper for Overwatcher STOPLOSS events.
    Overwatcher already builds a clean ev payload.
    """
    emit("stop_loss_triggered", dict(ev, ts=_ts()))


def emit_stoploss_child_created(
    *, parent_ref: str, child_id: int,
    marketId: str, selectionId: str,
    odds: float, stake: float
):
    emit("child_stoploss_created", {
        "ts": _ts(),
        "parent_ref": parent_ref,
        "child_id": int(child_id),
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "odds": float(odds),
        "stake": float(stake),
    })


# ============================================================
# MSC EVENTS
# ============================================================

def emit_msc_plan(plan: Dict[str, Any]):
    """
    MSC passes its plan BEFORE routing into lanes.
    """
    emit("msc_plan", dict(plan, ts=_ts()))


# ============================================================
# Lanes Events
# ============================================================

def emit_lanes_route(*, plan: dict):
    emit("lanes_route", dict(plan, ts=_ts()))


# ============================================================
# ROUTER EVENTS
# ============================================================

def emit_router_place(
    *, cor: str, marketId: str, selectionId: str,
    side: str, odds: float, stake: float, engine: str
):
    emit("router_place", {
        "ts": _ts(),
        "parent_ref": cor,
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "side": side.upper(),
        "odds": float(odds),
        "stake": float(stake),
        "engine": engine.upper(),
    })


def emit_router_child_place(
    *, parent_ref: str, side: str, odds: float, stake: float,
    exit_kind: str
):
    emit("router_child_place", {
        "ts": _ts(),
        "parent_ref": parent_ref,
        "side": side.upper(),
        "odds": float(odds),
        "stake": float(stake),
        "exit_kind": exit_kind.upper(),
    })


# ============================================================
# SETTLEMENT EVENTS
# ============================================================

def emit_settlement(
    *, marketId: str, selectionId: str,
    result: str, pnl: float
):
    emit("settlement", {
        "ts": _ts(),
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "result": str(result).upper(),
        "pnl": float(pnl),
    })


# ============================================================
# BANKSTATE / BUDGET EVENTS
# ============================================================

def emit_bank_update(*, pot: str, delta: float, balance: float):
    emit("bank_update", {
        "ts": _ts(),
        "pot": pot,
        "delta": float(delta),
        "balance": float(balance),
    })


def emit_budget_allocation(*, engine: str, amount: float):
    emit("budget_allocation", {
        "ts": _ts(),
        "engine": engine.upper(),
        "amount": float(amount),
    })


# ============================================================
# EXPORTS
# ============================================================

__all__ = [
    "emit_parent_queued", "emit_parent_placed", "emit_parent_matched",
    "emit_parent_exit",

    "emit_child_queued", "emit_child_placed", "emit_child_matched",
    "emit_child_stoploss_created", "emit_stoploss_triggered",

    "emit_msc_plan",
    "emit_lanes_route",

    "emit_router_place", "emit_router_child_place",

    "emit_settlement",
    "emit_bank_update", "emit_budget_allocation",
]
