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

_last_rebalance_day = None
_alloc_lock = threading.Lock()

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
    "MSC_RISK":        0.30,  # Primary engine (dominant allocation)
    "LEGACY":          0.10,  # Anchors only
    "MSC_EXPLORATORY": 0.20,  # Reduced exploratory bleed
    "MSC_INPLAY":      0.40,  # In-play ladder capital
    "OVERWATCHER":     0.00,  # Protective hedge
    "SAFETY_NET":      0.00,  # System safety buffer
}


# Hard floors (minimum operational runway)
FLOOR_PCT = {
    "MSC_RISK":        0.25,
    "LEGACY":          0.10,
    "MSC_EXPLORATORY": 0.05,
    "MSC_INPLAY":      0.20,
    "OVERWATCHER":     0.00,
    "SAFETY_NET":      0.00,
}



# Dynamic pool (total = 7% of daily allocation)
DYNAMIC_POOL = 0.07

# Exploratory ↔ Legacy performance bonus shift (0 → 10% max)
_bonus_shift = 0   # increases/decreases by 1 step per rebalance

# Initialise working allocations from baseline
_current_allocations = BASELINE_PCT.copy()

# Initialise profitability tracker for raw_perf calculations
_profit_tracker = {
    eng: {"pnl": 0.0, "count": 0}
    for eng in ENGINES
}

def _collect_pnl_today():
    """
    Canonical realised P&L per engine for TODAY (UTC),
    using Betfair-cleared settlements (dashboard truth).
    """
    import sqlite3
    from engines.config_paths import settlements_db_path

    out = {}

    con = sqlite3.connect(settlements_db_path())
    con.row_factory = sqlite3.Row

    # Attach autoscalp DB to resolve engine
    con.execute("ATTACH DATABASE 'data/autoscalp_gui.db' AS auto")

    rows = con.execute("""
        SELECT
            o.engine AS engine,
            ROUND(SUM(c.profit), 2) AS pnl
        FROM bf_cleared_orders c
        JOIN auto.orders o
          ON (
               o.customerOrderRef = c.customerOrderRef
               OR o.betId = c.betId
             )
        WHERE date(c.settledDate) = date('now','utc')
          AND o.engine IS NOT NULL
        GROUP BY o.engine
    """).fetchall()

    for r in rows:
        out[r["engine"]] = float(r["pnl"] or 0.0)

    con.close()
    return out



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

def _collect_pnl_today():
    """
    Realised P&L by engine (dashboard-truth).
    Source:
      - settlements.db (money)
      - autoscalp_gui.db (engine attribution)
    Scope:
      - today (UTC)
    """
    import sqlite3
    import datetime
    from pathlib import Path

    SETTLEMENTS_DB = Path("data/settlements.db")
    AUTOSCALP_DB   = Path("data/autoscalp_gui.db")

    day = datetime.datetime.utcnow().strftime("%Y-%m-%d")

    con = sqlite3.connect(SETTLEMENTS_DB)
    con.row_factory = sqlite3.Row

    # Attach autoscalp for engine attribution
    con.execute(f"ATTACH DATABASE '{AUTOSCALP_DB}' AS auto")

    sql = """
    SELECT
        COALESCE(a.engine, 'UNATTRIBUTED') AS engine,
        SUM(COALESCE(s.profit,0.0) - COALESCE(s.commission,0.0)) AS pnl
    FROM bf_cleared_orders s
    LEFT JOIN auto.orders a
      ON a.customerOrderRef = s.customerOrderRef
    WHERE s.settledDate IS NOT NULL
      AND date(datetime(s.settledDate,'utc')) = ?
    GROUP BY 1
    """

    rows = con.execute(sql, (day,)).fetchall()
    con.close()

    # Build dict with float values
    out = {}
    for r in rows:
        out[r["engine"]] = float(r["pnl"] or 0.0)

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

from engines.daily_config import get_session_token

def _rebalance_allocations():
    global _last_rebalance_day

    # ⛔ Do NOT rebalance until we actually have a session token
    if not get_session_token():
        print("[BUDGET] defer: no session token yet — skipping rebalance")
        return

    today = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    if _last_rebalance_day == today:
        return

    _last_rebalance_day = today

    perf = _collect_pnl_today()
    avg_ticks = {eng: 0.0 for eng in ENGINES}

    # === PATCH START =====================================
    # 📆 PATCHED: 2026-02-10 — Solve circular daily_config import
    # Lazy import avoids daily_config → bank_state → budget_manager loop
    try:
        from engines.daily_config import fetch_available_budget

        raw_bank = float(fetch_available_budget())

        # ============================================================
        # RISK BANK FLOOR
        # ------------------------------------------------------------
        # The trading system operates on a fixed risk envelope.
        # If cash balance drops below this floor, risk bank remains.
        # ============================================================

        RISK_BANK_FLOOR = 300.0

        live_bank = max(raw_bank, RISK_BANK_FLOOR)

        if raw_bank < RISK_BANK_FLOOR:
            print(
                f"[BUDGET] risk floor applied: raw_bank={raw_bank:.2f} → risk_bank={live_bank:.2f}"
            )

    except Exception as e:
        print(f"[BUDGET] warn: could not fetch live bank ({e}) — using fallback 0.0")
        live_bank = 0.0
    # === PATCH END =======================================


    final_pct = allocate_with_performance(perf, avg_ticks, live_bank)

    # === PATCH START =====================================================
    # 📍 TARGET: engines/risk/budget_manager.py:_rebalance_allocations
    # 📆 PATCHED: 2026-02-12 — Persist daily engine allocations
    # PURPOSE:
    #   • Make BudgetManager output durable
    #   • Single source of truth for BankState
    # ===============================================================

    try:
        from engines.config_paths import open_auto_db

        con = open_auto_db(rw=True)
        cur = con.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS budget_allocations (
                day TEXT NOT NULL,
                engine TEXT NOT NULL,
                pct REAL NOT NULL,
                bank REAL NOT NULL,
                pot REAL NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (day, engine)
            )
        """)

        day = today

        for engine, pct in final_pct.items():
            pot = float(live_bank) * float(pct)

            cur.execute("""
                INSERT INTO budget_allocations (
                    day, engine, pct, bank, pot, created_at
                )
                VALUES (?, ?, ?, ?, ?, datetime('now','utc'))
                ON CONFLICT(day, engine) DO UPDATE SET
                    pct = excluded.pct,
                    bank = excluded.bank,
                    pot = excluded.pot,
                    created_at = excluded.created_at
            """, (day, engine, float(pct), float(live_bank), float(pot)))

        con.commit()
        con.close()

        print("[BUDGET] allocations persisted to budget_allocations")

    except Exception as e:
        print(f"[BUDGET] ERROR persisting allocations: {e}")

    # === PATCH END =======================================================


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
    V7 exposure snapshot — runtime truth.
    """
    try:
        from engines.live import bank_state
        return bank_state.get_engine_used_map()
    except Exception:
        return {}



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
    perf = _collect_pnl_today()      # ✅ now real PnL
    exp  = exposure_snapshot()

    print("\n============ V7 ENGINE BUDGET REPORT ============")
    print(f"Day: {datetime.datetime.utcnow().strftime('%Y-%m-%d')}")

    hidden = []

    for eng in ENGINES:
        pct      = final.get(eng, 0.0)
        pnl      = perf.get(eng, 0.0)
        exposure = exp.get(eng, 0.0)

        if pct == 0 and exposure == 0 and pnl == 0:
            hidden.append(eng)

        print(
            f"{eng:16s} "
            f"pct={pct*100:5.1f}%   "
            f"pnl={pnl:+.2f}   "
            f"exposure={exposure:.2f}"
        )

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
