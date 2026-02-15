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
        rebuild_live_exposure_from_db()   # ← ADD THIS
        _restore_risk_bank_from_market_exposure()
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

def _restore_risk_bank_from_market_exposure():
    """
    Restart-safe capital reconstruction using Betfair floor.
    """

    global _ENGINE_POTS

    try:
        from engines.daily_config import fetch_available_budget
        betfair_balance = float(fetch_available_budget())
    except Exception:
        return

    rows = _compute_market_floor_from_betfair_surface()

    floor = sum(
        float(r.get("true_market_exposure") or 0.0)
        for r in rows
    )

    risk_bank = betfair_balance + floor

    total_pct = sum(_ENGINE_POTS.values()) or 1.0

    for engine in _ENGINE_POTS:
        pct = _ENGINE_POTS[engine] / total_pct
        _ENGINE_POTS[engine] = pct * risk_bank

    print(
        f"[BankState] restart risk rebuild | "
        f"betfair={betfair_balance:.2f} "
        f"+ floor={floor:.2f} "
        f"→ risk_bank={risk_bank:.2f}"
    )


# ======================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🧩 ADD: exposure rehydration on restart
# 📆 PATCHED: 2026-04-12 — restart-safe exposure rebuild
# ======================================================================

def rebuild_live_exposure_from_db():
    """
    Reconstruct engine exposure + open exposure from DB.

    MUST be called at startup AFTER init_from_budget_allocations().

    Rules:
    - Only MATCHED parents
    - Only exposure_released = 0
    - Use required_exposure (authoritative)
    - Do NOT mutate pots
    """

    global _ENGINE_USED, _OPEN_EXPOSURE

    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=False)
    con.row_factory = None
    cur = con.cursor()

    rows = cur.execute("""
        SELECT engine,
               SUM(required_exposure)
        FROM orders
        WHERE role='PARENT'
          AND entry_status='MATCHED'
          AND COALESCE(exposure_released,0)=0
          AND (exit_status IS NULL OR exit_status NOT IN ('CANCELLED','EXPIRED','SETTLED'))
          AND date(opened_at)=date('now','utc')
        GROUP BY engine
    """).fetchall()

    con.close()

    with _LOCK:
        # reset runtime exposure only
        _ENGINE_USED = {eng: 0.0 for eng in _ENGINE_POTS.keys()}
        _OPEN_EXPOSURE = 0.0

        for engine, total in rows:
            amount = _clamp(total or 0.0)

            _ENGINE_USED[engine] = amount
            _OPEN_EXPOSURE += amount

        print(
            f"[BankState] exposure rebuilt | open={_OPEN_EXPOSURE:.2f}"
        )

# ======================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def _compute_market_over_reserve_today(
# 🧩 ACTION: ADD NEW BETFAIR FLOOR FUNCTION (NON-DESTRUCTIVE)
# 📆 PATCHED: 2026-04-XX — Betfair Execution Surface Floor
#
# PURPOSE:
# - Replace SQL parent-based floor
# - Use authoritative Betfair execution surface
# - Only count source='CURRENT'
# - Maintain exact BankState return contract
# - No mutation
# - No redistribution here
# ======================================================================

def _compute_market_floor_from_betfair_surface():
    """
    Authoritative floor from Betfair execution surface.

    Uses:
        betfair_execution_surface
    Filters:
        source = 'CURRENT'

    Returns same structure as _compute_market_over_reserve_today()
    but only includes:
        marketId
        true_market_exposure
    """

    import sqlite3
    from engines.config_paths import autoscalp_db
    from collections import defaultdict

    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    cur = con.cursor()

# ======================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: inside _compute_market_floor_from_betfair_surface()
# 📆 PATCHED: 2026-04-XX — Restore unconditional Betfair floor surface
#
# PURPOSE:
# - Remove marketStartTime time gate
# - Restore floor emission for ALL current Betfair markets
# - Prevent silent zero-floor state
#
# INVARIANT:
# - Floor is computed ONLY from betfair_execution_surface
# - No DB time filtering
# - No scope filtering
# ======================================================================

    rows = cur.execute("""
        SELECT
            marketId,
            selectionId,
            side,
            matched_size,
            avg_price
        FROM betfair_execution_surface
        WHERE source='CURRENT'
    """).fetchall()



    con.close()

    if not rows:
        return []

    # --------------------------------------------------
    # Build per-market runner set
    # --------------------------------------------------
    runners_by_market = defaultdict(set)
    for r in rows:
        runners_by_market[r["marketId"]].add(r["selectionId"])

    # --------------------------------------------------
    # Simulate worst-case per market
    # --------------------------------------------------
    market_pnl = defaultdict(lambda: defaultdict(float))

    for r in rows:

        mid = r["marketId"]
        sid = r["selectionId"]
        side = (r["side"] or "").upper()
        matched = float(r["matched_size"] or 0.0)
        odds = float(r["avg_price"] or 0.0)

        if matched <= 0 or odds <= 0:
            continue

        for winner in runners_by_market[mid]:

            if winner == sid:
                if side == "LAY":
                    pnl = -matched * (odds - 1)
                else:
                    pnl = matched * (odds - 1)
            else:
                if side == "LAY":
                    pnl = matched
                else:
                    pnl = -matched

            market_pnl[mid][winner] += pnl

    results = []

    for mid, outcomes in market_pnl.items():

        worst_loss = 0.0

        for pnl in outcomes.values():
            if pnl < worst_loss:
                worst_loss = pnl

        results.append({
            "marketId": mid,
            "true_market_exposure": -worst_loss
        })

    return results

# ======================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🧩 ADD: compute market over-reserve (LIVE / TODAY)
# 📆 PATCHED: 2026-02-05 — market-aware exposure reconciliation
#
# CONTRACT:
# - READ-ONLY
# - Uses the same proven SQL as offline analysis
# - TODAY (UTC)
# - MATCHED PARENTS ONLY
# ======================================================================

def _compute_market_over_reserve_today():
    """
    Return per-market, per-engine over-reserve information for TODAY (UTC).

    Output rows include:
      marketId
      bankstate_exposure
      true_market_exposure
      over_reserved
      engine
      engine_exposure
      engine_pct
      engine_should_be_returned
    """
    import sqlite3
    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=False)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute("""
        WITH matched_parents AS (
            SELECT
                id,
                marketId,
                selectionId,
                engine,
                side,
                entry_odds,
                entry_stake,
                required_exposure
            FROM orders
            WHERE role = 'PARENT'
              AND entry_status = 'MATCHED'
              AND date(opened_at) = date('now','utc')
        ),

        -- enumerate each possible winning runner
        possible_winners AS (
            SELECT DISTINCT
                marketId,
                selectionId AS winning_selection
            FROM matched_parents
        ),

        -- compute loss if THAT runner wins
        loss_if_wins AS (
            SELECT
                w.marketId,
                w.winning_selection,
                SUM(
                    CASE
                        -- LAY loses if its runner wins
                        WHEN p.side = 'LAY'
                             AND p.selectionId = w.winning_selection
                            THEN p.entry_stake * (p.entry_odds - 1)

                        -- BACK loses if its runner does NOT win
                        WHEN p.side = 'BACK'
                             AND p.selectionId != w.winning_selection
                            THEN p.entry_stake

                        ELSE 0
                    END
                ) AS total_market_loss
            FROM possible_winners w
            JOIN matched_parents p
              ON p.marketId = w.marketId
            GROUP BY w.marketId, w.winning_selection
        ),

        -- true worst-case market exposure
        market_worst_case AS (
            SELECT
                marketId,
                MAX(total_market_loss) AS true_market_exposure
            FROM loss_if_wins
            GROUP BY marketId
        ),

        -- what BankState actually reserved (router pessimism)
        bankstate_exposure AS (
            SELECT
                marketId,
                SUM(required_exposure) AS bankstate_exposure
            FROM matched_parents
            GROUP BY marketId
        ),

        -- exposure per engine (still based on router reservations)
        engine_exposure AS (
            SELECT
                marketId,
                engine,
                SUM(required_exposure) AS engine_exposure
            FROM matched_parents
            GROUP BY marketId, engine
        )

        SELECT
            b.marketId,
            b.bankstate_exposure,
            m.true_market_exposure,
            (b.bankstate_exposure - m.true_market_exposure) AS over_reserved,
            e.engine,
            e.engine_exposure,
            ROUND(e.engine_exposure * 1.0 / b.bankstate_exposure, 6) AS engine_pct,
            ROUND(
                (b.bankstate_exposure - m.true_market_exposure)
                * (e.engine_exposure * 1.0 / b.bankstate_exposure),
                2
            ) AS engine_should_be_returned
        FROM bankstate_exposure b
        JOIN market_worst_case m USING (marketId)
        JOIN engine_exposure e USING (marketId)
        WHERE b.bankstate_exposure > m.true_market_exposure
        ORDER BY over_reserved DESC;

    """).fetchall()

    con.close()
    return rows

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def _reconcile_market_exposure_live(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-04-15 — Per-market proportional refund (engine-accurate)
#
# PURPOSE:
# - Remove global exposure scaling
# - Refund over-reserved capital per market, per engine
# - Preserve engine-level capital integrity
# - Align exactly with router pessimistic reservation model
#
# MODEL:
#   1) Router reserves required_exposure per parent
#   2) BankState accumulates per-engine reservations
#   3) Betfair floor computes true_market_exposure per market
#   4) Over-reserve = reserved - true_floor
#   5) Refund distributed proportionally to engines that funded that market
#
# INVARIANTS:
# - No global scaling
# - No cross-market mixing
# - Never refund more than engine_used
# - _OPEN_EXPOSURE always matches sum(_ENGINE_USED)
# - Idempotent
# ======================================================================================================

def _reconcile_market_exposure_live():

    global _OPEN_EXPOSURE

    rows = _compute_market_over_reserve_today()
    if not rows:
        return []

    total_refund = 0.0

    with _LOCK:

        for r in rows:

            engine = r["engine"]
            refund = float(r.get("engine_should_be_returned") or 0.0)

            if refund <= 0.0:
                continue

            used = _ENGINE_USED.get(engine, 0.0)

            # Safety clamp — never refund more than used
            refund = min(refund, used)

            if refund <= 0.0:
                continue

            _ENGINE_USED[engine] = _clamp(used - refund)
            total_refund += refund

        # Adjust global open exposure once
        if total_refund > 0.0:
            _OPEN_EXPOSURE = _clamp(_OPEN_EXPOSURE - total_refund)

    return rows


# -------------------------------------------------------------------
# OBSERVABILITY REPORT LOOP (REFINED, LOW-NOISE)
# -------------------------------------------------------------------

def _bankstate_report_loop(interval_s: int = 60):
    """
    Unified BankState reporting loop.
    - No behaviour changes
    - No extra noise
    - Integrates market reconciliation cleanly
    """
    while True:
        try:
            with _LOCK:
                now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")
                open_exp = _clamp(_OPEN_EXPOSURE)

                # ==================================================
                # REPORT 1 — BANK STATE SUMMARY
                # ==================================================
                print("\n============ V7 BANK STATE ============")
                print(
                    f"t={now}   "
                    f"open_exposure={open_exp:.2f}   "
                    f"markets={_effective_market_count()}"
                )
                print("\nENGINE            POT      USED     AVAIL")
                print("------------------------------------------")

                tot_pot = tot_used = tot_avail = 0.0

                for engine in sorted(_ENGINE_POTS.keys()):
                    pot = _ENGINE_POTS.get(engine, 0.0)
                    used = _ENGINE_USED.get(engine, 0.0)
                    avail = pot - used

                    tot_pot += pot
                    tot_used += used
                    tot_avail += avail

                    print(
                        f"{engine:<16} "
                        f"{pot:>7.2f}  "
                        f"{used:>7.2f}  "
                        f"{_clamp(avail):>7.2f}"
                    )

                print("------------------------------------------")
                print(
                    f"{'TOTAL':<16} "
                    f"{tot_pot:>7.2f}  "
                    f"{tot_used:>7.2f}  "
                    f"{_clamp(tot_avail):>7.2f}"
                )
                print("==========================================")

                # ==================================================
                # REPORT 2 — ENGINE BUDGET SNAPSHOT (EXISTING)
                # ==================================================
                print("\n============ V7 ENGINE BUDGET ============")
                print(f"day={_utc_day()}\n")
                print("ENGINE            ALLOC%    PNL       EXPOSURE")
                print("----------------------------------------------")

                for engine, pot in _ENGINE_POTS.items():
                    used = _ENGINE_USED.get(engine, 0.0)
                    pct = (pot / tot_pot * 100.0) if tot_pot else 0.0

                    # pnl already reconciled elsewhere
                    pnl = 0.0

                    print(
                        f"{engine:<16} "
                        f"{pct:>6.1f}%   "
                        f"{pnl:>+7.2f}   "
                        f"{used:>7.2f}"
                    )

                print("==============================================")

            # ======================================================
            # REPORT 3 — BETFAIR FLOOR (AUTHORITATIVE)
            # ======================================================
            rec = _compute_market_floor_from_betfair_surface()
            if rec:
                print("\n====== BETFAIR FLOOR (EXECUTION SURFACE) ======\n")

                for r in rec[:5]:
                    print(
                        f"market={r['marketId']} "
                        f"floor={float(r['true_market_exposure']):.2f}"
                    )

                print("===========================================")


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
    Then reconcile market over-reserve authoritatively.
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
        raise RuntimeError(
            f"[BankState] invariant violation: required_exposure missing for parent_id={parent_id}"
        )

    amount = _clamp(row[0])

    # --------------------------------------------------
    # 1️⃣ RAW RESERVATION (router pessimism preserved)
    # --------------------------------------------------
    with _LOCK:
        _OPEN_EXPOSURE += amount
        _ENGINE_USED[engine] = _ENGINE_USED.get(engine, 0.0) + amount

        print(
            f"[BankState] +RESERVE engine={engine} "
            f"amount={amount:.2f} "
            f"open={_OPEN_EXPOSURE:.2f}"
        )

    # --------------------------------------------------
    # 2️⃣ AUTHORITATIVE MARKET RECONCILIATION
    # --------------------------------------------------
    refunds = _reconcile_market_exposure_live()

    if refunds:
        print(
            f"[BankState] reconciliation applied | "
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


