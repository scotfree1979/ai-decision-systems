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
# === PATCH START ==============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🧩 ACTION: cache last computed floor/unmatched for snapshot mirror
# ==============================================================================

_ENGINE_FLOOR_CACHE: Dict[str, float] = {}
_ENGINE_UNMATCHED_CACHE: Dict[str, float] = {}

# === PATCH END ==============================================================

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🧩 ACTION: LOCK ledger schema (mirror only)
# ======================================================================================================

def _ensure_bank_ledger_schema():
    from engines.config_paths import open_auto_db
    con = open_auto_db(rw=True)
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS bank_ledger(
            day TEXT NOT NULL,
            engine TEXT NOT NULL,
            engine_used REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY(day, engine)
        )
    """)

    con.commit()
    con.close()

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
# === PATCH START ==============================================================
# 📍 TARGET: engines/live/bank_state.py:init_bank_state
# 🔎 ANCHOR: start of function
# 🛠 ACTION: purge old-day ledger rows
# 📆 PATCHED: 2026-02-23
# ==============================================================================

        from engines.config_paths import open_auto_db
        today = _utc_day()

        con = open_auto_db(rw=True)
        cur = con.cursor()

        cur.execute("""
            DELETE FROM bank_ledger
            WHERE day != ?
        """, (today,))

        con.commit()
        con.close()

# === PATCH END ==============================================================
        init_from_budget_allocations()
        rebuild_live_exposure_from_db()   # ← ADD THIS
        _restore_risk_bank_from_market_exposure()
        if _is_simulation():
            print("[BankState] initialised from budget_allocations")
    except Exception as e:
        print(f"[BankState][WARN] init failed: {e}")

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def _compute_engine_unmatched_working_capital():
# 🛠 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-04-25 — Working capital from Betfair CURRENT surface (parents + children)
#
# PURPOSE:
# - Compute unmatched liability from Betfair listCurrentOrders
# - Include BOTH parents and children
# - Use sizeRemaining (authoritative)
# - Group by engine via orders table mapping
#
# INVARIANT:
#   Working capital = TRUE unmatched exposure currently reserved at Betfair
#   No DB guessing
#   No role filtering
# ======================================================================================================

def _compute_engine_unmatched_working_capital():

    from engines.live.live_router import _keys
    from engines.config_paths import open_auto_db
    import requests
    import json
    from collections import defaultdict

    app_key, token = _keys()
    if not app_key or not token:
        return {}

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
        return {}

    if not current_orders:
        return {}

    # --------------------------------------------------
    # Map betId → engine (DB truth)
    # --------------------------------------------------
    con = open_auto_db(rw=False)
    con.row_factory = None
    cur = con.cursor()

    betid_to_engine = {}

    rows = cur.execute("""
        SELECT entry_bet_id, engine
        FROM orders
        WHERE entry_bet_id IS NOT NULL
          AND date(opened_at) = date('now','utc')
    """).fetchall()

    con.close()

    # --------------------------------------------------
    # Build entry_bet_id → engine map
    # --------------------------------------------------
    for entry_bet_id, engine in rows:
        if entry_bet_id:
            betid_to_engine[str(entry_bet_id)] = engine

    # --------------------------------------------------
    # Compute unmatched working capital per engine
    # --------------------------------------------------
    engine_wc = defaultdict(float)

    for o in current_orders:

        remaining = float(o.get("sizeRemaining") or 0.0)
        if remaining <= 0:
            continue

        price = float((o.get("priceSize") or {}).get("price") or 0.0)
        if price <= 0:
            continue

        side = (o.get("side") or "").upper()
        bet_id = str(o.get("betId") or "")

        engine = betid_to_engine.get(bet_id)
        if not engine:
            continue  # ignore unknown orders

        if side == "LAY":
            liability = remaining * (price - 1.0)
        else:  # BACK
            liability = remaining

        engine_wc[engine] += float(liability)

    return {
        engine: round(amount, 2)
        for engine, amount in engine_wc.items()
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

# === PATCH START ==============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def rebuild_live_exposure_from_db(
# 🛠 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-02-23 — Day-scoped ledger mirror
#
# PURPOSE:
# - Load only TODAY exposure
# - Ledger mirrors _ENGINE_USED
# - No cross-day contamination
#
# INVARIANT:
#   ledger.day == today
#   _OPEN_EXPOSURE == sum(engine_used for today)
# ==============================================================================

def rebuild_live_exposure_from_db():

    global _ENGINE_USED, _OPEN_EXPOSURE

    from engines.config_paths import open_auto_db
    today = _utc_day()

    con = open_auto_db(rw=False)
    cur = con.cursor()

    rows = cur.execute("""
        SELECT engine, engine_used
        FROM bank_ledger
        WHERE day = ?
    """, (today,)).fetchall()

    con.close()

    with _LOCK:
        _ENGINE_USED = {eng: 0.0 for eng in _ENGINE_POTS.keys()}
        _OPEN_EXPOSURE = 0.0

        for engine, used in rows:
            used = _clamp(used or 0.0)
            _ENGINE_USED[engine] = used
            _OPEN_EXPOSURE += used

        print(f"[BankState] exposure rebuilt (ledger mirror) | open={_OPEN_EXPOSURE:.2f}")

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
# 🧩 ACTION: FULL REBUILD — ENGINE_USED = floor + unmatched (no mutation drift)
# 📆 PATCHED: 2026-04-FIX
#
# LOCKED INVARIANT:
#   ENGINE_USED = ENGINE_FLOOR_SHARE + ENGINE_UNMATCHED
#   _OPEN_EXPOSURE = sum(ENGINE_USED)
#   bank_ledger mirrors ENGINE_USED only
# ======================================================================================================

def _reconcile_market_exposure_live():

    global _ENGINE_USED, _OPEN_EXPOSURE

    # 1️⃣ Get authoritative floor (matched only)
    floor_rows = _compute_market_floor_from_betfair_surface()

    floor_by_market = {
        r["marketId"]: float(r["true_market_exposure"])
        for r in floor_rows
    }

    # 2️⃣ Allocate floor by WORST-RUNNER EXPOSURE (correct model)

    from engines.config_paths import open_auto_db
    con = open_auto_db(rw=False)
    con.row_factory = None
    cur = con.cursor()

    engine_floor = {eng: 0.0 for eng in _ENGINE_POTS.keys()}

    # For each market
    for mid, true_floor in floor_by_market.items():

        # Pull matched exposure per engine PER RUNNER
        rows = cur.execute("""
            SELECT
                o.selectionId,
                o.engine,
                SUM(
                    CASE
                        WHEN o.side='LAY'
                            THEN o.entry_stake * (o.entry_odds - 1)
                        ELSE
                            -o.entry_stake
                    END
                ) AS net_exposure
            FROM orders o
            WHERE o.role='PARENT'
              AND o.entry_status='MATCHED'
              AND date(o.opened_at)=date('now','utc')
              AND o.marketId=?
            GROUP BY o.selectionId, o.engine
        """, (mid,)).fetchall()

        # Build runner exposure map
        runner_engine_exposure = {}
        runner_totals = {}

        for selectionId, engine, net in rows:
            net = float(net or 0.0)

            runner_engine_exposure.setdefault(selectionId, {})
            runner_engine_exposure[selectionId][engine] = net

            runner_totals[selectionId] = (
                runner_totals.get(selectionId, 0.0) + net
            )

        if not runner_totals:
            continue

        # Identify worst-case runner
        worst_runner = max(
            runner_totals.items(),
            key=lambda x: x[1]
        )[0]

        # Allocate floor share = engine exposure on worst runner
        for engine, net in runner_engine_exposure.get(worst_runner, {}).items():
            engine_floor[engine] += max(0.0, float(net))

    con.close()

    
    # 3️⃣ Compute unmatched (working capital)
    unmatched_map = _compute_engine_unmatched_working_capital()

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: # 4️⃣ Rebuild ENGINE_USED fresh (NO DRIFT)
# 🧩 ACTION: REPLACE — enforce hard engine pot invariant
# 📆 PATCHED: 2026-04-XX — Prevent negative engine pots
#
# ROOT CAUSE
# ----------
# ENGINE_USED was computed as:
#
#     USED = FLOOR + UNMATCHED
#
# If FLOOR + UNMATCHED exceeded the engine pot, AVAILABLE became negative.
#
# CORRECT MODEL
# -------------
# USED must never exceed the engine pot.
#
#     USED = min(POT, FLOOR + UNMATCHED)
#
# where:
#
#     FLOOR      = matched worst-case exposure
#     UNMATCHED  = working capital reserved at Betfair
#
# HARD INVARIANT
# --------------
#     USED ≤ POT
#     AVAILABLE ≥ 0
#
# RESULT
# ------
# • prevents negative engine pots
# • preserves full exposure math
# • protects against placement gate failures
# • does not change floor or unmatched logic
# ======================================================================================================

    # 4️⃣ Rebuild ENGINE_USED fresh (NO DRIFT)
    with _LOCK:

        for engine in _ENGINE_POTS.keys():

            floor_part = engine_floor.get(engine, 0.0)
            unmatched_part = unmatched_map.get(engine, 0.0)

            pot = _ENGINE_POTS.get(engine, 0.0)

            used = floor_part + unmatched_part

            # 🔒 HARD SAFETY INVARIANT
            # Engine usage can never exceed its pot
            used = min(pot, used)

            _ENGINE_USED[engine] = _clamp(used)

        _OPEN_EXPOSURE = _clamp(sum(_ENGINE_USED.values()))

        # 5️⃣ Mirror ledger (pure mirror, no arithmetic)
        try:
            from engines.config_paths import open_auto_db
            con2 = open_auto_db(rw=True)
            cur2 = con2.cursor()

            today = _utc_day()

            # wipe today's rows
            cur2.execute("DELETE FROM bank_ledger WHERE day=?", (today,))

            for engine, used in _ENGINE_USED.items():
                cur2.execute("""
                    INSERT INTO bank_ledger(day, engine, engine_used)
                    VALUES (?, ?, ?)
                """, (today, engine, float(used)))

            con2.commit()
            con2.close()

        except Exception:
            pass

    return engine_floor, unmatched_map, floor_rows

# -------------------------------------------------------------------
# OBSERVABILITY REPORT LOOP (REFINED, LOW-NOISE)
# -------------------------------------------------------------------

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def _bankstate_report_loop(
# 🛠 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-04-27 — Unified Atomic BankState Snapshot
#
# PURPOSE:
# - Remove scattered print sections
# - Produce one atomic block
# - Eliminate interleaving noise
# - Make exposure math unequivocal
# - No logic changes
# ======================================================================================================

def _bankstate_report_loop(interval_s: int = 60):

    while True:
        try:
            engine_floor, unmatched_map, floor_rows = _reconcile_market_exposure_live()

            # 🔒 Snapshot write moved HERE so dashboard = print (atomic)
            _write_bank_runtime_snapshot()

            with _LOCK:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")

                total_pot = sum(_ENGINE_POTS.values())
                total_used = sum(_ENGINE_USED.values())
                total_avail = total_pot - total_used
                open_exp = _clamp(_OPEN_EXPOSURE)

# === PATCH START ==============================================================
                # Cache split values for snapshot mirror
                _ENGINE_FLOOR_CACHE.clear()
                _ENGINE_UNMATCHED_CACHE.clear()

                for engine in _ENGINE_POTS.keys():
                    _ENGINE_FLOOR_CACHE[engine] = engine_floor.get(engine, 0.0)
                    _ENGINE_UNMATCHED_CACHE[engine] = unmatched_map.get(engine, 0.0)
# === PATCH END ==============================================================

                # --------------------------------------------------
                # Compute per-engine floor + unmatched
                # --------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: inside _bankstate_report_loop() after reconciliation call
# 🧩 ACTION: Remove duplicated floor recomputation in report
# 📆 PATCHED: 2026-04-XX — BankState report must use reconciliation values
#
# ROOT CAUSE
# ----------
# The report recomputed floor/unmatched independently from
# _reconcile_market_exposure_live().
#
# This produced incorrect floor attribution per engine.
#
# FIX
# ---
# The report must display the authoritative values already
# returned by reconciliation.
#
# INVARIANT
# ---------
# Report values MUST equal runtime accounting values.
# ======================================================================================================

                # Use authoritative values returned from reconciliation
                # DO NOT recompute floor or unmatched

                floor_by_market = {
                    r["marketId"]: float(r["true_market_exposure"])
                    for r in floor_rows
                }

                # engine_floor and unmatched_map are already correct
                # from _reconcile_market_exposure_live()


                # --------------------------------------------------
                # Build atomic report block
                # --------------------------------------------------

                lines = []
                lines.append("\n======================================================================")
                lines.append(f"🏦  V7 BANKSTATE SNAPSHOT — {now}")
                lines.append("======================================================================")
                lines.append("")
                lines.append("GLOBAL SUMMARY")
                lines.append("----------------------------------------------------------------------")
                lines.append(f"Open Exposure        : {open_exp:8.2f}")
                lines.append(f"Total Pot            : {total_pot:8.2f}")
                lines.append(f"Total Used           : {total_used:8.2f}")
                lines.append(f"Total Available      : {total_avail:8.2f}")
                lines.append(f"Active Markets       : {_effective_market_count():8d}")
                lines.append("----------------------------------------------------------------------")
                lines.append("")
                lines.append("ENGINE BREAKDOWN")
                lines.append("----------------------------------------------------------------------")
                lines.append("ENGINE             POT      FLOOR    UNMATCHED    USED     AVAIL")
                lines.append("----------------------------------------------------------------------")

                for engine in sorted(_ENGINE_POTS.keys()):
                    pot = _ENGINE_POTS.get(engine, 0.0)
                    floor_part = engine_floor.get(engine, 0.0)
                    unmatched_part = unmatched_map.get(engine, 0.0)
                    used = _ENGINE_USED.get(engine, 0.0)
                    avail = pot - used

                    lines.append(
                        f"{engine:<16} "
                        f"{pot:8.2f}  "
                        f"{floor_part:8.2f}  "
                        f"{unmatched_part:10.2f}  "
                        f"{used:8.2f}  "
                        f"{avail:8.2f}"
                    )

                lines.append("----------------------------------------------------------------------")
                lines.append(f"{'TOTAL':<16} {total_pot:8.2f}  "
                             f"{sum(engine_floor.values()):8.2f}  "
                             f"{sum(unmatched_map.values()):10.2f}  "
                             f"{total_used:8.2f}  "
                             f"{total_avail:8.2f}")
                lines.append("----------------------------------------------------------------------")
                lines.append("")
                lines.append("BETFAIR FLOOR (MATCHED WORST-CASE PER MARKET)")
                lines.append("----------------------------------------------------------------------")

                if floor_rows:
                    for r in floor_rows[:5]:
                        lines.append(
                            f"market={r['marketId']}   "
                            f"floor={float(r['true_market_exposure']):.2f}"
                        )
                else:
                    lines.append("No matched exposure detected.")

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: inside unified report block before INVARIANT CHECKS
# 🛠 ACTION: ADD DELTA GATE PREVIEW SECTION
# 📆 PATCHED: 2026-04-27 — Placement gate visibility
#
# PURPOSE:
# - Show whether BankState would block a small incremental bet
# - Expose phantom "pot full" situations
# - No state mutation
# - Pure simulation
# ======================================================================================================

                lines.append("")
                lines.append("PLACEMENT GATE PREVIEW (Δ FLOOR TEST)")
                lines.append("----------------------------------------------------------------------")
                lines.append("ENGINE             DELTA_NEXT    AVAILABLE    CAN_PLACE")
                lines.append("----------------------------------------------------------------------")

                for engine in sorted(_ENGINE_POTS.keys()):

                    available = get_engine_available(engine)

                    # Skip if no markets
                    if not floor_by_market:
                        lines.append(
                            f"{engine:<16} "
                            f"{0.00:10.2f}  "
                            f"{available:10.2f}  "
                            f"{'N/A':>10}"
                        )
                        continue

                    # Pick first market as probe
                    probe_mid = list(floor_by_market.keys())[0]

                    # Minimal synthetic probe plan (£1 BACK)
                    probe_plan = {
                        "marketId": probe_mid,
                        "selectionId": "0",
                        "side": "BACK",
                        "size": 1.0,
                        "px": 2.0,
                    }

                    current_floor = floor_by_market.get(probe_mid, 0.0)
                    projected_floor = _simulate_floor_with_new_bet(probe_mid, probe_plan)

                    delta_floor = projected_floor - current_floor

                    if delta_floor <= 0:
                        can_place_flag = "YES"
                    else:
                        can_place_flag = "YES" if delta_floor <= available else "NO"

                    lines.append(
                        f"{engine:<16} "
                        f"{delta_floor:10.2f}  "
                        f"{available:10.2f}  "
                        f"{can_place_flag:>10}"
                    )

                lines.append("----------------------------------------------------------------------")

                lines.append("----------------------------------------------------------------------")
                lines.append("")
                lines.append("INVARIANT CHECKS")
                lines.append("----------------------------------------------------------------------")

                if abs(total_used - open_exp) < 0.01:
                    lines.append("✔ TOTAL_USED == OPEN_EXPOSURE")
                else:
                    lines.append("❌ TOTAL_USED != OPEN_EXPOSURE")

                lines.append("----------------------------------------------------------------------")
                lines.append("======================================================================")
                print("\n".join(lines))

        except Exception as e:
            print(f"[BankState][REPORT][WARN] {e}")

        time.sleep(interval_s)


def start_bankstate_reporter(interval_s: int = 2):
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
    with _LOCK:
        pot = _ENGINE_POTS.get(engine, 0.0)
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
    Parent placed event.

    IMPORTANT:
    - Do NOT mutate ENGINE_USED here.
    - Do NOT reserve required_exposure.
    - Unmatched liability is derived from orders table.
    - Floor share is derived from Betfair surface.
    - ENGINE_USED is rebuilt inside reconciliation.

    This function only triggers reconciliation.
    """

    try:
        # Just verify the parent exists (sanity guard)
        from engines.config_paths import open_auto_db
        con = open_auto_db(rw=False)
        row = con.execute(
            "SELECT id FROM orders WHERE id=?",
            (int(parent_id),)
        ).fetchone()
        con.close()

        if not row:
            return

    except Exception:
        return

    # 🔒 No exposure mutation here.
    # 🔒 No ledger write here.
    # 🔒 No required_exposure reservation.

    # 1️⃣ Rebuild exposure deterministically
    _reconcile_market_exposure_live()
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

    pot = _ENGINE_POTS.get(engine, 0.0)

# ======================================================================================================
# 📍 TARGET: engines/live/bank_state.py
# 🔎 ANCHOR: start of can_place()
# 🧩 ACTION: stop-loss parent bypass
# 📆 PATCHED: 2026-XX-XX — stop-loss orders must never be blocked
#
# PURPOSE
# -------
# Stop-loss parents reduce exposure and must always be executable.
#
# Even though the floor-delta model should naturally allow them
# (delta_floor <= 0), this explicit bypass guarantees they can never
# be blocked by the placement gate.
#
# INVARIANT
# ---------
# bet_type == STOPLOSS  → always allowed
#
# SAFETY
# ------
# Only applies to STOPLOSS plans emitted by Unified.
# Does not affect normal parents or children.
# ======================================================================================================

    if plan.get("bet_type") == "STOPLOSS":
        return True

    # --------------------------------------------------
    # 1️⃣ current market floor
    # --------------------------------------------------
    floor_rows = _compute_market_floor_from_betfair_surface()

    floor_by_market = {
        r["marketId"]: float(r["true_market_exposure"])
        for r in floor_rows
    }

    # --------------------------------------------------
    # 2️⃣ compute engine floor share (same as reconciliation)
    # --------------------------------------------------
    from engines.config_paths import open_auto_db
    con = open_auto_db(rw=False)
    cur = con.cursor()

    engine_floor = 0.0

    rows = cur.execute("""
        SELECT
            o.selectionId,
            o.engine,
            SUM(
                CASE
                    WHEN o.side='LAY'
                        THEN o.entry_stake * (o.entry_odds - 1)
                    ELSE
                        -o.entry_stake
                END
            ) AS net_exposure
        FROM orders o
        WHERE o.role='PARENT'
          AND o.entry_status='MATCHED'
          AND date(o.opened_at)=date('now','utc')
          AND o.marketId=?
        GROUP BY o.selectionId, o.engine
    """, (mid,)).fetchall()

    runner_engine = {}
    runner_totals = {}

    for selectionId, eng, net in rows:
        net = float(net or 0.0)
        runner_engine.setdefault(selectionId, {})
        runner_engine[selectionId][eng] = net
        runner_totals[selectionId] = runner_totals.get(selectionId, 0.0) + net

    if runner_totals:
        worst_runner = max(runner_totals.items(), key=lambda x: x[1])[0]
        engine_floor = max(
            0.0,
            float(runner_engine.get(worst_runner, {}).get(engine, 0.0))
        )

    con.close()

    # --------------------------------------------------
    # 3️⃣ unmatched working capital
    # --------------------------------------------------
    unmatched_map = _compute_engine_unmatched_working_capital()
    unmatched = unmatched_map.get(engine, 0.0)

    current_used = engine_floor + unmatched

    # --------------------------------------------------
    # 4️⃣ simulate new order floor impact
    # --------------------------------------------------
    projected_floor = _simulate_floor_with_new_bet(mid, plan)
    current_floor = floor_by_market.get(mid, 0.0)

    delta_floor = max(projected_floor - current_floor, 0.0)

    # --------------------------------------------------
    # 5️⃣ unmatched impact of new order
    # --------------------------------------------------
    side = str(plan.get("side", "")).upper()
    size = float(plan.get("size", 0.0))
    px = float(plan.get("px", 0.0))

    if side == "LAY":
        new_unmatched = size * (px - 1.0)
    else:
        new_unmatched = size

    # --------------------------------------------------
    # 6️⃣ final capital check
    # --------------------------------------------------
    projected_used = current_used + delta_floor + new_unmatched

    if projected_used > pot:
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

def on_parent_matched(*_, **__):
    """
    Parent MATCHED event.

    Exposure does NOT mutate here.
    We simply rebuild floor + unmatched from exchange truth.
    """
    _reconcile_market_exposure_live()
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
def on_parent_closed(*_, **__):
    """
    Parent terminal event (CANCELLED / EXPIRED / SETTLED).

    Unmatched may drop.
    Floor may drop.

    Rebuild deterministically.
    """
    _reconcile_market_exposure_live()

# -------------------------------------------------------------------
# PATCH 2️⃣ — on_child_matched
# -------------------------------------------------------------------
def on_child_matched(*_, **__):
    """
    Child MATCHED event.

    Child moving from unmatched → matched
    changes both working capital and floor.

    Rebuild from authoritative surface.
    """
    _reconcile_market_exposure_live()




# -------------------------------------------------------------------
# PATCH 3️⃣ — release_parent (router housekeeping)
# -------------------------------------------------------------------
def release_parent(*_, **__):
    """
    Legacy compatibility hook.

    Exposure is no longer manually released.
    We rebuild instead.
    """
    _reconcile_market_exposure_live()


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

# === PATCH START ==============================================================
# 📍 TARGET: engines/live/bank_state.py (append at end)
# 📆 PATCHED: 2026-02-27 — Structured runtime snapshot (BANKSTATE)
# ==============================================================================

def _ensure_bank_runtime_schema():
    import sqlite3
    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db(), timeout=6, isolation_level=None)
    con.execute("""
        CREATE TABLE IF NOT EXISTS bankstate_runtime_snapshot(
            ts TEXT,
            total_pot REAL,
            total_used REAL,
            total_available REAL,
            total_exposure REAL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS bankstate_engine_snapshot(
            ts TEXT,
            engine TEXT,
            pot REAL,
            used REAL,
            available REAL,
            floor REAL,
            unmatched REAL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS bankstate_runner_snapshot(
            ts TEXT,
            marketId TEXT,
            selectionId TEXT,
            horse_name TEXT,
            pnl_if_win REAL,
            engine TEXT
        )
    """)
    con.close()

# === PATCH START ==============================================================
# 📍 TARGET: engines/live/bank_state.py
# 🧩 ACTION: snapshot is now pure memory mirror (NO recompute)
# ==============================================================================

def _write_bank_runtime_snapshot():

    import sqlite3
    from datetime import datetime, timezone
    from engines.config_paths import open_auto_db

    

    ts = datetime.now(timezone.utc).isoformat()

    with _LOCK:
        total_pot = sum(_ENGINE_POTS.values())
        total_used = sum(_ENGINE_USED.values())
        total_available = total_pot - total_used

        floor_cache = dict(_ENGINE_FLOOR_CACHE)
        unmatched_cache = dict(_ENGINE_UNMATCHED_CACHE)

        pots = dict(_ENGINE_POTS)
        used_map = dict(_ENGINE_USED)
        open_exp = _OPEN_EXPOSURE

    con = open_auto_db(rw=True)

    # GLOBAL
    con.execute("""
        INSERT INTO bankstate_runtime_snapshot
        VALUES (?,?,?,?,?)
    """, (ts, total_pot, total_used, total_available, open_exp))

    # ENGINE SPLIT
    for engine in pots:

        pot = float(pots.get(engine, 0.0))
        used = float(used_map.get(engine, 0.0))
        available = pot - used

        floor_part = float(floor_cache.get(engine, 0.0))
        unmatched_part = float(unmatched_cache.get(engine, 0.0))

        con.execute("""
            INSERT INTO bankstate_engine_snapshot
            (ts, engine, pot, used, available, floor, unmatched)
            VALUES (?,?,?,?,?,?,?)
        """, (
            ts,
            engine,
            pot,
            used,
            available,
            floor_part,
            unmatched_part,
        ))

    # --------------------------------------------------
    # RUNNER PnL SNAPSHOT (dashboard intelligence layer)
    # --------------------------------------------------

    try:

        from engines.config_paths import open_auto_db

        con2 = open_auto_db(rw=True)
        cur2 = con2.cursor()

        rows = cur2.execute("""
            SELECT
                o.marketId,
                o.selectionId,
                COALESCE(b.horse_name,'') AS horse_name,
                o.engine,
                SUM(
                    CASE
                        WHEN o.side='LAY'
                             THEN -o.entry_stake*(o.entry_odds-1)
                        WHEN o.side='BACK'
                             THEN  o.entry_stake*(o.entry_odds-1)
                        ELSE 0
                    END
                ) AS pnl_if_win
            FROM orders o
            LEFT JOIN bets b
                   ON b.selectionId=o.selectionId
                  AND b.marketId=o.marketId
            WHERE o.role='PARENT'
              AND o.entry_status='MATCHED'
              AND date(o.opened_at)=date('now','utc')
            GROUP BY o.marketId, o.selectionId, o.engine
        """).fetchall()

        for mid, sid, horse, engine, pnl in rows:

            cur2.execute("""
                INSERT INTO bankstate_runner_snapshot
                (ts, marketId, selectionId, horse_name, pnl_if_win, engine)
                VALUES (?,?,?,?,?,?)
            """, (
                ts,
                str(mid),
                str(sid),
                str(horse or sid),
                float(pnl or 0.0),
                str(engine),
            ))

        con2.commit()
        con2.close()

    except Exception:
        pass

    con.close()

# === PATCH END ==============================================================


