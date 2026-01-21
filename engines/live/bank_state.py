# engines/live/bank_state.py
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Dict

# --- Scope integration (read-only) ---
try:
    from engines.decision_engine.decide_once.scope import _SCOPE_STATE
except Exception:
    _SCOPE_STATE = {}

# ---- Engine pots (persisted, BudgetManager-owned) ----------------
_ENGINE_POTS: Dict[str, float] = {}
_ENGINE_AVAILABLE: Dict[str, float] = {}

# -------------------------------------------------------------------
# COMPATIBILITY SHIMS (required by BUS / reports)
# -------------------------------------------------------------------

def get_engine_pots() -> dict:
    """
    Return declared engine pots for TODAY.
    Source of truth: budget_allocations table.
    """
    with _LOCK:
        return dict(_ENGINE_POTS)


def get_engine_available_map() -> dict:
    """
    Convenience helper for dashboards.
    """
    with _LOCK:
        return dict(_ENGINE_AVAILABLE)

def get_engine_used_map() -> dict:
    with _LOCK:
        return dict(_ENGINE_USED)

def init_bank_state():
    """
    Compatibility init called by orchestrator / GUI.

    BudgetManager is the source of truth.
    BankState initialises itself from persisted budget_allocations.
    """
    try:
        init_from_budget_allocations()
        if _is_simulation():
            print("[BankState] initialised from budget_allocations")
    except Exception as e:
        print(f"[BankState][WARN] init failed: {e}")

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: end of file
# 🧩 ACTION: ADD OBSERVABILITY REPORT LOOP (READ-ONLY)
# 📆 PATCHED: 2026-01-11 — BankState minute telemetry (pots / used / available)
#
# PURPOSE:
# - Provide live visibility during runtime smoke tests
# - Verify exposure vs pots behaviour in real time
# - NO execution logic
# - NO state mutation
#
# SAFETY:
# - Read-only
# - Daemon thread
# - Never blocks trading
# ======================================================================================================

import threading
import time

_REPORT_THREAD = None

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: get_engine_pot / get_engine_available / _bankstate_report_loop
# 🧩 ACTION: Remove divisor from capital math, retain divisor for reporting only
# 📆 PATCHED: 2026-01-16 — Decouple BankState capital from scope divisor
#
# RATIONALE:
# - BUS now enforces market concurrency and routing
# - Divisor no longer protects against any real failure mode
# - Capital must reflect true pot availability
# - Divisor is retained ONLY as a diagnostic signal
#
# INVARIANT:
# - Pots mutate ONLY via realised P&L
# - Availability = pot − used
# - No scope-derived scaling of capital
# ======================================================================================================
# -------------------------------------------------------------------
# OBSERVABILITY REPORT LOOP (READ-ONLY)
# -------------------------------------------------------------------

def _bankstate_report_loop(interval_s: int = 60):
    """
    Periodic read-only BankState report.
    Prints engine pots, used, available, and total open exposure.

    NOTE:
    - Divisor is reported for diagnostics ONLY
    - It no longer affects any financial calculations
    """
    while True:
        try:
            with _LOCK:
                divisor = _effective_market_count()
                open_exp = _clamp(_OPEN_EXPOSURE)

                now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                print("\n============ V7 BANK STATE REPORT ============")
                print(
                    f"[BANKSTATE][REPORT] t={now} "
                    f"divisor={divisor} (diagnostic) "
                    f"open={open_exp:.2f}"
                )

                for engine in sorted(_ENGINE_POTS.keys()):
                    pot   = _ENGINE_POTS.get(engine, 0.0)
                    used  = _ENGINE_USED.get(engine, 0.0)
                    avail = pot - used

                    print(
                        f"  {engine:<15} "
                        f"pot={pot:.2f} "
                        f"used={used:.2f} "
                        f"avail={_clamp(avail):.2f}"
                    )
                print("=================================================\n")
        except Exception as e:
            print(f"[BankState][REPORT][WARN] {e}")

        time.sleep(interval_s)

def start_bankstate_reporter(interval_s: int = 60):
    """
    Start the BankState observability reporter.
    Safe to call multiple times (singleton).
    """
    global _REPORT_THREAD

    try:
        if _REPORT_THREAD and _REPORT_THREAD.is_alive():
            return
    except Exception:
        pass

    t = threading.Thread(
        target=_bankstate_report_loop,
        args=(int(interval_s),),
        name="BankStateReporter",
        daemon=True,
    )
    _REPORT_THREAD = t
    t.start()

    print(f"[BankState] observability reporter started (interval={interval_s}s)")



# -------------------------------------------------------------------
# SIMULATION MODE (OFF BY DEFAULT)
# -------------------------------------------------------------------

_SIMULATION_MODE = False
_SIMULATION_LOCK = threading.RLock()
_SIMULATION_DIVISOR = None

def _enable_simulation_mode(*, divisor: int | None = None):
    global _SIMULATION_MODE, _SIMULATION_DIVISOR
    with _SIMULATION_LOCK:
        _SIMULATION_MODE = True
        _SIMULATION_DIVISOR = int(divisor) if divisor else None
        print(
            f"[BankState][SIM] ENABLED"
            f"{' divisor='+str(_SIMULATION_DIVISOR) if _SIMULATION_DIVISOR else ''}"
        )


def _disable_simulation_mode():
    global _SIMULATION_MODE, _SIMULATION_DIVISOR
    with _SIMULATION_LOCK:
        _SIMULATION_MODE = False
        _SIMULATION_DIVISOR = None
        print("[BankState][SIM] DISABLED")


def _is_simulation():
    return _SIMULATION_MODE

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


_ENGINE_USED: Dict[str, float] = {
    "LEGACY": 0.0,
    "MSC_EXPLORATORY": 0.0,
    "MSC_RISK": 0.0,
    "MSC_INPLAY": 0.0,
}

_OPEN_EXPOSURE: float = 0.0

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------

def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _clamp(x: float) -> float:
    return round(max(0.0, float(x)), 2)

# -------------------------------------------------------------------
# Scope-aware capital divisor
# -------------------------------------------------------------------

_MIN_DAILY_SPLIT = 10
_MAX_CONCURRENT_MARKETS = 20  # or remove cap entirely

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: _effective_market_count
# 🧩 ACTION: Replace heuristic divisor with scope-derived unique market count
# 📆 PATCHED: 2026-01-16 — Divisor = distinct marketIds in scope
#
# RATIONALE:
# - BUS owns routing, cadence, and duplication control
# - BankState divisor is diagnostic only
# - Scope is the authoritative source of "markets tradable right now"
#
# DEFINITION (LOCKED):
#   divisor = COUNT(DISTINCT marketId IN scope_snapshot)
#
# NO:
# - weighting
# - floors
# - caps
# - heuristics
# ======================================================================================================

def _effective_market_count() -> int:
    """
    Diagnostic-only divisor.

    Returns the number of DISTINCT marketIds currently in scope.
    """
    try:
        from engines.decision_engine.decide_once.scope import scope_snapshot

        sc = scope_snapshot(inplay_window_min=15)

        mids = set()

        # pre_near / pre_far: (mid, tto, name, off)
        for row in sc.get("pre_near", []):
            try:
                mids.add(str(row[0]))
            except Exception:
                pass

        for row in sc.get("pre_far", []):
            try:
                mids.add(str(row[0]))
            except Exception:
                pass

        # in_play: (mid, elapsed)
        for row in sc.get("in_play", []):
            try:
                mids.add(str(row[0]))
            except Exception:
                pass

        return len(mids)

    except Exception:
        # Diagnostic only — never block
        return 0



from engines.config_paths import autoscalp_db
import sqlite3

def _open_auto_strict():
    return sqlite3.connect(
        autoscalp_db(),
        timeout=10,
        check_same_thread=False
    )

# -------------------------------------------------------------------
# INITIALISATION (ONCE PER UTC DAY)
# -------------------------------------------------------------------

def init_from_budget_allocations(day: str | None = None):
    global _ENGINE_POTS, _ENGINE_AVAILABLE

    if not day:
        from datetime import datetime
        day = datetime.utcnow().strftime("%Y-%m-%d")

    con = _open_auto_strict()
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
   
    if _is_simulation():
        print(f"[BANKSTATE] pots loaded from budget_allocations ({day})")



# -------------------------------------------------------------------
# READ API (USED BY ROUTER)
# -------------------------------------------------------------------


def get_open_exposure() -> float:
    with _LOCK:
        return _clamp(_OPEN_EXPOSURE)


def get_engine_pot(engine: str) -> float:
    """
    Engine pot (raw, unscaled).

    NOTE:
    - Divisor no longer applies to capital
    - Concurrency is enforced by BUS routing, not BankState
    """
    with _LOCK:
        return _clamp(_ENGINE_POTS.get(engine, 0.0))


def get_engine_available(engine: str) -> float:
    """
    Available capital for this engine RIGHT NOW.

    Canonical rule:
      available = pot − used

    No scope, no divisor, no concurrency heuristics.
    """
    with _LOCK:
        pot  = _ENGINE_POTS.get(engine, 0.0)
        used = _ENGINE_USED.get(engine, 0.0)
        return _clamp(pot - used)



# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: def on_parent_placed
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-01-14 — accept legacy kwargs (side) without changing behaviour
#
# RATIONALE:
# - Router still passes `side` during parent placement
# - BankState no longer needs it, but must accept it
# - Prevents parent placement from failing before Betfair call
#
# BEHAVIOUR:
# - Exposure reservation logic unchanged
# - Extra kwargs ignored safely
# ======================================================================================================

def on_parent_placed(
    *,
    engine: str,
    parent_id: int,
    **_ignored,
) -> None:
    """
    Reserve FULL lifecycle exposure at placement time.

    Contract:
    - Placement already computed and persisted required_exposure
    - BankState reads it from DB
    - BankState mutates exposure ledger only
    """

    global _OPEN_EXPOSURE

    try:
        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=False)
        row = con.execute(
            "SELECT required_exposure FROM orders WHERE id=?",
            (int(parent_id),)
        ).fetchone()
        con.close()
    except Exception:
        return

    if not row or row[0] is None:
        # Hard invariant: parent exists but exposure missing
        raise RuntimeError(
            f"[BankState] invariant violation: required_exposure missing for parent_id={parent_id}"
        )

    amount = _clamp(row[0])

    with _LOCK:
        _OPEN_EXPOSURE += amount
        _ENGINE_USED[engine] = _ENGINE_USED.get(engine, 0.0) + amount

        print(
            f"[BankState] +RESERVE engine={engine} "
            f"amount={amount:.2f} "
            f"open={_OPEN_EXPOSURE:.2f}"
        )



def can_place(engine: str, required: float) -> bool:
    with _LOCK:
        return get_engine_available(engine) >= float(required)

# -------------------------------------------------------------------
# EVENT API (CALLED BY ROUTER / SETTLEMENTS)
# -------------------------------------------------------------------

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: def on_parent_matched
# 🧩 ACTION: REMOVE EXPOSURE MUTATION
# 📆 PATCHED: 2025-12-21 — parent match no longer affects exposure
# ======================================================================================================

def on_parent_matched(*, engine: str, side: str,
                      entry_odds: float, entry_stake: float) -> None:
    """
    Parent MATCHED event.

    FIX:
    - Exposure is already reserved at PLACED
    - DO NOT mutate exposure here
    """
    if _is_simulation():
        print(
            f"[BankState] parent matched (no exposure change) "
            f"engine={engine}"
        )

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: EVENT API — RELEASE PATHS (on_parent_closed / on_child_matched / release_parent)
# 🧩 ACTION: ADD INVARIANT GUARD (prevent phantom exposure releases)
# 📆 PATCHED: 2026-01-13 — BankState v3 (hard reserve→release invariant)
#
# RATIONALE:
# BankState was releasing exposure even when no prior reservation occurred.
# This caused repeated "-EXPOSURE open=0.00" logs with zero active parents.
#
# INVARIANT:
# A release is ONLY valid if exposure was previously reserved for that engine.
# If _ENGINE_USED[engine] <= 0, the release MUST be ignored.
#
# SCOPE:
# - No schema changes
# - No router changes
# - No BUS changes
# - No simulation changes
# ======================================================================================================


# -------------------------------------------------------------------
# PATCH 1️⃣ — on_parent_closed
# -------------------------------------------------------------------
def on_parent_closed(*, engine: str, parent_id: int) -> None:
    global _OPEN_EXPOSURE

    try:
        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=False)
        row = con.execute(
            "SELECT required_exposure FROM orders WHERE id=?",
            (int(parent_id),)
        ).fetchone()
        con.close()
    except Exception:
        return

    if not row:
        return

    amount = _clamp(row[0])

    with _LOCK:
        used = _ENGINE_USED.get(engine, 0.0)
        if used <= 0.0:
            return

        _OPEN_EXPOSURE = max(0.0, _OPEN_EXPOSURE - amount)
        _ENGINE_USED[engine] = max(0.0, used - amount)

# -------------------------------------------------------------------
# PATCH 2️⃣ — on_child_matched
# -------------------------------------------------------------------
def on_child_matched(*, parent_id: int, **_ignored) -> None:
    """
    Child matched → release exposure from its parent.

    parent_id here is ACTUALLY the CHILD id.
    We must resolve hedge_of → parent.
    """
    global _OPEN_EXPOSURE

    try:
        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=False)
        row = con.execute(
            """
            SELECT
                p.engine,
                p.required_exposure
            FROM orders c
            JOIN orders p ON p.id = c.hedge_of
            WHERE c.id = ?
            """,
            (int(parent_id),)
        ).fetchone()
        con.close()
    except Exception:
        return

    if not row:
        return

    engine, required = row
    amount = _clamp(required)

    with _LOCK:
        used = _ENGINE_USED.get(engine, 0.0)
        if used <= 0.0:
            return

        _OPEN_EXPOSURE = max(0.0, _OPEN_EXPOSURE - amount)
        _ENGINE_USED[engine] = max(0.0, used - amount)



# -------------------------------------------------------------------
# PATCH 3️⃣ — release_parent (router housekeeping)
# -------------------------------------------------------------------
def release_parent(parent_id: int) -> None:
    global _OPEN_EXPOSURE

    try:
        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=False)
        row = con.execute(
            "SELECT engine, required_exposure FROM orders WHERE id=?",
            (int(parent_id),)
        ).fetchone()
        con.close()
    except Exception:
        return

    if not row:
        return

    engine, required = row
    amount = _clamp(required)

    with _LOCK:
        used = _ENGINE_USED.get(engine, 0.0)
        if used <= 0.0:
            return

        _OPEN_EXPOSURE = max(0.0, _OPEN_EXPOSURE - amount)
        _ENGINE_USED[engine] = max(0.0, used - amount)


def reconcile_realized_pnl_from_orders() -> None:
    """
    Return realized P&L (orders.realized_pnl) back to engine pots.

    • Reads authoritative DB state
    • Uses realized_pnl ONLY (no stake)
    • Idempotent via bank_reconciled flag
    """

    from engines.config_paths import open_auto_db

    with _LOCK:
        con = open_auto_db(rw=True)
        con.row_factory = None
        cur = con.cursor()

        rows = cur.execute("""
            SELECT engine,
                   SUM(COALESCE(realized_pnl, 0)) AS pnl
              FROM orders
             WHERE role = 'PARENT'
               AND exit_status IN ('SETTLED','MATCHED','EXPIRED')
               AND bank_reconciled = 0
               AND realized_pnl IS NOT NULL
               AND date(closed_at) = date('now','utc')
             GROUP BY engine
        """).fetchall()

        if not rows:
            con.close()
            return

        for engine, pnl in rows:
            if engine not in _ENGINE_POTS:
                continue

            pnl = _clamp(pnl or 0.0)

            _ENGINE_POTS[engine] += pnl
            _ENGINE_AVAILABLE[engine] += pnl

            if _is_simulation():

                print(
                    f"[BankState] +REALIZED_PNL engine={engine} "
                    f"pnl={pnl:+.2f} "
                    f"pot={_ENGINE_POTS[engine]:.2f}"
                )

        # mark as reconciled (CRITICAL)
        cur.execute("""
            UPDATE orders
               SET bank_reconciled = 1
             WHERE role = 'PARENT'
               AND bank_reconciled = 0
               AND realized_pnl IS NOT NULL
               AND date(closed_at) = date('now','utc')
        """)

        con.commit()
        con.close()


