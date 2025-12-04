#!/usr/bin/env python3
# engines/risk/budget_manager.py
"""
Unified Budget Manager v10
--------------------------
This module becomes the single source of truth for:
    • Engine allocations (Legacy / MSC-Exploratory / MSC-Risk / MSC-InPlay)
    • Daily rebalancing based on profitability
    • Intraday exposure checking (delegates to BankState)
    • Drawdown-smoothing of allocations
    • Exposure throttling
Nothing else in the system needs to change.
"""

from __future__ import annotations
import sqlite3, threading, time, datetime, json
from typing import Dict, Tuple

# DAL connectors
from engines.config_paths import auto_conn as _auto_conn
from engines.config_paths import open_bets_db as _bets
from engines.mastery import event_sink


# ============================================================
#  GLOBAL CONSTANTS AND STATE
# ============================================================

ENGINES = ["LEGACY", "MSC_EXPLORATORY", "MSC_RISK", "MSC_INPLAY"]

# base defaults (percentages)
DEFAULT_ALLOCATIONS = {
    "LEGACY":          0.40,
    "MSC_EXPLORATORY": 0.20,
    "MSC_RISK":        0.20,
    "MSC_INPLAY":      0.20,
}

# minimum runway per engine (keeps them firing)
MIN_ENGINE_PCT = 0.05    # 5%

# drawdown smoothing coefficient
ALPHA = 0.25

# rebalancing control
_last_rebalance_day = None
_alloc_lock = threading.Lock()

_current_allocations = DEFAULT_ALLOCATIONS.copy()

# stored profitability snapshot
_profit_tracker = {
    "LEGACY": {"pnl": 0.0, "count": 0},
    "MSC_EXPLORATORY": {"pnl": 0.0, "count": 0},
    "MSC_RISK": {"pnl": 0.0, "count": 0},
    "MSC_INPLAY": {"pnl": 0.0, "count": 0},
}


# ============================================================
#  PROFIT TRACKING (reads from orders)
# ============================================================

def _collect_pnl_today() -> Dict[str, float]:
    """
    Aggregate realized pnl per engine for the current UTC day.
    """
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT engine, SUM(COALESCE(net_pl,0)) AS pnl
          FROM orders
         WHERE date(opened_at)=date('now','utc')
         GROUP BY engine
    """).fetchall()
    con.close()

    out = {e: 0.0 for e in ENGINES}
    for r in rows:
        e = (r["engine"] or "").upper()
        if e in out:
            out[e] = float(r["pnl"] or 0.0)
    return out


# ============================================================
#  REBALANCING ENGINE (midnight or on-demand)
# ============================================================

def _rebalance_allocations():
    global _current_allocations, _profit_tracker

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    global _last_rebalance_day
    if _last_rebalance_day == today:
        return  # already done for today

    _last_rebalance_day = today

    # 1) collect PNL
    pnls = _collect_pnl_today()

    # 2) update smoothed profitability
    for eng in ENGINES:
        prev = _profit_tracker[eng]["pnl"]
        new  = pnls.get(eng, 0.0)

        # exponential smoothing
        smooth = ALPHA * new + (1 - ALPHA) * prev

        _profit_tracker[eng]["pnl"] = smooth

    # 3) convert smoothed pnl → weights (softmax-like absolute weight)
    raw_scores = {eng: abs(_profit_tracker[eng]["pnl"]) for eng in ENGINES}

    total = sum(raw_scores.values()) or 1.0
    weights = {eng: raw_scores[eng] / total for eng in ENGINES}

    # 4) enforce minimum runway
    adjusted = {}
    residual = 1.0
    for eng in ENGINES:
        adjusted[eng] = max(weights[eng], MIN_ENGINE_PCT)
        residual -= adjusted[eng]

    # If negative, normalise downward proportionally
    if residual < 0:
        # scale down to sum to 1.0
        factor = 1.0 / sum(adjusted.values())
        for eng in ENGINES:
            adjusted[eng] = adjusted[eng] * factor
        residual = 0.0

    # 5) final allocation
    with _alloc_lock:
        _current_allocations = adjusted.copy()

    print("[BUDGET] Rebalanced allocations:", json.dumps(_current_allocations, indent=2))


def midnight_rebalance_if_needed():
    """
    Called at tool start; then LiveRouter calls this once during init.
    If we have crossed UTC midnight since last execution, rebalances.
    """
    _rebalance_allocations()


# ============================================================
#  PUBLIC API — allocation queries
# ============================================================

def get_allocations() -> Dict[str, float]:
    """
    Return current engine percentages.
    """
    with _alloc_lock:
        return _current_allocations.copy()


def allocation_for_engine(engine: str) -> float:
    return get_allocations().get(engine.upper(), 0.0)


# ============================================================
#  BUDGET DISPATCH FOR DYNAMIC STAKE
# ============================================================

def allowed_stake_for_engine(engine: str, live_bank: float) -> float:
    """
    Returns the *absolute* maximum stake that this engine
    should allocate according to its percentage share.

    LiveRouter's dyn stake applies further per-letter logic.
    """
    pct = allocation_for_engine(engine)
    return max(0.0, pct * float(live_bank))


# ============================================================
#  ENGINE: exposure monitoring (optional signalling)
# ============================================================

def exposure_snapshot() -> Dict[str, float]:
    """
    Return total active stake per engine.
    """
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT engine, SUM(entry_stake) AS st
          FROM orders
         WHERE entry_status='MATCHED'
           AND (exit_status IS NULL OR exit_status<>'MATCHED')
         GROUP BY engine
    """).fetchall()
    con.close()

    out = {e: 0.0 for e in ENGINES}
    for r in rows:
        e = (r["engine"] or "").upper()
        if e in out:
            out[e] = float(r["st"] or 0.0)
    return out


def signal_if_over_budget(live_bank: float):
    """
    Sends events to Mastery if any engine exceeds its allocation.
    """
    caps = get_allocations()
    exp  = exposure_snapshot()

    for eng in ENGINES:
        allowed = caps[eng] * live_bank
        used = exp.get(eng, 0.0)
        if used > allowed:
            event_sink.on_decision({
                "type": "engine_over_budget",
                "engine": eng,
                "used": used,
                "allowed": allowed,
                "ts": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
            })
            print(f"[BUDGET] ⚠️ Engine {eng} over budget ({used:.2f} > {allowed:.2f})")


# ============================================================
#  DYNAMIC STAKE DELEGATION (LIVE ROUTER USES THIS)
# ============================================================

def global_stake_limit_for(letter: str, engine: str, live_bank: float) -> float:
    """
    Returns the maximum stake this order is allowed to use.
    LiveRouter’s calc_dynamic_stake still determines the actual stake.
    """
    try:
        engine = engine.upper()
        return allocation_for_engine(engine) * float(live_bank)
    except Exception:
        return 0.0


# ============================================================
#  MANUAL TERMINAL REPORTS (Python-free runnable)
# ============================================================

def report_allocations():
    """
    Print engine allocations and today's pnl.
    """
    pnls = _collect_pnl_today()
    alloc = get_allocations()

    print("\n=== ENGINE ALLOCATION REPORT ===")
    print(f"Day: {datetime.datetime.utcnow().strftime('%Y-%m-%d')}")
    for eng in ENGINES:
        print(f"{eng:20s} alloc={alloc[eng]*100:5.1f}%   pnl={pnls.get(eng,0):+.2f}")
    print("================================\n")


def report_exposure():
    """
    Print exposure per engine.
    """
    exp = exposure_snapshot()
    print("\n=== ENGINE EXPOSURE REPORT ===")
    for eng, v in exp.items():
        print(f"{eng:20s} exposure={v:.2f}")
    print("================================\n")

# Add near bottom of budget_manager.py

def start_watcher(interval_s: int = 60):
    """
    Legacy compatibility.
    Old GUI expects this function.
    New system does not require a watcher, but we provide:
      • one-time init
      • periodic allocation report (non-critical)
    """
    init_budget_manager()

    def _loop():
        while True:
            try:
                report_allocations()
                report_exposure()
            except Exception:
                pass
            time.sleep(max(10, interval_s))

    t = threading.Thread(target=_loop, name="BudgetWatcher", daemon=True)
    t.start()

    print("[BUDGET] Legacy start_watcher() shim active (V10-compatible)")



# ============================================================
#  CLEAN STARTUP ENTRYPOINT
# ============================================================

def init_budget_manager():
    """
    Called by GUI/orchestrator at startup.
    Ensures daily rebalance executes once per calendar day.
    """
    midnight_rebalance_if_needed()


# CLI =========================================================
if __name__ == "__main__":
    import sys

    if len(sys.argv) == 1:
        report_allocations()
        report_exposure()

    elif sys.argv[1] == "rebalance":
        _rebalance_allocations()
        report_allocations()

    elif sys.argv[1] == "alloc":
        report_allocations()

    elif sys.argv[1] == "exposure":
        report_exposure()

    else:
        print("Usage:")
        print("  python budget_manager.py")
        print("  python budget_manager.py rebalance")
        print("  python budget_manager.py alloc")
        print("  python budget_manager.py exposure")
