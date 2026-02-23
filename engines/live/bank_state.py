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
# 🔎 ANCHOR: below _compute_market_floor_from_betfair_surface()
# 🧩 ACTION: ADD unmatched working capital surface (engine-split)
# 📆 PATCHED: 2026-04-XX — Separate unmatched from floor
#
# PURPOSE:
# - Compute unmatched liability per engine
# - Does NOT affect floor
# - Pure Betfair execution surface
# - Read-only
# ======================================================================================================

def _compute_engine_unmatched_working_capital():

    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=False)
    con.row_factory = None
    cur = con.cursor()

    rows = cur.execute("""
        SELECT
            o.engine,
            SUM(
                CASE
                    WHEN o.side = 'LAY'
                        THEN o.entry_stake * (o.entry_odds - 1)
                    ELSE
                        o.entry_stake
                END
            ) AS working_capital
        FROM orders o
        JOIN bets b ON b.marketId = o.marketId
        WHERE o.role = 'PARENT'
          AND o.entry_status IN ('PLACED')
          AND (o.exit_status IS NULL OR o.exit_status NOT IN ('CANCELLED','VOID','SETTLED','MATCHED','EXPIRED'))
          AND date(o.opened_at) = date('now','utc')
          AND (julianday(b.marketStartTime) - julianday('now','utc')) > 0
        GROUP BY o.engine
        ORDER BY o.engine;
    """).fetchall()

    con.close()

    return {
        engine: float(amount or 0.0)
        for engine, amount in rows
    }

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

# === PATCH START ============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 REPLACE: rebuild_live_exposure_from_db
# 📆 PATCHED: 2026-04-19 — rebuild from persistent ledger
# ============================================================================

def rebuild_live_exposure_from_db():

    global _ENGINE_USED, _OPEN_EXPOSURE

    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=False)
    con.row_factory = None
    cur = con.cursor()

    rows = cur.execute("""
        SELECT engine, SUM(reserved_amount)
          FROM bank_ledger
         WHERE active = 1
         GROUP BY engine
    """).fetchall()

    con.close()

    with _LOCK:
        _ENGINE_USED = {eng: 0.0 for eng in _ENGINE_POTS.keys()}
        _OPEN_EXPOSURE = 0.0

        for engine, total in rows:
            amount = _clamp(max(0.0, total or 0.0))
            _ENGINE_USED[engine] = amount
            _OPEN_EXPOSURE += amount

        print(f"[BankState] exposure rebuilt (ledger) | open={_OPEN_EXPOSURE:.2f}")

# === PATCH END ==============================================================


def _compute_market_floor_from_betfair_surface():
    """
    Authoritative floor from Betfair CURRENT surface (Table 1).

    No DB surface.
    No persistence.
    Pure Betfair truth.

    Returns:
        [
            {
                "marketId": str,
                "true_market_exposure": float
            }
        ]
    """

    from engines.daily_config import get_app_key
    from engines.live.live_router import _keys
    import requests
    import json
    from collections import defaultdict

    app_key, token = _keys()
    if not app_key or not token:
        return []

    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"

    headers = {
        "X-Application": app_key,
        "X-Authentication": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listCurrentOrders",
        "params": {},
        "id": 1
    }])

    try:
        r = requests.post(url, headers=headers, data=payload, timeout=10)
        r.raise_for_status()
        current_orders = r.json()[0]["result"]["currentOrders"]
    except Exception:
        return []

    if not current_orders:
        return []

    # --------------------------------------------------
    # Build per-market runner set
    # --------------------------------------------------
    runners_by_market = defaultdict(set)
    orders_by_market = defaultdict(list)

    for o in current_orders:

        mid = str(o.get("marketId"))
        sid = str(o.get("selectionId"))
        side = (o.get("side") or "").upper()
        matched = float(o.get("sizeMatched") or 0.0)
        price = float((o.get("priceSize") or {}).get("price") or 0.0)

        if matched <= 0 or price <= 0:
            continue

        runners_by_market[mid].add(sid)

        orders_by_market[mid].append({
            "selectionId": sid,
            "side": side,
            "matched": matched,
            "price": price,
        })

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: # --------------------------------------------------
# 🧩 ACTION: REPLACE worst-case simulation logic
# 📆 PATCHED: 2026-02-22 — Add NONE-of-traded bucket to floor calculation
#
# PURPOSE:
# - Include scenario where NONE of our traded runners win
# - Floor = worst P&L across:
#       • each traded runner winning
#       • none of traded runners winning
#
# ARCHITECTURE:
# - We only model traded runners (system abstraction)
# - We do NOT enumerate full exchange field
# - NONE bucket captures all non-traded winners
#
# INVARIANT:
# - Floor = max loss across simulated scenarios
# ======================================================================================================

    results = []

    # --------------------------------------------------
    # Worst-case simulation per market (CORRECTED)
    # --------------------------------------------------
    for mid, orders in orders_by_market.items():

        traded_runners = set(o["selectionId"] for o in orders)

        worst_loss = 0.0

        # --------------------------------------------------
        # 1️⃣ Scenario: each traded runner wins
        # --------------------------------------------------
        for winner in traded_runners:

            pnl = 0.0

            for o in orders:
                sid = o["selectionId"]
                side = o["side"]
                matched = o["matched"]
                price = o["price"]

                if winner == sid:
                    if side == "LAY":
                        pnl -= matched * (price - 1)
                    else:  # BACK
                        pnl += matched * (price - 1)
                else:
                    if side == "LAY":
                        pnl += matched
                    else:
                        pnl -= matched

            if pnl < worst_loss:
                worst_loss = pnl



        # --------------------------------------------------
        # 2️⃣ Scenario: NONE of traded runners win
        # --------------------------------------------------
        pnl_none = 0.0

        for o in orders:
            side = o["side"]
            matched = o["matched"]

            # If none of traded runners win:
            # - All BACK bets lose stake
            # - All LAY bets win stake
            if side == "LAY":
                pnl_none += matched
            else:  # BACK
                pnl_none -= matched

        if pnl_none < worst_loss:
            worst_loss = pnl_none

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: inside _compute_market_floor_from_betfair_surface(), after matched worst_loss computed
# 🧩 ACTION: ADD unmatched exposure bucket
# 📆 PATCHED: 2026-02-22 — Include unmatched liability in exposure calculation
#
# PURPOSE:
# - Betfair reserves unmatched orders immediately
# - Floor must include unmatched liability
#
# INVARIANT:
# exposure = matched_floor + unmatched_liability
# ======================================================================================================

        # --------------------------------------------------
        # 3️⃣ UNMATCHED LIABILITY BUCKET
        # --------------------------------------------------
        unmatched_liability = 0.0

        for o in current_orders:
            if str(o.get("marketId")) != mid:
                continue

            remaining = float(o.get("sizeRemaining") or 0.0)
            price     = float((o.get("priceSize") or {}).get("price") or 0.0)
            side      = (o.get("side") or "").upper()

            if remaining > 0 and price > 0:
                if side == "LAY":
                    unmatched_liability += remaining * (price - 1)
                else:
                    unmatched_liability += remaining

        # Add unmatched bucket to matched floor
# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: # 3️⃣ UNMATCHED LIABILITY BUCKET
# 🛠 ACTION: REMOVE unmatched from floor (floor = matched worst-case only)
# 📆 PATCHED: 2026-04-XX — Floor now PURE matched exposure (unmatched separated)
#
# PURPOSE:
# - Unmatched working capital now handled separately
# - Floor must represent ONLY matched worst-case exposure
# - Prevent double-counting in engine used
#
# NEW INVARIANT:
#   floor = matched worst-case only
#   unmatched = working capital only
# ======================================================================================================

        # --------------------------------------------------
        # FINAL FLOOR (MATCHED ONLY)
        # --------------------------------------------------
        true_exposure = round((-worst_loss), 2)

        results.append({
            "marketId": mid,
            "true_market_exposure": true_exposure,
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
# 🔎 SEARCH: def _reconcile_market_exposure_live():
# 🛠 ACTION: Replace reconciliation source with Betfair floor
# 📆 PATCHED: 2026-02-15 — Floor now execution-surface authoritative
#
# PURPOSE:
# - Stop using SQL parent model for floor
# - Use betfair_execution_surface as sole floor authority
# - Refund based on true market exposure
#
# INVARIANT:
# - Router reserves pessimistically
# - Floor uses Betfair CURRENT surface only
# - Refund = reserved - true_floor
# ======================================================================================================

def _reconcile_market_exposure_live():

    global _OPEN_EXPOSURE

    # --------------------------------------------------
    # 1️⃣ Authoritative Betfair floor
    # --------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: if not floor_rows:
# 🧩 ACTION: REMOVE early return on empty Betfair surface
# 📆 PATCHED: 2026-02-19 — Fix exposure not collapsing after markets finish
#
# WHY:
# - floor_rows empty means true_floor = 0
# - Previously returned early and skipped refund logic
# - Caused exposure to remain inflated (e.g. 599.80)
#
# NEW BEHAVIOUR:
# - Empty floor_rows ⇒ treat as floor_by_market = {}
# - Refund entire reserved surface
# ======================================================================================================

    floor_rows = _compute_market_floor_from_betfair_surface()

    # DO NOT early-return here.
    # Empty floor_rows means floor = 0.
    # Allow refund logic to process.

    floor_by_market = {
        r["marketId"]: float(r["true_market_exposure"])
        for r in floor_rows
    }


    # --------------------------------------------------
    # 2️⃣ Compute reserved per market from runtime state
    # --------------------------------------------------
    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=False)
    con.row_factory = None
    cur = con.cursor()

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: reserved_rows = cur.execute("""
# 🧩 ACTION: REPLACE — use bank_ledger as authoritative reservation surface
# 📆 PATCHED: 2026-02-19 — Floor reconciliation now ledger-driven
#
# WHY:
# - orders.required_exposure is router pessimism
# - bank_ledger.reserved_amount is true live reservation surface
# - Floor must reconcile against ledger only
#
# INVARIANT:
#   Σ ledger.reserved_amount == Σ _ENGINE_USED == _OPEN_EXPOSURE
# ======================================================================================================

    reserved_rows = cur.execute("""
        SELECT
            o.marketId,
            SUM(l.reserved_amount)
        FROM bank_ledger l
        JOIN orders o ON o.id = l.parent_id
        WHERE l.active = 1
          AND date(o.opened_at)=date('now','utc')
        GROUP BY o.marketId
    """).fetchall()


    con.close()

    reserved_by_market = {
        mid: float(total or 0.0)
        for mid, total in reserved_rows
    }

    floor_by_market = {
        r["marketId"]: float(r["true_market_exposure"])
        for r in floor_rows
    }

    total_refund = 0.0

    with _LOCK:

        for mid, reserved in reserved_by_market.items():

            true_floor = floor_by_market.get(mid, 0.0)

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: inside _reconcile_market_exposure_live() after true_floor calculation
# 🧩 ACTION: ADD floor-underflow correction (reserved < floor)
# 📆 PATCHED: 2026-02-22 — Enforce floor as absolute exposure truth
#
# PURPOSE:
# - If reserved < true_floor, exposure must increase to match floor
# - Floor is authoritative
# - Prevent drift when router under-reserves
#
# INVARIANT:
#   _OPEN_EXPOSURE == Σ true_floor
#   Σ _ENGINE_USED == _OPEN_EXPOSURE
# ======================================================================================================

            # --------------------------------------------------
            # 🟥 FLOOR UNDER-RESERVE CORRECTION
            # --------------------------------------------------
            if reserved < true_floor:

                shortfall = true_floor - reserved

                # distribute shortfall proportionally to engines already in market
                engine_rows = con2 = None

                from engines.config_paths import open_auto_db
                con2 = open_auto_db(rw=False)
                con2.row_factory = None

                engine_rows = con2.execute("""
                    SELECT
                        l.engine,
                        SUM(l.reserved_amount)
                    FROM bank_ledger l
                    JOIN orders o ON o.id = l.parent_id
                    WHERE l.active = 1
                      AND o.marketId = ?
                      AND date(o.opened_at)=date('now','utc')
                    GROUP BY l.engine
                """, (mid,)).fetchall()

                con2.close()

                total_market_reserved = sum(e[1] for e in engine_rows) or 1.0

                for engine, eng_reserved in engine_rows:

                    pct = eng_reserved / total_market_reserved
                    add_amount = shortfall * pct

                    _ENGINE_USED[engine] = _clamp(
                        _ENGINE_USED.get(engine, 0.0) + add_amount
                    )

                _OPEN_EXPOSURE = _clamp(
                    _OPEN_EXPOSURE + shortfall
                )

                continue

            if reserved <= true_floor:
                continue

            over = reserved - true_floor

            # --------------------------------------------------
            # Proportional engine refund per market
            # --------------------------------------------------
            engine_rows = cur = None

            from engines.config_paths import open_auto_db
            con2 = open_auto_db(rw=False)
            con2.row_factory = None

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: engine_rows = con2.execute("""
# 🧩 ACTION: REPLACE — per-engine allocation from ledger, not orders
# 📆 PATCHED: 2026-02-19 — Engine refund proportional to ledger reservation
#
# WHY:
# - orders.required_exposure no longer authoritative
# - ledger holds current reserved_amount after partial refunds
#
# INVARIANT:
#   Refund proportion = engine_reserved / total_market_reserved
# ======================================================================================================

            engine_rows = con2.execute("""
                SELECT
                    l.engine,
                    SUM(l.reserved_amount)
                FROM bank_ledger l
                JOIN orders o ON o.id = l.parent_id
                WHERE l.active = 1
                  AND o.marketId = ?
                  AND date(o.opened_at)=date('now','utc')
                GROUP BY l.engine
            """, (mid,)).fetchall()


            con2.close()

            total_market_reserved = sum(e[1] for e in engine_rows)

            if total_market_reserved <= 0:
                continue

            for engine, eng_reserved in engine_rows:

                pct = eng_reserved / total_market_reserved
                refund = over * pct

                used = _ENGINE_USED.get(engine, 0.0)
                refund = min(refund, used)

                if refund <= 0:
                    continue

                _ENGINE_USED[engine] = _clamp(used - refund)
# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: UPDATE bank_ledger
# 📆 PATCHED: 2026-02-19 — Clamp ledger to prevent negative reserves
#
# PURPOSE:
# - Prevent ledger from going negative
# - Ledger must mirror live reserved surface only
# - Ledger is memory, not authority
# ======================================================================================================

                try:
                    from engines.config_paths import open_auto_db
                    con3 = open_auto_db(rw=True)
                    cur3 = con3.cursor()

                    # Clamp at zero
                    cur3.execute("""
                        UPDATE bank_ledger
                           SET reserved_amount =
                               MAX(0, reserved_amount - ?),
                               updated_at = datetime('now','utc')
                         WHERE engine = ?
                           AND active = 1
                    """, (
                        float(refund),
                        engine
                    ))

                    con3.commit()
                    con3.close()

                except Exception:
                    pass


                total_refund += refund

        if total_refund > 0:
            _OPEN_EXPOSURE = _clamp(_OPEN_EXPOSURE - total_refund)

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: end of _reconcile_market_exposure_live()
# 📆 PATCHED: 2026-02-19 — Force ledger to mirror in-memory reserved surface
#
# PURPOSE:
# - Ledger must reflect _ENGINE_USED exactly
# - Ledger never computes exposure
# - Ledger only stores state for restart recovery
# ======================================================================================================

        try:
            from engines.config_paths import open_auto_db
            con4 = open_auto_db(rw=True)
            cur4 = con4.cursor()

            # Reset ledger to match live engine used
            for engine, used in _ENGINE_USED.items():
                cur4.execute("""
                    UPDATE bank_ledger
                       SET reserved_amount = ?,
                           updated_at = datetime('now','utc')
                     WHERE engine = ?
                       AND active = 1
                """, (
                    float(used),
                    engine
                ))

            con4.commit()
            con4.close()

        except Exception:
            pass


    return floor_rows

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

            # --------------------------------------------------
            # REPORT 4 - WORKING CAPITAL REPORT (UNMATCHED ONLY)
            # --------------------------------------------------

            unmatched_map = _compute_engine_unmatched_working_capital()

            print("\n=== ENGINE WORKING CAPITAL (UNMATCHED) ===")

            for engine in sorted(_ENGINE_POTS.keys()):
                wc = unmatched_map.get(engine, 0.0)
                print(f"{engine:<20} working_capital={wc:.2f}")

            print("==========================================")


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
        # subtract unmatched working capital
        unmatched_map = _compute_engine_unmatched_working_capital()
        used += unmatched_map.get(engine, 0.0)
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

# === PATCH START ============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: inside on_parent_placed (after _ENGINE_USED update)
# 📆 PATCHED: 2026-04-19 — persistent ledger reservation
# ============================================================================

        # --------------------------------------------------
        # Persist reservation in ledger
        # --------------------------------------------------
        try:
            from engines.config_paths import open_auto_db
            con2 = open_auto_db(rw=True)
            cur2 = con2.cursor()

            cur2.execute("""
                INSERT OR REPLACE INTO bank_ledger
                (parent_id, engine, reserved_amount, active, updated_at)
                VALUES (?, ?, ?, 1, datetime('now','utc'))
            """, (
                int(parent_id),
                engine,
                float(amount),
            ))

            con2.commit()
            con2.close()

        except Exception:
            pass

# === PATCH END ==============================================================


    # --------------------------------------------------
    # 2️⃣ AUTHORITATIVE MARKET RECONCILIATION
    # --------------------------------------------------
    refunds = _reconcile_market_exposure_live()

    if refunds:
        print(
            f"[BankState] reconciliation applied | "
            f"open={_OPEN_EXPOSURE:.2f}"
        )

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def _simulate_floor_with_new_bet(
# 📆 PATCHED: 2026-04-21 — True runner-level floor simulation (authoritative)
#
# PURPOSE:
# - Inject hypothetical matched order into Betfair CURRENT surface
# - Recompute full worst-case market loss
# - Exact same logic as floor computation
# ======================================================================================================

def _simulate_floor_with_new_bet(mid: str, plan: dict) -> float:
    """
    Compute true projected worst-case exposure for a market
    if this bet were already matched.
    """

    from engines.live.live_router import _keys
    import requests, json
    from collections import defaultdict

    app_key, token = _keys()

    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": app_key,
        "X-Authentication": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listCurrentOrders",
        "params": {},
        "id": 1
    }])

    try:
        r = requests.post(url, headers=headers, data=payload, timeout=10)
        r.raise_for_status()
        current_orders = r.json()[0]["result"]["currentOrders"]
    except Exception:
        return 0.0

    # --------------------------------------------------
    # Build matched order surface
    # --------------------------------------------------
    orders = []

    for o in current_orders:
        if str(o.get("marketId")) != mid:
            continue

        matched = float(o.get("sizeMatched") or 0.0)
        price = float((o.get("priceSize") or {}).get("price") or 0.0)
        side = (o.get("side") or "").upper()
        sid = str(o.get("selectionId"))

        if matched > 0 and price > 0:
            orders.append({
                "selectionId": sid,
                "side": side,
                "matched": matched,
                "price": price,
            })

    # --------------------------------------------------
    # Inject hypothetical new matched order
    # --------------------------------------------------
    orders.append({
        "selectionId": str(plan["selectionId"]),
        "side": str(plan["side"]).upper(),
        "matched": float(plan["size"]),
        "price": float(plan["px"]),
    })

    # --------------------------------------------------
    # Enumerate all runners
    # --------------------------------------------------
    runners = set(o["selectionId"] for o in orders)

    worst_loss = 0.0

    for winner in runners:
        pnl = 0.0

        for o in orders:
            sid = o["selectionId"]
            side = o["side"]
            matched = o["matched"]
            price = o["price"]

            if winner == sid:
                if side == "LAY":
                    pnl -= matched * (price - 1)
                else:
                    pnl += matched * (price - 1)
            else:
                if side == "LAY":
                    pnl += matched
                else:
                    pnl -= matched

        if pnl < worst_loss:
            worst_loss = pnl

    return round(-worst_loss, 2)

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def can_place(
# 📆 PATCHED: 2026-04-21 — Floor-delta placement gate (engine allocation aware)
# ======================================================================================================

def can_place(engine: str, plan: dict) -> bool:

    mid = str(plan.get("marketId"))
    if not mid:
        return False

    # 1️⃣ Current floor
    floor_rows = _compute_market_floor_from_betfair_surface()
    floor_by_market = {
        r["marketId"]: float(r["true_market_exposure"])
        for r in floor_rows
    }

    current_floor = floor_by_market.get(mid, 0.0)

    # 2️⃣ Projected floor
    projected_floor = _simulate_floor_with_new_bet(mid, plan)

    delta_floor = projected_floor - current_floor

    # 3️⃣ Floor reduction → always allow
    if delta_floor <= 0:
        return True

    # 4️⃣ Engine allocation check
    available = get_engine_available(engine)

    if delta_floor > available:
        return False

    return True

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
    # Exposure is governed by Betfair floor reconciliation only.
    # Do not mutate _OPEN_EXPOSURE here.
    return

# -------------------------------------------------------------------
# PATCH 2️⃣ — on_child_matched
# -------------------------------------------------------------------
def on_child_matched(*, parent_id: int, **_ignored) -> None:
    # Exposure is governed by Betfair floor reconciliation only.
    # Do not mutate _OPEN_EXPOSURE here.
    return




# -------------------------------------------------------------------
# PATCH 3️⃣ — release_parent (router housekeeping)
# -------------------------------------------------------------------
def release_parent(parent_id: int) -> None:
    # Exposure is governed by Betfair floor reconciliation only.
    # Do not mutate _OPEN_EXPOSURE here.
    return


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


