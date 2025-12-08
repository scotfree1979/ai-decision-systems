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
# 📍 TARGET: engines/risk/budget_manager.py — global engine constants
# 🔎 SEARCH: ENGINES = ["LEGACY", "MSC_EXPLORATORY", "MSC_RISK", "MSC_INPLAY"]
# 📆 PATCHED: 2026-01-19

# ----------------------------------------------------------------------
# Unified Engine Set (v7 Budgeting Model)
# ----------------------------------------------------------------------
ENGINES = [
    "LEGACY",
    "MSC_EXPLORATORY",
    "MSC_RISK",
    "MSC_INPLAY",
    "OVERWATCHER",
    "SAFETY_NET",
]

# Baseline allocations (start-of-day percentages)
BASELINE_PCT = {
    "LEGACY":          0.40,
    "MSC_EXPLORATORY": 0.05,
    "MSC_RISK":        0.05,
    "MSC_INPLAY":      0.05,
    "OVERWATCHER":     0.05,
    "SAFETY_NET":      0.05,
}

# Hard floors (minimum operational runway)
FLOOR_PCT = {
    "LEGACY":          0.30,
    "MSC_EXPLORATORY": 0.05,
    "MSC_RISK":        0.05,
    "MSC_INPLAY":      0.05,
    "OVERWATCHER":     0.05,
    "SAFETY_NET":      0.05,
}

# Dynamic pool (total = 35% of daily allocation)
DYNAMIC_POOL = 0.35

# Exploratory ↔ Legacy performance bonus shift (0 → 10% max)
_bonus_shift = 0   # increases/decreases by 1 step per rebalance

# Initialise working allocations from baseline
_current_allocations = BASELINE_PCT.copy()

# Initialise profitability tracker for raw_perf calculations
_profit_tracker = {
    eng: {"pnl": 0.0, "count": 0}
    for eng in ENGINES
}

# 📍 TARGET: engines/risk/budget_manager.py — add v7 budget table schema
# 🔎 SEARCH: _profit_tracker = {
# 📆 PATCHED: 2026-01-19

# ----------------------------------------------------------------------
# V7 Budget Audit Table (stores ALL reallocation events)
# ----------------------------------------------------------------------
def _ensure_v7_table():
    try:
        con = _auto_conn(rw=True)
        con.execute("""
            CREATE TABLE IF NOT EXISTS budgets_v7(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                date TEXT,
                engine TEXT,
                pct REAL,
                raw_pnl REAL,
                raw_ticks REAL,
                bonus_shift INTEGER
            )
        """)
        con.commit()
        con.close()
    except Exception:
        pass

# ensure table exists at import time
_ensure_v7_table()

# existing profitability tracker follows...
_profit_tracker = {
    eng: {"pnl": 0.0, "count": 0}
    for eng in ENGINES
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

# 📍 TARGET: engines/risk/budget_manager.py — add v7 allocator
# 🔎 SEARCH: #  REBALANCING ENGINE (midnight or on-demand)
# 📆 PATCHED: 2026-01-19

# ============================================================
#  V7 PERFORMANCE-DRIVEN ALLOCATOR (NEW)
# ============================================================

def allocate_with_performance(perf_dict: Dict[str, float],
                              avg_ticks: Dict[str, float],
                              live_bank: float):
    """
    Full v7 allocation engine:
      • Baseline percentages
      • Dynamic pool (35%) weighted by performance
      • Exploratory–Legacy bonus shift (+/- 1 per rebalance)
      • Floors (Legacy=30%, others=5%)
      • Writes full allocation snapshot for review (Option A)
    """

    global _current_allocations, _bonus_shift

    # -----------------------------
    # 1) Compute raw performance
    # -----------------------------
    raw_perf = {}
    for eng in ENGINES:
        pnl = perf_dict.get(eng, 0.0)
        ticks = avg_ticks.get(eng, 0.0)
        raw_perf[eng] = pnl + (0.25 * ticks)

    # -----------------------------
    # 2) Dynamic pool weighting
    # -----------------------------
    # Only legacy + MSC engines participate (not overwatcher/safety_net)
    dyn_engines = [
        "LEGACY", "MSC_EXPLORATORY", "MSC_RISK", "MSC_INPLAY"
    ]

    weights = {eng: max(raw_perf[eng], 0.0) for eng in dyn_engines}
    total = sum(weights.values()) or 1.0
    weights = {eng: weights[eng] / total for eng in dyn_engines}

    dynamic_pct = {
        eng: weights[eng] * DYNAMIC_POOL
        for eng in dyn_engines
    }

    # zero dynamic component for static engines
    dynamic_pct.update({"OVERWATCHER": 0.0, "SAFETY_NET": 0.0})

    # -----------------------------
    # 3) Bonus shift (Exploratory <→ Legacy)
    # -----------------------------
    ex_perf = raw_perf["MSC_EXPLORATORY"]
    le_perf = raw_perf["LEGACY"]

    if ex_perf > le_perf:
        _bonus_shift = min(10, _bonus_shift + 1)
    elif ex_perf < le_perf:
        _bonus_shift = max(0, _bonus_shift - 1)

    # bonus = 0.01 per shift step
    bonus = _bonus_shift / 100.0

    # -----------------------------
    # 4) Combine baseline + bonus + dynamic
    # -----------------------------
    final_pct = {}

    for eng in ENGINES:
        base = BASELINE_PCT[eng]
        dyn = dynamic_pct.get(eng, 0.0)

        if eng == "LEGACY":
            v = base - bonus + dyn
        elif eng == "MSC_EXPLORATORY":
            v = base + bonus + dyn
        else:
            v = base + dyn

        # enforce floors
        v = max(v, FLOOR_PCT[eng])
        final_pct[eng] = v

    # normalise to 1.0
    total_final = sum(final_pct.values()) or 1.0
    final_pct = {eng: v / total_final for eng, v in final_pct.items()}

    # -----------------------------
    # 5) Save + persist snapshot
    # -----------------------------
    with _alloc_lock:
        _current_allocations = final_pct.copy()

    # full audit trail (Option A)
    try:
        con = _auto_conn(rw=True)
        today = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for eng, pct in final_pct.items():
            con.execute("""
                INSERT INTO budgets_v7(date, engine, pct, raw_pnl, raw_ticks, bonus_shift)
                VALUES (?,?,?,?,?,?)
            """, (today, eng, pct,
                  raw_perf.get(eng, 0.0),
                  avg_ticks.get(eng, 0.0),
                  _bonus_shift))
        con.commit()
        con.close()
    except Exception:
        pass

    return final_pct

# 📍 TARGET: engines/risk/budget_manager.py — function _rebalance_allocations
# 🔎 SEARCH: def _rebalance_allocations():
# 📆 PATCHED: 2026-01-19

def _rebalance_allocations():
    """
    V7 Rebalance:
      • Collect today's pnl per engine
      • Use stored available_bank from update_available_budget()
      • Call v7 allocate_with_performance()
    """
    global _last_rebalance_day

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    if _last_rebalance_day == today:
        return

    _last_rebalance_day = today

    # performance source
    perf = _collect_pnl_today()

    # avg ticks not yet available until lanes rebuild
    avg_ticks = {eng: 0.0 for eng in ENGINES}

    live_bank = getattr(BudgetManager, "available_budget", 0.0)

    final_pct = allocate_with_performance(perf, avg_ticks, live_bank)

    print("[BUDGET] Rebalanced allocations (V7):",
          json.dumps(final_pct, indent=2))



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

# 📍 TARGET: engines/risk/budget_manager.py — reporting functions
# 🔎 SEARCH: def report_allocations():
# 📆 PATCHED: 2026-01-19

def report_allocations():
    """
    V7 unified budget report.
    Always prints all 6 engines.
    Hidden engines printed separately if 0 activity.
    """
    final = get_allocations()
    perf = _collect_pnl_today()
    exp = exposure_snapshot()

    print("\n============ V7 ENGINE BUDGET REPORT ============")
    print(f"Day: {datetime.datetime.utcnow().strftime('%Y-%m-%d')}")

    hidden = []

    for eng in ENGINES:
        pct = final.get(eng, 0.0)
        pnl = perf.get(eng, 0.0)
        exposure = exp.get(eng, 0.0)

        if pct == 0 and exposure == 0 and pnl == 0:
            hidden.append(eng)

        print(f"{eng:16s} pct={pct*100:5.1f}%   pnl={pnl:+.2f}   exposure={exposure:.2f}")

    if hidden:
        print("\n(HIDDEN ENGINES — NO ACTIVITY)")
        for h in hidden:
            print(f"  {h}")

    print("=================================================\n")


def report_exposure():
    """
    Maintained for compatibility; prints v7 exposure only.
    """
    exp = exposure_snapshot()
    print("\n=== ENGINE EXPOSURE (V7) ===")
    for eng, v in exp.items():
        print(f"{eng:20s} exposure={v:.2f}")
    print("============================\n")



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
