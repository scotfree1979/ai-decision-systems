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
        print("[BankState] initialised from budget_allocations")
    except Exception as e:
        print(f"[BankState][WARN] init failed: {e}")



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

_MAX_CONCURRENT_MARKETS = 8   # ← your chosen cap

def _effective_market_count() -> int:
    """
    Compute effective market concurrency using weighted liquidity pressure.

    Each market contributes fractional pressure based on how tradable it is.
    This works consistently for early, peak, and late trading periods.
    """

    try:
        buckets = {
            "in_play":  (_SCOPE_STATE.get("in_play", []) or [], 1.0),
            "near20":   (_SCOPE_STATE.get("near20", []) or [], 0.7),
            "near60":   (_SCOPE_STATE.get("near60", []) or [], 0.4),
            "next5":    (_SCOPE_STATE.get("next5", []) or [], 0.2),
        }

        weighted_sum = 0.0

        for markets, weight in buckets.values():
            weighted_sum += len(markets) * weight

        # Floor + cap
        effective = int(round(weighted_sum))

        if effective < 3:
            return 3

        return min(effective, _MAX_CONCURRENT_MARKETS)

    except Exception:
        # Fail-safe: never block trading
        return 3



# -------------------------------------------------------------------
# INITIALISATION (ONCE PER UTC DAY)
# -------------------------------------------------------------------

def init_from_budget_allocations(day: str | None = None) -> None:
    """
    Initialise engine pots from BudgetManager output.
    """
    global _ENGINE_POTS, _ENGINE_AVAILABLE

    from engines.config_paths import open_auto_db
    from datetime import datetime

    if not day:
        day = datetime.utcnow().strftime("%Y-%m-%d")

    con = open_auto_db(rw=False)
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

    print(f"[BANKSTATE] pots loaded from budget_allocations ({day})")


# -------------------------------------------------------------------
# READ API (USED BY ROUTER)
# -------------------------------------------------------------------

def get_engine_pot(engine: str) -> float:
    """
    Engine pot AFTER scope-aware concurrency scaling.
    Source: BudgetManager allocations.
    """
    with _LOCK:
        base_pot = _ENGINE_POTS.get(engine, 0.0)
        divisor = _effective_market_count()
        return _clamp(base_pot / float(divisor))


def get_open_exposure() -> float:
    with _LOCK:
        return _clamp(_OPEN_EXPOSURE)





def get_engine_available(engine: str) -> float:
    """
    Available capital for this engine RIGHT NOW,
    respecting scope-aware market concurrency.
    """
    with _LOCK:
        pot  = get_engine_pot(engine)
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


