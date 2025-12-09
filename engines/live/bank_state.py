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
ENGINES = [
    "LEGACY",
    "MSC_EXPLORATORY",
    "MSC_RISK",
    "MSC_INPLAY",
    "OVERWATCHER",
]

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

# === PATCH START ============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: top-level definitions (after ENGINES list)
# 📆 PATCHED: 2026-02-14 — add in-memory pot cache
# ============================================================================

class BankState:
    # In-memory pot cache: always today's pots
    pots: Dict[str, float] = {}

# === PATCH END ==============================================================
# 📍 TARGET: engines/live/bank_state.py — after class BankState
# 🔎 SEARCH: class BankState:
# === PATCH START (BANKSTATE v11 CORRECTION: delta tracking) ===

# Track which parents we've already applied delta for, so delta only runs once
BankState.delta_applied = set()

# === PATCH END ===


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

# === PATCH START ============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def init_pots_if_needed(
# 📆 PATCHED: 2026-02-14 — daily-first initialisation + restore
# ============================================================================

def init_pots_if_needed():
    """
    Create today's engine pots exactly once per UTC day.
    If pots already exist for today → restore them as BankState.pots.
    Otherwise → fetch allocations from BudgetManager and
    multiply by today's starting balance to initialize fresh pots.
    """
    # 📍 TARGET: engines/live/bank_state.py — init_pots_if_needed
    # 🔎 SEARCH: def init_pots_if_needed():
    # === PATCH START (BANKSTATE v11: init guard) ===

    # Do not run twice in one boot — if pots already loaded, skip init/restore
    if BankState.pots:
        return

    # === PATCH END ===


    from engines.risk import budget_manager

    today = _today()
    con = _bank_conn(rw=True)
    con.row_factory = sqlite3.Row
    _ensure_pot_table(con)

    # 1️⃣ Check if today already exists
    rows = con.execute(
        "SELECT engine, pot FROM engine_pots WHERE day=?",
        (today,)
    ).fetchall()

    if rows:
        # Restore into memory
        BankState.pots = {str(r["engine"]).upper(): float(r["pot"]) for r in rows}
        con.close()

        # === PATCH START (BANKSTATE v11 CORRECTION: mark deltas on restore) ===
        # Identify parent-child pairs already closed (child.exit_status='MATCHED')
        con2 = _bank_conn(rw=False); con2.row_factory = sqlite3.Row
        closed_rows = con2.execute("""
            SELECT p.id AS pid
              FROM orders p
         LEFT JOIN orders c ON c.hedge_of = p.id
             WHERE p.role='PARENT'
               AND p.entry_status='MATCHED'
               AND UPPER(COALESCE(c.exit_status,''))='MATCHED'
        """).fetchall()
        con2.close()

        for r in closed_rows:
            BankState.delta_applied.add(int(r["pid"]))
        # === PATCH END ===

        print(f"[BankState] Pots restored for {today}: {BankState.pots}")
        _apply_drift_correction()
        return


    # 2️⃣ FIRST LAUNCH OF DAY → build new pots
    # allocations come ONLY from BudgetManager
    allocs = budget_manager.get_allocations()


    # Fetch the bank value from DailyConfig (BudgetManager calls this too)
    from engines.daily_config import fetch_available_budget
    start_bal = float(fetch_available_budget()) or 0.0

    BankState.pots = {}

    for eng, pct in allocs.items():
        pot_value = start_bal * pct
        BankState.pots[eng] = pot_value
        con.execute(
            "INSERT INTO engine_pots(day, engine, pot, updated_at) VALUES (?,?,?,?)",
            (today, eng, pot_value, _now())
        )

    con.commit()
    con.close()
    _apply_drift_correction()
    print(f"[BankState] Pots initialised for {today}: {BankState.pots}")
  

# === PATCH END ==============================================================


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

# 📍 TARGET: engines/live/bank_state.py — after _get_pot and _set_pot definitions
# 🔎 SEARCH: def _set_pot(engine: str, value: float):
# === PATCH START (BANKSTATE v11: drift correction helper) ===

def _apply_drift_correction():
    """
    If real Betfair balance exceeds the sum of pots, upscale pots proportionally.
    """
    try:
        from engines.daily_config import fetch_available_budget
        real_bal = float(fetch_available_budget() or 0.0)
    except Exception:
        return

    today = _today()
    con = _bank_conn(rw=True); con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT engine, pot FROM engine_pots WHERE day=?",
        (today,)
    ).fetchall()

    if not rows:
        con.close()
        return

    total = sum(float(r["pot"]) for r in rows)
    if real_bal <= total or total <= 0:
        con.close()
        return

    scale = real_bal / total
    for r in rows:
        eng = str(r["engine"]).upper()
        new_pot = float(r["pot"]) * scale
        BankState.pots[eng] = new_pot
        con.execute("""
            UPDATE engine_pots
               SET pot=?, updated_at=?
             WHERE day=? AND engine=?
        """, (new_pot, _now(), today, eng))
    con.commit(); con.close()

# === PATCH END ===
# 📍 TARGET: engines/live/bank_state.py — add below drift correction
# 🔎 SEARCH: def _apply_drift_correction():
# === PATCH START (BANKSTATE v11: parent liability before) ===

def _compute_liability_before(parent) -> float:
    """
    Compute liability_before for a parent order.
    BACK: stake
    LAY:  stake × (odds − 1)
    """
    side = (parent["side"] or "").upper()
    stake = float(parent["entry_stake"])
    odds  = float(parent["entry_odds"])

    if side == "BACK":
        return stake
    return stake * max(0.0, odds - 1.0)

# === PATCH END ===
# 📍 TARGET: engines/live/bank_state.py — below _compute_liability_before
# 🔎 SEARCH: def _compute_liability_before
# === PATCH START (BANKSTATE v11: child finder) ===

def _find_child_for_parent(parent):
    """
    Return the child row for this parent, or None.
    Child linkage is ALWAYS via hedge_of = parent.id
    """
    parent_id = int(parent["id"])
    con = _bank_conn(rw=False); con.row_factory = sqlite3.Row
    child = con.execute("""
        SELECT * FROM orders
         WHERE hedge_of=?
           AND role='CHILD'
    """, (parent_id,)).fetchone()
    con.close()
    return child

# === PATCH END ===
# 📍 TARGET: engines/live/bank_state.py — below _find_child_for_parent
# 🔎 SEARCH: def _find_child_for_parent
# === PATCH START (BANKSTATE v11: liability_after) ===

def _compute_liability_after(parent, child) -> float:
    """
    Compute exposure (liability_after) for parent+child combined.
    Uses worst-case-loss model derived from Router semantics.
    """
    # parent
    p_side  = (parent["side"] or "").upper()
    p_stake = float(parent["entry_stake"])
    p_odds  = float(parent["entry_odds"])

    # child
    c_side  = (child["side"] or "").upper()
    c_stake = float(child["entry_stake"])
    c_odds  = float(child["entry_odds"])

    # Convert back/lay pair into exposure model:
    # Compute profit if horse wins / loses
    # BACK profit: stake × (odds - 1)
    # LAY  loss:  stake × (odds - 1)

    # Parent cashflows
    if p_side == "BACK":
        p_win  = p_stake * (p_odds - 1.0)
        p_lose = -p_stake
    else:  # LAY parent
        p_win  = -p_stake * (p_odds - 1.0)
        p_lose = p_stake

    # Child cashflows
    if c_side == "BACK":
        c_win  = c_stake * (c_odds - 1.0)
        c_lose = -c_stake
    else:  # LAY child
        c_win  = -c_stake * (c_odds - 1.0)
        c_lose = c_stake

    # Combine
    profit_win  = p_win  + c_win
    profit_lose = p_lose + c_lose

    worst_loss = min(profit_win, profit_lose)
    liability_after = max(0.0, -worst_loss)
    return liability_after

# 📍 TARGET: engines/live/bank_state.py — def _apply_delta_return
# 🔎 SEARCH: def _apply_delta_return(engine: str, parent, child):
# === PATCH START (BANKSTATE v11 CORRECTION: safe delta application) ===

def _apply_delta_return(engine: str, parent, child):
    """
    Compute delta = liability_before − liability_after
    and apply to pot exactly ONCE.
    """
    engine = engine.upper()
    parent_id = int(parent["id"])

    # If delta was already applied for this parent, skip
    if parent_id in BankState.delta_applied:
        return

    liab_before = _compute_liability_before(parent)
    liab_after  = _compute_liability_after(parent, child)
    delta       = liab_before - liab_after

    # Mark as processed BEFORE applying delta to avoid race loops
    BankState.delta_applied.add(parent_id)

    if abs(delta) < 1e-9:
        return  # no-op

    today = _today()
    old_pot = BankState.pots.get(engine, _get_pot(engine))
    new_pot = old_pot + delta

    # Persist to DB
    con = _bank_conn(rw=True)
    con.execute("""
        UPDATE engine_pots
           SET pot=?, updated_at=?
         WHERE day=? AND engine=?
    """, (new_pot, _now(), today, engine))
    con.commit(); con.close()

    BankState.pots[engine] = new_pot

# === PATCH END ===


# ============================================================
#  SETTLEMENT — POT MUTATION
# ============================================================

# === PATCH START ============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def apply_settlement(
# 📆 PATCHED: 2026-02-14 — settlement updates persistent pot rows + memory
# ============================================================================

def apply_settlement(engine: str, pnl: float):
    """
    Called by settlement daemon AFTER each market is fully settled.
    Adds PnL to the engine's pot and persists the update.
    """

    engine = engine.upper()
    today = _today()

    old = _get_pot(engine)
    new = old + float(pnl or 0.0)

    # Persist to DB
    con = _bank_conn(rw=True)
    con.execute("""
        UPDATE engine_pots
           SET pot=?, updated_at=?
         WHERE day=? AND engine=?
    """, (new, _now(), today, engine))
    con.commit()
    con.close()

    # Update memory
    BankState.pots[engine] = new

    print(f"[BankState] Settlement applied: {engine}: {old:.2f} → {new:.2f} (pnl {pnl:+.2f})")

# === PATCH END ==============================================================



# ============================================================
#  OPEN LIABILITY CALCULATION
# ============================================================
# 📍 TARGET: engines/live/bank_state.py — _calculate_open_liability
# 🔎 SEARCH: def _calculate_open_liability(engine: str):
# === PATCH START (BANKSTATE v11 CORRECTION: stable liability scan) ===

def _calculate_open_liability(engine: str) -> float:
    """
    Compute open liability:
    • For open parents → use liability_before
    • For closed parents → use liability_after
    • Apply delta exactly once per parent-child closure
    """
    engine = engine.upper()
    con = _bank_conn(rw=False); con.row_factory = sqlite3.Row

    parents = con.execute("""
        SELECT * FROM orders
         WHERE engine=?
           AND role='PARENT'
           AND entry_status='MATCHED'
    """, (engine,)).fetchall()
    con.close()

    total_liability = 0.0

    for p in parents:
        parent_id = int(p["id"])
        child = _find_child_for_parent(p)

        if child and (child["exit_status"] or "").upper() == "MATCHED":
            # child is closed → apply delta once, use liability_after
            _apply_delta_return(engine, p, child)
            la = _compute_liability_after(p, child)
            total_liability += la
        else:
            # parent open → full liability_before
            lb = _compute_liability_before(p)
            total_liability += lb

    return total_liability

# === PATCH END ===

# ============================================================
#  PUBLIC API FOR LIVEROUTER
# ============================================================

def get_engine_pot(engine: str) -> float:
    """Return static pot for engine (unchanging except for settlements)."""
    return _get_pot(engine)

# 📍 TARGET: engines/live/bank_state.py — get_engine_available
# 🔎 SEARCH: def get_engine_available(engine: str):
# === PATCH START (BANKSTATE v11 CORRECTION: stable pot fetch) ===

def get_engine_available(engine: str) -> float:
    """
    Compute availability:
      pot - open_liability (after delta returns)
    Pot is taken from in-memory cache first.
    """
    engine = engine.upper()

    liab = _calculate_open_liability(engine)

    # Prefer memory pot; fallback to DB pot
    pot = BankState.pots.get(engine)
    if pot is None:
        pot = _get_pot(engine)
        BankState.pots[engine] = pot

    return pot - liab

# === PATCH END ===




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
