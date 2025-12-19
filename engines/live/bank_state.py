# engines/live/bank_state.py
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict

# -------------------------------------------------------------------
# BankState — Live Exposure Ledger (v7)
# -------------------------------------------------------------------
# Authoritative source of:
#   • starting balance (daily)
#   • current balance (with realised P&L)
#   • open exposure (liability of matched parents)
#   • per-engine available capital
#
# BankState is EVENT-DRIVEN.
# It never polls Betfair.
# It never reads orders tables.
# -------------------------------------------------------------------

_LOCK = threading.RLock()

# ---- Global state --------------------------------------------------
_STARTING_BALANCE: float = 0.0
_CURRENT_BALANCE: float = 0.0
_OPEN_EXPOSURE: float = 0.0
_LAST_INIT_DAY: str | None = None

# ---- Engine allocations -------------------------------------------
_ENGINE_ALLOC_PCT: Dict[str, float] = {
    "LEGACY": 0.37,
    "MSC_EXPLORATORY": 0.25,
    "MSC_RISK": 0.20,
    "MSC_INPLAY": 0.18,
}

_ENGINE_USED: Dict[str, float] = {
    "LEGACY": 0.0,
    "MSC_EXPLORATORY": 0.0,
    "MSC_RISK": 0.0,
    "MSC_INPLAY": 0.0,
}

# === PATCH START =========================================================
# 📍 TARGET: engines/live/bank_state.py
# 📆 PATCHED: 2026-02-12 — Initialize BankState from budget_allocations
# ================================================================

_ENGINE_POTS = {}
_ENGINE_AVAILABLE = {}

def init_from_budget_allocations(day: str | None = None) -> None:
    """
    Initialize engine pots and available balances from persisted allocations.

    Invariant:
        pot == available + open_liability

    This function MUST be called once at startup.
    """

    global _ENGINE_POTS, _ENGINE_AVAILABLE

    from engines.config_paths import open_auto_db
    from datetime import datetime

    if not day:
        day = datetime.utcnow().strftime("%Y-%m-%d")

    con = open_auto_db(rw=False)
    con.row_factory = None
    cur = con.cursor()

    rows = cur.execute("""
        SELECT engine, pot
          FROM budget_allocations
         WHERE day = ?
    """, (day,)).fetchall()

    con.close()

    _ENGINE_POTS.clear()
    _ENGINE_AVAILABLE.clear()

    for engine, pot in rows:
        pot = float(pot)
        _ENGINE_POTS[engine] = pot
        _ENGINE_AVAILABLE[engine] = pot

    print(f"[BANKSTATE] initialized from budget_allocations ({day})")

# === PATCH END =========================================================


# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _clamp(x: float) -> float:
    return round(max(0.0, float(x)), 2)

# -------------------------------------------------------------------
# INITIALISATION (ONCE PER UTC DAY)
# -------------------------------------------------------------------

def init_bank_state():
    """
    Initialise BankState once per UTC day.
    Called by orchestrator at startup.
    """
    global _STARTING_BALANCE, _CURRENT_BALANCE, _OPEN_EXPOSURE, _LAST_INIT_DAY

    with _LOCK:
        today = _utc_day()
        if _LAST_INIT_DAY == today:
            return

        # Fetch live available balance ONCE
        try:
            from engines.daily_config import fetch_available_budget
            fetched = float(fetch_available_budget() or 0.0)
        except Exception:
            fetched = 0.0

        # Never allow downward correction
        if _STARTING_BALANCE > 0.0:
            _STARTING_BALANCE = max(_STARTING_BALANCE, fetched)
        else:
            _STARTING_BALANCE = fetched

        _CURRENT_BALANCE = _STARTING_BALANCE
        _OPEN_EXPOSURE = 0.0

        for k in _ENGINE_USED:
            _ENGINE_USED[k] = 0.0

        _LAST_INIT_DAY = today

        print(
            f"[BankState] init day={today} "
            f"starting_balance={_STARTING_BALANCE:.2f}"
        )

# -------------------------------------------------------------------
# READ API (USED BY ROUTER)
# -------------------------------------------------------------------

def get_balance() -> float:
    with _LOCK:
        return _clamp(_CURRENT_BALANCE)

def get_open_exposure() -> float:
    with _LOCK:
        return _clamp(_OPEN_EXPOSURE)

def get_available_balance() -> float:
    with _LOCK:
        return _clamp(_CURRENT_BALANCE - _OPEN_EXPOSURE)

def get_engine_pot(engine: str) -> float:
    with _LOCK:
        pct = _ENGINE_ALLOC_PCT.get(engine, 0.0)
        return _clamp(pct * get_available_balance())

def get_engine_available(engine: str) -> float:
    with _LOCK:
        pot = get_engine_pot(engine)
        used = _ENGINE_USED.get(engine, 0.0)
        return _clamp(pot - used)

def can_place(engine: str, required: float) -> bool:
    with _LOCK:
        return get_engine_available(engine) >= float(required)

# -------------------------------------------------------------------
# EVENT API (CALLED BY ROUTER / SETTLEMENTS)
# -------------------------------------------------------------------

def on_parent_matched(*, engine: str, side: str,
                      entry_odds: float, entry_stake: float) -> None:
    """
    Apply exposure when a parent becomes MATCHED.
    """
    global _OPEN_EXPOSURE

    with _LOCK:
        if side.upper() == "LAY":
            liability = entry_stake * (entry_odds - 1.0)
        else:
            liability = entry_stake

        liability = _clamp(liability)

        _OPEN_EXPOSURE += liability
        _ENGINE_USED[engine] = _ENGINE_USED.get(engine, 0.0) + liability

        print(
            f"[BankState] +EXPOSURE engine={engine} "
            f"liab={liability:.2f} "
            f"open={_OPEN_EXPOSURE:.2f}"
        )

def on_parent_closed(*, engine: str,
                     entry_odds: float, entry_stake: float) -> None:
    """
    Release exposure when hedge/stoploss closes parent.
    """
    global _OPEN_EXPOSURE

    with _LOCK:
        if entry_stake <= 0.0:
            return

        if engine not in _ENGINE_USED:
            return

        # Recompute same liability used on entry
        if entry_odds > 0.0:
            liability = (
                entry_stake * (entry_odds - 1.0)
                if entry_odds >= 1.01 else entry_stake
            )
        else:
            liability = entry_stake

        liability = _clamp(liability)

        _OPEN_EXPOSURE = max(0.0, _OPEN_EXPOSURE - liability)
        _ENGINE_USED[engine] = max(
            0.0, _ENGINE_USED.get(engine, 0.0) - liability
        )

        print(
            f"[BankState] -EXPOSURE engine={engine} "
            f"liab={liability:.2f} "
            f"open={_OPEN_EXPOSURE:.2f}"
        )

def apply_settlement(pnl: float) -> None:
    """
    Apply realised P&L after market settlement.
    """
    global _CURRENT_BALANCE

    with _LOCK:
        pnl = float(pnl or 0.0)
        _CURRENT_BALANCE += pnl

        print(
            f"[BankState] SETTLEMENT pnl={pnl:+.2f} "
            f"balance={_CURRENT_BALANCE:.2f}"
        )
