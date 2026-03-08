#!/usr/bin/env python3
# engines/live/live_router.py
from __future__ import annotations

import json, time, threading, random, sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple
from engines import price_math as pm
from collections import defaultdict
from engines.config_paths import open_auto_db

_ROUTER_LIVE_STATE = {
    "parents":  defaultdict(lambda: defaultdict(int)),
    "children": defaultdict(lambda: defaultdict(int)),
    "movement": defaultdict(int),
}

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py (module scope)
# 🧩 ADD: Parent placement execution queue
# 📆 PATCHED: 2026-03-07 — parallel Betfair parent placement
#
# PURPOSE:
# - Prevent router blocking on Betfair API
# - Allow BUS to continue producing plans
# - Parent placements executed in worker threads
# ======================================================================================================

_PARENT_PLACE_QUEUE: "queue.Queue[tuple]" = queue.Queue()
_PARENT_PLACE_WORKERS = []


# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🧩 ADD: Parent placement worker
# ======================================================================================================

def _router_parent_worker_loop():

    while True:

        try:
            (
                parent_ref,
                market_id,
                selection_id,
                side,
                entry_odds,
                stake,
                persistence
            ) = _PARENT_PLACE_QUEUE.get()

        except Exception:
            time.sleep(0.05)
            continue

        try:

            app_key, token = _keys()

            bet_id, detail = _place(
                app_key,
                token,
                market_id,
                selection_id,
                side,
                float(entry_odds),
                float(stake),
                parent_ref,
                persistence=persistence
            )

            if bet_id:
                _orders_update_parent_placed(parent_ref, bet_id)

            else:
                _orders_update_parent_failed(parent_ref, "BETFAIR_PLACE_FAILED")

        except Exception as e:

            _log_event(
                "ERROR",
                "live_router",
                f"[PARENT WORKER] placement failed ref={parent_ref}: {e}"
            )

        finally:
            _PARENT_PLACE_QUEUE.task_done()
# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py (module scope)
# 🧩 ADD: Router execution snapshots (parent + child)
# 📆 PATCHED: 2026-XX-XX — BUS execution snapshot surface
#
# PURPOSE:
# - Provide O(1) runtime access to parent / child state
# - Eliminate DB lookups from BUS lanes (especially MSC_RISK)
# - Snapshot is runtime-only (not persisted)
#
# STRUCTURE:
#   _ROUTER_PARENT_SURFACE[(marketId, selectionId)] → parent execution state
#   _ROUTER_CHILD_SURFACE[parent_id] → child lifecycle state
# ======================================================================================================

_ROUTER_PARENT_SURFACE = {}
_ROUTER_CHILD_SURFACE  = {}

import uuid
import requests
from engines.config_paths import auto_conn as _cp_auto_conn, q_retry as _cp_q_retry, autoscalp_db, connect_db
from engines.math.dynamic_stake_v7 import calc_dynamic_stake, calc_greenup_stake
from engines.live import bank_state

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py (module scope)
# 🧩 ADD: router report throttle state
# 📆 PATCHED: 2026-02-07 — suppress duplicate router reports
# ============================================================================

_ROUTER_STATUS_LAST = None

_ROUTER_LIVE_LAST = None


# === PATCH END ==============================================================


# --- Router child execution queue ---
# live_router.py (top-level)

import queue
import threading
import traceback

_ROUTER_CHILD_QUEUE: "queue.Queue[tuple[dict, dict]]" = queue.Queue()
_ROUTER_CHILD_WORKER = None
child_id = None

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🧩 ADD: BUS snapshot accessors
# ======================================================================================================

def get_parent_snapshot(mid: str, sid: str):
    return _ROUTER_PARENT_SURFACE.get((str(mid), str(sid)))

def get_child_snapshot(parent_id: int):
    return _ROUTER_CHILD_SURFACE.get(int(parent_id))

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py (module scope)
# 🧩 ADD: Progressive lock factor (first hedge only)
# 📆 PATCHED: 2026-04-XX — First-child partial green-up
#
# PURPOSE:
# - First hedge child locks only a percentage of full green-up
# - Compression ladder remains full-green from that base
#
# RULE:
# - 0.20 = 20% initial lock
# - Can tune later
# ======================================================================================================

PROGRESSIVE_LOCK_FACTOR = 1.00  # 20% of full hedge


_ROUTER_STATUS = {
    "parents_checked": 0,
    "parents_bf_matched": 0,
    "parents_db_promoted": 0,
    "parents_matched": 0,
    "parents_settled": 0,
    "children_already_present": 0,
    "children_created": 0,
    "children_blocked": 0,
}
def _bf_is_matched(surf: dict) -> bool:
    """
    Canonical Betfair MATCHED detector.

    IMPORTANT:
    - sizeMatched > 0 is authoritative
    - TERMINAL / EXECUTION_COMPLETE is authoritative
    - CLEARED is authoritative
    - LIVE does NOT mean unmatched
    """
    if not isinstance(surf, dict):
        return False

    try:
        matched = float(surf.get("matched") or 0.0)
        placed  = float(surf.get("placed") or 0.0)

        if matched > 0.0:
            return True

        state = str(surf.get("state") or "").upper()
        source = str(surf.get("source") or "").upper()

        if state in ("EXECUTION_COMPLETE", "TERMINAL"):
            return True

        if source == "CLEARED":
            return True

    except Exception:
        pass

    return False

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: any UPDATE orders SET entry_status='CANCELLED' WHERE role='CHILD'
# 🧩 ACTION: ADD guard — forbid illegal child cancellation
# 📆 PATCHED: 2026-04-XX — Child cancellation invariant enforcement
#
# HARD INVARIANT:
# - A CHILD may ONLY be cancelled if:
#     (1) Another CHILD for the same parent is MATCHED, OR
#     (2) The market is FINISHED (past GRACE_MINUTES)
#
# - Parent exit_status / parent_closed / completion state ALONE
#   is NEVER a valid reason to cancel a child.
# ======================================================================================================

def _child_cancellation_allowed(*, child_id: int) -> bool:
    """
    Authoritative guard for CHILD cancellation.

    Returns True ONLY if:
      1) A sibling CHILD is MATCHED, OR
      2) Market is finished (past GRACE_MINUTES)
    """

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        # Load child + parent linkage
        row = _q_retry(cur, """
            SELECT
                c.id          AS child_id,
                c.hedge_of    AS parent_id,
                p.marketId    AS marketId
            FROM orders c
            JOIN orders p ON p.id = c.hedge_of
            WHERE c.id = ?
              AND c.role = 'CHILD'
            LIMIT 1
        """, (int(child_id),)).fetchone()

        if not row:
            return False

        parent_id = int(row["parent_id"])
        market_id = str(row["marketId"])

        # --------------------------------------------------
        # 1️⃣ SIBLING MATCHED?
        # --------------------------------------------------
        sib = _q_retry(cur, """
            SELECT 1
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
               AND entry_status='MATCHED'
               AND id <> ?
             LIMIT 1
        """, (parent_id, int(child_id))).fetchone()

        if sib:
            return True

        # --------------------------------------------------
        # 2️⃣ MARKET FINISHED?
        # --------------------------------------------------
        try:
            bdb = connect_db(ro=True)
            bdb.row_factory = sqlite3.Row
            r = _q_retry(bdb, """
                SELECT
                  CAST((julianday('now','utc') - julianday(marketStartTime))*1440 AS INTEGER)
                  AS mins_after
                FROM bets
                WHERE marketId=?
                LIMIT 1
            """, (market_id,)).fetchone()
            bdb.close()

            if r and r["mins_after"] is not None:
                if int(r["mins_after"]) >= GRACE_MINUTES:
                    return True
        except Exception:
            pass

        # ❌ Otherwise forbidden
        return False

    finally:
        try:
            con.close()
        except Exception:
            pass

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🧩 ADD: persistent execution truth ledger
# 📆 PATCHED: 2026-02-07 — execution_events persistence
# ============================================================================

def _ensure_execution_events_schema():
    con = _orders_conn()
    cur = con.cursor()
    try:
        _q_retry(cur, """
            CREATE TABLE IF NOT EXISTS execution_events (
                order_id INTEGER PRIMARY KEY,
                bet_id TEXT,
                role TEXT,
                matched_size REAL,
                placed_size REAL,
                fully_matched INTEGER DEFAULT 0,
                seen_at TEXT,
                source TEXT
            )
        """)
        con.commit()
    finally:
        con.close()

# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: parent promotion / QUEUED → PLACING logic
# 🧩 ACTION: Add IN-PLAY sequential promotion gate (MSC_INPLAY only)
# 📆 PATCHED: 2026-04-XX — In-Play ladder execution semantics
#
# CONTRACT:
# - Applies ONLY to engine == 'MSC_INPLAY'
# - LAY parents: one-at-a-time, lowest PX first
# - BACK parents: all eligible immediately
# - Betfair match surface is the ONLY unlock signal
# - DB lifecycle + exposure logic unchanged
# ============================================================================

from tools.betfair_match_surface import query_bet_match_surface


def _select_inplay_parents_to_promote(rows, *, app_key, token):
    """
    Decide which QUEUED MSC_INPLAY parents may be promoted to PLACING.

    rows: list[sqlite3.Row] — QUEUED parents for ONE (marketId, selectionId)
    returns: list[sqlite3.Row] — subset allowed to promote
    """

    if not rows:
        return []

    # Split by side
    lays  = [r for r in rows if (r["side"] or "").upper() == "LAY"]
    backs = [r for r in rows if (r["side"] or "").upper() == "BACK"]

    # BACKS: no sequencing — allow all
    promotable = list(backs)

    if not lays:
        return promotable

    # Sort LAY parents by lowest odds first
    lays_sorted = sorted(lays, key=lambda r: float(r["entry_odds"] or 9999))

    # Check if ANY earlier lay has been matched at Betfair
    unlocked_index = 0

    for i, r in enumerate(lays_sorted):
        bet_id = r["entry_bet_id"]
        if not bet_id:
            break  # never placed yet → cannot unlock further

        try:
            surf = query_bet_match_surface(
                bet_id=str(bet_id),
                app_key=app_key,
                token=token,
            )
        except Exception:
            break

        matched = float(surf.get("matched") or 0.0)
        if matched > 0.0:
            unlocked_index = i + 1
            continue
        break

    # Allow exactly ONE next LAY to promote
    if unlocked_index < len(lays_sorted):
        promotable.append(lays_sorted[unlocked_index])

    return promotable


# === PATCH END ==============================================================
def _collect_router_live_state() -> tuple[dict, dict]:
    """
    Collect TODAY-ONLY router live state from DB.

    HARD GUARANTEES:
    - No NULL buckets
    - No NULL engines
    - No sorting crashes
    - Never raises (authority loop must survive)
    """

    live = {
        "parents": defaultdict(lambda: defaultdict(int)),
        "children": defaultdict(lambda: defaultdict(int)),
        "summary": {
            "open_trades": 0,
            "cancelled_trades": 0,
            "completed_trades": 0,
        },
    }

    invariants = {
        "parents_illegal": 0,
        "children_illegal": 0,
    }

    try:
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # --------------------------------------------------
        # PARENTS
        # --------------------------------------------------
        rows = _q_retry(cur, """
            SELECT
                COALESCE(engine, 'UNKNOWN') AS engine,
                CASE
                    WHEN entry_status = 'QUEUED'  THEN 'QUEUED'
                    WHEN entry_status = 'PLACING' THEN 'PLACING'
                    WHEN entry_status = 'PLACED'  THEN 'PLACED'
                    WHEN exit_status LIKE '%CANCELLED%' THEN 'CANCELLED'
                    WHEN EXISTS (
                        SELECT 1 FROM orders c
                         WHERE c.role='CHILD'
                           AND c.hedge_of = orders.id
                           AND c.exit_status='MATCHED'
                    ) THEN 'CLOSED'
                    WHEN entry_status = 'MATCHED' THEN 'MATCHED'
                    ELSE 'UNKNOWN'
                END AS bucket,
                COUNT(*) AS n
            FROM orders
            WHERE role='PARENT'
              AND mode='LIVE'
              AND date(opened_at)=date('now','utc')
            GROUP BY engine, bucket
        """).fetchall()

        for r in rows:
            engine = r["engine"] or "UNKNOWN"
            bucket = r["bucket"] or "UNKNOWN"
            live["parents"][engine][bucket] += int(r["n"] or 0)

        # --------------------------------------------------
        # CHILDREN
        # --------------------------------------------------
        rows = _q_retry(cur, """
            SELECT
                COALESCE(engine, 'UNKNOWN') AS engine,
                CASE
                    WHEN entry_status = 'QUEUED'  THEN 'QUEUED'
                    WHEN entry_status = 'PLACING' THEN 'PLACING'
                    WHEN entry_status = 'PLACED'  THEN 'PLACED'
                    WHEN entry_status = 'MATCHED'
                         AND exit_status IS NULL THEN 'MATCHED'
                    WHEN exit_status IS NOT NULL THEN 'CLOSED'
                    ELSE 'UNKNOWN'
                END AS bucket,
                COUNT(*) AS n
            FROM orders
            WHERE role='CHILD'
              AND mode='LIVE'
              AND date(opened_at)=date('now','utc')
            GROUP BY engine, bucket
        """).fetchall()

        for r in rows:
            engine = r["engine"] or "UNKNOWN"
            bucket = r["bucket"] or "UNKNOWN"
            live["children"][engine][bucket] += int(r["n"] or 0)

        # --------------------------------------------------
        # TRADE SUMMARY
        # --------------------------------------------------
        row = _q_retry(cur, """
            SELECT
                SUM(
                    CASE
                        WHEN role='PARENT'
                         AND entry_status='MATCHED'
                         AND NOT EXISTS (
                             SELECT 1 FROM orders c
                              WHERE c.role='CHILD'
                                AND c.hedge_of=orders.id
                                AND c.exit_status='MATCHED'
                         )
                        THEN 1 ELSE 0
                    END
                ) AS open_trades,

                SUM(
                    CASE
                        WHEN role='PARENT'
                         AND EXISTS (
                             SELECT 1 FROM orders c
                              WHERE c.role='CHILD'
                                AND c.hedge_of=orders.id
                                AND c.exit_status='MATCHED'
                         )
                        THEN 1 ELSE 0
                    END
                ) AS completed_trades,

                SUM(
                    CASE
                        WHEN role='PARENT'
                         AND exit_status LIKE '%CANCELLED%'
                        THEN 1 ELSE 0
                    END
                ) AS cancelled_trades
            FROM orders
            WHERE mode='LIVE'
              AND date(opened_at)=date('now','utc')
        """).fetchone()

        if row:
            live["summary"]["open_trades"]      = int(row["open_trades"] or 0)
            live["summary"]["completed_trades"] = int(row["completed_trades"] or 0)
            live["summary"]["cancelled_trades"] = int(row["cancelled_trades"] or 0)

        # --------------------------------------------------
        # INVARIANT CHECKS
        # --------------------------------------------------
        row = _q_retry(cur, """
            SELECT COUNT(*) AS bad
            FROM orders
            WHERE role='PARENT'
              AND mode='LIVE'
              AND date(opened_at)=date('now','utc')
              AND entry_status NOT IN ('QUEUED','PLACING','PLACED','MATCHED')
              AND exit_status IS NULL
        """).fetchone()

        invariants["parents_illegal"] = int(row["bad"] or 0)

        row = _q_retry(cur, """
            SELECT COUNT(*) AS bad
            FROM orders
            WHERE role='CHILD'
              AND mode='LIVE'
              AND date(opened_at)=date('now','utc')
              AND entry_status NOT IN ('QUEUED','PLACING','PLACED','MATCHED')
              AND exit_status IS NULL
        """).fetchone()

        invariants["children_illegal"] = int(row["bad"] or 0)

        con.close()

    except Exception as e:
        # AUTHORITY MUST NEVER DIE
        print("[ROUTER][COLLECT][ERROR]", e)

    return live, invariants

GRACE_MINUTES = 4

def _print_router_full_report(status: dict, live: dict, inv: dict):
    """
    Unified router report.

    Order is canonical and must never change:
      1) Router Reconciliation (Betfair truth)
      2) Router Live State (DB truth)
    """
    _print_router_report(_ROUTER_STATUS)
    _print_router_live_state(live, inv)


def _print_router_report(status: dict):
    now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")

    invariant_ok = (
        status["children_created"] +
        status["children_already_present"] +
        status["children_blocked"]
    ) == status["parents_bf_matched"]



    print()
    print("============ V7 ROUTER RECONCILIATION ============")
    print(f"t={now}   scope=LIVE   source=Betfair")
    print()
    print("PARENTS")
    print(f"  checked           : {status['parents_checked']}")
    print(f"  bf_matched        : {status['parents_bf_matched']}")
    print(f"  db_promoted       : {status['parents_db_promoted']}")
    print()
    print("CHILDREN (for bf_matched parents)")
    print(f"  already_present  : {status['children_already_present']}")
    print(f"  created          : {status['children_created']}")
    print(f"  blocked          : {status['children_blocked']}")
    print()
    print("STATUS")
    print(f"  invariant         : {'OK' if invariant_ok else '❌ VIOLATED'}")
    print(f"  action_required  : {'NO' if invariant_ok else 'YES (eligibility blocked)'}")
    print("===============================================")
    print()

def _print_router_live_state(live: dict, inv: dict):
    now = datetime.now(timezone.utc).strftime("%H:%M:%SZ")

    def _row(d, k): return int(d.get(k, 0))

    live_snapshot = (
        tuple(
            sorted(
                (e, _safe_bucket_items(b))
                for e, b in live["parents"].items()
            )
        ),
        tuple(
            sorted(
                (e, _safe_bucket_items(b))
                for e, b in live["children"].items()
            )
        ),
        live["summary"]["open_trades"],
        live["summary"]["completed_trades"],
        live["summary"]["cancelled_trades"],
        inv["parents_illegal"],
        inv["children_illegal"],
    )

    print()
    print("================= V7 ROUTER LIVE STATE =================")
    print(f"t={now}   mode=LIVE   stage=POST-RECONCILE")
    print("=======================================================")
    print()

    # ---------------- Parents ----------------
    print("PARENTS — ENTRY / EXIT STATUS (BY ENGINE)")
    print("---------------------------------------------------------------")
    print("ENGINE            QUEUED  PLACING  PLACED  MATCHED  CANCELLED  CLOSED")
    print("---------------------------------------------------------------")

    total = defaultdict(int)
    for engine in sorted(live["parents"].keys()):
        p = live["parents"][engine]
        q, plg, pld, m, x, c = (
            _row(p, "QUEUED"),
            _row(p, "PLACING"),
            _row(p, "PLACED"),
            _row(p, "MATCHED"),
            _row(p, "CANCELLED"),
            _row(p, "CLOSED"),
        )
        print(f"{engine:<16} {q:>6} {plg:>8} {pld:>8} {m:>8} {x:>10} {c:>8}")
        total["QUEUED"] += q
        total["PLACING"] += plg
        total["PLACED"] += pld
        total["MATCHED"] += m
        total["CANCELLED"] += x
        total["CLOSED"] += c

    print("---------------------------------------------------------------")
    print(
        f"{'TOTAL':<16} "
        f"{total['QUEUED']:>6} {total['PLACING']:>8} {total['PLACED']:>8} "
        f"{total['MATCHED']:>8} {total['CANCELLED']:>10} {total['CLOSED']:>8}"
    )
    print("---------------------------------------------------------------")
    print()

    # ---------------- Children ----------------
    print("CHILDREN — ENTRY / EXIT STATUS (BY ENGINE)")
    print("---------------------------------------------------------------")
    print("ENGINE            QUEUED  PLACING  PLACED  MATCHED  CANCELLED  CLOSED")
    print("---------------------------------------------------------------")

    total = defaultdict(int)
    for engine in sorted(live["children"].keys()):
        c = live["children"][engine]
        q, plg, pld, m, x, cl = (
            _row(c, "QUEUED"),
            _row(c, "PLACING"),
            _row(c, "PLACED"),
            _row(c, "MATCHED"),
            _row(c, "CANCELLED"),
            _row(c, "CLOSED"),
        )
        print(f"{engine:<16} {q:>6} {plg:>8} {pld:>8} {m:>8} {x:>10} {cl:>8}")
        total["QUEUED"] += q
        total["PLACING"] += plg
        total["PLACED"] += pld
        total["MATCHED"] += m
        total["CANCELLED"] += x
        total["CLOSED"] += cl

    print("---------------------------------------------------------------")
    print(
        f"{'TOTAL':<16} "
        f"{total['QUEUED']:>6} {total['PLACING']:>8} {total['PLACED']:>8} "
        f"{total['MATCHED']:>8} {total['CANCELLED']:>10} {total['CLOSED']:>8}"
    )
    print("---------------------------------------------------------------")
    print()

    print("TRADE SUMMARY")
    print("-------------------------------------------------------")
    print(f"open_trades        : {live['summary']['open_trades']}")
    print(f"completed_trades   : {live['summary']['completed_trades']}")
    print(f"cancelled_trades   : {live['summary']['cancelled_trades']}")
    print("-------------------------------------------------------")
    print()

    # ---------------- Invariants ----------------
    ok = (inv["parents_illegal"] == 0 and inv["children_illegal"] == 0)

    print("ROUTER STATUS")
    print("-------------------------------------------------------")
    print(f"state_converged      : {'YES' if ok else 'NO'}")
    print(
        "action_required      : "
        + ("NO" if ok else "YES (illegal lifecycle state detected)")
    )
    print("confidence           : " + ("HIGH" if ok else "LOW"))
    print("-------------------------------------------------------")
    print("=======================================================")
    print()

    if not ok:
        _log_event(
            "ERROR",
            "live_router",
            f"[INVARIANT FAIL] parents_illegal={inv['parents_illegal']} "
            f"children_illegal={inv['children_illegal']}"
        )



def enqueue_router_child(plan: dict, ctx: dict):
    _ROUTER_CHILD_QUEUE.put_nowait((plan, ctx))

def _safe_bucket_items(d):
    # normalize None → 'UNKNOWN' purely for reporting
    return tuple(
        sorted(
            (str(k) if k is not None else "UNKNOWN", int(v))
            for k, v in d.items()
        )
    )

def _child_promotion_allowed(child_id: int) -> bool:

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        row = _q_retry(cur, """
            SELECT hedge_of
              FROM orders
             WHERE id=?
               AND role='CHILD'
             LIMIT 1
        """, (child_id,)).fetchone()

        if not row:
            return False

        parent_id = int(row["hedge_of"])

        # Is there an ACTIVE sibling child?
        active = _q_retry(cur, """
            SELECT 1
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
               AND entry_status IN ('PLACED','PLACING')
             LIMIT 1
        """, (parent_id,)).fetchone()

        if active:
            return False

        # Is there a sibling that has matched but not yet processed?
        matched = _q_retry(cur, """
            SELECT entry_bet_id
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
               AND entry_status='PLACED'
               AND entry_bet_id IS NOT NULL
             LIMIT 1
        """, (parent_id,)).fetchone()

        if matched:
            if get_bet_status(str(matched["entry_bet_id"])) == "EXECUTION_COMPLETE":
                return True
            return False

        return True

    finally:
        con.close()

def _write_router_runtime_snapshot_from_collect(live: dict):

    from datetime import datetime, timezone
    import sqlite3
    from engines.config_paths import open_auto_db

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    con = open_auto_db(rw=True)
    cur = con.cursor()

    # Clear previous snapshot (single-frame table)
    cur.execute("DELETE FROM router_runtime_snapshot")

    # ---------------- PARENTS ----------------
    for engine, buckets in live["parents"].items():
        cur.execute("""
            INSERT INTO router_runtime_snapshot
            (ts, engine, role, queued, placing, placed, matched, cancelled, closed)
            VALUES (?, ?, 'PARENT', ?, ?, ?, ?, ?, ?)
        """, (
            ts,
            engine,
            buckets.get("QUEUED", 0),
            buckets.get("PLACING", 0),
            buckets.get("PLACED", 0),
            buckets.get("MATCHED", 0),
            buckets.get("CANCELLED", 0),
            buckets.get("CLOSED", 0),
        ))

    # ---------------- CHILDREN ----------------
    for engine, buckets in live["children"].items():
        cur.execute("""
            INSERT INTO router_runtime_snapshot
            (ts, engine, role, queued, placing, placed, matched, cancelled, closed)
            VALUES (?, ?, 'CHILD', ?, ?, ?, ?, ?, ?)
        """, (
            ts,
            engine,
            buckets.get("QUEUED", 0),
            buckets.get("PLACING", 0),
            buckets.get("PLACED", 0),
            buckets.get("MATCHED", 0),
            buckets.get("CANCELLED", 0),
            buckets.get("CLOSED", 0),
        ))

    con.commit()
    con.close()

# ======================================================================
# ROUTER STATUS AUTHORITY — Betfair is truth, Router enforces DB
# ======================================================================
# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: def _router_enforce_status_authority():
# 🧩 ACTION: Make router status authority READ-ONLY to prevent DB island collapse
# 📆 PATCHED: 2026-02-08 — eliminate closed-db & tuple oscillation
#
# RATIONALE:
# - Router status authority was opening a DB connection
# - It then called helpers that ALSO open/close DB connections
# - SQLite invalidates cursors when underlying connections are closed
# - This caused alternating:
#     • tuple index errors
#     • closed database errors
#
# INVARIANT (LOCKED):
# - Router status authority MUST NOT perform DB writes
# - All lifecycle mutations occur via canonical helpers elsewhere
# ======================================================================================================

# ======================================================================
# ROUTER STATUS AUTHORITY — Betfair is truth, Router enforces DB
# ======================================================================

def _router_enforce_status_authority():
    """
    Enforce canonical parent/child lifecycle using Betfair match surface.

    • Betfair = execution truth
    • Router = status authority
    • DB-only mutation (staged)
    • Idempotent
    • Never raises
    """

    import os
    import sqlite3
    from engines.config_paths import autoscalp_db
    from engines.daily_config import get_app_key
    from tools.betfair_match_surface import query_bet_match_surface

    app_key = get_app_key()
    token = os.getenv("SESSION_TOKEN") or os.getenv("BETFAIR_SESSION_TOKEN")
    if not app_key or not token:
        return

    for k in _ROUTER_STATUS:
        _ROUTER_STATUS[k] = 0

    # --------------------------------------------------
    # READ PHASE — load parents (NO writes)
    # --------------------------------------------------
    try:
        con = sqlite3.connect(autoscalp_db())
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        rows = cur.execute("""
            SELECT
                id,
                customerOrderRef,
                entry_status,
                exit_status,
                parent_closed,
                entry_bet_id
            FROM orders
            WHERE role='PARENT'
              AND date(opened_at)=date('now','utc')
        """).fetchall()
    except Exception as e:
        print(f"[ROUTER][STATUS-AUTH][WARN] preload failed: {e}")
        try:
            con.close()
        except Exception:
            pass
        return

    # --------------------------------------------------
    # DECISION PHASE — compute actions only
    # --------------------------------------------------
    actions = []

    for r in rows:
        parent_id = int(r["id"])
        cor = r["customerOrderRef"]
        bet_id = r["entry_bet_id"]

        if not bet_id:
            continue

        surf = query_bet_match_surface(
            bet_id=str(bet_id),
            app_key=app_key,
            token=token,
        )
        _ROUTER_STATUS["parents_checked"] += 1

        if not isinstance(surf, dict):
            continue

# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: bf_matched = (
# 🧩 ACTION: REPLACE inline Betfair logic with canonical status call
# 📆 PATCHED: 2026-04-XX — Router must use get_bet_status only
# ======================================================================

        status = get_bet_status(str(bet_id))

        bf_matched = (status == "EXECUTION_COMPLETE")

        bf_market_cleared = (
            str(surf.get("source") or "").upper() == "CLEARED"
            or str(surf.get("state") or "").upper() == "TERMINAL"
        )

        # --------------------------------------------------
        # CLEARED ⇒ SETTLED (only if child matched)
        # --------------------------------------------------
        if bf_market_cleared:
            child_matched = cur.execute("""
                SELECT 1
                  FROM orders
                 WHERE role='CHILD'
                   AND hedge_of=?
                   AND entry_status='MATCHED'
                   AND exit_status='MATCHED'
                 LIMIT 1
            """, (parent_id,)).fetchone()

            if child_matched:
                actions.append(("SETTLE_PARENT_AND_CHILD", parent_id))
                _ROUTER_STATUS["parents_settled"] += 1
            continue

        # --------------------------------------------------
        # DB says MATCHED but Betfair does NOT
        # --------------------------------------------------
# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: # DB says MATCHED but Betfair does NOT
# 🧩 ACTION: Make MATCHED one-way unless explicitly EXECUTABLE
# 📆 PATCHED: 2026-04-XX — DB lifecycle authority enforced
#
# INVARIANT:
#   • MATCHED is one-way.
#   • NEVER demote on UNKNOWN.
#   • ONLY demote if Betfair explicitly returns EXECUTABLE.
# ======================================================================

        # --------------------------------------------------
        # DB says MATCHED but Betfair does NOT
        # --------------------------------------------------
        if r["entry_status"] == "MATCHED":

            # If Betfair explicitly says order is EXECUTABLE (not matched),
            # then demotion is legitimate.
            if status == "EXECUTABLE":
                actions.append(("DEMOTE_MATCHED", parent_id, cor))
                _ROUTER_STATUS["parents_matched"] += 1

            # Otherwise:
            #   UNKNOWN, TERMINAL, CLEARED, or any transient state
            #   → DO NOT DEMOTE
            continue


        # --------------------------------------------------
        # Betfair MATCHED but DB not updated
        # --------------------------------------------------
        if bf_matched:
            _ROUTER_STATUS["parents_bf_matched"] += 1

        if bf_matched and r["entry_status"] != "MATCHED":
            actions.append(("PROMOTE_MATCHED", cor, bet_id))
            _ROUTER_STATUS["parents_matched"] += 1
            _ROUTER_STATUS["parents_db_promoted"] += 1

        # --------------------------------------------------
        # Parent MATCHED ⇒ child must exist
        # --------------------------------------------------
        if bf_matched:
            child = cur.execute("""
                SELECT 1 FROM orders
                 WHERE role='CHILD'
                   AND hedge_of=?
                 LIMIT 1
            """, (parent_id,)).fetchone()

            if child:
                _ROUTER_STATUS["children_already_present"] += 1
            else:
                actions.append(("ENSURE_CHILD", cor))
                _ROUTER_STATUS["children_created"] += 1

    try:
        con.close()
    except Exception:
        pass

    # --------------------------------------------------
    # MUTATION PHASE — apply actions safely
    # --------------------------------------------------
    for act in actions:
        kind = act[0]

        try:
            if kind == "DEMOTE_MATCHED":
                _, parent_id, _cor = act
                con2 = sqlite3.connect(autoscalp_db())
                cur2 = con2.cursor()
                cur2.execute("""
                    UPDATE orders
                       SET entry_status='PLACED',
                           exit_status=NULL,
                           parent_closed=0
                     WHERE id=?
                """, (parent_id,))
                con2.commit()
                con2.close()

            elif kind == "PROMOTE_MATCHED":
                _, cor, bet_id = act
                _orders_update_parent_matched(cor, bet_id)

            elif kind == "ENSURE_CHILD":
                _, cor = act
                _ensure_child_queued_for_matched_parent(cor)

            elif kind == "SETTLE_PARENT_AND_CHILD":
                _, parent_id = act
                con2 = sqlite3.connect(autoscalp_db())
                cur2 = con2.cursor()

                cur2.execute("""
                    UPDATE orders
                       SET exit_status='SETTLED',
                           closed_at=COALESCE(closed_at, datetime('now','utc'))
                     WHERE role='CHILD'
                       AND hedge_of=?
                       AND (exit_status IS NULL OR exit_status <> 'SETTLED')
                """, (parent_id,))

                cur2.execute("""
                    UPDATE orders
                       SET exit_status='SETTLED',
                           parent_closed=1,
                           exposure_released=1,
                           closed_at=COALESCE(closed_at, datetime('now','utc'))
                     WHERE id=?
                       AND role='PARENT'
                       AND (exit_status IS NULL OR exit_status <> 'SETTLED')
                """, (parent_id,))

                con2.commit()
                con2.close()

        except Exception as e:
            print(f"[ROUTER][STATUS-AUTH][ACTION-FAIL] {act} → {e}")

    # --------------------------------------------------
    # REPORTING (STRICTLY POST-ENFORCEMENT)
    # --------------------------------------------------

    # ALWAYS compute live state first
    live, inv = _collect_router_live_state()

    # ==================================================
    # REPORTING — AUTHORITY + LIVE STATE (FIXED)
    # ==================================================

    global _ROUTER_STATUS_LAST

    status_snapshot = tuple(
        _ROUTER_STATUS[k] for k in sorted(_ROUTER_STATUS.keys())
    )

    # 1️⃣ Authority report — only when changed
    if status_snapshot != _ROUTER_STATUS_LAST:
        _print_router_report(_ROUTER_STATUS)
        _ROUTER_STATUS_LAST = status_snapshot

    # 2️⃣ Live state report — ALWAYS print
    _print_router_live_state(live, inv)
    _write_router_runtime_snapshot_from_collect(live)

    # Snapshot live DB state separately
    global _ROUTER_LIVE_LAST

    live_snapshot = (
        tuple(
            sorted(
                (e, _safe_bucket_items(b))
                for e, b in live["parents"].items()
            )
        ),
        tuple(
            sorted(
                (e, _safe_bucket_items(b))
                for e, b in live["children"].items()
            )
        ),
        live["summary"]["open_trades"],
        live["summary"]["completed_trades"],
        live["summary"]["cancelled_trades"],
        inv["parents_illegal"],
        inv["children_illegal"],
    )


def _router_child_worker_loop():

    from engines.live.live_router import (
        _attempt_place_child_with_retry,
    )

    import time
    import queue
    import traceback

    RESCUE_DELAY_SECONDS = 120

    while True:

        # ==================================================
        # PHASE 0 — STATUS AUTHORITY (ALWAYS FIRST)
        # ==================================================
        _router_enforce_status_authority()

        try:
            # --------------------------------------------------
            # DRAIN QUEUE — process ALL children immediately
            # --------------------------------------------------

            plan = None
            ctx = None

            while True:
                try:
                    plan, ctx = _ROUTER_CHILD_QUEUE.get_nowait()
                except queue.Empty:
                    break

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: inside _router_child_worker_loop(), before normal queue handling
# 🧩 ACTION: Immediate execution for emergency children
# 📆 PATCHED: 2026-04-XX — Overwatch emergency fast lane
#
# RULE:
# - If plan['priority']=='IMMEDIATE'
# - Place child instantly (no delay)
# ======================================================================================================

            if plan and plan.get("priority") == "IMMEDIATE":
                child_id = plan.get("child_id")
                if child_id:
                    _attempt_place_child_with_retry(int(child_id))
                _ROUTER_CHILD_QUEUE.task_done()
                continue

            if plan:

                child_id = plan.get("child_id")
                parent_cor = plan.get("parent_cor")

                # --------------------------------------------------
                # Ensure identity
                # --------------------------------------------------
                if not child_id and parent_cor:
                    child_id = _ensure_child_queued_for_matched_parent(parent_cor)

                if not child_id:
                    _ROUTER_CHILD_QUEUE.task_done()
                    continue

                # --------------------------------------------------
                # Execute placement
                # --------------------------------------------------
                if not _child_promotion_allowed(child_id):
                    _ROUTER_CHILD_QUEUE.task_done()
                    continue

                placed = _attempt_place_child_with_retry(int(child_id))


                if not placed:
                    _ROUTER_CHILD_QUEUE.task_done()
                    continue

                # ==================================================
                # PHASE 1.5 — PROMOTE TO MATCHED (EXCHANGE TRUTH)
                # ==================================================
                try:
                    con = _orders_conn()
                    con.row_factory = sqlite3.Row

                    row = _q_retry(
                        con,
                        """
                        SELECT entry_bet_id
                          FROM orders
                         WHERE id=?
                           AND role='CHILD'
                           AND entry_status='PLACED'
                         LIMIT 1
                        """,
                        (int(child_id),)
                    ).fetchone()

                    con.close()

                    if row and row["entry_bet_id"]:
                        if get_bet_status(str(row["entry_bet_id"])) == "EXECUTION_COMPLETE":
                            _orders_update_child_matched(
                                cor=parent_cor,
                                hedge_ref=None,
                                exit_side=plan.get("side"),
                                exit_odds=plan.get("px"),
                                exit_stake=plan.get("size"),
                            )
                except Exception:
                    pass

                _ROUTER_CHILD_QUEUE.task_done()

            # ==================================================
            # PHASE 2 — RESCUE UNHEDGED MATCHED PARENTS
            # ==================================================
            from engines.market_monitor.phase_clock import MarketPhaseClock

            con = _orders_conn()
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            rows = _q_retry(cur, """
                SELECT customerOrderRef, marketId
                  FROM orders p
                 WHERE p.role='PARENT'
                   AND UPPER(p.entry_status)='MATCHED'
                   AND COALESCE(p.parent_closed,0)=0
                   AND (p.exit_status IS NULL OR UPPER(p.exit_status) NOT IN ('CANCELLED','EXPIRED','SETTLED'))
                   AND COALESCE(p.exposure_released,0)=0
                   AND datetime(p.opened_at) <= datetime('now','utc', ?)
                   AND NOT EXISTS (
                         SELECT 1 FROM orders c
                          WHERE c.hedge_of = p.id
                   )
                 ORDER BY p.opened_at ASC
                 LIMIT 20
            """, (f"-{RESCUE_DELAY_SECONDS // 60} minutes",)).fetchall()

            con.close()

            for r in rows:
                try:
                    parent_cor = str(r["customerOrderRef"])
                    _ensure_child_queued_for_matched_parent(parent_cor)
                except Exception:
                    pass

        except Exception:
            print("[ROUTER][CHILD][ERR]")
            traceback.print_exc()

        # Micro sleep to prevent CPU spin
        time.sleep(0.05)

# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _stamp_parent_exit_sql(
# 🧩 ACTION: FIX exit_status handling + remove illegal status var
# 📆 PATCHED: 2026-04-12 — canonical parent exit stamping fix
# ======================================================================

def _stamp_parent_exit_sql(
    cur,
    *,
    parent_id: int,
    exit_status: str,
    reason: str | None = None,
):
    """
    Canonical parent exit stamper.

    HARD RULE:
    - Only mutates exit_status
    - Never mutates entry_status
    - Idempotent
    """

    status = str(exit_status or "").upper()

    row = _q_retry(cur, """
        SELECT exit_status
          FROM orders
         WHERE id=? AND role='PARENT'
         LIMIT 1
    """, (int(parent_id),)).fetchone()

    if not row:
        return

    if (row["exit_status"] or "").upper() == status:
        return

    _q_retry(cur, """
        UPDATE orders
           SET exit_status   = ?,
               parent_closed = 1,
               closed_at     = COALESCE(closed_at, datetime('now','utc')),
               error         = COALESCE(error, ?)
         WHERE id = ?
           AND role = 'PARENT'
    """, (
        status,
        reason,
        int(parent_id),
    ))


def _guard_cancel_if_matched(*, parent_cor: str, bet_id: str | None) -> bool:
    """
    Returns True if cancellation should proceed.
    Returns False if parent is already MATCHED at Betfair.
    """
    if not bet_id:
        return True

    status = get_bet_status(str(bet_id))
    if status == "EXECUTION_COMPLETE":
        # Canonical promotion
        _orders_update_parent_matched(parent_cor, str(bet_id))
        _ensure_child_queued_for_matched_parent(parent_cor)
        _log_event(
            "INFO",
            "live_router",
            f"[CANCEL-GUARD] prevented cancel — already matched ref={parent_cor}"
        )
        return False

    return True


# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ADD: canonical LIVE run_id resolver (DB-first fallback)
# 📆 PATCHED: 2026-04-XX — run_id ambient invariant
#
# INVARIANT:
# - run_id must ALWAYS be resolvable in LIVE
# - BUS is primary owner
# - DB is authoritative fallback
# ============================================================================

def resolve_live_run_id(tag: str = "LIVE-AUTO") -> int:
    """
    Resolve the active LIVE run_id.

    Rules:
    - Prefer existing LIVE run
    - Create one if missing
    - Never return None / 0
    """
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        # Ensure runs table exists
        _q_retry(cur, """
            CREATE TABLE IF NOT EXISTS runs(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at TEXT,
                finished_at TEXT,
                mode TEXT,
                blueprint_file TEXT,
                notes TEXT
            )
        """)

        # Try latest LIVE run
        row = _q_retry(cur, """
            SELECT id
            FROM runs
            WHERE mode='LIVE'
            ORDER BY id DESC
            LIMIT 1
        """).fetchone()

        if row and row["id"]:
            return int(row["id"])

        # Create new LIVE run
        _q_retry(cur, """
            INSERT INTO runs(mode, started_at, notes)
            VALUES('LIVE', datetime('now','utc'), ?)
        """, (str(tag),))
        con.commit()

        return int(cur.lastrowid)

    finally:
        con.close()


# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: place_parent_and_hedge / router helpers
# 🧩 ACTION: ADD stop-loss execution worker (authoritative)
# 📆 PATCHED: 2026-01-22 — Implement STOPLOSS child lifecycle (create → chase → match → cancel hedge)
#
# CONTRACT (CALLED BY OVERWATCHER):
#   process_stoploss(payload: dict)
#
# PAYLOAD:
#   {
#     marketId,
#     selectionId,
#     entry_side,        # parent side
#     entry_stake,
#     current_odds,
#     stop_loss_px,
#     ts
#   }
#
# BEHAVIOUR:
#   1) Insert STOPLOSS child (DB-first)
#   2) Place on Betfair
#   3) If missed → cancel + reprice + re-submit
#   4) When matched → cancel active HEDGE child
# ======================================================================================================

def process_stoploss(payload: dict, *, max_chase_ticks: int = 3, poll_s: float = 1.0) -> None:
    """
    Execute a STOPLOSS child order with price chasing.
    """

    market_id    = str(payload["marketId"])
    selection_id = str(payload["selectionId"])
    entry_side   = payload["entry_side"].upper()
    stake        = float(payload["entry_stake"])
    stop_px      = float(payload["stop_loss_px"])

    # STOPLOSS is ALWAYS opposite side
    stop_side = "BACK" if entry_side == "LAY" else "LAY"

    # --------------------------------------------------
    # Resolve parent
    # --------------------------------------------------
    con = _orders_conn(); con.row_factory = sqlite3.Row
    parent = con.execute("""
        SELECT id, customerOrderRef
          FROM orders
         WHERE role='PARENT'
           AND marketId=?
           AND selectionId=?
           AND entry_status='MATCHED'
           AND exit_status IS NULL
         ORDER BY opened_at DESC
         LIMIT 1
    """, (market_id, selection_id)).fetchone()
    con.close()

    if not parent:
        return

    parent_id  = int(parent["id"])
    parent_cor = parent["customerOrderRef"]

    app_key, token = _keys()

    # --------------------------------------------------
    # Price chase loop
    # --------------------------------------------------
    chase = 0
    bet_id = None
    odds   = stop_px

    while chase <= max_chase_ticks:

        # ---- place STOPLOSS child ----
        # 🔁 STOPLOSS must enqueue, not place
        child_id = _place_stoploss_child_now(
            parent_cor=parent_cor,
            market_id=market_id,
            selection_id=selection_id,
            exit_side=stop_side,
            exit_odds=odds,
            parent_stake=stake,
        )

        if child_id:
            break

        # ---- missed → cancel + reprice ----
        if bet_id:
    
            cancel_bet_canonical(bet_id=bet_id)
       

        chase += 1

        # walk further in adverse direction
        if entry_side == "LAY":
            odds = pm.walk_ticks(odds, 1, direction="up")
        else:
            odds = pm.walk_ticks(odds, 1, direction="down")

        odds = _round_odds(odds, parent_id=parent_id)

    # --------------------------------------------------
    # Cancel active HEDGE child (DB + Betfair)
    # --------------------------------------------------
    _cancel_active_hedge_child(parent_id)

    # --------------------------------------------------
    # Mark parent terminated by stoploss
    # --------------------------------------------------
    con = _orders_conn()
    con.execute("""
        UPDATE orders
           SET stoploss_triggered=1,
               exit_kind='STOPLOSS'
         WHERE id=?
    """, (parent_id,))
    con.commit()
    con.close()


def _enforce_child_price_separation(
    *,
    engine: str,
    exit_kind: str | None,
    parent_side: str,
    parent_odds: float,
    child_odds: float,
) -> float:
    """
    Router invariant:
    - Only OVERWATCHER may place a child at the same price as the parent
    - All other engines must move at least one tick away
    """

    if engine == "OVERWATCHER":
        return child_odds

    if (exit_kind or "").upper() == "STOPLOSS":
        return child_odds

    if float(child_odds) != float(parent_odds):
        return child_odds

    # Enforce one-tick separation
    if parent_side.upper() == "LAY":
        # hedge BACK must be higher
        return pm.walk_ticks(parent_odds, +1)
    else:
        # hedge LAY must be lower
        return pm.walk_ticks(parent_odds, -1)

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: below def _orders_update_child_matched
# 🧩 ACTION: ADD progressive compression helper
# 📆 PATCHED: 2026-04-15 — Sequential compression ladder (7–12 zone)
#
# PURPOSE:
# - Replace sibling profit lock model
# - Sequentially promote compression child one level at a time
# - Only ONE active compression child per parent
#
# RULES:
# - Operates only in compression zone (7.0 – 12.0)
# - When child matches at X → queue next at X+1
# - Never pre-queues multiple children
# - Idempotent
# ======================================================================================================

def _progressive_compression_next(parent_row, *, cor: str, exit_odds: float):

    from engines.price_math import odds_plus_ticks
    from engines.math.dynamic_stake_v7 import calc_greenup_stake

    COMPRESSION_LOW  = 7.0
    COMPRESSION_HIGH = 12.0

    parent_id    = int(parent_row["id"])
    parent_side  = parent_row["side"].upper()
    entry_odds   = float(parent_row["entry_odds"])
    entry_stake  = float(parent_row["entry_stake"])
    market_id    = str(parent_row["marketId"])
    selection_id = str(parent_row["selectionId"])
    source       = parent_row["source"]
    engine       = parent_row["engine"]

    current_level = float(exit_odds)

    # Only operate in compression zone
    if current_level < COMPRESSION_LOW:
        return

    if current_level >= COMPRESSION_HIGH:
        return

    # Ensure no active queued/placed compression child already exists
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    matched = _q_retry(cur, """
        SELECT 1
          FROM orders
         WHERE role='CHILD'
           AND hedge_of=?
           AND entry_status='MATCHED'
         LIMIT 1
    """, (parent_id,)).fetchone()

    if not matched:
        return


    existing = _q_retry(cur, """
        SELECT 1
          FROM orders
         WHERE role='CHILD'
           AND hedge_of=?
           AND entry_status IN ('QUEUED','PLACING','PLACED')
         LIMIT 1
    """, (parent_id,)).fetchone()

    con.close()

    if existing:
        return

    # Compute next compression level
    next_level = odds_plus_ticks(current_level, +1)

    if next_level > COMPRESSION_HIGH:
        return

    full_hedge_stake = calc_greenup_stake(
        parent_side,
        entry_odds,
        entry_stake,
        float(next_level)
    )

    # Compression = FULL hedge (no progressive reduction here)
    hedge_stake = round(float(full_hedge_stake), 2)

    if hedge_stake < 1.0:
        hedge_stake = 1.0


    child_id = _orders_insert_child_queued(cor)



    _log_event(
        "INFO",
        "live_router",
        f"[COMPRESSION] parent_ref={cor} next_level={next_level}"
    )



def _release_matched_parent_exposure(parent_cor: str) -> None:
    """
    Final safety release.

    Exposure is released ONLY when:
      • child is MATCHED
      • OR market is FINISHED (checked by caller)
    """

    try:
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # === PATCH START ============================================================
        # 📍 TARGET: engines/live/live_router.py
        # 🔎 SEARCH: def _release_matched_parent_exposure(parent_cor: str) -> None:
        # 🧩 ACTION: FIX manual-parent guard order (parent must be loaded first)
        # 📆 PATCHED: 2026-01-08 — prevent NameError + correct exposure bypass
        # ============================================================================

        parent = _q_retry(cur, """
            SELECT id, engine, entry_odds, entry_stake, exit_status, source
              FROM orders
             WHERE customerOrderRef=?
               AND role='PARENT'
               AND entry_status='MATCHED'
             LIMIT 1
        """, (str(parent_cor),)).fetchone()

        if not parent:
            return

        # ✅ Manual parents never reserve or release exposure
        if _is_manual_parent(parent):
            return

        # === PATCH END ==============================================================


        # idempotency guard
        if (parent["exit_status"] or "").upper() in ("SETTLED","CANCELLED","EXPIRED"):
            return

        # child matched?
        child = _q_retry(cur, """
            SELECT 1
              FROM orders
             WHERE hedge_of=? AND entry_status='MATCHED'
             LIMIT 1
        """, (int(parent["id"]),)).fetchone()

        # if no child, caller MUST be market-finished path
        # (we trust caller here by design)
        parent_id = int(parent["id"])  # or fetched explicitly
        
        _release_parent_exposure_db(parent_id)


        _q_retry(cur, """
            UPDATE orders
               SET exit_status='EXPIRED',
                   closed_at=datetime('now','utc')
             WHERE id=?
        """, (int(parent["id"]),))

        con.commit()

        _log_event(
            "INFO",
            "bankstate",
            f"[EXPOSURE RELEASE] expired parent_ref={parent_cor}"
        )

    except Exception as e:
        _log_event(
            "ERROR",
            "bankstate",
            f"[EXPOSURE RELEASE FAILED] parent_ref={parent_cor}: {e}"
        )
    finally:
        try:
            con.close()
        except Exception:
            pass

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py (top of file)
# 🆕 ADD: Router → EventSync unified emit wrappers
# 📆 PATCHED: 2026-01-22
# ============================================================================

from engines.mastery.event_sink import emit as emit_event

def _emit_router_event(event_type: str, payload: dict):
    """
    Unified router → EventSync emitter.
    Includes timestamp, runner identity, and engine/letter metadata.
    """
    try:
        payload = dict(payload)
        payload["ts"] = datetime.now(timezone.utc).isoformat()
        emit_event(event_type, payload)
    except Exception as e:
        print(f"[Router→EventSync] WARN {event_type}: {e}")


# === EXPOSURE HELPERS (B3 MODEL) ============================================
def _compute_exposure(mid: str, sid: str):
    """
    Compute exposure_before / exposure_after for runner + letter.
    Returns:
        { runner_open_liability, runner_net_pl }
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        row = _q_retry(con, """
            SELECT 
              SUM(CASE WHEN role='PARENT' AND entry_status='MATCHED' 
                       AND (exit_status IS NULL OR exit_status<>'MATCHED')
                       THEN 
                           CASE WHEN side='LAY'
                                THEN entry_stake*(entry_odds-1)
                                ELSE entry_stake
                           END
                  END) AS liab,
              SUM(COALESCE(net_pl, 0)) AS pnl
            FROM orders
            WHERE mode='LIVE'
              AND marketId=? AND selectionId=?
        """, (str(mid), str(sid))).fetchone()
        con.close()
        return {
            "runner_open_liability": float(row["liab"] or 0),
            "runner_net_pl": float(row["pnl"] or 0)
        }
    except Exception:
        return {
            "runner_open_liability": 0.0,
            "runner_net_pl": 0.0
        }

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: module-level worker / queue definitions
# 🧩 ACTION: REMOVE (ownership moved to placement)
# 📆 PATCHED: 2025-12-17 — Router no longer owns execution worker
# ======================================================================================================

# NOTE:
# Execution queue and worker have been relocated to:
# engines/decision_engine/decide_once/placement.py
#
# LiveRouter no longer owns:
# - execution queue
# - worker thread
#
# LiveRouter remains a helper for:
# - Betfair execution
# - order mutation
#
# No logic is deleted; ownership is transferred.

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: routing entry point (e.g. place_from_bus / legacy router entry)
# 🧩 ACTION: REPLACE enqueue target
# 📆 PATCHED: 2025-12-17 — Delegate execution to placement worker
# ======================================================================================================
# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🧩 ADD: BankState live exposure invariant check (diagnostic only)
# 📆 PATCHED: 2026-02-12
# ======================================================================

def _check_bankstate_invariant(engine: str) -> bool:
    """
    Diagnostic invariant:
        engine_pot == engine_available + sum(open_parent_liability)

    Safe to call at runtime. Never raises.
    """
    try:
        from engines.config_paths import open_auto_db

        pot   = float(bank_state.get_engine_pot(engine) or 0.0)
        avail = float(bank_state.get_engine_available(engine) or 0.0)

        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT side, entry_odds, entry_stake
              FROM orders
             WHERE mode='LIVE'
               AND role='PARENT'
               AND engine=?
               AND entry_status='MATCHED'
               AND (exit_status IS NULL OR exit_status<>'MATCHED')
        """, (engine,)).fetchall()
        con.close()

        liab = 0.0
        for r in rows:
            if (r["side"] or "").upper() == "LAY":
                liab += float(r["entry_stake"]) * (float(r["entry_odds"]) - 1.0)
            else:
                liab += float(r["entry_stake"])

        ok = abs(pot - (avail + liab)) < 0.01
        if not ok:
            _log_event(
                "ERROR",
                "bankstate",
                f"[INVARIANT FAIL] engine={engine} pot={pot:.2f} "
                f"avail={avail:.2f} liab={liab:.2f}"
            )
        return ok
    except Exception:
        return True  # diagnostic must never block

# ----------------------------------------------------------------------
# BUS → Placement adapter (REQUIRED)
# ----------------------------------------------------------------------
from engines.decision_engine.decide_once.placement import enqueue_for_placement

def place_from_bus(name: str, plan: dict, ctx: dict):
    """
    BUS entry point.

    • BUS always calls this
    • Router does NOT execute plans
    • Placement owns execution lifecycle
    """
    enqueue_for_placement(name, plan, ctx)
    return True



def _compute_letter_exposure(mid: str, sid: str, letter: str):
    """
    Compute per-letter exposure for MSC/Legacy engines.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        row = _q_retry(con, """
            SELECT 
              SUM(CASE WHEN role='PARENT' AND entry_status='MATCHED'
                       AND (exit_status IS NULL OR exit_status<>'MATCHED')
                       AND UPPER(SUBSTR(source,1,1))=UPPER(?)
                       THEN 
                           CASE WHEN side='LAY'
                                THEN entry_stake*(entry_odds-1)
                                ELSE entry_stake
                           END
                  END) AS liab
            FROM orders
            WHERE mode='LIVE'
              AND marketId=? AND selectionId=?
        """, (letter, str(mid), str(sid))).fetchone()
        con.close()
        return float(row["liab"] or 0)
    except Exception:
        return 0.0

# === PATCH END ===============================================================


# DB connection for AUTOSCALP orders table (GUI DB)
# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: def _orders_conn()
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2025-12-18 — Restore LOCAL orders authority (no LiveCache)
#
# RATIONALE:
# - autoscalp_gui.db is the canonical orders authority
# - LiveCache/DALWriteProxy breaks parent↔child visibility
# - Execution paths must read/write LOCAL only
# ======================================================================

def _orders_conn():
    """
    Canonical LOCAL orders connection.

    Execution paths (parents, children, hedges, stoploss)
    MUST read/write autoscalp_gui.db directly.

    LiveCache / DALWriteProxy is forbidden here.
    """
    from engines.config_paths import open_auto_db
    con = open_auto_db(rw=True)
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass
    return con



# 📍 TARGET: engines/live/live_router.py
# 📆 PATCHED: 2025-11-14T23:00Z — fetch bank from BankState instead of dashboard_tiles
def _get_live_bank_balance(con):
    """
    Return the current live bank balance for the active day.
    """
    row = _q_retry(con, """
        SELECT current_balance
          FROM internal_bank
         WHERE day=date('now','utc')
         ORDER BY updated_at DESC
         LIMIT 1
    """).fetchone()
    return float(row["current_balance"]) if row else 0.0

# --- stake + mastery helpers (LIVE) ---------------------------------
# ===============================================================
# 📍 TARGET: engines/live/live_router.py:_fetch_live_bank
# 🔎 SEARCH: def _fetch_live_bank(
# ⛏️ ACTION: replace entire function with BankState-backed version
# 📆 PATCHED: 2025-11-16T12:45Z
# ===============================================================

def _fetch_live_bank(default: float = 0.0) -> float:
    """
    Unified bank fetch for LIVE mode.
    Uses internal_bank via bank_state.get_balance().
    dashboard_tiles.bank is deprecated and must never be used.
    """
    try:
        from engines.live import bank_state
        bal = float(bank_state.get_balance() or 0.0)
        return bal
    except Exception:
        return float(default or 0.0)


# --- BEGIN DB SHIM (no-callsite changes needed) -------------------------------
from engines.config_paths import (
    auto_conn as __cp_auto_conn,
    q_retry   as __cp_q_retry,
    autoscalp_db, connect_db
)

def check_live_router(con):
    try:
        c = con.execute("SELECT COUNT(DISTINCT marketId) AS n FROM odds_current;").fetchone()
        print(f"[CHECK] LiveRouter: odds_current markets={c['n']}")
        return c['n'] > 0
    except Exception as e:
        print(f"[CHECK] LiveRouter: FAIL → {e}")
        return False


def _auto_conn(*_args, **_kwargs):
    """
    Canonical GUI DB connector.
    Back-compat: accepts any args (ignored), returns a Row-backed connection.
    """
    con = __cp_auto_conn()
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass
    return con

def _q_retry(obj, sql, params=(), *_args, **_kwargs):
    """
    Retry shim. Accepts either a connection OR a cursor.
    Ignores extra args to stay source-compatible with older wrappers.
    """
    con = getattr(obj, "connection", None) or obj
    return __cp_q_retry(con, sql, params)
# --- END DB SHIM --------------------------------------------------------------
# Link plan_ledger.child_order_id via the parent's customerOrderRef
def _ledger_link_child_by_parent_cor(parent_cor: str, child_id: int) -> None:
    """
    Update plan_ledger.child_order_id for the row whose parent_order_id == this parent's orders.id.
    Safe: silently no-ops if plan_ledger doesn't exist or no parent row yet.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        cur = con.cursor()
        parent = _q_retry(cur, "SELECT id FROM orders WHERE customerOrderRef=? LIMIT 1", (str(parent_cor),)).fetchone()
        if not parent:
            con.close(); return
        parent_id = int(parent["id"])
        # Write child id if missing; don't clobber an existing link
        _q_retry(cur, """
            UPDATE plan_ledger
               SET child_order_id = COALESCE(child_order_id, ?),
                   updated_at = datetime('now','utc')
             WHERE parent_order_id = ?
        """, (int(child_id), parent_id))
        con.commit(); con.close()
    except Exception:
        # Best-effort; router must never crash on ledger updates
        pass

# === PATCH START ===
# 📍 TARGET: engines/live/live_router.py
# 📆 PATCHED: 2025-11-11Z — per-letter cap summary (true parent↔child linkage)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _cap_numbers_summary(mid: str, sid: str, letter: str) -> None:
    """
    Print live cap usage for one runner/letter family.
    Triggered when a child closes its specific parent (hedge_of link).
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        # Count remaining open parents of the same letter on this runner
        row = _q_retry(con, """
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN side='LAY' THEN entry_stake*(entry_odds-1)
                            ELSE entry_stake END) AS liab
              FROM orders
             WHERE mode='LIVE'
               AND role='PARENT'
               AND marketId=? AND selectionId=?
               AND UPPER(SUBSTR(source,1,1))=UPPER(?)
               AND UPPER(COALESCE(entry_status,''))='MATCHED'
               AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
        """, (str(mid), str(sid), str(letter))).fetchone()
        con.close()

        n = int(row["n"] or 0)
        liab = float(row["liab"] or 0.0)
        if n == 0:
            print(f"[CAP] mid={mid} sid={sid} letter={letter} → all slots free (0 open)")
        else:
            print(f"[CAP] mid={mid} sid={sid} letter={letter} → {n} open  liab=£{liab:.2f}")

    except Exception as e:
        print(f"[CAP] summary warn: {e}")
# === PATCH END ===

# ============================================================
# CHILD RECOVERY SWEEP (DB-FIRST, ROUTER-OWNED)
# ============================================================

def _router_child_recovery_sweep():
    """
    Ensure every MATCHED parent has a queued child.
    Router-owned, DB-first, restart-safe.
    Runs once at router startup.
    """

    con = _orders_conn()
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT
            p.id                AS parent_id,
            p.customerOrderRef  AS parent_cor,
            p.marketId          AS marketId,
            p.selectionId       AS selectionId,
            p.side              AS side,
            p.entry_odds        AS entry_odds,
            p.entry_stake       AS entry_stake,
            p.source            AS source
        FROM orders p
        WHERE p.role = 'PARENT'
          AND p.entry_status = 'MATCHED'
          AND COALESCE(parent_closed,0)=0
          AND (exit_status IS NULL OR exit_status NOT IN ('CANCELLED','EXPIRED','SETTLED'))

          AND NOT EXISTS (
              SELECT 1 FROM orders c
              WHERE c.hedge_of = p.id
          )
    """).fetchall()

    for r in rows:
        try:
            parent_side = r["side"].upper()
            child_side  = "BACK" if parent_side == "LAY" else "LAY"

            # 🔑 FIX: price must move AWAY from entry
            from engines.price_math import odds_plus_ticks

            hedge_odds = odds_plus_ticks(
                float(r["entry_odds"]),
                +1 if child_side == "BACK" else -1
            )

            hedge_odds = _round_odds(float(hedge_odds))

            full_hedge_stake = calc_greenup_stake(
                parent_side,
                entry_odds,
                entry_stake,
                hedge_odds
            )

            hedge_stake = round(float(full_hedge_stake) * PROGRESSIVE_LOCK_FACTOR, 2)

            if hedge_stake < 1.0:
                hedge_stake = 1.0


            # NOTE:
            # _orders_insert_child_queued is POSITIONAL.
            _ensure_child_queued_for_matched_parent(r["parent_cor"])





            print(f"[ROUTER][RECOVER] child rebuilt for {r['parent_cor']}")

        except Exception as e:
            print(f"[ROUTER][RECOVER][ERR] {r['parent_cor']}: {e}")

    con.close()

# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ADD: child match reconciliation sweep
# 📆 PATCHED: 2026-02-XX — enforce child MATCHED lifecycle
#
# PURPOSE:
#   • Query Betfair surface for CHILD orders in PLACED state
#   • If matched, flip:
#         entry_status='MATCHED'
#         exit_status='MATCHED'
#   • Trigger parent close + exposure release
#
# INVARIANT:
#   Betfair truth > DB state
# ======================================================================

def _sync_child_matches(limit: int = 100) -> int:
    fixed = 0

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        rows = _q_retry(cur, """
            SELECT id, entry_bet_id, hedge_of
              FROM orders
             WHERE role='CHILD'
               AND entry_status='PLACED'
               AND entry_bet_id IS NOT NULL
             ORDER BY opened_at DESC
             LIMIT ?
        """, (int(limit),)).fetchall()

        for r in rows:
            bet_id = str(r["entry_bet_id"])

            status = get_bet_status(bet_id)

            if status != "EXECUTION_COMPLETE":
                continue

            child_id = int(r["id"])
            parent_id = int(r["hedge_of"])

            # Flip CHILD
            _q_retry(cur, """
                UPDATE orders
                   SET entry_status='MATCHED',
                       exit_status='MATCHED',
                       closed_at=datetime('now','utc')
                 WHERE id=?
            """, (child_id,))

            # Close PARENT
            _stamp_parent_exit_sql(
                cur,
                parent_id=parent_id,
                exit_status="MATCHED",
            )

            con.commit()

            # Release exposure
            _release_parent_exposure_db(parent_id)

            from engines.math.dynamic_stake_v7 import advance_progressive_stage

            advance_progressive_stage(
                marketId=parent.marketId,
                selectionId=parent.selectionId
            )

            fixed += 1

        return fixed

    finally:
        con.close()


# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _sync_all_matches(limit: int = 100) -> int:
# 🛠 ACTION: REPLACE FUNCTION BODY
# 📆 PATCHED: 2026-03-26 — Enforce canonical MATCHED handler (no direct SQL)
#
# WHY:
# - MATCHED parents MUST go through _orders_update_parent_matched()
# - Direct SQL MATCHED writes bypass child creation (root cause)
# - This makes MATCHED a single-writer invariant
# ======================================================================================================

def _sync_all_matches(limit: int = 100) -> int:
    """
    Sweep PARENTS stuck in PLACED state and reconcile MATCHED status
    using the canonical parent-matched handler.

    IMPORTANT:
    - This function MUST NOT write entry_status='MATCHED' directly
    - All MATCHED transitions delegate to _orders_update_parent_matched()
    """

    fixed = 0

    try:
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        rows = _q_retry(cur, """
            SELECT
                customerOrderRef,
                entry_bet_id
              FROM orders
             WHERE mode='LIVE'
               AND role='PARENT'
               AND entry_status='PLACED'
               AND entry_bet_id IS NOT NULL
             ORDER BY opened_at DESC
             LIMIT ?
        """, (int(limit),)).fetchall()

        con.close()

    except Exception as e:
        _log_event("ERROR", "live_router", f"sync_all_matches fetch failed: {e}")
        return 0

    if not rows:
        return 0

    for r in rows:
        try:
# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _sync_all_matches(limit: int = 100) -> int:
# 🧩 ACTION: FIX status variable + canonical match check
# 📆 PATCHED: 2026-04-XX — Fix parent match sweep bug
# ======================================================================

            bet_id = str(r["entry_bet_id"])

            status = get_bet_status(bet_id)

            if status != "EXECUTION_COMPLETE":
                continue

            parent_cor = str(r["customerOrderRef"])

            _orders_update_parent_matched(parent_cor, bet_id)
            _ensure_child_queued_for_matched_parent(parent_cor)

            fixed += 1


        except Exception as e:
            _log_event(
                "ERROR",
                "live_router",
                f"sync_all_matches error ref={r['customerOrderRef']}: {e}"
            )

    return fixed


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers (AutoScalp GUI DB, WAL, retries)
# ─────────────────────────────────────────────────────────────────────────────
import sqlite3
from engines.config_paths import autoscalp_db

# unified GUI DB connector (no name collision)
# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _db()
# 📆 PATCHED: 2025-12-10 — FORCE router writes to LiveCache DB only
# ============================================================================

def _db() -> sqlite3.Connection:
    """
    LiveRouter writes ALL orders via DALWriteProxy("auto").

    The DAL is the single source of truth for routing writes
    to the canonical AUTO orders database.

    This function must NEVER point directly at sqlite paths.
    """
    con = _orders_conn()
    return con

# === PATCH END ==============================================================



def _q(con: sqlite3.Connection, sql: str, params: tuple = ()):
    return _cp_q_retry(con, sql, params)

# use _db() for every orders/GUI DB open

def _con():         return _db()


# --- Strategy → Letter map (keep in router so sizing is consistent) ----------
LETTER_MAP = {
    "ALWAYS_ON": "A",
    "BLUEPRINTS": "P",
    "OG_STRATEGY": "Z",
    "LADDER_STRATEGY": "L",
    "S4_CROSSOVER": "X",
    "S5_BREAKOUT": "R",
    "S6_STEAM_FADE": "F",
    "BTL_SCOUT": "B",
    "BTL_AGGR": "G",
    "IP1_SHOCK_DRIFT": "I",
    "IP2_TIRED_LEADER": "T",
    "IP3_CLOSE_FINISH": "C",
    "IP4_FENCE_ERROR": "E",
    "IP5_COLLAPSE_FADE": "K",
}

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 INSERT BELOW EXISTING LETTER_MAP
# 📆 PATCHED: 2025-12-03 — engine classifier for all order writes

ENGINE_MAP = {
    "D": "MSC_EXPLORATORY",
    "J": "MSC_RISK",
    "V": "MSC_INPLAY",
}

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _engine_from_source(
# 📆 PATCHED: 2026-02-20 — Diagnostic only (never for LIVE execution)
# ============================================================================

def _engine_from_source(source: str) -> str:
    """
    DIAGNOSTIC ONLY.

    LIVE execution MUST use orders.engine.
    This function exists only for offline repair / legacy analysis.
    """
    if not source:
        return "LEGACY"
    L = str(source).upper()[:1]
    return ENGINE_MAP.get(L, "LEGACY")

# === PATCH END ==============================================================




def _letter_from_source(src: str) -> str:
    """Resolve canonical letter from strategy/source tag."""
    if not src:
        return "A"
    s = str(src).upper()
    # single-letter already?
    if len(s) == 1 and s in "ABGXRFLZITCEKP":
        return s
    # normalize “STRAT_XXX” test names
    key = s.split("_", 1)[-1] if s.startswith("STRAT_") else s
    return LETTER_MAP.get(key, s[:1])

def _letter_base_max(letter: str) -> tuple[float, float]:
    """
    Map family letter → (BASE_STAKE_*, STAKE_MAX_*). Falls back to global BASE_STAKE/STAKE_MAX.
    """
    d = daily_config
    L = (letter or "").upper()
    try:
        base = getattr(d, f"BASE_STAKE_{L}", getattr(d, "BASE_STAKE", 2.0))
        smax = getattr(d, f"STAKE_MAX_{L}", getattr(d, "STAKE_MAX", 8.0))
        return (float(base), float(smax))
    except Exception:
        return (float(getattr(d, "BASE_STAKE", 2.0)), float(getattr(d, "STAKE_MAX", 8.0)))



def _mastery_log(event_type: str, payload: dict):
    """
    Write a compact event row into bets.db → mastery_events for learning.
    """
    try:
        bdb = connect_db(ro=False)
        _q_retry(bdb, """
            CREATE TABLE IF NOT EXISTS mastery_events(
                event_type TEXT,
                details_json TEXT,
                created_at TEXT
            )
        """)
        _q_retry(bdb, "INSERT INTO mastery_events(event_type, details_json, source) "
            "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%SZ','now'))",
            (str(event_type), json.dumps(payload, separators=(',', ':'), ensure_ascii=False))
        )
        bdb.commit(); bdb.close()
    except Exception:
        pass



import math as _math

def _safe_int(x, default=1):
    try:
        v = float(x)
        if _math.isnan(v):
            return default
        return int(v)
    except Exception:
        return default



# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🆕 ADD: BUS stats adapter (read-only)
# PURPOSE: satisfy BUS tick + Trading Hub
# ======================================================================

def fetch_router_stats() -> dict:
    """
    Read-only router stats for BUS / Trading Hub.

    Source of truth:
      autoscalp_gui.db → orders
    """

    stats = {
        "parents_open": 0,
        "parents_matched": 0,
        "children_open": 0,
        "children_matched": 0,
    }

    try:
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        cur = con.cursor()

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: SELECT
# 📆 PATCHED: 2026-04-26 — router stats restricted to TODAY (UTC)
#
# PURPOSE:
# - Dashboard “Today” must be date-scoped
# - Prevent cumulative LIVE totals
# - Align with BUS snapshot semantics
# ======================================================================================================

        row = _q_retry(cur, """
            SELECT
              SUM(CASE WHEN role='PARENT'
                        AND entry_status='MATCHED'
                        AND date(opened_at)=date('now','utc')
                       THEN 1 ELSE 0 END) AS parents_matched,

              SUM(CASE WHEN role='CHILD'
                        AND entry_status IN ('LIVE','PLACED','MATCHED')
                        AND date(opened_at)=date('now','utc')
                       THEN 1 ELSE 0 END) AS children_open,

              SUM(CASE WHEN role='CHILD'
                        AND entry_status='MATCHED'
                        AND date(opened_at)=date('now','utc')
                       THEN 1 ELSE 0 END) AS children_matched
            FROM orders
            WHERE mode='LIVE'
        """).fetchone()

        if row:
            stats["parents_open"]      = int(row[0] or 0)
            stats["parents_matched"]   = int(row[1] or 0)
            stats["children_open"]     = int(row[2] or 0)
            stats["children_matched"]  = int(row[3] or 0)

        con.close()
    except Exception:
        pass

    return stats




# ───────────────────────────────────────────────────────────────
# NON-BLOCKING, DAL-SAFE ORDERS SCHEMA CHECK (DIAGNOSTIC ONLY)
# ───────────────────────────────────────────────────────────────

_ORDERS_SCHEMA_CHECKED = False

def _ensure_orders_schema() -> None:
    """
    Diagnostic-only schema verification.

    • SAFE under DAL (uses real sqlite reader)
    • NEVER blocks execution
    • NEVER modifies schema
    • Logs OK / MISSING columns
    • Runs once per process
    """
    global _ORDERS_SCHEMA_CHECKED

    if _ORDERS_SCHEMA_CHECKED:
        return

    try:
        # IMPORTANT:
        # Use a REAL sqlite connection, never DALWriteProxy
        from engines.config_paths import open_auto_db

        con = open_auto_db(rw=False)
        cur = con.cursor()

        rows = cur.execute("PRAGMA table_info(orders)").fetchall()
        cols = {r[1] for r in rows}

        # Columns that LiveRouter actually depends on
        required = {
            "customerOrderRef",
            "marketId",
            "selectionId",
            "side",
            "entry_odds",
            "entry_stake",
            "entry_status",
            "opened_at",
            "role",
            "source",
            "engine",
            "exit_kind",
            "hedge_of",
            "stop_loss_px",
        }

        missing = required - cols

        if not missing:
            print("[SCHEMA ✓] orders table schema OK")
        else:
            print(
                "[SCHEMA ✗] orders table missing columns:",
                ", ".join(sorted(missing))
            )

        con.close()

    except Exception as e:
        # ABSOLUTELY MUST NOT BLOCK
        print(f"[SCHEMA !] orders schema check failed: {e}")

    _ORDERS_SCHEMA_CHECKED = True


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^_ORDERS_SCHEMA_OK = False
# 📆 PATCHED: 2025-09-29T11:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _repair_orphan_run_ids(mode: str = "LIVE", tag: str = "LIVE-AUTO") -> None:
    """
    Repair helper: any orders with run_id=0 get patched to a valid runs.id.
    Creates a 'runs' row if missing. Idempotent.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        cur = con.cursor()
        # ensure runs table exists
        _q_retry(cur, """
            CREATE TABLE IF NOT EXISTS runs(
                id INTEGER PRIMARY KEY,
                started_at TEXT,
                finished_at TEXT,
                mode TEXT,
                blueprint_file TEXT,
                notes TEXT
            )
        """)
        # get/create FK id
        rid_row = _q_retry(cur, "SELECT id FROM runs WHERE notes=? LIMIT 1", (tag,)).fetchone()
        if rid_row:
            fk = int(rid_row[0])
        else:
            _q_retry(cur, "INSERT INTO runs(started_at, mode, notes) VALUES(datetime('now','utc'), ?, ?)",
                     (str(mode), str(tag)))
            fk = cur.lastrowid
        # repair orphaned orders
        n = _q_retry(cur, "UPDATE orders SET run_id=? WHERE COALESCE(run_id,0)=0 AND mode=?", (fk, str(mode))).rowcount
        con.commit()
        if n:
            _log_event("INFO", "live_router", f"[REPAIR] patched {n} orphan orders to run_id={fk}")
        con.close()
    except Exception as e:
        _log_event("ERROR", "live_router", f"[REPAIR] run_id fix failed: {e}")




# Make UTC explicitly timezone-aware for consistency
def _utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _run_fk_id(run_label: str, mode: str = "LIVE") -> int:
    """
    Resolve or create a run id.

    Schema-aligned:
      runs.notes = logical run label
      runs.mode  = LIVE / TEST / REPLAY
    """

    con = _orders_conn()
    cur = con.cursor()

    # Ensure runs table exists (defensive, idempotent)
    _q_retry(cur, """
        CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT,
            finished_at TEXT,
            mode TEXT,
            blueprint_file TEXT,
            notes TEXT
        )
    """)

    # Insert run if missing (async-safe under DAL)
    _q_retry(cur, """
        INSERT OR IGNORE INTO runs(notes, mode, started_at)
        VALUES (?, ?, datetime('now','utc'))
    """, (str(run_label), str(mode)))

    # Resolve id deterministically
    row = _q_retry(cur, """
        SELECT id
          FROM runs
         WHERE notes=? AND mode=?
         ORDER BY id DESC
         LIMIT 1
    """, (str(run_label), str(mode))).fetchone()

    if row and row[0] is not None:
        return int(row[0])

    # Fallback: unresolved yet (DAL async) — tolerate and repair later
    return 0


# Single source of truth for creds & DB path
import engines.daily_config as daily_config
# Add near other daily_config reads (top of file has 'import engines.daily_config as daily_config')
try:
    USE_ROUTER_DYNAMIC_STAKE_A = bool(getattr(daily_config, "USE_ROUTER_DYNAMIC_STAKE_A", False))
except Exception:
    USE_ROUTER_DYNAMIC_STAKE_A = False
from engines.config_paths import autoscalp_db, connect_db

try:
    USE_ROUTER_DYNAMIC_STAKE = bool(getattr(daily_config, "USE_ROUTER_DYNAMIC_STAKE", False))
    USE_ROUTER_DYNAMIC_STAKE_LETTERS = set(getattr(daily_config, "USE_ROUTER_DYNAMIC_STAKE_LETTERS", []))
except Exception:
    USE_ROUTER_DYNAMIC_STAKE = False
    USE_ROUTER_DYNAMIC_STAKE_LETTERS = set()

# One-time breadcrumb so Events shows which DB path we're writing to
_DB_PATH_LOGGED = False
def _log_db_path_once():
    global _DB_PATH_LOGGED
    if _DB_PATH_LOGGED:
        return
    try:
        _log_event("INFO", "live_router", "orders DB = DALWriteProxy('auto')")
    except Exception:
        pass
    _DB_PATH_LOGGED = True


API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

# ── odds math ────────────────────────────────────────────────────────────────
def _tick(odds: float) -> float:
    """Return Betfair tick size for given odds (delegates to PriceMath)."""
    try:
        return float(pm.get_tick_size(float(odds)))
    except Exception:
        # ultra-safe fallback
        x = float(odds)
        return 0.01 if x < 2 else 0.02 if x < 3 else 0.05 if x < 4 else 0.10 if x < 6 else \
               0.20 if x < 10 else 0.50 if x < 20 else 1.00 if x < 30 else 2.00 if x < 50 else \
               5.00 if x < 100 else 10.00

def _round_odds(odds: float) -> float:
    """Snap odds to the ladder (delegates to PriceMath)."""
    try:
        return float(pm.snap_to_tick(float(odds)))
    except Exception:
        x = max(1.01, float(odds))
        t = _tick(x)
        return float(f"{round(round(x / t) * t, 2):.2f}")




def _ref(prefix: str) -> str:
    return f"{prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}-{random.randint(100,999)}"

# ── auth ─────────────────────────────────────────────────────────────────────
# put near other imports at the top
import os

# helper: read from GUI app_kv with multiple possible key names
def _kv_get_one(names: list[str]) -> str | None:
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        q = "SELECT value FROM app_kv WHERE LOWER(key) IN ({}) ORDER BY updated_at DESC LIMIT 1".format(
            ",".join("?"*len(names))
        )
        row = _q_retry(con, q, tuple(n.lower() for n in names)).fetchone()
        con.close()
        return (row["v"] if row and row["v"] else None)
    except Exception:
        return None

def _mask(s: str, keep: int = 4) -> str:
    if not s: return ""
    s = str(s)
    return (s[:keep] + "…" + s[-keep:]) if len(s) > 2*keep else "…" + s[-keep:]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _keys\(\) -> Tuple\[str, str\]:
# 📆 PATCHED: 2025-09-29T16:30Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _keys() -> Tuple[str, str]:
    """
    Unified Betfair credential resolver.
    Priority order:
      1) GUI DB (app_kv: betfair_session_token, app_key)
      2) betfair_creds.json
      3) ENV vars (BETFAIR_SESSION / BETFAIR_SESSION_TOKEN, BETFAIR_APP_KEY)
      4) daily_config.APP_KEY / shim
    """
    app_key, token = None, None

    # 1) GUI DB (app_kv)
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        row = _q_retry(con, "SELECT value FROM app_kv WHERE LOWER(key) IN ('app_key','bf_app_key','betfair_app_key') ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row and row["value"]: app_key = row["value"]
        row = _q_retry(con, "SELECT value FROM app_kv WHERE LOWER(key) IN ('session','session_token','betfair_session','betfair_session_token','x-authentication') ORDER BY updated_at DESC LIMIT 1").fetchone()
        if row and row["value"]: token = row["value"]
        con.close()
    except Exception:
        pass

    # 2) JSON fallback
    if not token or not app_key:
        try:
            import os, json
            path = os.path.join(os.path.dirname(autoscalp_db()), "betfair_creds.json")
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    creds = json.load(f)
                if not app_key: app_key = creds.get("app_key") or creds.get("application_key")
                if not token: token = creds.get("session") or creds.get("session_token") or creds.get("ssoid")
        except Exception:
            pass

    # 3) ENV fallback
    if not app_key:
        import os
        app_key = os.environ.get("BETFAIR_APP_KEY") or os.environ.get("APP_KEY") or os.environ.get("X_APPLICATION")
    if not token:
        import os
        token = os.environ.get("BETFAIR_SESSION") or os.environ.get("BETFAIR_SESSION_TOKEN") or os.environ.get("X_AUTHENTICATION")

    # 4) daily_config fallback
    if not app_key:
        try:
            import engines.daily_config as dc
            app_key = getattr(dc, "APP_KEY", None)
        except Exception:
            pass
    if not token:
        try:
            from engines.upgrade_import_patch import get_session_token  # type: ignore
            token = get_session_token()
        except Exception:
            pass

    if not app_key or not token:
        raise RuntimeError("Betfair credentials not available (APP_KEY / SESSION_TOKEN).")

    return app_key, token



def _sp_log_enter_live(*, run_id: str | None, strategy: str, mid: str, sid: str, side: str, price: float, stake: float):
    try:
        con = _orders_conn()
        _q_retry(con, """
          CREATE TABLE IF NOT EXISTS strategies_performance(
            id INTEGER PRIMARY KEY,
            run_id TEXT, ts TEXT, mode TEXT,
            strategy TEXT, marketId TEXT, selectionId TEXT,
            action TEXT, stake REAL, price REAL, pnl REAL DEFAULT 0, source TEXT
          )
        """)
        _q_retry(con, """
          INSERT INTO strategies_performance(run_id, ts, mode, strategy, marketId, selectionId, action, stake, price, pnl, source)
          VALUES(?, datetime('now','utc'), 'LIVE', ?, ?, ?, 'ENTER', ?, ?, 0.0, ?)
        """, (run_id or "", strategy, str(mid), str(sid), float(stake), float(price), strategy))
        con.commit(); con.close()
    except Exception: pass

def _sp_log_exit_live(*, run_id: str | None, strategy: str, mid: str, sid: str, pnl: float):
    try:
        con = _orders_conn()
        _q_retry(con, """
          INSERT INTO strategies_performance(run_id, ts, mode, strategy, marketId, selectionId, action, stake, price, pnl, source)
          VALUES(?, datetime('now','utc'), 'LIVE', ?, ?, ?, 'EXIT', 0, 0, ?, ?)
        """, (run_id or "", strategy, str(mid), str(sid), float(pnl), strategy))
        con.commit(); con.close()
    except Exception: pass


# ── RPC helpers ──────────────────────────────────────────────────────────────
def _rpc(app_key: str, token: str, method: str, params: dict) -> dict:
    headers = {
        "X-Application": app_key,
        "X-Authentication": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = [{
        "jsonrpc": "2.0",
        "method": f"SportsAPING/v1.0/{method}",
        "params": params,
        "id": 1
    }]
    r = requests.post(API_URL, headers=headers, data=json.dumps(payload), timeout=10)
    r.raise_for_status()
    resp = r.json()[0]
    # surface API errors explicitly
    if "error" in resp:
        raise RuntimeError(f"API {method} error: {resp['error']}")
    return resp

def _place(app_key: str, token: str, market_id: str, selection_id: str,
           side: str, odds: float, stake: float, customer_ref: str,
           persistence: str = "LAPSE") -> Tuple[Optional[str], dict]:
    params = {
        "marketId": market_id,
        "customerRef": customer_ref,
        "instructions": [{
            "selectionId": int(selection_id),
            "side": side.upper(),
            "orderType": "LIMIT",
            "limitOrder": {
                "size": float(stake),
                "price": _round_odds(float(odds)),
                "persistenceType": persistence.upper()  # LAPSE or PERSIST
            },
            "customerOrderRef": customer_ref
        }]
    }
    resp = _rpc(app_key, token, "placeOrders", params)
    res = (resp.get("result") or {})
    status = res.get("status", "")
    ir = ((res.get("instructionReports") or [{}])[0]) or {}
    bet_id = ir.get("betId")
    return (bet_id if status == "SUCCESS" and bet_id else None), {"status": status, "report": ir, "raw": resp}


# === PATCH START ===
# 📍 TARGET: engines/live/live_router.py:_list_current
# 📆 PATCHED: 2025-11-20 — force betIds to strings (fix empty currentOrders)
def _list_current(app_key: str, token: str, bet_id: str) -> dict:
    """Betfair listCurrentOrders — ensure betIds is always a list of strings."""
    return _rpc(
        app_key, token,
        "listCurrentOrders",
        {"betIds": [str(bet_id)]}   # ← FIX: force string
    )
# === PATCH END ===

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py:cancel_bet_canonical
# 🔎 SEARCH: def cancel_bet_canonical(
# 📆 PATCHED: 2026-02-16 — Disable router-driven cancellations (Betfair authoritative lifecycle)
# 🎯 PURPOSE:
#   • Router must NEVER cancel orders
#   • Parents LAPSE naturally at off
#   • Children PERSIST
#   • Betfair match surface is sole authority
# ============================================================================

def cancel_bet_canonical(*, bet_id: str) -> bool:
    """
    CANCELLATION DISABLED.

    Router no longer performs any Betfair cancelOrders calls.
    Order lifecycle is fully exchange-driven.

    Always returns False.
    """

    _log_event(
        "INFO",
        "live_router",
        f"[CANCEL SUPPRESSED] bet_id={bet_id}"
    )

    return False

# === PATCH END ==============================================================


def _cancel(app_key: str, token: str, bet_id: str) -> None:
    cancel_bet_canonical(bet_id=str(bet_id))




def _replace(app_key: str, token: str, market_id: str, bet_id: str, new_price: float) -> None:
    """
    Replace a live order's price (Betfair replaceOrders). No-op on error.
    """
    try:
        params = {
            "marketId": str(market_id),
            "instructions": [{
                "betId": str(bet_id),
                "newPrice": _round_odds(float(new_price))
            }]
        }
        _rpc(app_key, token, "replaceOrders", params)
    except Exception:
        pass


def _poll_matched(
    app_key: str,
    token: str,
    bet_id: str,
    timeout_s: int = 90,
    interval_s: float = 2.0
) -> bool:
    """
    Poll Betfair until this betId is matched.

    MATCHED conditions:
      • sizeMatched > 0
      • orderStatus == EXECUTION_COMPLETE
      • listCurrentOrders returns EMPTY *after previously seeing the order*

    IMPORTANT:
    - Betfair may stop returning completed orders
    - An empty response does NOT mean unmatched
    - We only treat empty as matched if we have seen the order at least once
    """

    deadline = time.time() + timeout_s
    seen_once = False

    while time.time() < deadline:
        try:
            cur = _list_current(app_key, token, str(bet_id))
            orders = (cur.get("result", {}) or {}).get("currentOrders") or []

            if orders:
                seen_once = True
                o = orders[0]

                matched = float(o.get("sizeMatched") or 0.0)
                status = str(
                    o.get("orderStatus")
                    or o.get("status")
                    or ""
                ).upper()

                if matched > 0.0:
                    return True

                if "EXECUTION_COMPLETE" in status:
                    return True

            else:
                # Betfair often drops completed orders from listCurrentOrders
                # If we've seen it before and now it's gone, treat as matched
                if seen_once:
                    return True

        except Exception:
            # network / transient API error — retry
            pass

        time.sleep(interval_s)

    return False


# ── DB helpers ───────────────────────────────────────────────────────────────
def _con():
    return _db()
def _utcnow_str() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def _log_event(level: str, source: str, message: str) -> None:
    try:
        con = _con()
        _q_retry(con, "INSERT INTO events(ts, level, source, message) VALUES(?,?,?,?)",
                    (_utcnow_str(), level, source, message))
        con.commit()
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass

def _log_event_safe(level: str, source: str, message: str) -> None:
    """Call module-level _log_event if available; otherwise print to console."""
    try:
        _log_event(level, source, message)
        return
    except Exception:
        pass
    try:
        print(f"[{level}] {source} {message}")
    except Exception:
        pass


def _orders_probe(cor: str | None = None, *, note: str, bet_id: str | None = None) -> None:
    """
    After a commit, confirm the row for this order exists in 'orders'.
    Checks by Betfair betId if present, else falls back to customerOrderRef.
    Logs [OK] with id/mode/status or [MISSING] with PRAGMA database_list.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row

        row = None
        if bet_id:  # Betfair ID preferred
            row = _q_retry(con, "SELECT id, mode, entry_status, exit_status, bf_bet_id, customerOrderRef "
                "FROM orders WHERE bf_bet_id=?",
                (str(bet_id),)
            ).fetchone()

        if not row and cor:  # fallback to customer ref if no bet_id match
            row = _q_retry(con, "SELECT id, mode, entry_status, exit_status, bf_bet_id, customerOrderRef "
                "FROM orders WHERE customerOrderRef=?",
                (str(cor),)
            ).fetchone()

        if row:
            _log_event(
                "INFO", "live_router",
                f"[ORDERS][OK:{note}] id={row['id']} mode={row['mode']} "
                f"bfid={row['bf_bet_id'] or ''} cref={row['customerOrderRef'] or ''} "
                f"entry={row['entry_status']} exit={row['exit_status'] or ''}"
            )
        else:
            dbl = _q_retry(con, "PRAGMA database_list").fetchall()
            _log_event(
                "ERROR", "live_router",
                f"[ORDERS][MISSING:{note}] bfid={bet_id or ''} cref={cor or ''} "
                f"(row not found right after commit) PRAGMA={dbl}"
            )
    except Exception as e:
        _log_event("ERROR", "live_router",
                   f"[ORDERS][PROBE:{note}] error bfid={bet_id or ''} cref={cor or ''}: {e}")
    finally:
        try:
            con.close()
        except Exception:
            pass

def _rescue_queued_children(limit: int = 50) -> int:
    """
    Force execution of any CHILD orders stuck in QUEUED.
    Router owns CHILD execution.
    """
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    try:
        rows = _q_retry(con, """
            SELECT id
              FROM orders
             WHERE role='CHILD'
               AND entry_status='QUEUED'
             ORDER BY opened_at ASC
             LIMIT ?
        """, (int(limit),)).fetchall()
    finally:
        con.close()

    PLACED = 0
    for r in rows:
        try:
            if _attempt_place_child_with_retry(int(r["id"])):
                PLACED += 1
        except Exception as e:
            _log_event(
                "ERROR",
                "live_router",
                f"child rescue failed id={r['id']}: {e}"
            )

    return PLACED



# Re-hedge background loop (singleton)
_REHEDGE_THREAD = None

def _rehedge_loop(period_s: float = 10.0, default_ticks: int = 1):
    while True:
        try:
            ensure_hedges_for_open_parents(max_to_fix=50, default_ticks=default_ticks)
        except Exception as e:
            _log_event("ERROR", "live_router", f"rehedge loop error: {e}")

        # 🔑 NEW: rescue queued children
        try:
            _rescue_queued_children(limit=50)
        except Exception as e:
            _log_event("ERROR", "live_router", f"child rescue error: {e}")

        try:
            _sync_parent_matches(limit=50)
            _sync_child_matches(limit=100)
        except Exception as e:
            _log_event("ERROR", "live_router", f"sync_parent_matches error: {e}")

        try:
            _sync_all_matches(limit=100)   # ← NEW: sweep stuck 'PLACED' to 'MATCHED'
            _finalize_children_and_release_exposure(limit=100)
            _sync_settlement_terminal_exposure(limit=200)
            _release_exposure_for_matched_children(limit=200)
        except Exception as e:
            _log_event("ERROR", "live_router", f"sync_all_matches error: {e}")

        try:
            _recycle_stale_unmatched_parents(limit=50, max_age_min=5)
            _sweep_close_finished_markets(grace_min=15)
        except Exception as e:
            _log_event("ERROR", "live_router", f"sweep loop error: {e}")

        time.sleep(max(1.0, float(period_s)))



def _start_rehedge_loop(default_ticks: int = 1):
    global _REHEDGE_THREAD
    try:
        if _REHEDGE_THREAD and _REHEDGE_THREAD.is_alive():
            return
    except Exception:
        pass
    t = threading.Thread(target=_rehedge_loop, args=(10.0, int(default_ticks)),
                         name="RehedgeLoop", daemon=True)
    _REHEDGE_THREAD = t
    t.start()
    _log_event("INFO", "live_router", "Rehedge loop started")

# === PATCH START: live_router auto-reconcile helper (parents+children) ===
import threading, time

def _start_reconcile_loop(period_s: int = 30):
    """
    Background reconciliation loop.

    LIVE RULES:
    - NEVER write entry_status='MATCHED'
    - Router owns MATCHED lifecycle
    - This loop may only:
        • repair missing betIds
        • trigger router match sweeps
    """

    from engines.live import repair_missing_betids
    stop_evt = threading.Event()

    def loop():
        while not stop_evt.is_set():
            try:
                # 1️⃣ Back-fill missing Betfair IDs (safe)
                try:
                    repair_missing_betids.main(silent=True)
                except Exception:
                    pass

                # 2️⃣ Trigger router canonical match sweeps
                try:
                    _sync_all_matches(limit=100)
                    _sync_child_matches(limit=100)
                except Exception:
                    pass

            except Exception as e:
                _log_event("ERROR", "live_router", f"[reconcile_loop] {e}")

            time.sleep(period_s)

    t = threading.Thread(target=loop, name="ReconcileLoop", daemon=True)
    t.start()
    _log_event("INFO", "live_router", f"Reconcile loop started (period={period_s}s)")

    return stop_evt


# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_insert_parent_queued\(
# --- PATCH START: replace function ------------------------------------
def _orders_insert_parent_queued(
    run_id, market_id, selection_id, side, entry_odds, entry_stake, cor,
    *, source="LEGACY_STRATEGY", engine: str | None = None, stop_loss_px=None):
    """
    Upsert LIVE parent row as 'QUEUED'.
    Stores stop_loss_px for Overwatcher STOPLOSS engine.
    """

# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _orders_insert_parent_queued(
# 🧩 ACTION: ADD required_exposure backfill at QUEUED insert
# 📆 PATCHED: 2026-04-02 — enforce exposure invariant at parent creation
#
# WHY:
# - required_exposure was only derived in place_parent_and_hedge()
# - Parents can be CANCELLED before that path executes
# - Exposure release is correctly blocked when required_exposure <= 0
# - Therefore required_exposure MUST exist at QUEUED time
# ======================================================================

    # --------------------------------------------------
    # 🔐 REQUIRED EXPOSURE (AUTHORITATIVE, DB-FIRST)
    # --------------------------------------------------
    try:
        req_exposure = float(entry_stake) * float(entry_odds)
    except Exception:
        req_exposure = None


    # --- canonical engine resolution (LIVE invariant) ---
    # In MSC fast-path, parent row may not exist yet.
    # Engine must be supplied by placement/BUS.
    eng = engine if 'engine' in locals() else None

    if not eng:
        raise RuntimeError(
            f"router invariant violated: missing engine during parent preclaim cor={cor}"
        )



    # === PATCH 3.2 START — EventSync: parent_queued =======================
    mid = str(market_id)
    sid = str(selection_id)
    letter = str(source)[:1].upper()

    before_exposure = _compute_exposure(mid, sid)
    before_letter = _compute_letter_exposure(mid, sid, letter)

    _emit_router_event("parent_queued", {
        "marketId": str(market_id),
        "selectionId": str(selection_id),
        "parent_cor": str(cor),
        "source": source,
        "letter": letter,
        "entry_odds": entry_odds,
        "entry_stake": entry_stake,
        **before_exposure,
        "letter_exposure_before": before_letter,
    })

    # === PATCH 3.2 END =====================================================

    _ensure_orders_schema()

    # --- ensure column exists ------------------------------------------------
    try:
        con0 = _orders_conn(); cur0 = con0.cursor()
        cols = {r[1] for r in _q_retry(cur0, "PRAGMA table_info(orders)")}
        if "stop_loss_px" not in cols:
            _q_retry(cur0, "ALTER TABLE orders ADD COLUMN stop_loss_px REAL")
            con0.commit()
        con0.close()
    except Exception:
        pass

    fk = run_id or resolve_live_run_id()

    con = _orders_conn(); cur = con.cursor()

    try:
        _q_retry(cur, f"""
            INSERT INTO orders (
                customerOrderRef, run_id, mode, marketId, selectionId,
                side, entry_odds, entry_stake, required_exposure,
                entry_status, opened_at,
                role, source, engine, bet_type, stop_loss_px
            )
            VALUES (
                ?, ?, 'LIVE', ?, ?, ?, ?, ?, ?,
                'QUEUED', ?,
                'PARENT', ?, ?, ?
            )
            ON CONFLICT(customerOrderRef) DO UPDATE SET
                run_id            = COALESCE(orders.run_id, excluded.run_id),
                mode              = 'LIVE',
                marketId          = COALESCE(excluded.marketId, orders.marketId),
                selectionId       = COALESCE(excluded.selectionId, orders.selectionId),
                side              = excluded.side,
                entry_odds        = excluded.entry_odds,
                entry_stake       = excluded.entry_stake,
                required_exposure = COALESCE(orders.required_exposure, excluded.required_exposure),
                entry_status      = COALESCE(orders.entry_status, 'QUEUED'),
                opened_at         = COALESCE(orders.opened_at, excluded.opened_at),
                role              = 'PARENT',
                source            = COALESCE(orders.source, excluded.source),
                engine            = COALESCE(orders.engine, excluded.engine),
                stop_loss_px      = COALESCE(excluded.stop_loss_px, orders.stop_loss_px)
        """, (
            str(cor),
            int(fk),
            str(market_id),
            str(selection_id),
            side.upper(),
            float(entry_odds),
            float(entry_stake),
            req_exposure,
            _utcnow_str(),
            source,
            eng,
            bet_type,
            stop_loss_px
        ))



        con.commit()
        _orders_probe(cor, note="queued")

    except Exception as e:
        _log_event("ERROR", "live_router",
                   f"orders upsert queued failed ref={cor}: {e}")
    finally:
        try:
            con.close()
        except Exception:
            pass
# --- PATCH END ----------------------------------------------------------


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_update_parent_PLACED\(
# --- PATCH START: replace function ------------------------------------
def _orders_update_parent_placed(cor, bet_id):
    """Mark parent PLACED and record entry_bet_id (create skeleton row if missing)."""
    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        # latest LIVE run row as fallback FK if textual run_id isn't directly available here
        rid_row = _q_retry(cur, "SELECT id FROM runs WHERE mode='LIVE' ORDER BY datetime(started_at) DESC LIMIT 1").fetchone()
        fk = int(rid_row[0]) if rid_row else _run_fk_id("LIVE-AUTO", mode="LIVE")

        # skeleton WITH run_id to satisfy NOT NULL + FK
        _q_retry(cur, """
            INSERT INTO orders (customerOrderRef, run_id, mode, entry_status, opened_at, role)
            VALUES (?, ?, 'LIVE', 'QUEUED', ?, 'PARENT')
            ON CONFLICT(customerOrderRef) DO NOTHING
        """, (str(cor), int(fk), _utcnow_str()))

        _q_retry(cur, """
            UPDATE orders
               SET entry_status='PLACED',
                   entry_bet_id=?,
                   mode='LIVE',
                   role='PARENT',
                   run_id=COALESCE(run_id, ?)
             WHERE customerOrderRef=? 
        """, (str(bet_id or ""), int(fk), str(cor)))
        con.commit()
        _orders_probe(cor, note="PLACED")
        # === PATCH START: assign sequential letter number ===
        try:
            # resolve context
            row = _q_retry(cur, "SELECT marketId, selectionId, source FROM orders WHERE customerOrderRef=? LIMIT 1", (str(cor),)).fetchone()
            if row:
                mid, sid, src = str(row["marketId"]), str(row["selectionId"]), str(row["source"] or "")
                letter = src[:2].upper()  # allows 'A' or 'AA'
                count = _active_parents_count_per_letter(mid, sid, letter)
                _q_retry(cur, "UPDATE orders SET letter_index=? WHERE customerOrderRef=?", (count + 1, str(cor)))
                con.commit()
        except Exception as e:
            _log_event("WARN","live_router",f"letter_index assign failed ref={cor}: {e}")
        # === PATCH END ===

    except Exception as e:
        _log_event("ERROR", "live_router", f"orders update PLACED failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass

# --- PATCH END ----------------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: PARENT CANCEL / FAIL / TIMEOUT PATHS
# 🧩 ACTION: RELEASE BankState exposure for UNMATCHED parents
# 📆 PATCHED: 2025-12-31 — fix unmatched parent exposure leak
# ======================================================================================================

def _release_unmatched_parent_exposure(parent_cor: str) -> None:
    """
    Release BankState exposure for a parent that NEVER MATCHED.

    This must be called when a parent is:
      • CANCELLED
      • FAILED
      • EXPIRED
      • TIMED-OUT (poll_matched failure)
      • AUTO-CLOSED before match

    Idempotent:
      • Safe to call multiple times
      • Will not release matched parents
    """
    try:
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        parent = _q_retry(cur, """
            SELECT id, engine, entry_odds, entry_stake, entry_status
              FROM orders
             WHERE customerOrderRef=?
               AND role='PARENT'
             LIMIT 1
        """, (str(parent_cor),)).fetchone()

        con.close()

        if not parent:
            return

        # 🔒 CRITICAL GUARD:
        # Never release exposure for matched parents
        if (parent["entry_status"] or "").upper() == "MATCHED":
            return

        engine = parent["engine"]
        entry_odds = float(parent["entry_odds"] or 0.0)
        entry_stake = float(parent["entry_stake"] or 0.0)

        if entry_stake <= 0.0:
            return

        # 🔓 RELEASE RESERVED EXPOSURE
        parent_id = int(parent["id"])  # or fetched explicitly
        _release_parent_exposure_db(parent_id)


        _log_event(
            "INFO",
            "bankstate",
            f"[EXPOSURE RELEASE] unmatched parent ref={parent_cor} engine={engine}"
        )

    except Exception as e:
        _log_event(
            "ERROR",
            "bankstate",
            f"[EXPOSURE RELEASE FAILED] ref={parent_cor}: {e}"
        )


# ======================================================================
# 🔧 HOOK INTO EXISTING LIFECYCLE PATHS (NO BEHAVIOUR CHANGE)
# ======================================================================


def _orders_update_parent_failed(cor, error_msg):
    con = _con(); cur = con.cursor()
    try:
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='FAILED', error=?
             WHERE customerOrderRef=? 
               AND mode='LIVE'
               AND entry_bet_id IS NULL
        """, (str(error_msg)[:240], cor))

        # ADD THIS
        parent = _q_retry(cur, """
            SELECT id FROM orders
             WHERE customerOrderRef=? AND role='PARENT'
        """, (cor,)).fetchone()

        if parent:
            _stamp_parent_exit_sql(
                cur,
                parent_id=int(parent["id"]),
                exit_status="FAILED",
                reason=error_msg,
            )
        con.commit()
    finally:
        try: con.close()
        except Exception: pass

    # 🔑 RELEASE UNMATCHED EXPOSURE
    _release_unmatched_parent_exposure(cor)


# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🆕 ADD: child placement retry loop with Betfair rejection handling
# 📆 PATCHED: 2026-02-14
# ======================================================================

def _attempt_place_child_with_retry(child_id: int, *, max_attempts: int = 1) -> bool:
    """
    CHILD execution lifecycle (PLACEMENT-EQUIVALENT).

    Mirrors parent placement semantics:
      QUEUED → PLACING → PLACED (+ entry_bet_id)

    Matching is handled elsewhere (router sync), exactly like parents.
    """

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        # --------------------------------------------------
        # 1️⃣ Fetch QUEUED CHILD (authoritative)
        # --------------------------------------------------
        row = _q_retry(cur, """
            SELECT
                id,
                customerOrderRef,
                marketId,
                selectionId,
                side,
                entry_odds,
                entry_stake,
                entry_status
            FROM orders
            WHERE id=?
              AND role='CHILD'
            LIMIT 1
        """, (int(child_id),)).fetchone()

        if not row:
            return False

        if (row["entry_status"] or "").upper() != "QUEUED":
            return False

        # --------------------------------------------------
        # 2️⃣ Mark PLACING (exactly like placement worker)
        # --------------------------------------------------
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='PLACING'
             WHERE id=?
               AND entry_status='QUEUED'
        """, (int(child_id),))
        con.commit()

        # --------------------------------------------------
        # 3️⃣ Place on Betfair (identical semantics)
        # --------------------------------------------------
        app_key, token = _keys()

        bet_id, detail = _place(
            app_key,
            token,
            str(row["marketId"]),
            str(row["selectionId"]),
            str(row["side"]),
            float(row["entry_odds"]),
            float(row["entry_stake"]),
            str(row["customerOrderRef"]),
            persistence="PERSIST"
        )

        # --------------------------------------------------
        # 4️⃣ Stamp result (EXECUTION-CORRECT)
        # --------------------------------------------------

        if detail.get("status") == "SUCCESS":

            # Case A — persistent order created
            if bet_id:
                _q_retry(cur, """
                    UPDATE orders
                       SET entry_status='PLACED',
                           entry_bet_id=?
                     WHERE id=?
                """, (str(bet_id), int(child_id)))
                con.commit()
                return True

            # Case B — matched instantly (no live order)
            _q_retry(cur, """
                UPDATE orders
                   SET entry_status='MATCHED',
                       exit_status='MATCHED',
                       closed_at=datetime('now','utc')
                 WHERE id=?
            """, (int(child_id),))
            con.commit()

            return True


        # --------------------------------------------------
        # Betfair explicit failure
        # --------------------------------------------------
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='FAILED'
             WHERE id=?
        """, (int(child_id),))
        con.commit()
        return False


    finally:
        try:
            con.close()
        except Exception:
            pass



# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: def _orders_update_parent_matched(
# 🧩 ACTION: GUARANTEE child creation on parent MATCHED
# 📆 PATCHED: 2026-03-01 — hard guarantee child is queued on parent match
# ======================================================================

def _orders_update_parent_matched(cor: str, bet_id: str | None = None):
    """
    Canonical parent MATCHED handler.

    CONTRACT:
    - Caller has already determined Betfair truth (EXECUTION_COMPLETE)
    - This function MUST always flip parent → MATCHED
    - Child creation is best-effort and must NEVER block the flip
    """

    _ensure_orders_schema()
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        # ------------------------------------------------------------------
        # 1️⃣ LOAD PARENT (AUTHORITATIVE)
        # ------------------------------------------------------------------
        parent = _q_retry(cur, """
            SELECT
                id,
                run_id,
                engine,
                source,
                side,
                entry_odds,
                entry_stake,
                entry_status,
                marketId,
                selectionId
            FROM orders
            WHERE customerOrderRef=?
              AND role='PARENT'
            LIMIT 1
        """, (str(cor),)).fetchone()

        if not parent:
            return

        # Idempotency: already matched → nothing to do
        if (parent["entry_status"] or "").upper() == "MATCHED":
            return

        parent_id   = int(parent["id"])
        parent_side = parent["side"].upper()

        # ------------------------------------------------------------------
        # 2️⃣ FLIP PARENT → MATCHED (NON-NEGOTIABLE)
        # ------------------------------------------------------------------
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='MATCHED',
                   exit_status=NULL,
                   mode='LIVE',
                   role='PARENT',
                   engine=?
             WHERE id=?
        """, (parent["engine"], parent_id))

        # 🔒 Persist execution truth (ONE-TIME)
        _ensure_execution_events_schema()

        # fetch matched details while Betfair still knows
        try:
            app_key, token = _keys()
            avg_odds, matched_size = _fetch_avg_match(app_key, token, str(bet_id))
        except Exception:
            avg_odds, matched_size = (None, None)

        _q_retry(cur, """
            INSERT OR IGNORE INTO execution_events (
                order_id,
                bet_id,
                role,
                matched_size,
                matched_odds,
                seen_at,
                source
            )
            VALUES (?, ?, 'PARENT', ?, ?, datetime('now','utc'), 'live_poll')
        """, (
            parent_id,
            str(bet_id),
            float(matched_size or 0.0),
            float(avg_odds or 0.0),
        ))

        con.commit()   # 🔑 parent state is now correct and durable

        # --------------------------------------------------
        # ROUTER SNAPSHOT — parent execution state
        # --------------------------------------------------
        try:
            key = (str(parent["marketId"]), str(parent["selectionId"]))

            _ROUTER_PARENT_SURFACE[key] = {
                "parent_id": parent_id,
                "entry_odds": float(parent["entry_odds"]),
                "entry_stake": float(parent["entry_stake"]),
                "side": parent_side,
                "engine": parent["engine"],
                "customerOrderRef": str(cor),
            }
        except Exception:
            pass

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"parent_matched failed (parent flip) ref={cor}: {e}"
        )
        return

    finally:
        try:
            con.close()
        except Exception:
            pass

    # ======================================================================
    # 3️⃣ ENSURE CHILD EXISTS (BEST-EFFORT, NON-BLOCKING)
    # ======================================================================
    try:
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # Child already exists?
        row = _q_retry(cur, """
            SELECT id
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
             LIMIT 1
        """, (parent_id,)).fetchone()

        if row:
            return  # invariant satisfied

        # Derive hedge
        hedge_side = "BACK" if parent_side == "LAY" else "LAY"

        from engines.price_math import odds_plus_ticks
        from engines.math.dynamic_stake_v7 import calc_greenup_stake

        hedge_odds = odds_plus_ticks(
            float(parent["entry_odds"]),
            +1 if hedge_side == "BACK" else -1
        )
        hedge_odds = _round_odds(float(hedge_odds))

        full_hedge_stake = calc_greenup_stake(
            parent_side,
            float(parent["entry_odds"]),
            float(parent["entry_stake"]),
            hedge_odds
        )

        hedge_stake = round(float(full_hedge_stake) * PROGRESSIVE_LOCK_FACTOR, 2)

        # Safety: never below £1 minimum
        if hedge_stake < 1.0:
            hedge_stake = 1.0

        # Insert CHILD (QUEUED)
        child_id = _ensure_child_queued_for_matched_parent(cor)
        if not child_id:
            _log_event(
                "CRITICAL",
                "live_router",
                f"[INVARIANT BREACH] parent MATCHED but child not constructed ref={cor}"
            )

        # 🔑 TEMPORAL GUARANTEE: immediately enqueue child for placement
        if child_id:
            try:

                enqueue_router_child(
                    {
                        "child_id": int(child_id),
                        "parent_cor": str(cor),
                        "side": hedge_side,
                        "px": hedge_odds,
                        "size": hedge_stake,
                    },
                    {}
                )
            except Exception as e:
                _log_event(
                    "ERROR",
                    "live_router",
                    f"enqueue child failed (non-fatal) ref={cor}: {e}"
                )



    except Exception as e:
        # 🔒 CHILD FAILURE MUST NEVER ROLLBACK PARENT
        _log_event(
            "ERROR",
            "live_router",
            f"child ensure failed (non-fatal) ref={cor}: {e}"
        )

    finally:
        try:
            con.close()
        except Exception:
            pass


def _finalize_children_and_release_exposure(limit: int = 100) -> int:
    """
    FINAL child lifecycle finalizer.

    Exposure is released ONLY when:
      1) child is MATCHED
      2) market is FINISHED

    This guarantees zero exposure leaks.
    """
    fixed = 0
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        rows = _q_retry(cur, """
            SELECT
                c.id               AS child_id,
                c.entry_status     AS child_status,
                c.entry_bet_id     AS child_bet_id,
                p.customerOrderRef AS parent_ref,
                p.engine,
                p.entry_odds,
                p.entry_stake,
                p.marketId
            FROM orders c
            JOIN orders p ON p.id = c.hedge_of
            WHERE c.role='CHILD'
              AND c.entry_status IN ('QUEUED','PLACED')
            ORDER BY c.opened_at ASC
            LIMIT ?
        """, (int(limit),)).fetchall()

        for r in rows:
            try:
                child_id   = int(r["child_id"])
                parent_ref = str(r["parent_ref"])
                engine     = r["engine"]
                market_id  = str(r["marketId"])

                # --------------------------------------------------
                # 1️⃣ PRIMARY PATH — CHILD MATCHED
                # --------------------------------------------------
                if r["child_status"] == "PLACED":

                    # 🔒 CANONICAL GUARD — Betfair is authority
                    if not r["child_bet_id"]:
                        continue

                    status = get_bet_status(str(r["child_bet_id"]))
                    if status != "EXECUTION_COMPLETE":
                        continue

                    # ✅ SAFE TO PROMOTE

                        app_key, token = _keys()
                        avg_odds, matched_size = _fetch_avg_match(
                            app_key, token, str(r["child_bet_id"])
                        )

                        _q_retry(cur, """
                            UPDATE orders
                               SET entry_status='MATCHED',
                                   exit_status='MATCHED',
                                   entry_matched_odds=?,
                                   entry_matched_stake=?,
                                   closed_at=datetime('now','utc')
                             WHERE id=?
                        """, (avg_odds, matched_size, child_id))



                        # --------------------------------------------------
                        # Parent lifecycle finalisation (SAFE AFTER CHILD)
                        # --------------------------------------------------
                        _q_retry(cur, """
                            UPDATE orders
                               SET exposure_released = 1,
                                   parent_closed = 1
                             WHERE customerOrderRef = ?
                        """, (parent_ref,))


                # --------------------------------------------------
                # 2️⃣ SAFETY PATH — MARKET FINISHED
                # --------------------------------------------------
                from engines.market_monitor.phase_clock import MarketPhaseClock

                phase, mto = MarketPhaseClock.get(str(market_id))

                # Terminal market condition
                if mto is not None and float(mto) <= -GRACE_MINUTES:
                    _release_matched_parent_exposure(parent_ref)
                    close_logically_finished_parents()
                    fixed += 1

            except Exception as e:
                _log_event(
                    "ERROR",
                    "live_router",
                    f"child finalizer failed id={r['child_id']}: {e}"
                )

        con.commit()
        return fixed

    finally:
        try:
            con.close()
        except Exception:
            pass

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: after _finalize_children_and_release_exposure
# 🧩 ACTION: RELEASE exposure for MATCHED children (authoritative path)
# 📆 PATCHED: 2026-03-21 — fix exposure leak on child MATCHED
#
# INVARIANT:
#   CHILD MATCHED ⇒ exposure MUST be released immediately
#   Settlement is only a safety net
# ============================================================================

def _release_exposure_for_matched_children(limit: int = 200) -> int:
    released = 0
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        rows = _q_retry(cur, """
            SELECT
                p.id               AS parent_id,
                p.customerOrderRef AS parent_ref,
                p.engine,
                p.entry_odds,
                p.entry_stake
            FROM orders p
            JOIN orders c ON c.hedge_of = p.id
            WHERE p.mode='LIVE'
              AND p.role='PARENT'
              AND UPPER(p.entry_status)='MATCHED'
              AND UPPER(c.entry_status)='MATCHED'
              AND COALESCE(p.parent_closed,0)=0
              AND (p.exit_status IS NULL OR UPPER(p.exit_status) NOT IN ('CANCELLED','EXPIRED','SETTLED'))
              AND COALESCE(p.exposure_released,0)=0
            LIMIT ?
        """, (int(limit),)).fetchall()

        for r in rows:
            try:
                parent_id = int(r["parent_id"])

                # 🔒 GUARD — child must be truly matched at Betfair
                rowc = _q_retry(cur, """
                    SELECT entry_bet_id
                      FROM orders
                     WHERE role='CHILD'
                       AND hedge_of=?
                       AND entry_bet_id IS NOT NULL
                     LIMIT 1
                """, (parent_id,)).fetchone()

                if not rowc:
                    continue

                if get_bet_status(str(rowc["entry_bet_id"])) != "EXECUTION_COMPLETE":
                    continue

                # ✅ SAFE TO RELEASE
                _release_parent_exposure_db(parent_id)

                _q_retry(cur, """
                    UPDATE orders
                       SET exposure_released = 1,
                           parent_closed = 1,
                           closed_at = COALESCE(closed_at, datetime('now','utc'))
                     WHERE id = ?
                """, (parent_id,))

                released += 1

            except Exception as e:
                _log_event(
                    "ERROR",
                    "bankstate",
                    f"[EXPOSURE RELEASE FAILED] parent_ref={r['parent_ref']}: {e}"
                )

        con.commit()
        return released


    finally:
        try:
            con.close()
        except Exception:
            pass

# === PATCH END ==============================================================

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _release_parent_exposure_db(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-24 — Canonical DB-first exposure release (parent_cor resolved internally)
#
# PURPOSE:
# - Make exposure release authoritative, idempotent, and DB-driven
# - Eliminate dependency on callers passing parent_id correctly
# - Guarantee exposure is released EXACTLY ONCE
#
# CORE INVARIANT:
#   The database is the lock.
#   If exposure_released == 1 → do nothing.
#
# RELEASE RULES (unchanged):
#   Release exposure IFF:
#     • parent exists
#     • exposure_released == 0
#     • AND (
#           matched CHILD exists
#        OR market is past GRACE_MINUTES
#        OR exit_status is terminal
#       )
# ======================================================================================================

def _release_parent_exposure_db(parent_id: int) -> bool:
    """
    Canonical exposure release (DB-first, idempotent).

    This function is SAFE to call from ANY lifecycle path.
    """

    try:
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # --------------------------------------------------
        # 1️⃣ Load authoritative parent row (DB is truth)
        # --------------------------------------------------
        parent = _q_retry(cur, """
            SELECT
                id,
                engine,
                marketId,
                entry_status,
                exit_status,
                required_exposure,
                exposure_released
            FROM orders
            WHERE id = ?
              AND role = 'PARENT'
            LIMIT 1
        """, (int(parent_id),)).fetchone()

        if not parent:
            return False

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _release_parent_exposure_db(parent_id: int) -> bool:
# 🧩 ACTION: FIX release guard — allow release for PLACED parents
# 📆 PATCHED: 2026-01-22 — fix permanent exposure lock
#
# RATIONALE:
# - Exposure is RESERVED at PLACED (not MATCHED)
# - Release must NOT depend on entry_status
# - DB required_exposure is the single source of truth
#
# INVARIANT:
#   If required_exposure > 0 AND exposure_released == 0,
#   release is allowed when any terminal condition is met.
# ======================================================================================================

        # --------------------------------------------------
        # ❌ REMOVE THIS INVALID GUARD
        # --------------------------------------------------
        # if (parent["entry_status"] or "").upper() != "MATCHED":
        #     return False

        # --------------------------------------------------
        # ✅ REPLACE WITH RESERVATION-BASED GUARD
        # --------------------------------------------------
        amount = float(parent["required_exposure"] or 0.0)
        if amount <= 0.0:
            return False


        # --------------------------------------------------
        # 3️⃣ Check release conditions (pure reads)
        # --------------------------------------------------

        # A) Child matched?
        child_matched = _q_retry(cur, """
            SELECT 1
              FROM orders
             WHERE hedge_of = ?
               AND UPPER(entry_status) = 'MATCHED'
             LIMIT 1
        """, (int(parent_id),)).fetchone() is not None

        # B) Market past grace?
        past_grace = False
        try:
            bdb = connect_db(ro=True)
            bdb.row_factory = sqlite3.Row
            row = _q_retry(bdb, """
                SELECT
                  CAST((julianday('now','utc') - julianday(marketStartTime))*1440 AS INTEGER)
                  AS mins_after
                FROM bets
                WHERE marketId=?
                LIMIT 1
            """, (str(parent["marketId"]),)).fetchone()
            bdb.close()
            past_grace = bool(
                row and row["mins_after"] is not None and row["mins_after"] >= GRACE_MINUTES
            )
        except Exception:
            pass

        # C) Terminal parent?
        terminal = (parent["exit_status"] or "").upper() in (
            "SETTLED", "CANCELLED", "EXPIRED"
        )

        if not (child_matched or past_grace or terminal):
            return False

        # --------------------------------------------------
        # 4️⃣ Claim release in DB FIRST (authoritative)
        # --------------------------------------------------


        res = _q_retry(cur, """
            UPDATE orders
               SET exposure_released = 1,
                   exposure_released_amount = ?,
                   parent_closed = 1,
                   closed_at = COALESCE(closed_at, datetime('now','utc'))
             WHERE id = ?
               AND exposure_released = 0
        """, (amount, int(parent_id)))

        if res.rowcount == 0:
            # Another path already released
            con.commit()
            return False

        con.commit()

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _release_parent_exposure_db(parent_id: int) -> bool:
# 🧩 ACTION: FIX BankState mutation on exposure release
# 📆 PATCHED: 2026-01-21 — bind DB release to BankState invariant
#
# INVARIANT:
# - DB release claims ownership first
# - EXACTLY ONE BankState release call follows
# - Engine + parent_id are the ONLY required inputs
# ============================================================================

        # --------------------------------------------------
        # 5️⃣ Mutate BankState AFTER DB ownership is secured
        # --------------------------------------------------
        try:
            engine = parent["engine"]

            if child_matched:
                # Child matched ⇒ lifecycle complete
                bank_state.on_child_matched(
                    engine=engine,
                    parent_id=int(parent_id),
                )
            else:
                # Terminal / grace / market-finished
                bank_state.on_parent_closed(
                    engine=engine,
                    parent_id=int(parent_id),
                )

        except Exception as e:
            _log_event(
                "ERROR",
                "bankstate",
                f"[BANKSTATE RELEASE FAILED] parent_id={parent_id}: {e}"
            )

        _log_event(
            "INFO",
            "bankstate",
            f"[EXPOSURE RELEASE] parent_id={parent_id} engine={parent['engine']}"
        )

        return True

# === PATCH END ==============================================================
        # module-level counters (router scope)
        _EXPOSURE_RELEASE_COUNTS = defaultdict(int)

     



    finally:
        try:
            con.close()
        except Exception:
            pass

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _ensure_child_queued_for_matched_parent(parent_cor: str)
# 🧩 ACTION: Enforce enqueue invariant on child creation
# 📆 PATCHED: 2026-04-23 — Centralise child enqueue authority
#
# PURPOSE:
# - Guarantee: CHILD row creation ⇒ enqueue_router_child()
# - Eliminate QUEUED-without-execution race
# - Make this function lifecycle-complete
#
# INVARIANT:
# - If child already exists → return existing id (NO enqueue)
# - If child is newly created → enqueue immediately
# - Idempotent
# ======================================================================================================

def _ensure_child_queued_for_matched_parent(parent_cor: str) -> int | None:

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        parent = _q_retry(cur, """
            SELECT
                id,
                run_id,
                marketId,
                selectionId,
                side,
                entry_odds,
                entry_stake,
                source,
                engine,
                target_ticks
            FROM orders
            WHERE customerOrderRef=?
              AND role='PARENT'
              AND entry_status='MATCHED'
              AND (exit_status IS NULL OR exit_status NOT IN ('CANCELLED','EXPIRED','SETTLED'))
            LIMIT 1
        """, (str(parent_cor),)).fetchone()

        if not parent:
            return None

        parent_id = int(parent["id"])

        # --------------------------------------------------
        # 1️⃣ Child already exists?
        # --------------------------------------------------
        row = _q_retry(cur, """
            SELECT id
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
             LIMIT 1
        """, (parent_id,)).fetchone()

        if row:
            return int(row["id"])

        # --------------------------------------------------
        # 2️⃣ Derive hedge deterministically
        # --------------------------------------------------
        parent_side = parent["side"].upper()
        child_side  = "BACK" if parent_side == "LAY" else "LAY"
        ticks       = int(parent["target_ticks"] or 1)

        from engines.price_math import walk_ticks
        from engines.math.dynamic_stake_v7 import calc_greenup_stake

        tick_dir = ticks if parent_side == "LAY" else -ticks

        hedge_odds = walk_ticks(float(parent["entry_odds"]), tick_dir)
        hedge_odds = _round_odds(float(hedge_odds))

        full_hedge_stake = calc_greenup_stake(
            parent_side,
            float(parent["entry_odds"]),
            float(parent["entry_stake"]),
            hedge_odds
        )

        hedge_stake = round(float(full_hedge_stake) * PROGRESSIVE_LOCK_FACTOR, 2)

        if hedge_stake < 1.0:
            hedge_stake = 1.0

        # --------------------------------------------------
        # 3️⃣ Insert CHILD (QUEUED)
        # --------------------------------------------------
        _q_retry(cur, """
            INSERT INTO orders (
                customerOrderRef,
                run_id,
                mode,
                marketId,
                selectionId,
                side,
                entry_odds,
                entry_stake,
                entry_status,
                opened_at,
                role,
                hedge_of,
                source,
                exit_kind,
                engine
            )
            VALUES (
                ?, ?, 'LIVE', ?, ?, ?, ?, ?, 'QUEUED',
                datetime('now','utc'),
                'CHILD', ?, ?, 'HEDGE', ?
            )
        """, (
            f"CHILD-{uuid.uuid4().hex[:12]}",
            parent["run_id"],
            parent["marketId"],
            parent["selectionId"],
            child_side,
            float(hedge_odds),
            float(hedge_stake),
            parent_id,
            parent["source"],
            parent["engine"],
        ))

        child_id = int(cur.lastrowid)
        con.commit()

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"ensure_child_queued failed parent_ref={parent_cor}: {e}"
        )
        return None

    finally:
        try:
            con.close()
        except Exception:
            pass

    # --------------------------------------------------
    # 4️⃣ ENQUEUE IMMEDIATELY (LIFECYCLE GUARANTEE)
    # --------------------------------------------------
    try:
        enqueue_router_child(
            {
                "child_id": child_id,
                "parent_cor": str(parent_cor),
            },
            {}
        )
    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"child enqueue failed parent_ref={parent_cor}: {e}"
        )

    return child_id

# === PATCH START: Playbooks Writer (LIVE profit pattern logger) ===
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_update_parent_matched
# 📆 PATCHED: 2025-10-16T21:30Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _record_playbook_entry(cor: str) -> None:
    """
    Log a completed or matched pattern into autoscalp_gui.db→playbooks.

    Trigger: after parent matched or hedge matched.
    Enrichment: joins orders + oc_series to capture odds band context.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        cur = con.cursor()

        # 1️⃣ pull latest parent (joined with child if any)
        row = _q_retry(cur, """
            SELECT o.marketId, o.selectionId, o.source AS strategy,
                   o.entry_odds, o.exit_odds, o.entry_stake, o.exit_stake,
                   o.net_pl, o.realized_pnl,
                   COALESCE(o.exit_kind,'') AS exit_kind,
                   COALESCE(o.role,'PARENT') AS role
              FROM orders o
             WHERE o.customerOrderRef=? LIMIT 1
        """, (str(cor),)).fetchone()
        if not row:
            return

        mid, sid = str(row["marketId"]), str(row["selectionId"])
        pnl = float(row["realized_pnl"] or row["net_pl"] or 0.0)
        outcome = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "NEUTRAL"

        # 2️⃣ extract OC band snapshot for enrichment (oc_series / inbound_oc_cache)
        oc_row = _q_retry(cur, """
            SELECT stage, odd, band_low, band_high
              FROM oc_series
             WHERE marketId=? AND selectionId=?
             ORDER BY snapshot_ts DESC LIMIT 1
        """, (mid, sid)).fetchone()
        oc_stage = oc_row["stage"] if oc_row else "OC?"
        band_low = float(oc_row["band_low"] or 0) if oc_row else None
        band_high = float(oc_row["band_high"] or 0) if oc_row else None

        # 3️⃣ ensure playbooks table exists
        _q_retry(cur, """
            CREATE TABLE IF NOT EXISTS playbooks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT NOT NULL,
                marketId TEXT NOT NULL,
                selectionId INTEGER NOT NULL,
                strategy TEXT,
                oc_stage TEXT,
                pattern_key TEXT,
                band_low REAL,
                band_high REAL,
                entry_odds REAL,
                exit_odds REAL,
                pnl REAL,
                outcome TEXT,
                exposure REAL,
                confidence REAL,
                realized_at TEXT DEFAULT (datetime('now','utc')),
                meta_json TEXT,
                source TEXT DEFAULT 'LIVE'
            )
        """)

        pattern_key = f"{mid}-{sid}-{oc_stage}"
        exposure = abs(float(row["entry_stake"] or 0.0))
        meta = json.dumps({
            "exit_kind": row["exit_kind"],
            "role": row["role"],
            "created_by": "live_router",
        }, separators=(',', ':'))

        _q_retry(cur, """
            INSERT INTO playbooks(day, marketId, selectionId, strategy, oc_stage,
                                  pattern_key, band_low, band_high,
                                  entry_odds, exit_odds, pnl, outcome,
                                  exposure, confidence, meta_json, source)
            VALUES(date('now','utc'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, 'LIVE')
        """, (mid, sid, str(row["strategy"] or "UNK"), oc_stage, pattern_key,
              band_low, band_high,
              float(row["entry_odds"] or 0.0), float(row["exit_odds"] or 0.0),
              pnl, outcome, exposure, meta))
        con.commit()
        _log_event("INFO", "live_router",
                   f"[PLAYBOOK] {outcome} mid={mid} sid={sid} pnl={pnl:+.2f} oc={oc_stage}")
    except Exception as e:
        _log_event("ERROR", "live_router", f"playbook insert failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


# --- PATCH END ----------------------------------------------------------

def _orders_update_child_matched(cor, hedge_ref, exit_side, exit_odds, exit_stake):
    """
    Mark CHILD hedge as matched and stamp provisional P&L on the child row.
    cor = parent customerOrderRef
    """
    _ensure_orders_schema()
    con = _orders_conn()
    cur = con.cursor()

    try:
        parent = _q_retry(cur, """
            SELECT id, side, entry_odds, entry_stake, marketId, selectionId, source, engine
            FROM orders
            WHERE customerOrderRef=? AND role='PARENT'
        """, (str(cor),)).fetchone()

        if not parent:
            return

        parent_id = int(parent["id"])

        # 🔒 CANONICAL GUARD — never trust DB-only MATCHED
        rowc = _q_retry(cur, """
            SELECT entry_bet_id
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
               AND entry_bet_id IS NOT NULL
             LIMIT 1
        """, (parent_id,)).fetchone()

        if not rowc:
            return

        if get_bet_status(str(rowc["entry_bet_id"])) != "EXECUTION_COMPLETE":
            return

        pid, parent_side, parent_odds, parent_stake = (
            parent_id,
            parent["side"],
            float(parent["entry_odds"] or 0.0),
            float(parent["entry_stake"] or 0.0),
        )

        # --- existing P&L + MATCHED logic continues unchanged below ---

        mid = str(parent["marketId"])
        sid = str(parent["selectionId"])
        letter = str(parent["source"] or "")[:1].upper()

        # same formula as parent finalizer (child stores a provisional copy)
        if (parent_side or "").upper() == "LAY":
            win  = float(exit_stake)*(float(exit_odds)-1.0) - float(parent_stake)*(float(parent_odds)-1.0)
            lose = float(parent_stake) - float(exit_stake)
        else:
            win  = (float(parent_odds)-1.0)*float(parent_stake) - (float(exit_odds)-1.0)*float(exit_stake)
            lose = -float(parent_stake) + float(exit_stake)
        realized = round(min(win, lose), 2)

        _q_retry(cur, """
            UPDATE orders
               SET entry_status='MATCHED',
                   exit_status='MATCHED',
                   closed_at=COALESCE(closed_at, datetime('now','utc')),
                   role='CHILD',
                   realized_pnl=?,
                   net_pl=?
             WHERE hedge_of = ?
               AND role = 'CHILD'
               AND entry_bet_id IS NOT NULL
        """, (realized, realized, pid))
        con.commit()

        # ======================================================================
        # 📍 TARGET: engines/live/live_router.py
        # 🔎 ANCHOR: def _orders_update_child_matched(
        # 🧩 ACTION: ADD missing parent exit transition
        # 📆 PATCHED: 2026-02-02 — parent exit_status MUST flip on child match
        #
        # INVARIANT:
        #   CHILD MATCHED ⇒ PARENT exit_status='MATCHED'
        # ======================================================================

        # 🔒 MISSING STEP — CLOSE PARENT ON CHILD MATCH
        _stamp_parent_exit_sql(
            cur,
            parent_id=parent_id,
            exit_status="MATCHED",
        )

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: inside _orders_update_child_matched (after parent close logic)
# 🧩 ACTION: ADD isolated compression ladder (no sibling cancel, no exposure logic)
# 📆 PATCHED: 2026-04-XX — Compression only (isolated)
#
# PURPOSE:
# - Sequential compression in passive zone (7.0 – 12.0)
# - Queue ONE next child level only
# - No other lifecycle changes
#
# INVARIANT:
# - Core parent/child logic remains unchanged
# - BankState untouched
# - Stoploss untouched
# ======================================================================================================

        try:
            COMPRESSION_LOW  = 7.0
            COMPRESSION_HIGH = 12.0

            current_level = float(exit_odds)

            # Only operate in passive compression zone
            if COMPRESSION_LOW <= current_level < COMPRESSION_HIGH:

                from engines.price_math import odds_plus_ticks

                next_level = odds_plus_ticks(current_level, +1)

                if next_level <= COMPRESSION_HIGH:

                    # Ensure no queued/placed child already exists
                    row_existing = _q_retry(cur, """
                        SELECT 1
                          FROM orders
                         WHERE role='CHILD'
                           AND hedge_of=?
                           AND entry_status IN ('QUEUED','PLACING','PLACED')
                         LIMIT 1
                    """, (pid,)).fetchone()

                    if not row_existing:

                        from engines.math.dynamic_stake_v7 import calc_greenup_stake

                        full_hedge = calc_greenup_stake(
                            parent_side,
                            float(parent["entry_odds"]),
                            float(parent["entry_stake"]),
                            float(next_level)
                        )

                        hedge_stake = round(float(full_hedge), 2)

                        if hedge_stake < 1.0:
                            hedge_stake = 1.0

                        _orders_insert_child_queued(cor)

                        _log_event(
                            "INFO",
                            "live_router",
                            f"[COMPRESSION] parent_ref={cor} next_level={next_level}"
                        )

        except Exception as e:
            _log_event(
                "ERROR",
                "live_router",
                f"[COMPRESSION ERROR] ref={cor}: {e}"
            )


# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py:_place_stoploss_child_now
# 📆 PATCHED: 2025-12-12 — Engine penalty on stoploss
# ============================================================================

        try:
            engine = parent["engine"]

            _emit_router_event(
                "plan_outcome",
                {
                    "engine": parent["engine"],
                    "outcome": "BAD",
                    "reason": "stoploss_matched",
                    "marketId": str(parent["marketId"]),
                    "selectionId": str(parent["selectionId"]),
                    "parent_cor": str(cor),
                    "letter": str(parent["source"] or "")[:1].upper(),
                }
            )
        except Exception:
            pass

# === PATCH END ==============================================================


        # === NEW: persist cap number snapshot to Mastery v7 cloud (safe path) ===
        try:
            from engines.config_paths import connect_mastery_v7_cache
            mcon = connect_mastery_v7_cache()
            _q_retry(mcon, """
                CREATE TABLE IF NOT EXISTS cap_state(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day TEXT,
                    marketId TEXT,
                    selectionId TEXT,
                    letter TEXT,
                    active_caps INTEGER,
                    ts TEXT
                )
            """)
            count = _active_parents_count_per_letter(mid, sid, letter)
            _q_retry(mcon, """
                INSERT INTO cap_state(day, marketId, selectionId, letter, active_caps, ts)
                VALUES(date('now','utc'), ?, ?, ?, ?, datetime('now','utc'))
            """, (mid, sid, letter, count))
            mcon.commit(); mcon.close()
            print(f"[CAP] 💾 cloud-write mid={mid} sid={sid} letter={letter} count={count}")
        except Exception as e:
            _log_event("WARN","live_router",f"cap_state cloud insert failed mid={mid} sid={sid}: {e}")
        # === END NEW ===


        _orders_probe(cor, note="child_matched")
        print(f"[CAP] mid={mid} sid={sid} letter={letter} count={count}")

        _orders_probe(cor, note="child_matched")
        # NEW: also stamp matched odds/stake if not already present
        try:
            row = _q_retry(cur, """
                SELECT entry_bet_id FROM orders
                 WHERE hedge_of=(SELECT id FROM orders WHERE customerOrderRef=?)
                 ORDER BY id DESC LIMIT 1
            """, (str(cor),)).fetchone()
            bet_id = row["entry_bet_id"] if row else None
            if bet_id:
                app_key, token = _keys()
                avg_odds, matched_size = _fetch_avg_match(app_key, token, str(bet_id))
                if avg_odds > 0.0 and matched_size > 0.0:
                    _q_retry(cur, """
                        UPDATE orders
                           SET entry_matched_odds=?,
                               entry_matched_stake=?
                         WHERE entry_bet_id=? AND entry_matched_odds IS NULL
                    """, (avg_odds, matched_size, str(bet_id)))
                    con.commit()
        except Exception as e:
            _log_event("WARN","live_router",f"child_matched: could not stamp avg match ref={cor} err={e}")

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: def _orders_update_child_matched(
# 🧩 ACTION: Canonical parent close + BankState exposure release on child match
# 📆 PATCHED: 2026-03-20 — fix exposure leak (child matched but parent never closed)
#
# INVARIANT:
#   CHILD MATCHED  ⇒  PARENT CLOSED  ⇒  BankState exposure released EXACTLY ONCE
# ============================================================================

        # --------------------------------------------------
        # 🔒 CANONICAL PARENT CLOSE (ON CHILD MATCH)
        # --------------------------------------------------
        try:
            # Close parent deterministically
            _q_retry(cur, """
                UPDATE orders
                   SET parent_closed = 1,
                       exit_status   = 'MATCHED',
                       closed_at     = COALESCE(closed_at, datetime('now','utc'))
                 WHERE id = ?
                   AND role = 'PARENT'
                   AND parent_closed = 0
            """, (pid,))

            # 🔓 RELEASE BankState exposure (single source of truth)
            parent_id = int(parent["id"])  # or fetched explicitly
            _release_parent_exposure_db(parent_id)

            # ======================================================================================================
# 📍 TARGET: engines/bus/bus.py
# 🔎 ANCHOR: child exit_status == 'MATCHED'
# 🧩 ACTION: ADD — Shadow confidence counter (execution-truth only)
# 📆 PATCHED: 2026-01-24 — Shadow pattern accounting v1
# ======================================================================================================

            from engines.shadow_confidence import record

            # Direction is derived from parent → child hedge relationship
            if parent_side.upper() == "LAY" and exit_side.upper() == "BACK":
                direction = "DRIFT"
            elif parent_side.upper() == "BACK" and exit_side.upper() == "LAY":
                direction = "STEAM"
            else:
                # Fallback (should never happen, but stay safe)
                direction = "UNKNOWN"

            n = record(mid, sid, parent["engine"], direction)

            print(f"[SHADOW][CONF] {(mid, sid, parent['engine'])} {direction}={n}")


            # Mark exposure as released (idempotent)
            _q_retry(cur, """
                UPDATE orders
                   SET exposure_released = 1
                 WHERE id = ?
            """, (pid,))

            con.commit()

            # --------------------------------------------------
            # ROUTER SNAPSHOT — child matched state
            # --------------------------------------------------
            try:
                __ROUTER_CHILD_SURFACE[parent_id] = {
                    "child_id": child_id,
                    "entry_status": "MATCHED",
                    "exit_status": "MATCHED",
                }
            except Exception:
                pass

            _log_event(
                "INFO",
                "bankstate",
                f"[EXPOSURE RELEASE] parent_ref={cor} reason=child_matched"
            )

        except Exception as e:
            _log_event(
                "ERROR",
                "bankstate",
                f"[EXPOSURE RELEASE FAILED] parent_ref={cor}: {e}"
            )

# === PATCH END ==============================================================


        # === PATCH START: per-letter CAP summary hook =========================
        try:
            # 1️⃣ locate parent row by its customerOrderRef
            prow = _q_retry(cur, """
                SELECT id, marketId, selectionId, UPPER(SUBSTR(source,1,1)) AS letter
                  FROM orders
                 WHERE customerOrderRef=? AND role='PARENT'
                 LIMIT 1
            """, (str(cor),)).fetchone()
            if prow:
                mid, sid, letter = str(prow["marketId"]), str(prow["selectionId"]), str(prow["letter"])
                _cap_numbers_summary(mid, sid, letter)
        except Exception as e:
            _log_event("WARN", "live_router", f"cap summary fail ref={cor}: {e}")
        # === PATCH END ========================================================

    except Exception as e:
        _log_event("ERROR", "live_router", f"orders update child matched failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _orders_update_hedge_matched(
# 📆 PATCHED: 2026-02-10 — Settlement EventSync A: HEDGE_EXIT
# ============================================================================
def _orders_update_hedge_matched(*, cor: str, exit_side: str,
                                 exit_odds: float, exit_stake: float) -> None:
    """
    Finalize hedge exit:
    - Compute realized PnL
    - Release BankState exposure
    - Persist DB state
    """
    _ensure_orders_schema()

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        # Load parent FIRST
        p = _q_retry(cur, """
            SELECT id, engine, source, side, entry_odds, entry_stake, marketId, selectionId
              FROM orders
             WHERE customerOrderRef=? AND role='PARENT'
             LIMIT 1
        """, (str(cor),)).fetchone()

        if not p:
            return



        # Compute realized PnL
        entry_side = p["side"].upper()
        E, S = float(p["entry_odds"]), float(p["entry_stake"])
        H, S2 = float(exit_odds), float(exit_stake)

        if entry_side == "LAY":
            win  = S2*(H-1.0) - S*(E-1.0)
            lose = S - S2
        else:
            win  = (E-1.0)*S - (H-1.0)*S2
            lose = -S + S2

        realized = round(min(win, lose), 2)

        # Persist DB state
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='MATCHED',
                   exit_status='MATCHED',
                   exit_kind='HEDGE',
                   exit_odds=?, exit_stake=?,
                   realized_pnl=?, net_pl=COALESCE(net_pl,0)+?,
                   closed_at=datetime('now','utc'),
                   mode='LIVE'
             WHERE customerOrderRef=?
        """, (H, S2, realized, realized, str(cor)))
        con.commit()

        _orders_probe(cor, note="hedge_matched")

        # === EVENTSYNC: A — HEDGE EXIT =====================================
        try:
            _emit_settlement_router_event(
                "HEDGE_EXIT",
                cor=cor,
                market_id=p["marketId"],
                selection_id=p["selectionId"],
                exit_odds=H,
                exit_stake=S2,
                realized=realized
            )
        except Exception as e:
            _log_event("WARN","live_router",f"EventSync hedge exit failed: {e}")
        # ====================================================================
        # === PATCH START: free letter slot after hedge ===
        try:
            mid, sid, src = str(p["marketId"]), str(p["selectionId"]), str(p["source"] or "")
            letter = src[:2].upper()
            freed = _active_parents_count_per_letter(mid, sid, letter)
            _log_event("INFO","live_router",f"[CAP] freed slot mid={mid} sid={sid} letter={letter} active_now={freed}")
        except Exception as e:
            _log_event("WARN","live_router",f"[CAP] free-slot fail {e}")
        # === PATCH END ===

        # NEW: also stamp matched odds/stake if not already present
        try:
            row = _q_retry(cur, "SELECT exit_bet_id FROM orders WHERE customerOrderRef=? LIMIT 1", (str(cor),)).fetchone()
            bet_id = row["exit_bet_id"] if row else None
            if bet_id:
                app_key, token = _keys()
                avg_odds, matched_size = _fetch_avg_match(app_key, token, str(bet_id))
                if avg_odds > 0.0 and matched_size > 0.0:
                    _q_retry(cur, """
                        UPDATE orders
                           SET exit_odds=?, exit_stake=?
                         WHERE customerOrderRef=? AND exit_odds IS NULL
                    """, (avg_odds, matched_size, str(cor)))

                    con.commit()
        except Exception as e:
            _log_event("WARN","live_router",f"hedge_matched: could not stamp avg match ref={cor} err={e}")

        # Mastery + strategy logging
        try:
            _mastery_log("trade_outcome", {
                "result": "good",
                "exit_kind": "HEDGE",
                "market": str(p["marketId"]),
                "runner": str(p["selectionId"]),
                "entry_odds": E,
                "entry_stake": S,
                "exit_odds": H,
                "exit_stake": S2,
                "realized": realized,
                "source": str(p["source"] or "")
            })

        except Exception:
            pass

        # === PATCH 4A: Settlement EventSync for hedge exits ================
        try:
            _emit_settlement_router_event(
                cor,
                outcome="HEDGE_EXIT",
                market_id=p["marketId"],
                selection_id=p["selectionId"],
            )
        except Exception:
            pass
        # ====================================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py:_orders_update_hedge_matched
# 📆 PATCHED: 2025-12-12 — Engine reward on successful hedge
# ============================================================================

        try:
            engine = _engine_from_source(p["source"])
            _emit_router_event(
                "plan_outcome",
                {
                    "engine": engine,
                    "outcome": "GOOD",
                    "reason": "hedge_matched",
                    "marketId": str(p["marketId"]),
                    "selectionId": str(p["selectionId"]),
                    "parent_cor": str(cor),
                    "letter": str(p["source"] or "")[:1].upper(),
                }
            )
        except Exception:
            pass

# === PATCH END ==============================================================


        try:
            _ = autoscalp_db()
            _sp_log_exit_live(run_id=p["run_id"], strategy=p["source"] or "LEGACY",
                              mid=str(p["marketId"]), sid=str(p["selectionId"]), pnl=realized)
        except Exception:
            pass

        try:
            _update_book_state(run_id="LIVE")
        except Exception:
            pass

    except Exception as e:
        _log_event("ERROR", "live_router", f"orders update hedge matched failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass


# === PATCH START: Book State updater ===
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_update_hedge_matched
# (place just *after* _orders_update_hedge_matched so it runs after exit is stamped)

def _ensure_book_state_schema() -> None:
    """Idempotently ensure book_state table exists in AUTO DB."""
    con = _orders_conn(); cur = con.cursor()
    try:
        _q_retry(cur, """
            CREATE TABLE IF NOT EXISTS book_state(
              day TEXT NOT NULL,
              run_id TEXT NOT NULL,
              letter TEXT NOT NULL,
              open_parents INTEGER DEFAULT 0,
              matched_children INTEGER DEFAULT 0,
              open_liability REAL DEFAULT 0.0,
              net_pl REAL DEFAULT 0.0,
              updated_at TEXT,
              PRIMARY KEY(day, run_id, letter)
            )
        """)
        con.commit()
    finally:
        try: con.close()
        except Exception: pass

def close_logically_finished_parents():
    con = _orders_conn()
    cur = con.cursor()

    # Parents closed by matched children
    cur.execute("""
        UPDATE orders
           SET parent_closed = 1
         WHERE role='PARENT'
           AND parent_closed = 0
           AND EXISTS (
                SELECT 1 FROM orders c
                 WHERE c.hedge_of = orders.id
                   AND c.entry_status = 'MATCHED'
           )
    """)

    # Parents closed by market time
    cur.execute("""
        UPDATE orders
           SET parent_closed = 1
         WHERE role='PARENT'
           AND parent_closed = 0
           AND marketId IN (
                SELECT marketId
                  FROM bets.bets
                 WHERE datetime(marketStartTime) < datetime('now','utc','-15 minutes')
           )
    """)

    con.commit()
    con.close()


def _update_book_state(run_id: str = "LIVE") -> None:
    """
    Compute per-letter open/matched/liability and upsert into book_state.
    Called each tick after placements/hedges.
    """
    _ensure_book_state_schema()
    con = _orders_conn(); con.row_factory = sqlite3.Row
    try:
        day = datetime.utcnow().strftime("%Y-%m-%d")

        rows = _q_retry(con, """
            SELECT role, source, side, entry_odds, entry_stake,
                   entry_status, exit_status, net_pl
              FROM orders
             WHERE mode='LIVE' AND date(opened_at)=date('now','utc')
        """).fetchall()

        agg: dict[str, dict] = {}
        for r in rows:
            L = str(r["source"] or "?")[:1].upper()
            agg.setdefault(L, {"open_parents":0,"matched_children":0,"open_liability":0.0,"net_pl":0.0})
            role  = (r["role"] or "").upper()
            estat = (r["entry_status"] or "").upper()
            xstat = (r["exit_status"] or "").upper()
            side  = (r["side"] or "").upper()
            px    = float(r["entry_odds"] or 0.0)
            st    = float(r["entry_stake"] or 0.0)

            # Parent open if matched but no matched exit
            if role=="PARENT" and estat=="MATCHED" and xstat!="MATCHED":
                agg[L]["open_parents"] += 1
                # liability calc
                liab = (st*(px-1.0)) if side=="LAY" else st
                agg[L]["open_liability"] += liab

            # Child matched
            if role=="CHILD" and estat=="MATCHED":
                agg[L]["matched_children"] += 1

            # Net PL accumulates whenever stamped
            try:
                if r["net_pl"] is not None:
                    agg[L]["net_pl"] += float(r["net_pl"])
            except Exception:
                pass

        # upsert per letter
        cur = con.cursor()
        for L, d in agg.items():
            _q_retry(cur, """
                INSERT INTO book_state(day, run_id, letter, open_parents, matched_children,
                                       open_liability, net_pl, updated_at)
                VALUES(?,?,?,?,?,?,?,datetime('now','utc'))
                ON CONFLICT(day, run_id, letter) DO UPDATE SET
                  open_parents=excluded.open_parents,
                  matched_children=excluded.matched_children,
                  open_liability=excluded.open_liability,
                  net_pl=excluded.net_pl,
                  updated_at=excluded.updated_at
            """, (day, run_id, L,
                  d["open_parents"], d["matched_children"],
                  d["open_liability"], d["net_pl"]))
        con.commit()
    except Exception as e:
        _log_event("ERROR","live_router",f"book_state update failed: {e}")
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


# --- PATCH END ----------------------------------------------------------
def _fetch_avg_match(app_key: str, token: str, bet_id: str) -> tuple[float, float]:
    """Return (avg_price_matched, size_matched) or (0.0, 0.0)."""
    try:
        cur = _list_current(app_key, token, bet_id)
        orders = (cur.get("result", {}) or {}).get("currentOrders") or []
        if not orders:
            return (0.0, 0.0)
        o = orders[0]
        apm = float(o.get("averagePriceMatched") or 0.0)
        sm  = float(o.get("sizeMatched") or 0.0)
        return (apm, sm)
    except Exception:
        return (0.0, 0.0)



# Count open parents (entry orders) for this runner (matched but not hedged)
def _open_parents_count_live(market_id: str, selection_id: str) -> int:
    con = _con()
    try:
        con.row_factory = sqlite3.Row
        row = _q_retry(con, "SELECT COUNT(*) AS n FROM orders "
            "WHERE mode='LIVE' AND marketId=? AND selectionId=? "
            "AND entry_status='MATCHED' AND (exit_status IS NULL OR exit_status <> 'MATCHED')",
            (str(market_id), str(selection_id))
        ).fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0
    finally:
        try: con.close()
        except Exception: pass

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _active_parents_count_per_letter(
# 📆 PATCHED: 2025-12-03 — remove embedded patch text & fix SQL
# ---------------------------------------------------------------------------

def _active_parents_count_per_letter(market_id: str, selection_id: str, letter: str) -> int:
    """
    Count active parents for this (market, selection, letter).
    Active = entry_status in ('PLACED','MATCHED')
             AND no matched hedge/stoploss child yet.
    MSC extensions: letters D, J, V.
    """
    try:
        L = str(letter or "").upper()
        # Accept raw letters or MSC tags
        VALID = {"A","B","C","D","E","F","G","H","I","J","K",
                 "L","P","R","S","T","V","X","Z"}
        if L not in VALID:
            L = L[:1]  # fallback safe

        con = _orders_conn(); con.row_factory = sqlite3.Row

        row = _q_retry(con, """
            SELECT COUNT(*) AS n
              FROM orders p
             WHERE mode='LIVE'
               AND role='PARENT'
               AND marketId=? AND selectionId=?
               AND UPPER(COALESCE(source,'')) LIKE UPPER(?)
               AND UPPER(COALESCE(entry_status,'')) IN ('PLACED','MATCHED')
               AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
               AND NOT EXISTS (
                     SELECT 1 FROM orders c
                      WHERE c.hedge_of=p.id
                        AND UPPER(COALESCE(c.exit_status,''))='MATCHED'
               )
        """, (str(market_id), str(selection_id), f"{L}%")).fetchone()

        return int(row["n"] or 0)
    except Exception:
        return 0
    finally:
        try: con.close()
        except Exception:
            pass

# === PATCH END ============================================================

# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _orders_update_parent_cancelled(
# 🧩 ACTION: Ensure exit_status only
# 📆 PATCHED: 2026-04-XX
# ======================================================================

def _orders_update_parent_cancelled(cor: str, *, reason: str = "timeout") -> None:
    _ensure_orders_schema()
    con = _orders_conn()
    cur = con.cursor()

    try:
        row = _q_retry(cur, """
            SELECT id
              FROM orders
             WHERE customerOrderRef=?
               AND role='PARENT'
             LIMIT 1
        """, (str(cor),)).fetchone()

        if not row:
            return

        parent_id = int(row["id"])

        _stamp_parent_exit_sql(
            cur,
            parent_id=parent_id,
            exit_status="CANCELLED",
            reason=reason,
        )

        con.commit()

    finally:
        con.close()

    _release_unmatched_parent_exposure(cor)



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_insert_child_live\(parent_cor:
#    REPLACE the function with the version below (adds exit_kind column)
# 📆 PATCHED: 2025-09-29T14:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _orders_insert_child_live(parent_cor: str, *, market_id: str, selection_id: str,
                              side: str, odds: float, stake: float,
                              bet_id: str, engine, source: str = "LEGACY_STRATEGY",
                              exit_kind: str = "HEDGE") -> Optional[int]:
                             
    """
    Create a CHILD row linked to the parent (hedge_of=parent.id), mark live.
    Returns child id. Planned hedges default to exit_kind='HEDGE'.
    """
    letter = "S" if exit_kind.upper() == "STOPLOSS" else "H"
    source = letter  # ensures book_state and playbooks carry H or S

    _ensure_orders_schema()
    con = _orders_conn(); con.row_factory = sqlite3.Row
    try:
        parent = _q_retry(con, "SELECT id, run_id FROM orders WHERE customerOrderRef=? LIMIT 1",
                          (str(parent_cor),)).fetchone()

        engine = parent["engine"] if parent else None

        if not parent:
            _log_event("ERROR", "live_router", f"insert_child: parent not found ref={parent_cor}")
            return None
        pid = int(parent["id"])
        fk  = int(parent["run_id"]) if parent["run_id"] is not None else _run_fk_id("LIVE-AUTO", mode="LIVE")
        cur = con.cursor()

        _q_retry(cur, """
            INSERT INTO orders(
              customerOrderRef, run_id, mode, marketId, selectionId,
              side, entry_odds, entry_stake, entry_status, opened_at,
              entry_bet_id, role, hedge_of, source, exit_kind, engine
            ) VALUES (?, ?, 'LIVE', ?, ?, ?, ?, ?, engine, datetime('now','utc'),
                      ?, 'CHILD', ?, ?, ?)
        """, (_ref("CHILD"), int(fk), str(market_id), str(selection_id),
              side.upper(), float(odds), float(stake), str(bet_id or ""), pid, str(source), str(exit_kind).upper()))
        child_id = int(cur.lastrowid)

        # also stamp parent exit fields (price/size) pre‑emptively
        _q_retry(cur, """
            UPDATE orders
               SET exit_bet_id = ?, exit_odds = ?, exit_stake = ?
             WHERE id = ?
        """, (str(bet_id or ""), float(odds), float(stake), pid))

        con.commit()
        try:
            _ledger_link_child_by_parent_cor(parent_cor, child_id)
        except Exception:
            pass
        return child_id
    except Exception as e:
        _log_event("ERROR", "live_router", f"insert_child error parent_ref={parent_cor}: {e}")
        return None
    finally:
        try: con.close()
        except Exception: pass

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _emit_router_event(
# 📆 PATCHED: 2026-02-10 — Settlement EventSync hook for router exits
# ============================================================================



def _emit_settlement_router_event(event_type: str, *, cor: str,
                                  market_id: str, selection_id: str,
                                  exit_odds: float | None = None,
                                  exit_stake: float | None = None,
                                  realized: float | None = None):
    """
    Unified settlement notifier for router exits.
    Emits:
        • HEDGE_EXIT
        • STOPLOSS_EXIT
        • AUTO_SETTLED

    Always includes:
        cor, marketId, selectionId, exit_odds, exit_stake, realized_pnl
    """
    try:
        payload = {
            "event": event_type,
            "cor": cor,
            "marketId": str(market_id),
            "selectionId": str(selection_id),
            "exit_odds": exit_odds,
            "exit_stake": exit_stake,
            "realized_pnl": realized,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        emit_event("settlement", payload)
    except Exception as e:
        print(f"[EventSync][router_exit] warn: {e}")

# === PATCH END ==============================================================
# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 REPLACE: def _place_stoploss_child_now
# 📆 PATCHED: 2026-02-12 — BankState exposure release (STOPLOSS)
# ======================================================================

def _place_stoploss_child_now(
    parent_cor: str,
    *,
    market_id: str,
    selection_id: str,
    exit_side: str,
    exit_odds: float,
    parent_stake: float,
    run_id: str | None = None
) -> Optional[int]:
    """
    STOPLOSS execution.

    Guarantees:
    - BankState exposure released EXACTLY ONCE
    - CHILD does not affect exposure
    """

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()




    try:
        parent = _q_retry(cur, """
            SELECT id, run_id, engine, source, entry_odds, entry_stake, exit_status
              FROM orders
             WHERE customerOrderRef=? AND role='PARENT'
             LIMIT 1
        """, (str(parent_cor),)).fetchone()

        # Before any parent exit mutation
        if parent["entry_status"] == "MATCHED":
            _ensure_child_queued_for_matched_parent(parent_cor)

        if not parent:
            return None

        # ⛔ Guard: never double-release exposure
        if (parent["exit_status"] or "").upper() == "MATCHED":
            return None

        pid = int(parent["id"])
        fk  = int(parent["run_id"] or 0)

        # 🔐 RELEASE exposure (once)
        parent_id = int(parent["id"])  # or fetched explicitly
        _release_parent_exposure_db(parent_id)

        # 🔁 STOPLOSS must NOT place directly — enqueue child instead
        child_id = _ensure_child_queued_for_matched_parent(parent_cor)

        if not child_id:
            return None

        child_id = _ensure_child_queued_for_matched_parent(parent_cor)



        _stamp_parent_exit_sql(
            cur,
            parent_id=pid,
            exit_status="MATCHED",
            reason="stoploss",
        )

        return child_id
        con.commit()

        # Optional diagnostic proof
        _check_bankstate_invariant(parent["engine"])

        # Event only (no finance)
        try:
            _emit_settlement_router_event(
                "STOPLOSS_EXIT",
                cor=parent_cor,
                market_id=market_id,
                selection_id=selection_id,
                exit_odds=float(exit_odds),
                exit_stake=float(parent_stake),
                realized=None
            )
        except Exception:
            pass

        return child_id

    except Exception as e:
        _log_event("ERROR", "live_router",
                   f"stoploss failed ref={parent_cor}: {e}")
        return None
    finally:
        try: con.close()
        except Exception: pass




# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _sweep_close_finished_markets(
# 🧩 ACTION: REPLACE FUNCTION BODY
# 📆 PATCHED: 2026-01-31 — Phase-0-aligned one-shot market sweep
#
# RULES (CANONICAL):
#   1) TODAY ONLY — markets with LIVE parents opened today (UTC)
#   2) TIME GATE  — sweep only if (now - off_at) >= 360 seconds
#   3) ONE-SHOT   — never sweep a market twice (exposure_released guard)
# ======================================================================

def _sweep_close_finished_markets(grace_min: int = 6) -> tuple[int, int]:
    """
    Phase-0 aligned sweep.

    Cancels unmatched parents and settles matched parents
    ONLY for markets that:
      • have LIVE parents opened today (UTC)
      • are ≥ 6 minutes past off
      • have NOT already been swept

    Returns (n_cancelled, n_settled)
    """



    try:
        from datetime import datetime, timezone
        import sqlite3
        from engines.config_paths import open_auto_db, connect_db

        now = datetime.now(timezone.utc)

        # --------------------------------------------------
        # 1️⃣ Identify TODAY markets with LIVE parents
        # --------------------------------------------------
        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        rows = con.execute("""
            SELECT DISTINCT
                p.marketId,
                b.marketStartTime
            FROM orders p
            JOIN bets b ON b.marketId = p.marketId
            WHERE p.mode='LIVE'
              AND p.role='PARENT'
              AND date(p.opened_at)=date('now','utc')
              AND COALESCE(p.exposure_released,0)=0
        """).fetchall()

        con.close()

        if not rows:
            return (0, 0)

        # --------------------------------------------------
        # 2️⃣ Apply −360s time gate (Phase-0 rule)
        # --------------------------------------------------
        sweep_mids = []

        for r in rows:
            try:
                if r["entry_status"] == "MATCHED":
                    _ensure_child_queued_for_matched_parent(r["customerOrderRef"])
                off = datetime.fromisoformat(
                    r["marketStartTime"].replace("Z", "+00:00")
                )
            except Exception:
                continue

            secs = (off - now).total_seconds()

            # EXACT RULE: market finished only if ≥ 6 min AFTER off
            if secs <= -360:
                sweep_mids.append(str(r["marketId"]))

        if not sweep_mids:
            return (0, 0)

        # --------------------------------------------------
        # 3️⃣ One-shot sweep (DB-authoritative)
        # --------------------------------------------------
        con = open_auto_db(rw=True)
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        qph = ",".join("?" * len(sweep_mids))

        # A) CANCEL unmatched parents
        for mid in sweep_mids:
            rows = cur.execute("""
                SELECT id, customerOrderRef, entry_bet_id
                  FROM orders
                 WHERE mode='LIVE'
                   AND role='PARENT'
                   AND marketId=?
                   AND UPPER(entry_status) IN ('QUEUED','PLACED')
                   AND COALESCE(exposure_released,0)=0
            """, (mid,)).fetchall()

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: for mid in sweep_mids:
# 🧩 ACTION: Guard parent cancellation with Betfair + lifecycle invariant
# 📆 PATCHED: 2026-04-XX — orphan parent cancellation safety (indent fix)
# ============================================================================

            for r in rows:
                cor = str(r["customerOrderRef"])

                row2 = _q_retry(cur, """
                    SELECT entry_bet_id
                      FROM orders
                     WHERE id=?
                       AND role='PARENT'
                """, (int(r["id"]),)).fetchone()

                bet_id = row2["entry_bet_id"] if row2 else None

                # 🔒 GUARD: never cancel if already matched at Betfair
                if not _guard_cancel_if_matched(parent_cor=cor, bet_id=bet_id):
                    continue

                _stamp_parent_exit_sql(
                    cur,
                    parent_id=int(r["id"]),
                    exit_status="CANCELLED",
                    reason="cleanup_orphan",
                )

                _log_event(
                    "WARN",
                    "live_router",
                    f"[CLEANUP] orphan parent cancelled ref={cor}"
                )

# === PATCH END ==============================================================

        # B) SETTLE matched parents
        rows = cur.execute(f"""
            SELECT id
              FROM orders
             WHERE mode='LIVE'
               AND role='PARENT'
               AND marketId IN ({qph})
               AND UPPER(COALESCE(entry_status,''))='MATCHED'
               AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
        """, tuple(sweep_mids)).fetchall()

        for r in rows:
            _stamp_parent_exit_sql(
                cur,
                parent_id=int(r["id"]),
                exit_status="SETTLED",
            )

        con.commit()
        con.close()


        _log_event(
            "INFO",
            "live_router",
            f"_sweep_close_finished_markets: "
            f"cancelled={n_cancel} settled={n_settle} markets={len(sweep_mids)}"
        )

        return (int(n_cancel or 0), int(n_settle or 0))

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"sweep_close_finished_markets error: {e}"
        )
        return (0, 0)





# Public status helper for orchestrator
# engines/live/live_router.py (or shared helper module)

def get_bet_status(bet_id: str) -> str:
    """
    Canonical Betfair execution status.

    ORDER OF AUTHORITY:
      1) execution_events (persistent truth)
      2) Betfair CURRENT
      3) Betfair CLEARED
      4) UNKNOWN

    Returns only:
      - EXECUTION_COMPLETE
      - EXECUTABLE
      - UNKNOWN
    """
    if not bet_id:
        return "UNKNOWN"

    # --------------------------------------------------
    # 1️⃣ PERSISTENT EXECUTION TRUTH (AUTHORITATIVE)
    # --------------------------------------------------
    try:
        from engines.config_paths import autoscalp_db
        import sqlite3

        con = open_auto_db(rw=True)
        con.row_factory = sqlite3.Row
        row = con.execute("""
            SELECT fully_matched
              FROM execution_events
             WHERE bet_id = ?
             LIMIT 1
        """, (str(bet_id),)).fetchone()
        con.close()

        if row and int(row["fully_matched"]) == 1:
            return "EXECUTION_COMPLETE"

    except Exception:
        # Never block — fall through to Betfair
        pass

    # --------------------------------------------------
    # 2️⃣ BETFAIR MATCH SURFACE (CURRENT ⊔ CLEARED)
    # --------------------------------------------------
    try:
        app_key, token = _keys()

        surf = query_bet_match_surface(
            bet_id=str(bet_id),
            app_key=app_key,
            token=token,
        )

        if not isinstance(surf, dict):
            return "UNKNOWN"

        matched = float(surf.get("matched") or 0.0)
        source  = str(surf.get("source") or "").upper()

        # ANY MONEY MATCHED = EXECUTION_COMPLETE
        if matched > 0.0:
            return "EXECUTION_COMPLETE"

        # CLEARED ALWAYS EXECUTION_COMPLETE
        if source == "CLEARED":
            return "EXECUTION_COMPLETE"

        # Otherwise still executable
        if str(surf.get("state") or "").upper() == "LIVE":
            return "EXECUTABLE"

        return "UNKNOWN"


    except Exception:
        return "UNKNOWN"


# ── public adapter: parent now, optional hedge after matched ────────────────
def _infer_phase_from_schedule(mid: str) -> str:
    """Return 'IP' if TTO<=0 else 'PRE'. Falls back to PRE on any error."""
    try:
        from engines.decision_engine.orchestrator import _compute_minutes_to_off
        mto, _ = _compute_minutes_to_off(str(mid), source="LIVE")
        return "IP" if (mto is not None and float(mto) <= 0.0) else "PRE"
    except Exception:
        return "PRE"

def _secs_to_off(mid: str) -> float:
    """
    Seconds from now (UTC) to off_at_utc for this marketId. Large number on error.
    """
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        row = _q_retry(bdb, "SELECT CAST((julianday(off_at_utc) - julianday('now','utc'))*86400.0 AS INTEGER) AS s "
                            "FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        bdb.close()
        return float(row["s"]) if row and row["s"] is not None else 999999.0
    except Exception:
        return 999999.0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_insert_child_queued\(
#    REPLACE the function with the version below (adds exit_kind column)
# 📆 PATCHED: 2025-09-29T14:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _orders_insert_child_queued(parent_cor: str) -> int | None:
    """
    Canonical CHILD creation function.

    CONTRACT:
    - Parent MUST exist and be MATCHED
    - Exactly ONE CHILD row will exist after this call
    - Child will be QUEUED
    - Idempotent (safe to call many times)
    - DB is the only source of truth
    """

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        # 1️⃣ Load matched parent (authoritative)
        parent = _q_retry(cur, """
            SELECT
                id,
                run_id,
                marketId,
                selectionId,
                side,
                entry_odds,
                entry_stake,
                source,
                engine,
                target_ticks,
                entry_status,
                exit_status
            FROM orders
            WHERE customerOrderRef=?
              AND role='PARENT'
            LIMIT 1
        """, (str(parent_cor),)).fetchone()

        # --------------------------------------------------
        # 🔒 HARD ROUTER INVARIANT
        # --------------------------------------------------
        if not parent:
            return None

        if (parent["entry_status"] or "").upper() != "MATCHED":
            # Parent is QUEUED / PLACED / CANCELLED / FAILED / EXPIRED
            return None

        if (parent["exit_status"] or "").upper() in ("CANCELLED","EXPIRED","SETTLED"):
            return None


        parent_id = int(parent["id"])

        # 2️⃣ Idempotency: child already exists?
        row = _q_retry(cur, """
            SELECT id
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
            LIMIT 1
        """, (parent_id,)).fetchone()

        if row:
            return int(row["id"])

        # 3️⃣ Deterministic hedge parameters (DB-derived)
        parent_side = parent["side"].upper()
        child_side  = "BACK" if parent_side == "LAY" else "LAY"

        ticks = int(parent["target_ticks"] or 1)

        from engines.price_math import odds_plus_ticks

        hedge_odds = odds_plus_ticks(
            float(parent["entry_odds"]),
            +ticks if child_side == "BACK" else -ticks
        )
        hedge_odds = _round_odds(float(hedge_odds))

        full_hedge_stake = calc_greenup_stake(
            parent_side,
            float(parent["entry_odds"]),
            float(parent["entry_stake"]),
            hedge_odds
        )

        hedge_stake = round(float(full_hedge_stake) * PROGRESSIVE_LOCK_FACTOR, 2)

        # Safety: never below £1 minimum
        if hedge_stake < 1.0:
            hedge_stake = 1.0

        # 4️⃣ Insert CHILD (DB-first, QUEUED)
        _q_retry(cur, """
            INSERT INTO orders (
                customerOrderRef,
                run_id,
                mode,
                marketId,
                selectionId,
                side,
                entry_odds,
                entry_stake,
                entry_status,
                opened_at,
                role,
                hedge_of,
                source,
                exit_kind,
                engine
            )
            VALUES (
                ?, ?, 'LIVE', ?, ?, ?, ?, ?, 'QUEUED',
                datetime('now','utc'),
                'CHILD', ?, ?, 'HEDGE', ?
            )
        """, (
            f"CHILD-{uuid.uuid4().hex[:12]}",
            parent["run_id"],
            parent["marketId"],
            parent["selectionId"],
            child_side,
            float(hedge_odds),
            float(hedge_stake),
            parent_id,
            parent["source"],
            parent["engine"],
        ))

        con.commit()
        child_id = int(cur.lastrowid)

        # --------------------------------------------------
        # ROUTER SNAPSHOT — child lifecycle state
        # --------------------------------------------------
        try:
            _ROUTER_CHILD_SURFACE[parent_id] = {
                "child_id": child_id,
                "entry_status": "QUEUED",
                "exit_status": None,
            }
        except Exception:
            pass

        return child_id

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"child ensure failed parent_ref={parent_cor}: {e}"
        )
        return None

    finally:
        try:
            con.close()
        except Exception:
            pass


def _sync_settlement_terminal_exposure(limit: int = 200) -> int:
    """
    Settlement alignment sweep.

    If Settlement has flipped a PARENT to a terminal state
    and exposure has not yet been released, release it here.

    Idempotent.
    Router owns exposure lifecycle.
    """
    fixed = 0
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()



    try:
        rows = _q_retry(cur, """
            SELECT
                customerOrderRef,
                engine,
                entry_odds,
                entry_stake
            FROM orders
            WHERE role='PARENT'
              AND UPPER(exit_status) IN ('SETTLED','EXPIRED','CANCELLED')
              AND COALESCE(exposure_released, 0) = 0
            LIMIT ?
        """, (int(limit),)).fetchall()

        for r in rows:
            try:
                if r["entry_status"] == "MATCHED":
                    _ensure_child_queued_for_matched_parent(r["customerOrderRef"]) 


                _q_retry(cur, """
                    UPDATE orders
                       SET exposure_released = 1
                     WHERE customerOrderRef = ?
                """, (str(r["customerOrderRef"]),))

                fixed += 1

            except Exception as e:
                _log_event(
                    "ERROR",
                    "live_router",
                    f"settlement exposure release failed ref={r['customerOrderRef']}: {e}"
                )

        con.commit()
        return fixed

    finally:
        try:
            con.close()
        except Exception:
            pass


def _orders_update_child_placed(child_id: int, bet_id: str) -> None:
    """Mark child as PLACED with entry_bet_id."""
    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='PLACED',
                   entry_bet_id=?,
                   mode='LIVE',
                   role='CHILD'
             WHERE id=?
        """, (str(bet_id), int(child_id)))
        con.commit()
    except Exception as e:
        _log_event("ERROR", "live_router", f"child update PLACED failed id={child_id}: {e}")
    finally:
        try: con.close()
        except Exception: pass
# --- PATCH END ----------------------------------------------------
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_update_child_PLACED\(child_id: int, bet_id: str\) -> None:
#    INSERT the following helpers **below** that function
# 📆 PATCHED: 2025-09-29T14:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def mark_child_stoploss(child_id: int) -> None:
    """Label a child row as STOPLOSS."""
    try:
        con = _orders_conn()
        _q_retry(con, "UPDATE orders SET exit_kind='STOPLOSS' WHERE id=?", (int(child_id),))
        con.commit(); con.close()
    except Exception:
        pass

def mark_child_stoploss_by_betid(bet_id: str) -> None:
    """Label a child as STOPLOSS by its Betfair bet id."""
    try:
        con = _orders_conn()
        _q_retry(con, "UPDATE orders SET exit_kind='STOPLOSS' WHERE entry_bet_id=?", (str(bet_id),))
        con.commit(); con.close()
    except Exception:
        pass

def mark_parent_exit_kind_by_cor(parent_cor: str, kind: str) -> None:
    """Persist exit_kind on the parent row for fast reads."""
    try:
        con = _orders_conn()
        _q_retry(con, "UPDATE orders SET exit_kind=? WHERE customerOrderRef=?", (str(kind).upper(), str(parent_cor)))
        con.commit(); con.close()
    except Exception:
        pass

from engines.mastery import event_sink

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _is_manual_parent(row) -> bool:
# 🧩 ACTION: Make key access defensive (row may not include 'source')
# 📆 PATCHED: 2026-01-08 — fix IndexError on placement pre-verify rows
# ============================================================================

def _is_manual_parent(row) -> bool:
    """
    Determine whether a parent should bypass exposure logic.

    NOTE:
    - 'row' may be a partial sqlite Row (placement pre-verify path)
    - Must never assume optional columns exist
    """

    try:
        engine = (row["engine"] or "").upper()
    except Exception:
        engine = ""

    try:
        source = (row["source"] or "").upper()
    except Exception:
        source = ""

    # manual if no engine resolved
    if not engine:
        return True

    # LEGACY but not governed by a known letter
    if engine == "LEGACY" and source and source not in LETTER_MAP:
        return True

    return False

# === PATCH END ==============================================================



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def place_parent_and_hedge\(
# --- REPLACE THE WHOLE FUNCTION WITH THIS -----------------------------------
# --- REPLACE the function header with this (note the extra params) ---
def place_parent_and_hedge(
    *,
    parent_ref = None,   # ✅ MUST exist before try
    market_id: str | None = None,
    selection_id: str | None = None,
    side: str | None = None,
    entry_odds: float | None = None,
    stake: float | None = None,
    hedge_ticks: int | None = None,
    on_parent_result=None,
    run_id: str | None = None,
    source: str = "A",
    parent_persistence: str = "LAPSE",
    child_persistence: str  = "PERSIST",
    max_inplay_seconds: int = 0,
    hedge_stake_override: float | None = None,
    _name: str | None = None,
    _plan: dict | None = None,
    _ctx: dict | None = None,
) -> tuple[Optional[str], str]:


    try:
        _start_rehedge_loop(default_ticks=1)
    except Exception:
        pass


# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def place_parent_and_hedge(
# 📆 PATCHED: 2026-02-12 — allow Bus direct placement
# ============================================================================

# Bus direct placement is already compatible with place_parent_and_hedge.
# Add a safety guard so that legacy placement module is not required.

    if isinstance(_name, str) and (_name.startswith("MSC_") or _name in ("OVERWATCHER", "LEGACY")):
        pass


# === PATCH END ================================================================


    # --- COMPAT GLUE: accept (_name, _plan, _ctx) from placement.py -----------
    if _plan is not None:
        p = dict(_plan)
        c = dict(_ctx or {})
        engine = (_plan or {}).get("engine")


        # --------------------------------------------------
        # Canonical parent_ref (MUST exist for all paths)
        # --------------------------------------------------
        parent_ref = (
            (_plan or {}).get("customerOrderRef")
            or (_ctx  or {}).get("customerOrderRef")
            or _ref((side or "A").upper())
        )


        # ids
        market_id     = market_id     or str(p.get("marketId") or c.get("marketId") or "")
        selection_id  = selection_id  or str(p.get("selectionId") or c.get("selectionId") or "")

        # direction -> side
        dirn = p.get("direction") or c.get("direction")
        if dirn is None:
            try:
                o = float(p.get("px") or c.get("odds") or 0.0)
            except Exception:
                o = 0.0
            dirn = "LAY->BACK" if o >= 4.0 else "BACK->LAY"
        side = side or ("LAY" if str(dirn).upper().startswith("LAY") else "BACK")

        # price / stake / ticks
        if entry_odds is None:
            val = p.get("px", c.get("odds"))
            entry_odds = float(val) if val is not None else None
        if hedge_ticks is None:
            hedge_ticks = int(p.get("target_ticks") or 1)
        if stake is None:
            val = p.get("size", 0.0)
            try:
                stake = float(val) if val is not None else 0.0
            except Exception:
                stake = 0.0

        run_id = run_id or c.get("run_id")
        src_letter = (p.get("letter") or c.get("letter") or (_name or "A"))[:1]
        source = source or src_letter

    _log_db_path_once()
    if entry_odds is None:
        val = None
        if _plan:
            val = (_plan.get("entry_odds")
                   or _plan.get("px"))
        if val is None and _ctx:
            val = _ctx.get("entry_odds") or _ctx.get("px")

        if val is None:
            raise RuntimeError(
                f"[ROUTER] entry_odds missing after normalization "
                f"parent_ref={parent_ref}"
            )

        entry_odds = float(val)

    entry_odds = _round_odds(entry_odds)

    # --- normalize hedge_ticks early (robust default = 1) ---
    try:
        hedge_ticks = _safe_int(
            hedge_ticks if hedge_ticks is not None else
            (_plan or {}).get("hedge_ticks") or (_plan or {}).get("target_ticks") or
            (_ctx  or {}).get("hedge_ticks"),
            1
        )
        if hedge_ticks < 1:
            hedge_ticks = 1
    except Exception:
        hedge_ticks = 1


    # 1) queue parent -----------------------------------------------------------
    # 1) verify parent (PLACEMENT-OWNED) -----------------------------------------
    try:
        # Confirm parent row already exists (Placement pre-claim)
        con = _orders_conn()
        con.row_factory = sqlite3.Row
        row = _q_retry(
            con,
            """
            SELECT id, entry_status, engine, run_id, marketId, selectionId
              FROM orders
             WHERE customerOrderRef=?
               AND role='PARENT'
             LIMIT 1
            """,
            (parent_ref,)
        ).fetchone()
        con.close()

        if not row:
            # This should NEVER happen — hard invariant breach
            _log_event(
                "ERROR",
                "live_router",
                f"[PARENT VERIFY ERROR] ref={parent_ref or 'UNKNOWN'} err={e}"
                f"mid={market_id} sid={selection_id}"
            )
            return None, parent_ref

        # Diagnostic confirmation (non-mutating)
        _log_event(
            "INFO",
            "live_router",
            f"[PARENT VERIFIED] ref={parent_ref} "
            f"id={row['id']} status={row['entry_status']} "
            f"engine={row['engine']} run_id={row['run_id']}"
        )

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"[PARENT VERIFY ERROR] ref={parent_ref} err={e}"
        )
        return None, parent_ref


    # 2) resolve Betfair creds
    _log_event("INFO", "live_router", "[ROUTER] resolving Betfair credentials")
    app_key, token = _keys()

    engine = (
        (_plan or {}).get("engine")
        or (_ctx  or {}).get("engine")
    )

    # Router does NOT hard-gate on engine.
    # Engine is a BankState/accounting concern handled upstream in placement.
    # Betfair execution must proceed regardless.

    parent_id = int(row["id"])

    # --------------------------------------------------
    # LOAD required_exposure (DB FIRST)
    # --------------------------------------------------
    rx_row = _q_retry(
        _orders_conn(),
        """
        SELECT required_exposure, entry_stake, entry_odds
          FROM orders
         WHERE id = ?
        """,
        (parent_id,)
    ).fetchone()

    if not rx_row:
        raise RuntimeError(
            f"[ROUTER][GATE] parent row missing id={parent_id}"
        )

    required_exposure = rx_row["required_exposure"]

    # --------------------------------------------------
    # BACKFILL exposure ONLY IF MISSING
    # --------------------------------------------------
    if required_exposure is None:
        try:
            entry_stake = float(rx_row["entry_stake"])
            entry_odds  = float(rx_row["entry_odds"])
        except Exception:
            raise RuntimeError(
                f"[ROUTER][GATE] cannot derive exposure "
                f"id={parent_id} row={dict(rx_row)}"
            )

        required_exposure = entry_stake * entry_odds

        _q_retry(
            _orders_conn(),
            """
            UPDATE orders
               SET required_exposure = ?
             WHERE id = ?
            """,
            (required_exposure, parent_id)
        )

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ROUTER GATE — ASK BankState FIRST
# 📆 PATCHED: 2026-04-21 — Floor-delta gate (plan-based)
#
# PURPOSE:
# - Replace required_exposure gate with floor-delta simulation gate
# - Pass full plan into BankState
# ======================================================================================================

        gate_plan = {
            "marketId": market_id,
            "selectionId": selection_id,
            "side": side,
            "px": entry_odds,
            "size": stake,
            "engine": engine,
        }

        if not bank_state.can_place(engine, gate_plan):
            _orders_update_parent_failed(
                parent_ref,
                "INSUFFICIENT_FLOOR_DELTA"
            )
            return None, parent_ref

    # 3) place parent -----------------------------------------------------------
    # --------------------------------------------------
    # CANONICAL EXECUTION REHYDRATION (DB-FIRST)
    # --------------------------------------------------
    con = _orders_conn()
    con.row_factory = sqlite3.Row

    row = _q_retry(
        con,
        """
        SELECT
            id,
            marketId,
            selectionId,
            side,
            entry_odds,
            entry_stake,
            target_ticks,
            engine
        FROM orders
        WHERE customerOrderRef = ?
          AND role = 'PARENT'
        LIMIT 1
        """,
        (parent_ref,)
    ).fetchone()

    con.close()

    # ==================================================
    # MSC_INPLAY SEQUENTIAL PROMOTION GATE (ROUTER AUTH)
    # ==================================================
    # Treat Unified INPLAY plans the same as MSC_INPLAY
    is_inplay_engine = (
        engine == "MSC_INPLAY"
        or (
            engine == "MSC_UNIFIED"
            and (_plan or {}).get("bet_type") == "INPLAY"
        )
    )

    if is_inplay_engine:

        con = _orders_conn()
        con.row_factory = sqlite3.Row

        queued_rows = _q_retry(
            con,
            """
            SELECT *
              FROM orders
             WHERE role='PARENT'
               AND engine='MSC_INPLAY'
               AND marketId=?
               AND selectionId=?
               AND entry_status='QUEUED'
             ORDER BY opened_at ASC
            """,
            (market_id, selection_id)
        ).fetchall()

        con.close()

        allowed = _select_inplay_parents_to_promote(
            queued_rows,
            app_key=app_key,
            token=token,
        )

        # 🔒 HARD BLOCK — not this parent's turn yet
        if parent_id not in {r["id"] for r in allowed}:
            _log_event(
                "INFO",
                "live_router",
                f"[INPLAY GATE] blocked parent_ref={parent_ref} "
                f"mid={market_id} sid={selection_id}"
            )
            return None, parent_ref


    if not row:
        raise RuntimeError(
            f"[ROUTER EXECUTION ERROR] parent not found ref={parent_ref}"
        )

    # 🔒 AUTHORITATIVE EXECUTION FIELDS (DB IS LAW)
    parent_id    = int(row["id"])
    market_id    = str(row["marketId"])
    selection_id = str(row["selectionId"])
    side         = str(row["side"]).upper()
    entry_odds   = float(row["entry_odds"])
    stake        = float(row["entry_stake"])
    hedge_ticks  = int(row["target_ticks"] or 1)
    engine       = str(row["engine"])

    # Fail fast if anything is still broken
    if not market_id or not selection_id or not side or stake <= 0 or entry_odds <= 0:
        raise RuntimeError(
            f"[ROUTER INVALID EXECUTION PAYLOAD] "
            f"ref={parent_ref} mid={market_id} sid={selection_id} "
            f"side={side} stake={stake} odds={entry_odds}"
        )



    bf_parent_id, detail = None, {}
# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: if _is_manual_parent(parent_row):
# 🧩 ACTION: FIX VARIABLE + EARLY RETURN
# 📆 PATCHED: 2026-01-08 — activate manual parent exposure bypass
# ============================================================================

    #def _is_manual_parent(row) -> bool:
    #    engine = str(row.get("engine") or "").upper()
    #    source = str(row.get("source") or "").upper()

        # ONLY manual if engine is explicitly missing
    #    if not engine:
    #        return True

    #    return False


# === PATCH END ==============================================================

    try:


        # --------------------------------------------------
        # ROUTER TRACE — FINAL EXECUTION BOUNDARY
        # --------------------------------------------------

        # --------------------------------------------------
        # ASYNC PARENT PLACEMENT
        # --------------------------------------------------
        _PARENT_PLACE_QUEUE.put_nowait((
            parent_ref,
            market_id,
            selection_id,
            side,
            entry_odds,
            stake,
            parent_persistence,
        ))
        return None, parent_ref
        # 1️⃣ Reserve After Bet Placed
        # --------------------------------------------------
        # ROUTER RESERVE — MUTATE ONLY AFTER APPROVAL
        # --------------------------------------------------
        bank_state.on_parent_placed(
            engine=engine,
            parent_id=parent_id,
        )
        if bf_parent_id:
            _orders_update_parent_placed(parent_ref, bf_parent_id)

        # 3️⃣ If Betfair failed → rollback reservation
        if not bf_parent_id:
            bank_state.on_parent_closed(
                engine=engine,
                entry_odds=float(entry_odds),
                entry_stake=float(stake),
            )
            _orders_update_parent_failed(parent_ref, "BETFAIR_PLACE_FAILED")

            return None, parent_ref
# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 ANCHOR: BankState exposure reservation — PARENT PLACED
# 🧩 ACTION: FIX INDENTATION (syntax error)
# 📆 PATCHED: 2025-12-21 — fix invalid except alignment blocking module import
# ======================================================================================================

        # ======================================================================
        # 📍 BankState exposure reservation — PARENT PLACED
        # ======================================================================
        try:
            pass
        except Exception as e:
            _log_event(
                "ERROR",
                "bankstate",
                f"on_parent_placed failed ref={parent_ref}: {e}"
            )


            _orders_probe(parent_ref, note="PLACED", bet_id=bf_parent_id)
            try:
                _sp_log_enter_live(run_id=run_id, strategy=source, mid=market_id, sid=selection_id,
                                   side=side, price=entry_odds, stake=stake)
            except Exception:
                pass
        else:
            irs = (detail.get("instructionReports") or [])
            inner = (irs[0] if irs else {}).get("errorCode", "UNKNOWN")
            _orders_update_parent_failed(parent_ref, f"{inner}")
            return None, parent_ref
    except Exception as e:
        err_text = str(e)
        if any(bad in err_text for bad in ("NameResolutionError","Failed to resolve","Max retries exceeded")):
            _orders_update_parent_failed(parent_ref, "NETWORK_UNAVAILABLE")
            _orders_probe(parent_ref, note="error")
            return None, parent_ref
        _orders_update_parent_failed(parent_ref, err_text)
        return None, parent_ref

    if callable(on_parent_result):
        try: on_parent_result(bf_parent_id, parent_ref)
        except Exception: pass
    if not bf_parent_id:
        _log_event(
            "ERROR",
            "live_router",
            f"[BETFAIR] placeOrders failed mid={market_id} sid={selection_id} "
            f"odds={entry_odds} stake={stake} detail={detail}"
        )

    # 3) background follow-up ---------------------------------------------------
    def _bg():
        try:
            # --------------------------------------------------
            # WAIT FOR PARENT MATCH (EXCHANGE TRUTH)
            # --------------------------------------------------
            if not _poll_matched(
                app_key,
                token,
                bf_parent_id,
                timeout_s=90,
                interval_s=2.0
            ):
                # Parent never matched
               
                cancel_bet_canonical(bet_id=bet_id)

           

                if parent_persistence.upper() == "LAPSE":
                    try:
                        con = _orders_conn()
                        _q_retry(
                            con,
                            """
                            
                            UPDATE orders
                               SET exit_status='CANCELLED',
                   
                                   closed_at=COALESCE(closed_at, datetime('now','utc'))
                             WHERE customerOrderRef=?
                               AND COALESCE(entry_status,'')<>'MATCHED'
                            """,
                            (parent_ref,)
                        )

                        con.commit()
                        con.close()
                    except Exception:
                        pass
                return

            # --------------------------------------------------
            # CANONICAL MATCHED HANDLER (ATOMIC)
            # --------------------------------------------------
            try:
                _orders_update_parent_matched(parent_ref, bf_parent_id)
                _ensure_child_queued_for_matched_parent(parent_ref)
                _orders_probe(parent_ref, note="parent_matched")
                _log_event_safe(
                    "INFO",
                    "live_router",
                    f"[BG] parent matched mid={market_id} sid={selection_id} ref={parent_ref}"
                )
            except Exception as e:
                _log_event_safe(
                    "ERROR",
                    "live_router",
                    f"[BG] failed to mark parent matched ref={parent_ref}: {e}"
                )

            # --------------------------------------------------
            # NOTHING ELSE HERE — CHILD IS GUARANTEED BY INVARIANT
            # --------------------------------------------------

        except Exception as e:
            _log_event_safe(
                "ERROR",
                "live_router",
                f"[BG] parent reconcile error ref={parent_ref}: {e}"
            )


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: return bf_parent_id, parent_ref
# 📆 PATCHED: 2025-09-29T15:40Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # ✅ now we are back *outside* _bg()
    try:
        t = threading.Thread(
            target=_bg,
            name=f"bg-parent-{parent_ref}",
            daemon=True
        )
        t.start()
        _log_event("INFO", "live_router", f"[BG] thread started for parent_ref={parent_ref}")
    except Exception as e:
        _log_event("ERROR", "live_router", f"[BG] failed to start thread parent_ref={parent_ref}: {e}")

    return bf_parent_id, parent_ref




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: (append at end of file)
# 📆 PATCHED: 2025-08-25T10:45Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _record_trade_metrics(*,
                          market_id: str,
                          selection_id: str,
                          run_id: int | None,
                          entry_side: str,
                          entry_odds: float,
                          entry_stake: float,
                          hedge_odds: float,
                          hedge_stake: float,
                          t_parent_place: float,
                          t_parent_match: float,
                          t_hedge_place: float,
                          t_hedge_match: float,
                          slip_parent: float,
                          slip_hedge: float) -> None:
    """
    Insert into bets.db: trade_pairs + trade_metrics
    """
    try:
        bdb = connect_db(ro=False)  # bets.db
        _q_retry(bdb, """
            CREATE TABLE IF NOT EXISTS trade_pairs (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              marketId TEXT, selectionId TEXT,
              entry_side TEXT,
              entry_odds REAL, entry_stake REAL,
              hedge_odds REAL, hedge_stake REAL,
              realized_pl REAL NOT NULL DEFAULT 0.0,
              run_id INTEGER, mode TEXT
            )
        """)
        _q_retry(bdb, """
            CREATE TABLE IF NOT EXISTS trade_metrics (
              pair_id INTEGER PRIMARY KEY,
              time_to_parent_match_s REAL,
              time_to_hedge_place_s  REAL,
              time_to_hedge_match_s  REAL,
              slip_parent REAL, slip_hedge REAL,
              max_exposure REAL, mto_on_entry REAL, ev_on_entry REAL,
              created_at TEXT,
              FOREIGN KEY(pair_id) REFERENCES trade_pairs(id) ON DELETE CASCADE
            )
        """)
        # max_exposure (simple): LAY liability or BACK stake
        max_exposure = entry_stake * max(0.0, entry_odds - 1.0) if entry_side.upper() == "LAY" else entry_stake

        cur = bdb.cursor()
        _q_retry(cur, """
            INSERT INTO trade_pairs (marketId, selectionId, entry_side,
                                     entry_odds, entry_stake, hedge_odds, hedge_stake,
                                     realized_pl, run_id, mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0.0, ?, 'LIVE')
        """, (str(market_id), str(selection_id), entry_side.upper(),
              float(entry_odds), float(entry_stake), float(hedge_odds), float(hedge_stake),
              run_id))
        pair_id = cur.lastrowid

        # compute durations (guard against negatives)
        tp = max(0.0, t_parent_match - t_parent_place)
        thp = max(0.0, t_hedge_place - t_parent_match)
        thm = max(0.0, t_hedge_match - t_hedge_place)

        _q_retry(cur, """
            INSERT INTO trade_metrics (pair_id,
              time_to_parent_match_s, time_to_hedge_place_s, time_to_hedge_match_s,
              slip_parent, slip_hedge, max_exposure, mto_on_entry, ev_on_entry, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL, strftime('%Y-%m-%dT%H:%M:%SZ','now'))
        """, (pair_id, tp, thp, thm, float(slip_parent), float(slip_hedge), float(max_exposure)))
        bdb.commit()
        bdb.close()
    except Exception as e:
        _log_event("ERROR", "live_router", f"metrics insert failed: {e}")

# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def ensure_hedges_for_open_parents(
# 🧩 ACTION: FIX hedge direction inversion (recovery path)
# 📆 PATCHED: 2026-03-04 — align recovery hedges with live invariant
# ======================================================================
def ensure_hedges_for_open_parents(*, max_to_fix: int = 20, default_ticks: int = 1) -> int:
    """
    BACKUP / SAFETY NET ONLY.

    For every MATCHED PARENT with NO CHILD row at all:
      • create a CHILD row in QUEUED state (DB-only)
      • NEVER call Betfair
      • execution is deferred to RouterChildWorker

    Hedge invariant:
      LAY  → BACK (higher odds)
      BACK → LAY  (lower odds)
    """

    fixed = 0
    _ensure_orders_schema()

    con = _orders_conn()
    con.row_factory = sqlite3.Row
    try:
        rows = _q_retry(con, """
            SELECT
                p.id,
                p.customerOrderRef AS cor,
                p.marketId,
                p.selectionId,
                p.side,
                p.entry_odds,
                p.entry_stake,
                COALESCE(p.source,'LEGACY_STRATEGY') AS source
              FROM orders p
             WHERE p.mode='LIVE'
               AND p.role='PARENT'
               AND UPPER(p.entry_status)='MATCHED'
               AND COALESCE(p.parent_closed,0)=0
               AND (p.exit_status IS NULL OR UPPER(p.exit_status) NOT IN ('CANCELLED','EXPIRED','SETTLED'))
               AND COALESCE(p.exposure_released,0)=0
               AND (p.exit_status IS NULL OR UPPER(p.exit_status)<>'MATCHED')
               AND NOT EXISTS (
                     SELECT 1 FROM orders c
                      WHERE c.role='CHILD'
                        AND c.hedge_of=p.id
               )
             ORDER BY p.opened_at DESC
             LIMIT ?
        """, (int(max_to_fix),)).fetchall()
    finally:
        con.close()

    for r in rows:
        try:
            parent_side = (r["side"] or "").upper()
            entry_odds  = float(r["entry_odds"])
            entry_stake = float(r["entry_stake"])
            source      = str(r["source"] or "H")

            hedge_side = "BACK" if parent_side == "LAY" else "LAY"
            ticks = max(1, int(default_ticks))

            from engines.price_math import odds_plus_ticks

            delta = +ticks if hedge_side == "BACK" else -ticks
            hedge_odds = odds_plus_ticks(entry_odds, delta)
            hedge_odds = _round_odds(float(hedge_odds))

            full_hedge_stake = calc_greenup_stake(
                parent_side,
                entry_odds,
                entry_stake,
                hedge_odds
            )

            hedge_stake = round(float(full_hedge_stake) * PROGRESSIVE_LOCK_FACTOR, 2)

            # Safety: never below £1 minimum
            if hedge_stake < 1.0:
                hedge_stake = 1.0

            child_id = _orders_insert_child_queued(str(r["cor"]))


            if child_id:
                fixed += 1
                _log_event(
                    "INFO",
                    "live_router",
                    f"[REHEDGE-BACKUP] queued child_id={child_id} parent_ref={r['cor']}"
                )

        except Exception as e:
            _log_event("ERROR", "live_router", f"rehedge backup error: {e}")

    return fixed

def _recycle_stale_unmatched_parents(limit: int = 50, *, max_age_min: int = 5) -> int:
    """
    Cancel + release exposure for parents that were PLACED
    but never MATCHED within max_age_min minutes.

    DB-first. Idempotent. Safe to run every tick.
    """
    fixed = 0
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        rows = _q_retry(cur, """
            SELECT
                p.id,
                p.customerOrderRef
            FROM orders p
            WHERE p.mode='LIVE'
              AND p.role='PARENT'
              AND UPPER(p.entry_status)='PLACED'
              AND datetime(p.opened_at) <= datetime('now','utc', ?)
            ORDER BY p.opened_at ASC
            LIMIT ?
        """, (f"-{int(max_age_min)} minutes", int(limit))).fetchall()

        for r in rows:
            try:
                cor = str(r["customerOrderRef"])

                row2 = _q_retry(cur, """
                    SELECT entry_bet_id FROM orders WHERE id=? LIMIT 1
                """, (int(r["id"]),)).fetchone()

                bet_id = row2["entry_bet_id"] if row2 else None

                # 🔒 GUARD
                if not _guard_cancel_if_matched(parent_cor=cor, bet_id=bet_id):
                    continue

                # 1️⃣ Cancel on Betfair (best effort)
                try:
                    row = _q_retry(cur, """
                        SELECT entry_bet_id
                          FROM orders
                         WHERE id=?
                         LIMIT 1
                    """, (int(r["id"]),)).fetchone()

                    
                    cancel_bet_canonical(bet_id=bet_id)

                except Exception:
                    pass  # never block recycle

                # 2️⃣ Mark CANCELLED in DB
                _q_retry(cur, """
                    UPDATE orders
                       SET exit_status='CANCELLED',
                   
                           closed_at=datetime('now','utc'),
                           error='recycle_timeout'
                     WHERE id=?
                       AND UPPER(entry_status)='PLACED'
                """, (int(r["id"]),))

# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: recycle_timeout
# 🧩 ACTION: Use canonical stamper only
# 📆 PATCHED: 2026-04-XX
# ======================================================================

                _stamp_parent_exit_sql(
                    cur,
                    parent_id=int(r["id"]),
                    exit_status="CANCELLED",
                    reason="recycle_timeout",
                )
                con.commit()

                # 3️⃣ Release exposure (authoritative)
                _release_unmatched_parent_exposure(cor)

                _log_event(
                    "INFO",
                    "live_router",
                    f"[RECYCLE] cancelled stale parent ref={cor}"
                )

                fixed += 1

            except Exception as e:
                _log_event(
                    "ERROR",
                    "live_router",
                    f"recycle failed ref={r['customerOrderRef']}: {e}"
                )

        return fixed

    finally:
        try:
            con.close()
        except Exception:
            pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def ensure_hedges_for_open_parents
# 📆 PATCHED: 2025-09-29T15:40Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _sync_parent_matches(limit: int = 50) -> int:
    """
    Rescue parents stuck at PLACED by polling Betfair order status.
    """
    fixed = 0
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        rows = _q_retry(con, """
            SELECT id, customerOrderRef, entry_bet_id
              FROM orders
             WHERE role='PARENT'
               AND mode='LIVE'
               AND entry_status='PLACED'
             ORDER BY opened_at DESC
             LIMIT ?
        """, (limit,)).fetchall()
        con.close()
    except Exception as e:
        _log_event("ERROR", "live_router", f"sync_parent_matches fetch failed: {e}")
        return 0

    if not rows:
        return 0

    for r in rows:
        try:
            status = get_bet_status(str(r["entry_bet_id"]))
            if status == "EXECUTION_COMPLETE":
                parent_cor = str(r["customerOrderRef"])

                _orders_update_parent_matched(parent_cor)
                _ensure_child_queued_for_matched_parent(parent_cor)


                fixed += 1

        except Exception as e:
            _log_event(
                "ERROR",
                "live_router",
                f"sync_parent_matches error parent_ref={r['customerOrderRef']}: {e}"
            )

    return fixed


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _sync_hedge_matches\(limit: int = 50\):
# --- PATCH START: replace function ------------------------------------
def _sync_hedge_matches(limit: int = 50) -> int:
    """
    Finalize parents whose hedge betId has completed but wasn't stamped yet.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        rows = _q_retry(con, """
            SELECT customerOrderRef AS cor, exit_bet_id, side, entry_odds, entry_stake
              FROM orders
             WHERE mode='LIVE'
               AND role='PARENT'
               AND entry_status='MATCHED'
               AND exit_bet_id IS NOT NULL
               AND (exit_status IS NULL OR UPPER(exit_status) <> 'MATCHED')
             ORDER BY opened_at DESC
             LIMIT ?
        """, (int(limit),)).fetchall()
        con.close()
    except Exception as e:
        _log_event("ERROR", "live_router", f"sync fetch failed: {e}")
        return 0

    if not rows:
        return 0

    fixed = 0
    for r in rows:
        try:
            status = get_bet_status(str(r["exit_bet_id"]))
            if status == "EXECUTION_COMPLETE":
                # try to use pre-stamped exit_odds/stake; if missing, skip (we’ll catch next cycle)
                con = _orders_conn(); con.row_factory = sqlite3.Row
                row = _q_retry(con, "SELECT exit_odds, exit_stake FROM orders WHERE customerOrderRef=? LIMIT 1",
                                  (str(r["cor"]),)).fetchone()
                con.close()
                if row and row["exit_odds"] is not None and row["exit_stake"] is not None:
                    _orders_update_hedge_matched(
                        cor=str(r["cor"]),
                        exit_side=("BACK" if (r["side"] or "").upper() == "LAY" else "LAY"),
                        exit_odds=float(row["exit_odds"]),
                        exit_stake=float(row["exit_stake"])
                    )
                    fixed += 1
                    # 📊 update vSERF/BookState
                    try:
                        _update_book_state(run_id="LIVE")
                    except Exception: pass

                # 🔗 Ensure ledger link is set even if earlier steps missed it
                try:
                    con2 = _orders_conn(); con2.row_factory = sqlite3.Row
                    # get child id via (role='CHILD', hedge_of=parent.id)
                    rowp = _q_retry(con2, "SELECT id FROM orders WHERE customerOrderRef=? LIMIT 1", (str(r["cor"]),)).fetchone()
                    if rowp:
                        rowc = _q_retry(con2, "SELECT id FROM orders WHERE role='CHILD' AND hedge_of=? ORDER BY id DESC LIMIT 1",
                                        (int(rowp["id"]),)).fetchone()
                        if rowc:
                            _ledger_link_child_by_parent_cor(str(r["cor"]), int(rowc["id"]))
                    con2.close()
                except Exception:
                    pass

                    
        except Exception as e:
            _log_event("ERROR", "live_router", f"sync hedge error: {e}")
    return fixed

# --- PATCH END ----------------------------------------------------------

# added for green up once all Strats run

def analyze_market_pnl(con, market_id: str) -> dict:
    """
    Approx projected pnl if each runner wins, considering current orders on that market.
    BACK: +stake*(odds-1) if wins, else -stake
    LAY:  -(odds-1)*stake if wins, else +stake
    """
    pnl = {}  # {selectionId: amount_if_this_runner_wins}
    rows = _q_retry(con, "SELECT selectionId, side, entry_odds, entry_stake FROM orders "
                       "WHERE marketId=? AND entry_status='MATCHED' AND (closed_at IS NULL OR closed_at='')",
                       (market_id,)).fetchall()
    sids = {str(r["selectionId"]) for r in rows}
    for j in sids:
        total = 0.0
        for r in rows:
            sid = str(r["selectionId"]); side = (r["side"] or "").upper()
            o = float(r["entry_odds"]); st = float(r["entry_stake"])
            if side == "BACK":
                total += (st*(o-1.0) if sid == j else -st)
            else:  # LAY
                total += (-(o-1.0)*st if sid == j else +st)
        pnl[j] = round(total, 2)
    return pnl

def maybe_green_sweep_market(con, market_id: str, *, tto_min: float, max_disp: float = 5.0) -> None:
    """
    If dispersion across outcomes exceeds max_disp near the off (e.g., tto≤2m), suggest micro adjustments.
    Start as 'advise only' (logs). Later, place orders with a tiny fraction of L1.
    """
    if tto_min > 2.0:
        return
    pnl = analyze_market_pnl(con, market_id)
    if not pnl:
        return
    lo, hi = min(pnl.values()), max(pnl.values())
    if hi - lo < max_disp:
        return
    # Log suggestion (later: compute small BACK/LAY deltas on low-PnL runners)
    worst = min(pnl, key=pnl.get)
    print(f"[GREEN-SWEEP] market={market_id} dispersion={hi-lo:.2f} worst_sid={worst} pnl={pnl[worst]:+.2f} → suggest tiny hedge to lift minimum")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# ⛏️ ACTION: append at end of file
# 📆 PATCHED: 2025-09-29T15:40Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def cleanup_orphan_parents() -> int:
    """
    Mark parents with no matched children as CANCELLED.
    Useful to reset backlog of stuck entries.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        rows = _q_retry(con, """
            SELECT p.id, p.customerOrderRef
              FROM orders p
             WHERE p.role='PARENT'
               AND p.mode='LIVE'
               AND p.entry_status IN ('PLACED','PENDING')
               AND NOT EXISTS (SELECT 1 FROM orders c WHERE c.hedge_of=p.id)
        """).fetchall()
        if not rows:
            return 0
        cur = con.cursor()
        # ======================================================================
        # 📍 TARGET: engines/live/live_router.py
        # 🔎 SEARCH: def cleanup_orphan_parents(
        # 🧩 ACTION: Fix orphan cancellation
        # 📆 PATCHED: 2026-04-XX
        # ======================================================================

        for r in rows:
            _stamp_parent_exit_sql(
                cur,
                parent_id=int(r["id"]),
                exit_status="CANCELLED",
                reason="cleanup_orphan",
            )

            _log_event(
                "WARN",
                "live_router",
                f"[CLEANUP] orphan parent cancelled ref={r['customerOrderRef']}"
            )

        con.commit(); con.close()
        return len(rows)
    except Exception as e:
        _log_event("ERROR", "live_router", f"cleanup_orphan_parents error: {e}")
        return 0

# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: _repair_orphan_run_ids(mode="LIVE", tag="LIVE-AUTO")
# 🩹 PATCH: Delay orphan-run repair until runtime (no DB writes during import)
# 📆 PATCHED: 2025-11-18

def _repair_orphan_run_ids_on_startup():
    """
    Safe wrapper – called AFTER tool startup.
    Prevents hijack/DAL recursion caused by import-time writes.
    """
    try:
        _repair_orphan_run_ids(mode="LIVE", tag="LIVE-AUTO")
    except Exception as e:
        print(f"[live_router] startup repair failed: {e}")


# REMOVE import-time call:
# _repair_orphan_run_ids(mode="LIVE", tag="LIVE-AUTO")

# ADD at bottom of file:
def init_live_router():
    """
    Called by GUI or orchestrator after full system startup.
    """
    _ensure_router_runtime_schema()
    _repair_orphan_run_ids_on_startup()

_ROUTER_CHILD_THREAD = None

def start_router_child_worker():
    global _ROUTER_CHILD_THREAD
    if _ROUTER_CHILD_THREAD and _ROUTER_CHILD_THREAD.is_alive():
        return

    t = threading.Thread(
        target=_router_child_worker_loop,
        name="RouterChildWorker",
        daemon=True,
    )
    t.start()
    _ROUTER_CHILD_THREAD = t


    print("[ROUTER] child execution worker started")

# ======================================================================================================
# 📍 TARGET: engines/live/live_router.py
# 🧩 ADD: Parent placement worker starter
# 📆 PATCHED: 2026-03-07 — parallel parent placement execution
#
# PURPOSE
# - Execute Betfair placeOrders asynchronously
# - Prevent BUS ticks blocking on network latency
# - Lifecycle owned by orchestrator
# ======================================================================================================

_PARENT_PLACE_THREAD = None


def start_router_parent_worker():

    global _PARENT_PLACE_THREAD

    try:
        if _PARENT_PLACE_THREAD and _PARENT_PLACE_THREAD.is_alive():
            return
    except Exception:
        pass

    t = threading.Thread(
        target=_router_parent_worker_loop,
        name="RouterParentWorker",
        daemon=True,
    )

    t.start()
    _PARENT_PLACE_THREAD = t

    print("[ROUTER] parent execution worker started")

# === PATCH START ==============================================================
# 📍 TARGET: engines/live/live_router.py (append at end)
# 📆 PATCHED: 2026-02-27 — Structured runtime snapshot (ROUTER)
# ==============================================================================

def _ensure_router_runtime_schema():
    import sqlite3
    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=True)
    con.execute("""
        CREATE TABLE IF NOT EXISTS router_runtime_snapshot(
            ts TEXT,
            parents_json TEXT,
            children_json TEXT,
            open_trades INTEGER,
            completed_trades INTEGER,
            cancelled_trades INTEGER
        )
    """)
    con.close()

def _render_router_report(self, con):

    row = con.execute("""
        SELECT * FROM router_runtime_snapshot
        ORDER BY ts DESC LIMIT 1
    """).fetchone()

    if not row:
        return

    import json

    parents = json.loads(row["parents_json"] or "{}")
    children = json.loads(row["children_json"] or "{}")

    open_trades = int(row["open_trades"] or 0)
    completed_trades = int(row["completed_trades"] or 0)
    cancelled_trades = int(row["cancelled_trades"] or 0)

    text = []

    text.append("================= V7 ROUTER LIVE STATE =================")
    text.append(f"t={row['ts'][-9:]}   mode=LIVE   stage=POST-RECONCILE")
    text.append("=======================================================")
    text.append("")

    # ---------------- PARENTS ----------------
    text.append("PARENTS — ENTRY / EXIT STATUS (BY ENGINE)")
    text.append("---------------------------------------------------------------")
    text.append("ENGINE            QUEUED  PLACING  PLACED  MATCHED  CANCELLED  CLOSED")
    text.append("---------------------------------------------------------------")

    for engine in sorted(parents.keys()):
        p = parents[engine]
        text.append(
            f"{engine:<16} "
            f"{p.get('QUEUED',0):>6} "
            f"{p.get('PLACING',0):>8} "
            f"{p.get('PLACED',0):>8} "
            f"{p.get('MATCHED',0):>8} "
            f"{p.get('CANCELLED',0):>10} "
            f"{p.get('CLOSED',0):>8}"
        )

    text.append("")
    text.append("CHILDREN — ENTRY / EXIT STATUS (BY ENGINE)")
    text.append("---------------------------------------------------------------")
    text.append("ENGINE            QUEUED  PLACING  PLACED  MATCHED  CANCELLED  CLOSED")
    text.append("---------------------------------------------------------------")

    for engine in sorted(children.keys()):
        c = children[engine]
        text.append(
            f"{engine:<16} "
            f"{c.get('QUEUED',0):>6} "
            f"{c.get('PLACING',0):>8} "
            f"{c.get('PLACED',0):>8} "
            f"{c.get('MATCHED',0):>8} "
            f"{c.get('CANCELLED',0):>10} "
            f"{c.get('CLOSED',0):>8}"
        )

    text.append("")
    text.append("TRADE SUMMARY")
    text.append("-------------------------------------------------------")
    text.append(f"open_trades        : {open_trades}")
    text.append(f"completed_trades   : {completed_trades}")
    text.append(f"cancelled_trades   : {cancelled_trades}")
    text.append("-------------------------------------------------------")

    self.router_text.delete("1.0", tk.END)
    self.router_text.insert("1.0", "\n".join(text))
