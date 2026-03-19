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
from datetime import datetime
import datetime
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
    "MSC_UNIFIED",
    "MSC_BLUEPRINT",
    "MSC_CONTEXT",
    "MSC_STRUCTURE",
    "MSC_META",
    "LEGACY",
    "MSC_EXPLORATORY",
    "MSC_RISK",
    "MSC_INPLAY",
    "OVERWATCHER",
    "SAFETY_NET",
]

# Baseline allocations (start-of-day percentages)
BASELINE_PCT = {
    "MSC_UNIFIED":     0.19,  # New Primary engine (new dominant allocation)
    "MSC_BLUEPRINT":   0.19,  # 1 0f 4 New Split test engine (new dominant allocation)
    "MSC_CONTEXT":     0.19,  # 2 0f 4 New Split test engine (new dominant allocation)
    "MSC_STRUCTURE":   0.19,  # 3 0f 4 New Split test engine (new dominant allocation)
    "MSC_META":        0.19,  # 4 0f 4 New Split test engine (new dominant allocation)
    "MSC_RISK":        0.00,  # Primary engine (dominant allocation)
    "LEGACY":          0.00,  # Anchors only
    "MSC_EXPLORATORY": 0.00,  # Reduced exploratory bleed
    "MSC_INPLAY":      0.00,  # In-play ladder capital
    "OVERWATCHER":     0.00,  # Protective hedge
    "SAFETY_NET":      0.05,  # System safety buffer
}


# Hard floors (minimum operational runway)
FLOOR_PCT = {
    "MSC_UNIFIED":     0.05,
    "MSC_BLUEPRINT":   0.05,  
    "MSC_CONTEXT":     0.05,  
    "MSC_STRUCTURE":   0.05,  
    "MSC_META":        0.05,
    "LEGACY":          0.00,
    "MSC_EXPLORATORY": 0.00,
    "MSC_INPLAY":      0.00,
    "OVERWATCHER":     0.00,
    "SAFETY_NET":      0.00,
}

# ======================================================================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🔎 SEARCH: BASELINE_PCT =
# 🧩 ACTION: INSERT ABOVE
# 📆 PATCHED: 2026-03-18 — Allocation Profiles (discrete strategy layer)
#
# PURPOSE:
# - Define fixed allocation structures
# - Enable daily profile selection
# - Replace dynamic % calculation
# ======================================================================================================

# --------------------------------------------------
# 🎯 ALLOCATION PROFILES (TOTAL ≈ 95%)
# --------------------------------------------------
ALLOCATION_PROFILES = {

    "P1_EXPLORATION": {
        "MSC_UNIFIED":   0.20,
        "MSC_BLUEPRINT": 0.20,
        "MSC_CONTEXT":   0.20,
        "MSC_STRUCTURE": 0.15,
        "MSC_META":      0.15,
    },

    "P2_BALANCED": {
        "MSC_UNIFIED":   0.19,
        "MSC_BLUEPRINT": 0.19,
        "MSC_CONTEXT":   0.19,
        "MSC_STRUCTURE": 0.19,
        "MSC_META":      0.18,
    },

    "P3_STRUCTURE_TILT": {
        "MSC_UNIFIED":   0.15,
        "MSC_BLUEPRINT": 0.15,
        "MSC_CONTEXT":   0.15,
        "MSC_STRUCTURE": 0.25,
        "MSC_META":      0.25,
    },

    "P4_META_DOMINANT": {
        "MSC_UNIFIED":   0.10,
        "MSC_BLUEPRINT": 0.10,
        "MSC_CONTEXT":   0.15,
        "MSC_STRUCTURE": 0.30,
        "MSC_META":      0.30,
    },

    "P5_EXPLOIT": {
        "MSC_UNIFIED":   0.05,
        "MSC_BLUEPRINT": 0.05,
        "MSC_CONTEXT":   0.10,
        "MSC_STRUCTURE": 0.40,
        "MSC_META":      0.35,
    },
}

# Dynamic pool (total = 10% of daily allocation)
DYNAMIC_POOL = 0.00

# Exploratory ↔ Legacy performance bonus shift (0 → 10% max)
_bonus_shift = 0   # increases/decreases by 1 step per rebalance

# Initialise working allocations from baseline
_current_allocations = BASELINE_PCT.copy()

# Initialise profitability tracker for raw_perf calculations
_profit_tracker = {
    eng: {"pnl": 0.0, "count": 0}
    for eng in ENGINES
}

# ======================================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🧩 ADD: Time-of-day allocation controller
# ======================================================================

def _get_time_phase_profile() -> str:
    """
    Determines base allocation profile from market timing.
    Uses GLOBAL market context (earliest active market).
    """

    try:
        from engines.decision_engine.decide_once.scope import scope_snapshot

        sc = scope_snapshot()

        # Collect all TTO values
        ttos = []

        for row in sc.get("pre_near", []):
            ttos.append(float(row[1]))

        for row in sc.get("pre_far", []):
            ttos.append(float(row[1]))

        for row in sc.get("in_play", []):
            ttos.append(float(row[1]))

        if not ttos:
            return "P1_EXPLORATION"  # default overnight

        nearest_tto = min(ttos)

        # --------------------------------------------------
        # PHASE LOGIC
        # --------------------------------------------------

        if nearest_tto > 60:
            return "P1_EXPLORATION"   # early day

        if 20 < nearest_tto <= 60:
            return "P2_BALANCED"

        if 5 < nearest_tto <= 20:
            return "P3_STRUCTURE_TILT"

        if 0 < nearest_tto <= 5:
            return "P4_META_DOMINANT"

        if nearest_tto <= 0:
            return "P5_EXPLOIT"

    except Exception:
        return "P1_EXPLORATION"

# ======================================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🧩 ADD: Market Cadence Engine (schedule-driven)
# ======================================================================

def _build_market_schedule():

    try:
        from engines.config_paths import connect_db

        con = connect_db(ro=True)
        rows = con.execute("""
            SELECT DISTINCT marketId, marketStartTime
            FROM bets
            WHERE date(marketStartTime) = date('now','utc')
            ORDER BY marketStartTime ASC
        """).fetchall()
        con.close()

        markets = [
            (str(r[0]), str(r[1]))
            for r in rows
        ]

        return markets

    except Exception:
        return []

# ======================================================================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🔎 SEARCH: def _get_market_cadence():
# 🧩 ACTION: FULL REPLACE (clean, no scope, correct)
# ======================================================================================================

def _get_market_cadence():

    markets = _build_market_schedule()

    if not markets:
        return "P1_EXPLORATION"

    total = len(markets)

# === PATCH START ============================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🧩 FIX: timezone-safe datetime comparison
# ============================================================================

    from datetime import timezone

    now = datetime.datetime.now(timezone.utc)

# === PATCH END ==============================================================

    current_index = 0

    for i, (_, ts) in enumerate(markets):
        try:
            off = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue

        if off >= now:
            current_index = i
            break

    chunk_size = max(1, total // 5)
    chunk = current_index // chunk_size

    if chunk == 0:
        return "P1_EXPLORATION"
    if chunk == 1:
        return "P2_BALANCED"
    if chunk == 2:
        return "P3_STRUCTURE_TILT"
    if chunk == 3:
        return "P4_META_DOMINANT"

    return "P5_EXPLOIT"
# ======================================================================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🧩 ACTION: ADD — load allocations from DB (authoritative)
# 📆 PATCHED: 2026-03-18 — remove baseline dependency
# ======================================================================================================

def _load_allocations_from_db():

    from engines.config_paths import open_auto_db

    try:
        con = open_auto_db(rw=False)
        rows = con.execute("""
            SELECT engine, pct
            FROM budget_allocations
            WHERE day = date('now','utc')
        """).fetchall()
        con.close()

        if not rows:
            return False

        alloc = {r[0]: float(r[1]) for r in rows}

        with _alloc_lock:
            global _current_allocations
            _current_allocations = alloc

        print("[BUDGET] loaded allocations from DB")

        return True

    except Exception as e:
        print(f"[BUDGET] load failed: {e}")
        return False

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

# ======================================================================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🔎 SEARCH: def allocate_with_performance
# 🧩 ACTION: INSERT ABOVE
# 📆 PATCHED: 2026-03-18 — Profile selection engine
#
# PURPOSE:
# - Choose allocation structure based on performance
# - Deterministic, stable
# ======================================================================================================

def _select_allocation_profile(perf: Dict[str, float]) -> str:

    total_pnl = sum(perf.values())

    structure = perf.get("MSC_STRUCTURE", 0.0)
    meta = perf.get("MSC_META", 0.0)

    # --------------------------------------------------
    # LOSS → reset
    # --------------------------------------------------
    if total_pnl < 0:
        return "P1_EXPLORATION"

    # --------------------------------------------------
    # STRUCTURE DOMINANT
    # --------------------------------------------------
    if structure > max(perf.values()):
        return "P3_STRUCTURE_TILT"

    # --------------------------------------------------
    # STRUCTURE + META DOMINANT
    # --------------------------------------------------
    if (structure + meta) > (0.6 * total_pnl):
        return "P5_EXPLOIT"

    # --------------------------------------------------
    # DEFAULT
    # --------------------------------------------------
    return "P2_BALANCED"

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
    
    # --------------------------------------------------
    # REBALANCE EVERY N MARKETS
    # --------------------------------------------------

    markets = _build_market_schedule()
    total = len(markets)

    rebalance_points = [
        int(total * 0.2),
        int(total * 0.4),
        int(total * 0.6),
        int(total * 0.8),
    ]

    # --------------------------------------------------
    # CURRENT MARKET INDEX (DB ONLY — NO SCOPE)
    # --------------------------------------------------
# === PATCH START ============================================================
# 📍 TARGET: engines/risk/budget_manager.py
# 🧩 FIX: timezone-safe datetime comparison
# ============================================================================

    from datetime import timezone

    now = datetime.datetime.now(timezone.utc)

# === PATCH END ==============================================================

    current_index = 0

    for i, (_, ts) in enumerate(markets):
        try:
            off = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except Exception:
            continue

        if off >= now:
            current_index = i
            break

    if not any(current_index >= p for p in rebalance_points):
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


    # --------------------------------------------------
    # PROFILE-BASED ALLOCATION (NEW)
    # --------------------------------------------------
    cadence_profile = _get_market_cadence()

    # Only override with performance AFTER learning phase
    if current_index > (len(markets) * 0.3) and sum(perf.values()) > 0:
        profile_id = _select_allocation_profile(perf)
    else:
        profile_id = cadence_profile

    profile = ALLOCATION_PROFILES.get(profile_id, {})

    final_pct = {}

    for eng in ENGINES:
        final_pct[eng] = float(profile.get(eng, 0.0))

    # Normalise to 95% (leave safety buffer)
    TARGET_TOTAL = 0.95

    active_engines = [
        "MSC_UNIFIED",
        "MSC_BLUEPRINT",
        "MSC_CONTEXT",
        "MSC_STRUCTURE",
        "MSC_META",
    ]

    total_active = sum(final_pct[e] for e in active_engines) or 1.0
    scale = TARGET_TOTAL / total_active

    for e in active_engines:
        final_pct[e] *= scale

    # SAFETY stays fixed
    final_pct["SAFETY_NET"] = 0.05

        # ======================================================================================================
        # 📍 TARGET: engines/risk/budget_manager.py:_rebalance_allocations
        # 🔎 ANCHOR: after final_pct constructed
        # 🧩 ACTION: SET runtime allocations (authoritative)
        # 📆 PATCHED: 2026-03-18 — Fix allocations not propagating to runtime
        #
        # WHY:
        # - get_allocations() reads _current_allocations
        # - rebalance was NOT updating it
        # - system stuck on BASELINE
        #
        # RESULT:
        # - runtime allocations now match rebalance output
        # ======================================================================================================

    with _alloc_lock:
        _current_allocations = final_pct.copy()


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
                profile_id TEXT
                bank REAL NOT NULL,
                pot REAL NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (day, engine)
            )
        """)

        day = today

        for engine, pct in final_pct.items():
            pot = float(live_bank) * float(pct)

            # ======================================================================================================
            # 📍 TARGET: engines/risk/budget_manager.py
            # 🔎 SEARCH: INSERT INTO budget_allocations
            # 🧩 ACTION: REPLACE
            # 📆 PATCHED: 2026-03-18 — Persist profile_id with allocations
            #
            # PURPOSE:
            # - Track which profile was used each day
            # - Enables audit + evolution
            # ======================================================================================================

            cur.execute("""
                INSERT INTO budget_allocations (
                    day, engine, pct, bank, pot, profile_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, datetime('now','utc'))
                ON CONFLICT(day, engine) DO UPDATE SET
                    pct = excluded.pct,
                    bank = excluded.bank,
                    pot = excluded.pot,
                    profile_id = excluded.profile_id,
                    created_at = excluded.created_at
            """, (
                day,
                engine,
                float(pct),
                float(live_bank),
                float(pot),
                profile_id,
            ))
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

    with _alloc_lock:

        # If empty → lazy load
        if not _current_allocations:
            if not _load_allocations_from_db():
                return {}

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

    # 1️⃣ Try load existing allocations
    loaded = _load_allocations_from_db()

    # 2️⃣ If none exist → run rebalance
    if not loaded:
        _rebalance_allocations()


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
