#!/usr/bin/env python3
# engines/live/bank_state.py
# ============================================================
# Unified Engine BankState v10
# ============================================================
# POTS:
#   LEGACY
#   MSC_EXPLORATORY
#   MSC_RISK
#   MSC_INPLAY
#
# RULES:
#   • Pots initialise ONCE per UTC day when tool first starts.
#   • Pots never change again except on settlement.
#   • Dynamic stake uses the STATIC pot (not the available).
#   • Can-Place uses AVAILABLE = pot – open_liability(engine).
#   • open_liability = sum of all unhedged PARENT orders.
#   • BACK liability = stake
#   • LAY liability = stake * (odds − 1)
#
# ============================================================

from __future__ import annotations
import sqlite3, datetime, json, os
from typing import Dict

# === PATCH START ============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: top-level _auto_conn usage
# 📆 PATCHED: 2025-12-10 — BankState reads correct DB per DAL mode
# ============================================================================

from engines.config_paths import auto_conn as _auto_conn
from engines.config_paths import auto_conn_live as _auto_conn_live
from engines.config_paths import DAL_MODE

def _bank_conn(rw=False):
    """
    BankState must read the SAME orders table that LiveRouter writes:
        • SETUP mode → Local autoscalp_gui.db
        • LIVE mode  → LiveCache autoscalp_livecache.db
    This ensures open_liability and can_place() match LiveRouter behaviour.
    """
    if DAL_MODE == "LIVE":
        return _auto_conn_live(rw=rw)
    return _auto_conn(rw=rw)

# === PATCH END ==============================================================


# DAL connector
from engines.config_paths import auto_conn as _auto_conn

# BudgetManager drives allocations
from engines.risk.budget_manager import (
    get_allocations,
)

# Engines we manage
ENGINES = ["LEGACY", "MSC_EXPLORATORY", "MSC_RISK", "MSC_INPLAY"]

# ============================================================
#  DB SCHEMA FOR ENGINE POTS
# ============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: class BankState(
# 🆕 ADD: ENGINE → POT mapping
# 📆 PATCHED: 2026-02-12
# ============================================================================

ENGINE_TO_POT = {
    "LEGACY":          "LEGACY",
    "MSC_EXPLORATORY": "MSC_EXPLORATORY",
    "MSC_RISK":        "MSC_RISK",
    "MSC_INPLAY":      "MSC_INPLAY",
    "OVERWATCHER":     "OVERWATCHER",
}

def get_engine_budget(engine: str) -> float:
    """
    Returns the allocated balance for the given engine.
    Fully Bus-compatible.
    """
    pot = ENGINE_TO_POT.get(engine.upper())
    if not pot:
        return 0.0
    try:
        return float(BankState.pots.get(pot, 0.0))
    except Exception:
        return 0.0

# === PATCH END ================================================================

# === PATCH START ======================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: CREATE TABLE IF NOT EXISTS engine_pots(
# ⛏️ ACTION: remove PRIMARY KEY, keep columns simple
# ======================================================================

def _ensure_pot_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS engine_pots(
            day TEXT NOT NULL,
            engine TEXT NOT NULL,
            pot REAL NOT NULL,
            updated_at TEXT NOT NULL
            -- No PRIMARY KEY → DAL-safe, LiveCache-safe
        )
    """)
    con.commit()

# === PATCH END ========================================================
# ============================================================
#  UTILS
# ============================================================

def _now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _today():
    return datetime.datetime.utcnow().strftime("%Y-%m-%d")


# ============================================================
#  POT INITIALISATION
# ============================================================

def _fetch_starting_balance() -> float:
    """
    Whatever the FIRST Betfair balance of the day is becomes the fixed pot base.
    DailyConfig previously did this, but we centralise it here.
    """
    try:
        from engines.daily_config import fetch_available_budget
        bal = float(fetch_available_budget())
        return bal if bal > 0 else 0.0
    except Exception:
        return 0.0

# ======================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: from engines.risk.budget_manager import (
# ⛏️ ACTION: Remove top-level import + fix init_pots_if_needed()
# 📆 PATCHED: 2025-12-04
# ======================================================================

### PATCH START
# REMOVE this at top of file if still present:
# from engines.risk.budget_manager import ( get_allocations, )

# REPLACE the current init_pots_if_needed() with this:

# === PATCH START ======================================================
# 📍 TARGET: engines/live/bank_state.py:init_pots_if_needed
# 🔎 SEARCH: def init_pots_if_needed():
# ======================================================================

def init_pots_if_needed():
    """
    Create today's engine pots once per day.
    Logic-based guard: If ANY row exists for today → skip insert.
    This avoids PK constraints and is fully DAL-safe.
    """

    from engines.risk import budget_manager
    allocs = budget_manager.get_allocations()

    today = _today()
    con = _bank_conn(rw=True)
    con.row_factory = sqlite3.Row

    _ensure_pot_table(con)

    # 🌞 1️⃣ DAY CHECK — If today's pots already exist, skip
    exists = con.execute(
        "SELECT 1 FROM engine_pots WHERE day=? LIMIT 1",
        (today,)
    ).fetchone()

    if exists:
        con.close()
        return  # today already initialised

    # 🌞 2️⃣ FIRST INITIALISATION FOR TODAY
    start_bal = _fetch_starting_balance()

    for eng in ENGINES:
        pct = allocs.get(eng, 0.0)
        pot_value = start_bal * pct

        con.execute(
            "INSERT INTO engine_pots(day, engine, pot, updated_at) VALUES (?,?,?,?)",
            (today, eng, pot_value, _now())
        )

    con.commit()
    con.close()
    print(f"[BankState] Pots initialised for {today}: {json.dumps(allocs)}")

# === PATCH END ========================================================



# === PATCH START =======================================================
# 📍 TARGET: engines/live/bank_state.py:_fetch_starting_balance
# ⛏️ ACTION: Replace the import inside the function
# =======================================================================

def _fetch_starting_balance() -> float:
    """
    Pull the FIRST Betfair balance of the day.
    """
    try:
        # 🔥 Lazy import to avoid circular with daily_config → orchestrator
        from engines.daily_config import fetch_available_budget
        bal = float(fetch_available_budget())
        return bal if bal > 0 else 0.0
    except Exception:
        return 0.0
# === PATCH END ===========================================================


# ============================================================
#  POT GET/SET
# ============================================================

def _get_pot(engine: str) -> float:
    """
    Returns the static pot for the engine today.
    """
    engine = engine.upper()
    today = _today()
    con = _bank_conn(rw=False)
    con.row_factory = sqlite3.Row
    row = con.execute("""
        SELECT pot FROM engine_pots 
         WHERE day=? AND engine=?
    """, (today, engine)).fetchone()
    con.close()
    return float(row["pot"]) if row else 0.0


def _set_pot(engine: str, value: float):
    """
    Updates the engine pot.  
    Only called on settlement.
    """
    engine = engine.upper()
    today = _today()
    con = _bank_conn(rw=True)
    con.execute("""
        UPDATE engine_pots
           SET pot=?, updated_at=?
         WHERE day=? AND engine=?
    """, (float(value), _now(), today, engine))
    con.commit()
    con.close()


# ============================================================
#  SETTLEMENT — POT MUTATION
# ============================================================

def apply_settlement(engine: str, pnl: float):
    """
    Settlement event per engine.  
    Adds pnl to that engine's pot.
    """
    engine = engine.upper()
    old = _get_pot(engine)
    new = old + float(pnl or 0.0)
    _set_pot(engine, new)
    print(f"[BankState] Settlement: {engine} pot {old:.2f} → {new:.2f} (pnl {pnl:+.2f})")


# ============================================================
#  OPEN LIABILITY CALCULATION
# ============================================================

def _calculate_open_liability(engine: str) -> float:
    """
    Sum liability of all *unhedged PARENT* orders belonging to this engine.
    """
    engine = engine.upper()
    con = _bank_conn(rw=False)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT side, entry_odds, entry_stake
          FROM orders
         WHERE engine=?
           AND role='PARENT'
           AND entry_status='MATCHED'
           AND (exit_status IS NULL OR exit_status<>'MATCHED')
    """, (engine,)).fetchall()
    con.close()

    total = 0.0
    for r in rows:
        side = (r["side"] or "").upper()
        st   = float(r["entry_stake"] or 0.0)
        od   = float(r["entry_odds"] or 0.0)

        if side == "BACK":
            total += st
        else:  # LAY
            total += st * max(0.0, od - 1.0)

    return total


# ============================================================
#  PUBLIC API FOR LIVEROUTER
# ============================================================

def get_engine_pot(engine: str) -> float:
    """Return static pot for engine (unchanging except for settlements)."""
    return _get_pot(engine)


def get_engine_available(engine: str) -> float:
    """
    Pot minus all open liability.
    """
    pot = _get_pot(engine)
    liab = _calculate_open_liability(engine)
    return pot - liab


def can_place(engine: str, stake_required: float) -> bool:
    """
    Check if this engine has enough free budget RIGHT NOW to place a parent order.
    """
    avail = get_engine_available(engine)
    return avail >= float(stake_required)


# ============================================================
#  DAILYCONFIG COMPATIBILITY LAYER
# ============================================================

def get_balance() -> float:
    """
    Alias used by older DailyConfig + LiveRouter dynamic stake.
    Returns TOTAL STATIC BANK across all pots.
    """
    return sum(_get_pot(e) for e in ENGINES)


def get_live_balance() -> float:
    """
    Alias preserved for compatibility.
    Same as get_balance().
    """
    return get_balance()

# ============================================================
#  DASHBOARD / DAILYCONFIG SNAPSHOT HELPERS
# ============================================================

def get_daily_pots() -> Dict[str, float]:
    """
    Return snapshot {engine: pot_value} for today's pots.
    Used by dashboard and daily_config.
    """
    return {eng: _get_pot(eng) for eng in ENGINES}



# ============================================================
#  STARTUP ENTRYPOINT
# ============================================================

def init_bank_state():
    """
    Called once at system startup.
    Ensures pots exist for the day.
    """
    init_pots_if_needed()


# CLI FOR DEBUGGING
if __name__ == "__main__":
    init_bank_state()
    print("=== ENGINE POTS ===")
    for e in ENGINES:
        print(e, "=", get_engine_pot(e))
    print("Total =", get_balance())
