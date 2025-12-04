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

def _ensure_pot_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS engine_pots(
            day TEXT NOT NULL,
            engine TEXT NOT NULL,
            pot REAL NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(day, engine)
        )
    """)
    con.commit()

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


def init_pots_if_needed():
    """
    Called at startup.  
    Creates pots for the day if they do not exist yet.
    """
    today = _today()
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row

    _ensure_pot_table(con)

    rows = con.execute("SELECT COUNT(*) AS n FROM engine_pots WHERE day=?", (today,)).fetchone()
    if rows["n"] > 0:
        con.close()
        return  # already initialised

    # get starting balance
    start_bal = _fetch_starting_balance()

    # get allocation percentages from BudgetManager v10
    allocs = get_allocations()

    # write pots
    for eng in ENGINES:
        pct = allocs.get(eng, 0.0)
        pot_value = start_bal * pct
        con.execute("""
            INSERT INTO engine_pots(day, engine, pot, updated_at)
            VALUES(?,?,?,?)
        """, (today, eng, pot_value, _now()))

    con.commit()
    con.close()
    print("[BankState] Pots initialised:", json.dumps(allocs))


# ============================================================
#  POT GET/SET
# ============================================================

def _get_pot(engine: str) -> float:
    """
    Returns the static pot for the engine today.
    """
    engine = engine.upper()
    today = _today()
    con = _auto_conn(rw=False)
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
    con = _auto_conn(rw=True)
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
    con = _auto_conn(rw=False)
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
