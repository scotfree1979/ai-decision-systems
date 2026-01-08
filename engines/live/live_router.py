#!/usr/bin/env python3
# engines/live/live_router.py
from __future__ import annotations

import json, time, threading, random, sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple
from engines import price_math as pm

import uuid
import requests
from engines.config_paths import auto_conn as _cp_auto_conn, q_retry as _cp_q_retry, autoscalp_db, connect_db
from engines.math.dynamic_stake_v7 import calc_dynamic_stake, calc_greenup_stake
from engines.live import bank_state

# --- Router child execution queue ---
# live_router.py (top-level)

import queue
import threading
import traceback

_ROUTER_CHILD_QUEUE: "queue.Queue[tuple[dict, dict]]" = queue.Queue()
_ROUTER_CHILD_WORKER = None

GRACE_MINUTES = 6

def enqueue_router_child(plan: dict, ctx: dict):
    _ROUTER_CHILD_QUEUE.put_nowait((plan, ctx))


def _router_child_worker_loop():
    from engines.live.live_router import (
        _attempt_place_child_with_retry,
        _orders_insert_child_queued,
    )

    import time
    import sqlite3


    RESCUE_DELAY_SECONDS = 120  # 2 minutes

    while True:
        try:
            # ==================================================
            # PHASE 1 — NORMAL CHILD EXECUTION PATH
            # ==================================================
            try:
                plan, ctx = _ROUTER_CHILD_QUEUE.get_nowait()

                parent_cor = plan.get("parent_cor")
                if parent_cor:
                    child_id = _ensure_child_queued_for_matched_parent(parent_cor)
                    if child_id:
                        plan["child_id"] = child_id

                # --------------------------------------------------
                # GUARANTEE CHILD ROW EXISTS (DB-FIRST)
                # --------------------------------------------------
                child_id = plan.get("child_id")

                # 🔒 ENSURE EXECUTION IDENTITY (router responsibility)
                if "marketId" not in plan or "selectionId" not in plan:

                    parent_cor = plan.get("parent_cor")
                    if not parent_cor:
                        raise RuntimeError(
                            "router child worker: missing marketId/selectionId and no parent_cor"
                        )

                    con = _orders_conn()
                    con.row_factory = sqlite3.Row

                    row = con.execute(
                        """
                        SELECT id, marketId, selectionId
                          FROM orders
                         WHERE customerOrderRef = ?
                           AND role = 'PARENT'
                         LIMIT 1
                        """,
                        (str(parent_cor),)
                    ).fetchone()

                    con.close()

                    if not row:
                        raise RuntimeError(
                            f"router child worker: failed to hydrate identity from parent_cor={parent_cor}"
                        )

                    # 🔑 Authoritative identity from DB
                    plan["parent_id"]   = int(row["id"])
                    plan["marketId"]    = row["marketId"]
                    plan["selectionId"] = row["selectionId"]


                if not child_id:
                    parent_cor = plan.get("parent_cor")
                    if not parent_cor:
                        raise RuntimeError(
                            "router child worker: missing child_id and parent_cor"
                        )

                    # Canonical DB-first child creation
                    child_id = _orders_insert_child_queued(parent_cor)

                    if not child_id:
                        raise RuntimeError(
                            f"failed to create child row for parent {parent_cor}"
                        )

# === PATCH START ==============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ok = _attempt_place_child_with_retry(int(child_id))
# 🧩 ACTION: INSERT GUARD IMMEDIATELY BEFORE CHILD PLACEMENT
# 📆 PATCHED: 2026-03-05 — Router-enforced child price separation invariant
#
# RATIONALE:
# - ONLY Overwatcher may place children at the same price as the parent
# - All other engines must place children at least 1 tick away
# - This is enforced at the final authority boundary (router child worker)
# - Fixes target_ticks=1 collapse, replay/live drift, and recovery anomalies
# ==============================================================================

                # --------------------------------------------------
                # ROUTER INVARIANT — CHILD PRICE SEPARATION
                # --------------------------------------------------
                try:
                    con = _orders_conn()
                    con.row_factory = sqlite3.Row
                    cur = con.cursor()

                    child = _q_retry(cur, """
                        SELECT id, entry_odds, hedge_of
                          FROM orders
                         WHERE id=?
                           AND role='CHILD'
                         LIMIT 1
                    """, (int(child_id),)).fetchone()

                    if child:
                        parent = _q_retry(cur, """
                            SELECT side, entry_odds, engine
                              FROM orders
                             WHERE id=?
                               AND role='PARENT'
                             LIMIT 1
                        """, (int(child["hedge_of"]),)).fetchone()

                        if parent:
                            enforced_px = _enforce_child_price_separation(
                                engine=parent["engine"],
                                exit_kind=plan.get("exit_kind"),
                                parent_side=parent["side"],
                                parent_odds=float(parent["entry_odds"]),
                                child_odds=float(child["entry_odds"]),
                            )

                            enforced_px = _round_odds(float(enforced_px))

                            if enforced_px != float(child["entry_odds"]):
                                _q_retry(cur, """
                                    UPDATE orders
                                       SET entry_odds=?
                                     WHERE id=?
                                """, (enforced_px, int(child_id)))
                                con.commit()

                    con.close()
                except Exception:
                    # invariant enforcement must NEVER block execution
                    try:
                        con.close()
                    except Exception:
                        pass

                # --------------------------------------------------
                # EXECUTE CHILD (PLACE + RETRY)
                # --------------------------------------------------
# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: raise RuntimeError(f"router child placement failed id={child_id}")
# 🧩 ACTION: Make child placement failure non-fatal to router worker
# 📆 PATCHED: 2026-01-08 — child placement failures are expected, not fatal
#
# RATIONALE:
# - _attempt_place_child_with_retry() already marks FAILED
# - Router must NOT crash on normal Betfair rejections
# - Recovery is handled by rehedge / rescue / market-finish logic
# ============================================================================

                ok = _attempt_place_child_with_retry(int(child_id))
                if not ok:
                    _log_event(
                        "WARN",
                        "live_router",
                        f"[CHILD PLACE FAILED] id={child_id} — marked FAILED, will retry/rescue later"
                    )
                    # IMPORTANT: do NOT raise
                    continue

# === PATCH END ==============================================================


            except queue.Empty:
                # No normal child work right now
                pass


            from engines.market_monitor.phase_clock import MarketPhaseClock

            # ==================================================
            # PHASE 2 — RESCUE HEDGING (DB + PHASE CLOCK)
            # ==================================================
            con = _orders_conn()
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            rows = _q_retry(cur, """
                SELECT customerOrderRef, marketId
                  FROM orders p
                 WHERE p.role='PARENT'
                   AND UPPER(p.entry_status)='MATCHED'
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
                    mid = str(r["marketId"])

                    # 🔑 AUTHORITATIVE MARKET STATE
                    phase, _ = MarketPhaseClock.get(mid)

                    parent_cor = str(r["customerOrderRef"])

                    # ❌ MARKET TOO LATE — RELEASE EXPOSURE
                    secs_to_off = _secs_to_off(mid)


                    # ✅ MARKET STILL HEDGEABLE — RESCUE
                    _ensure_child_queued_for_matched_parent(parent_cor)


                    # ✅ SAFE TO RESCUE
                    _ensure_child_queued_for_matched_parent(
                        str(r["customerOrderRef"])
                    )

                except Exception as e:
                    _log_event(
                        "ERROR",
                        "live_router",
                        f"rescue hedge failed parent_ref={r['customerOrderRef']}: {e}"
                    )


        except Exception:
            print("[ROUTER][CHILD][ERR]")
            traceback.print_exc()

        finally:
            try:
                _ROUTER_CHILD_QUEUE.task_done()
            except Exception:
                pass

        # Prevent tight loop
        time.sleep(1.0)

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

        bank_state.on_parent_closed(
            engine=parent["engine"],
            entry_odds=float(parent["entry_odds"]),
            entry_stake=float(parent["entry_stake"]),
        )

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

            hedge_stake = calc_greenup_stake(
                parent_side,
                float(r["entry_odds"]),
                float(r["entry_stake"]),
                hedge_odds
            )

            # NOTE:
            # _orders_insert_child_queued is POSITIONAL.
            _orders_insert_child_queued(r["parent_cor"])


            print(f"[ROUTER][RECOVER] child rebuilt for {r['parent_cor']}")

        except Exception as e:
            print(f"[ROUTER][RECOVER][ERR] {r['parent_cor']}: {e}")

    con.close()



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _sync_parent_matches
#    INSERT this helper just below the existing _sync_parent_matches/_sync_hedge_matches
# 📆 PATCHED: 2025-10-03T12:15Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _sync_all_matches(limit: int = 100) -> int:
    """
    Sweep both PARENTS and CHILDREN stuck in PLACED state and flip to MATCHED if
    Betfair shows sizeMatched > 0. Also stamps entry_matched_odds/stake.
    Returns count of rows flipped.
    """
    fixed = 0
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        cur = con.cursor()
        rows = _q_retry(cur, """
            SELECT id, customerOrderRef, entry_bet_id, role
              FROM orders
             WHERE mode='LIVE'
               AND entry_status='PLACED'
               AND entry_bet_id IS NOT NULL
             ORDER BY opened_at DESC
             LIMIT ?
        """, (limit,)).fetchall()
        con.close()
    except Exception as e:
        _log_event("ERROR", "live_router", f"sync_all_matches fetch failed: {e}")
        return 0

    if not rows: return 0

    for r in rows:
        try:
            bid = str(r["entry_bet_id"])
            status = get_bet_status(bid)
            if status == "EXECUTION_COMPLETE":
                app_key, token = _keys()
                avg_odds, matched_size = _fetch_avg_match(app_key, token, bid)
                con2 = _orders_conn(); cur2 = con2.cursor()
                _q_retry(cur2, """
                    UPDATE orders
                       SET entry_status='MATCHED',
                           entry_matched_odds=COALESCE(entry_matched_odds, ?),
                           entry_matched_stake=COALESCE(entry_matched_stake, ?)
                     WHERE id=?
                """, (avg_odds if avg_odds>0 else None,
                      matched_size if matched_size>0 else None,
                      int(r["id"])))
                con2.commit(); con2.close()
                _log_event("INFO","live_router",
                           f"[SYNC-ALL] flipped {r['role']} ref={r['customerOrderRef']} to MATCHED")
                fixed += 1
        except Exception as e:
            _log_event("ERROR", "live_router",
                       f"sync_all_matches error ref={r['customerOrderRef']}: {e}")
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

def calc_dynamic_stake(letter: str, *, phase: str = "PRE") -> tuple[float, str]:
    """
    Compute LIVE stake using config caps. Confidence/L1 are omitted in this first cut
    to keep the 'one change' surgical and safe.
    Returns (stake, reason_text).
    """
    d = daily_config
    bank = _fetch_live_bank(0.0)
    base, fam_max = _letter_base_max(letter)
    mult = float((d.LETTER_MULT or {}).get((letter or "").upper(), 1.0))
    raw = base * mult

    risk_cap  = max(0.0, float(getattr(d, "BANK_PCT_PER_ENTRY", 0.0)) * float(bank))
    phase_cap = float(getattr(d, "HARD_CAP_PRE", 5.0) if str(phase).upper() == "PRE"
                      else getattr(d, "HARD_CAP_IP", 3.0))
    global_max = float(getattr(d, "STAKE_MAX", fam_max))
    hard_cap = min(fam_max, global_max, phase_cap) if phase_cap > 0 else min(fam_max, global_max)

    # Final min/max: MIN_STAKE floor; ceiling is the smallest active cap among risk/hard caps
    ceil = min([x for x in (risk_cap, hard_cap) if x > 0] or [raw])
    stake = max(float(getattr(d, "MIN_STAKE", 2.0)), min(raw, ceil))
    stake = round(stake + 1e-9, 2)

    why = (f"raw={raw:.2f} bank={bank:.2f} risk_cap={risk_cap:.2f} "
           f"phase_cap={phase_cap:.2f} fam_max={fam_max:.2f} → stake={stake:.2f}")
    return stake, why

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

        row = _q_retry(cur, """
            SELECT
              SUM(CASE WHEN role='PARENT'
                        AND entry_status='MATCHED'
                        AND (exit_status IS NULL OR exit_status<>'MATCHED')
                       THEN 1 ELSE 0 END) AS parents_open,

              SUM(CASE WHEN role='PARENT'
                        AND entry_status='MATCHED'
                       THEN 1 ELSE 0 END) AS parents_matched,

              SUM(CASE WHEN role='CHILD'
                        AND entry_status IN ('LIVE','PLACED','MATCHED')
                        AND (exit_status IS NULL OR exit_status<>'MATCHED')
                       THEN 1 ELSE 0 END) AS children_open,

              SUM(CASE WHEN role='CHILD'
                        AND entry_status='MATCHED'
                       THEN 1 ELSE 0 END) AS children_matched
            FROM orders
            WHERE mode='LIVE'
        """).fetchone()

        if row:
            stats = {k: int(row[k] or 0) for k in stats}

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




def calc_greenup_stake(parent_side: str, entry_odds: float, parent_stake: float, hedge_odds: float) -> float:
    """
    Green-up stake to equalise profit across outcomes (pre-commission):
      S_hedge = S_parent * entry_odds / hedge_odds
    Works for both LAY→BACK and BACK→LAY.
    Enforce Betfair min stake (£2) and round to 2dp.
    """
    try:
        s = float(parent_stake) * float(entry_odds) / float(hedge_odds)
        s = max(2.0, s)
        return round(s + 1e-9, 2)
    except Exception:
        # fallback: keep parent stake if anything goes wrong
        return round(max(2.0, float(parent_stake)), 2)

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


def _cancel(app_key: str, token: str, bet_id: str) -> None:
    try:
        _rpc(app_key, token, "cancelOrders", {"betIds": [bet_id]})
    except Exception:
        pass

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
        except Exception as e:
            _log_event("ERROR", "live_router", f"sync_parent_matches error: {e}")

        try:
            _sync_all_matches(limit=100)   # ← NEW: sweep stuck 'PLACED' to 'MATCHED'
            _finalize_children_and_release_exposure(limit=100)
            _sync_settlement_terminal_exposure(limit=200)
        except Exception as e:
            _log_event("ERROR", "live_router", f"sync_all_matches error: {e}")

        try:
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
    Launch a background thread that runs repair_missing_betids + reconcile_live_orders
    every period_s seconds while LIVE mode is active.
    This keeps entry_status in sync with Betfair fills.
    """
    from engines.live import repair_missing_betids, reconcile_live_orders
    stop_evt = threading.Event()

    def loop():
        while not stop_evt.is_set():
            try:
                # 1️⃣ back-fill any missing betIds (rare after 2025-10 patch)
                try:
                    repair_missing_betids.main(silent=True)
                except Exception:
                    pass

                # 2️⃣ mark matched orders
                try:
                    reconcile_live_orders.main(silent=True)
                except Exception:
                    pass
            except Exception as e:
                _log_event("ERROR", "live_router", f"[reconcile_loop] {e}")
            time.sleep(period_s)

    t = threading.Thread(target=loop, name="ReconcileLoop", daemon=True)
    t.start()
    _log_event("INFO", "live_router", f"Reconcile loop started (period={period_s}s)")
    return stop_evt
# === PATCH END ===

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

    fk = _run_fk_id(run_id or "LIVE-AUTO", mode="LIVE")
    con = _orders_conn(); cur = con.cursor()

    try:
        _q_retry(cur, f"""
            INSERT INTO orders (
                customerOrderRef, run_id, mode, marketId, selectionId,
                side, entry_odds, entry_stake, entry_status, opened_at,
                role, source, engine, stop_loss_px
            )
            VALUES (
                ?, ?, 'LIVE', ?, ?, ?, ?, ?, 'QUEUED', ?,
                'PARENT', ?, ?, ?
            )
            ON CONFLICT(customerOrderRef) DO UPDATE SET
                run_id        = COALESCE(orders.run_id, excluded.run_id),
                mode          = 'LIVE',
                marketId      = COALESCE(excluded.marketId, orders.marketId),
                selectionId   = COALESCE(excluded.selectionId, orders.selectionId),
                side          = excluded.side,
                entry_odds    = excluded.entry_odds,
                entry_stake   = excluded.entry_stake,
                entry_status  = COALESCE(orders.entry_status, 'QUEUED'),
                opened_at     = COALESCE(orders.opened_at, excluded.opened_at),
                role          = 'PARENT',
                source        = COALESCE(orders.source, excluded.source),
                engine        = COALESCE(orders.engine, excluded.engine),
                stop_loss_px  = COALESCE(excluded.stop_loss_px, orders.stop_loss_px)
        """,   # 👈 FIX: properly close SQL f-string here
        (
            str(cor),
            int(fk),
            str(market_id),
            str(selection_id),
            side.upper(),
            float(entry_odds),
            float(entry_stake),
            _utcnow_str(),
            source,
            eng,
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
            SELECT engine, entry_odds, entry_stake, entry_status
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
        bank_state.on_parent_closed(
            engine=engine,
            entry_odds=entry_odds,
            entry_stake=entry_stake,
        )

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

        bet_id, _ = _place(
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
        # 4️⃣ Stamp result
        # --------------------------------------------------
        if bet_id:
            _q_retry(cur, """
                UPDATE orders
                   SET entry_status='PLACED',
                       entry_bet_id=?
                 WHERE id=?
            """, (str(bet_id), int(child_id)))
            con.commit()
            return True

        # Betfair failure → mark FAILED
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='FAILED'
             WHERE id=?
        """, (int(child_id),))
        con.commit()
        return False

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"child placement failed id={child_id}: {e}"
        )
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
    _ensure_orders_schema()
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        parent = _q_retry(cur, """
            SELECT id, engine, source, side,
                   entry_odds, entry_stake,
                   entry_status,
                   marketId, selectionId
              FROM orders
             WHERE customerOrderRef=? AND role='PARENT'
             LIMIT 1
        """, (str(cor),)).fetchone()

        if not parent:
            return

        # ── Idempotency guard ──────────────────────────────────────────
        if (parent["entry_status"] or "").upper() == "MATCHED":
            return

        engine = parent["engine"]

        # ── 1️⃣ Mark parent MATCHED ───────────────────────────────────
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='MATCHED',
                   mode='LIVE',
                   role='PARENT',
                   engine=?
             WHERE customerOrderRef=?
        """, (engine, str(cor)))
        con.commit()

        parent_id = int(parent["id"])

        # ── 2️⃣ GUARANTEE child row exists (THIS IS THE FIX) ───────────
        row = _q_retry(cur, """
            SELECT id
              FROM orders
             WHERE role='CHILD'
               AND hedge_of=?
             LIMIT 1
        """, (parent_id,)).fetchone()

        if not row:
            # compute deterministic hedge intent
            parent_side = (parent["side"] or "").upper()
            hedge_side  = "BACK" if parent_side == "LAY" else "LAY"

            from engines.price_math import odds_plus_ticks
            hedge_odds = odds_plus_ticks(
                float(parent["entry_odds"]),
                +1 if hedge_side == "BACK" else -1
            )

            hedge_odds = _round_odds(float(hedge_odds))

            hedge_stake = calc_greenup_stake(
                parent_side,
                float(parent["entry_odds"]),
                float(parent["entry_stake"]),
                hedge_odds
            )

            _orders_insert_child_queued(
                parent_cor=str(cor),
                market_id=str(parent["marketId"]),
                selection_id=str(parent["selectionId"]),
                side=hedge_side,
                odds=float(hedge_odds),
                stake=float(hedge_stake),
                source=str(parent["source"] or "H"),
                exit_kind="HEDGE",
            )

            _log_event(
                "INFO",
                "live_router",
                f"[CHILD-QUEUED] parent_ref={cor}"
            )

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"parent_matched → child queue failed ref={cor}: {e}"
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
                    status = get_bet_status(str(r["child_bet_id"]))
                    if status == "EXECUTION_COMPLETE":
                        app_key, token = _keys()
                        avg_odds, matched_size = _fetch_avg_match(
                            app_key, token, str(r["child_bet_id"])
                        )

                        _q_retry(cur, """
                            UPDATE orders
                               SET entry_status='MATCHED',
                                   entry_matched_odds=?,
                                   entry_matched_stake=?,
                                   closed_at=datetime('now','utc')
                             WHERE id=?
                        """, (avg_odds, matched_size, child_id))

                        bank_state.on_parent_closed(
                            engine=engine,
                            entry_odds=float(r["entry_odds"]),
                            entry_stake=float(r["entry_stake"]),
                        )
                        close_logically_finished_parents()
                        fixed += 1
                        continue

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


def _ensure_child_queued_for_matched_parent(parent_cor: str) -> int | None:
    """
    Standalone invariant enforcer.

    If a PARENT is MATCHED and no CHILD exists,
    insert exactly one CHILD row with entry_status='QUEUED'.

    Idempotent.
    """
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
            LIMIT 1
        """, (str(parent_cor),)).fetchone()

        if not parent:
            return None

        parent_id = int(parent["id"])

        # Child already exists?
        row = _q_retry(cur, """
            SELECT id
            FROM orders
            WHERE role='CHILD'
              AND hedge_of=?
            LIMIT 1
        """, (parent_id,)).fetchone()

        if row:
            return int(row["id"])

        # --- Hedge derivation (authoritative) ---
        parent_side = parent["side"].upper()
        child_side = "BACK" if parent_side == "LAY" else "LAY"
        ticks = int(parent["target_ticks"] or 1)

        from engines.price_math import walk_ticks
        from engines.math.dynamic_stake_v7 import calc_greenup_stake

        # ✅ CORRECT ladder direction
        tick_dir = ticks if parent_side == "LAY" else -ticks

        hedge_odds = walk_ticks(float(parent["entry_odds"]), tick_dir)
        hedge_odds = _round_odds(float(hedge_odds))

        hedge_stake = calc_greenup_stake(
            parent_side,
            float(parent["entry_odds"]),
            float(parent["entry_stake"]),
            hedge_odds
        )

        # Insert CHILD (DB-first, QUEUED)
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
            parent["source"],   # ← inherited (correct)
            parent["engine"],
        ))

        con.commit()
        return int(cur.lastrowid)

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
    con = _orders_conn(); cur = con.cursor()
    try:
        parent = _q_retry(cur, """
            SELECT id, side, entry_odds, entry_stake, marketId, selectionId, source
            FROM orders
            WHERE customerOrderRef=? AND role='PARENT'
        """, (str(cor),)).fetchone()
        if not parent:
            return

        pid, parent_side, parent_odds, parent_stake = (
            int(parent["id"]),
            parent["side"],
            float(parent["entry_odds"] or 0.0),
            float(parent["entry_stake"] or 0.0),
        )
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
                   closed_at=COALESCE(closed_at, datetime('now','utc')),
                   role='CHILD',
                   realized_pnl=?,
                   net_pl=?
             WHERE hedge_of = ?
        """, (realized, realized, pid))
        con.commit()

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

        # ============================================================
        # 🔒 CANONICAL PARENT CLOSE — CHILD MATCHED
        # ============================================================
        try:
            _q_retry(cur, """
                UPDATE orders
                   SET parent_closed = 1,
                       exit_status   = COALESCE(exit_status, 'MATCHED'),
                       closed_at     = COALESCE(closed_at, datetime('now','utc'))
                 WHERE id = ?
                   AND role = 'PARENT'
                   AND parent_closed = 0
            """, (pid,))
            con.commit()

            _log_event(
                "INFO",
                "live_router",
                f"[PARENT CLOSED] ref={cor} reason=child_matched"
            )

        except Exception as e:
            _log_event(
                "ERROR",
                "live_router",
                f"[PARENT CLOSE FAILED] ref={cor}: {e}"
            )

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

        # 🔐 BankState — release exposure
        bank_state.on_parent_closed(
            engine=p["engine"],
            entry_odds=float(p["entry_odds"]),
            entry_stake=float(p["entry_stake"]),
        )

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
               SET exit_status='MATCHED',
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

def _orders_update_parent_cancelled(cor: str, *, reason: str = "timeout") -> None:
    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='CANCELLED',
                   closed_at=COALESCE(closed_at, ?),
                   error=COALESCE(error, ?),
                   mode='LIVE'
             WHERE customerOrderRef=?
        """, (_utcnow_str(), reason[:200], str(cor)))
        con.commit()
    finally:
        con.close()

    # 🔑 RELEASE UNMATCHED EXPOSURE
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

        if not parent:
            return None

        # ⛔ Guard: never double-release exposure
        if (parent["exit_status"] or "").upper() == "MATCHED":
            return None

        pid = int(parent["id"])
        fk  = int(parent["run_id"] or 0)

        # 🔐 RELEASE exposure (once)
        bank_state.on_parent_closed(
            engine=parent["engine"],
            entry_odds=float(parent["entry_odds"]),
            entry_stake=float(parent["entry_stake"]),
        )

        app_key, token = _keys()
        cref = _ref("SL")

        bet_id, _ = _place(
            app_key,
            token,
            market_id,
            selection_id,
            exit_side,
            float(exit_odds),
            float(parent_stake),
            cref,
            persistence="LAPSE"
        )

        if not bet_id:
            return None

        _q_retry(cur, """
            INSERT INTO orders(
              customerOrderRef, run_id, mode,
              marketId, selectionId,
              side, entry_odds, entry_stake,
              entry_status, opened_at, entry_bet_id,
              role, hedge_of, source, exit_kind
            ) VALUES (
              ?, ?, 'LIVE',
              ?, ?,
              ?, ?, ?,
              'MATCHED', datetime('now','utc'), ?,
              'CHILD', ?, 'S', 'STOPLOSS'
            )
        """, (
            cref,
            fk,
            str(market_id),
            str(selection_id),
            exit_side.upper(),
            float(exit_odds),
            float(parent_stake),
            str(bet_id),
            pid
        ))

        child_id = int(cur.lastrowid)

        _q_retry(cur, """
            UPDATE orders
               SET exit_status='MATCHED',
                   exit_kind='STOPLOSS',
                   exit_odds=?,
                   exit_stake=?,
                   closed_at=datetime('now','utc')
             WHERE customerOrderRef=?
        """, (
            float(exit_odds),
            float(parent_stake),
            str(parent_cor)
        ))

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




def _sweep_close_finished_markets(grace_min: int = 15) -> tuple[int, int]:
    """
    Belt-and-braces closer:
      - For markets with off_at_utc <= now - grace_min, cancel any LIVE QUEUED/PLACED entries.
      - For those markets, mark still-open parents as SETTLED (so they stop counting as 'open risk').
    Returns (n_cancelled, n_settled).
    """
    try:
        # 1) Which markets are 'finished' (bets.db schedule, UTC)
        bdb = connect_db(ro=True)
        bdb.row_factory = sqlite3.Row
        rows = _q_retry(bdb,
            "SELECT marketId FROM markets_schedule "
            "WHERE datetime(off_at_utc) <= datetime('now','utc', ?) "
            "AND date(off_at_utc)=date('now','utc')",
            (f"+{int(grace_min)} minutes",)
        ).fetchall()
        bdb.close()

        mids = [str(r["marketId"]) for r in rows] if rows else []
        if not mids:
            return (0, 0)

        # 2) cancel unmatched entries; 3) settle open parents
        con = _orders_conn(); con.row_factory = sqlite3.Row
        qph = ",".join("?" * len(mids))

        # 2) CANCEL QUEUED/PLACED
        n_cancel = _q_retry(con, f"""
            UPDATE orders SET
                entry_status='CANCELLED',
                closed_at=COALESCE(closed_at, datetime('now','utc')),
                mode='LIVE'
            WHERE mode='LIVE'
              AND marketId IN ({qph})
              AND UPPER(COALESCE(entry_status,'')) IN ('QUEUED','PLACED')
        """, tuple(mids)).rowcount

        # 3) SETTLE OPEN PARENTS
        n_settle = _q_retry(con, f"""
            UPDATE orders SET
                exit_status='SETTLED',
                closed_at=COALESCE(closed_at, datetime('now','utc')),
                mode='LIVE'
            WHERE mode='LIVE'
              AND marketId IN ({qph})
              AND COALESCE(role, CASE WHEN hedge_of IS NULL OR hedge_of='' THEN 'PARENT' ELSE 'CHILD' END)='PARENT'
              AND UPPER(COALESCE(entry_status,''))='MATCHED'
              AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
        """, tuple(mids)).rowcount

        con.commit(); con.close()


# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: "AUTO_SETTLED"
# 📆 PATCHED: 2026-02-10 — Settlement EventSync C: AUTO_SETTLED
# ============================================================================

        # -----------------------------------------------------------------
        # EVENTSYNC: C — AUTO-SETTLED PARENTS
        # -----------------------------------------------------------------
        if n_settle:
            try:
                con2 = _orders_conn(); con2.row_factory = sqlite3.Row
                rows2 = _q_retry(con2, f"""
                    SELECT customerOrderRef AS cor, marketId, selectionId,
                           exit_odds, exit_stake, realized_pnl
                      FROM orders
                     WHERE mode='LIVE'
                       AND marketId IN ({qph})
                       AND UPPER(exit_status)='SETTLED'
                """, tuple(mids)).fetchall()
                con2.close()

                for r2 in rows2:
                    _emit_settlement_router_event(
                        "AUTO_SETTLED",
                        cor=r2["cor"],
                        market_id=r2["marketId"],
                        selection_id=r2["selectionId"],
                        exit_odds=r2["exit_odds"],
                        exit_stake=r2["exit_stake"],
                        realized=r2["realized_pnl"],
                    )
            except Exception as e:
                _log_event("WARN","live_router",f"AUTO_SETTLED EventSync failed: {e}")

# === PATCH END ==============================================================


            _log_event(
                "INFO", "live_router",
                f"_sweep_close_finished_markets: cancelled={n_cancel} "
                f"settled={n_settle} mids={len(mids)}"
            )

        return (int(n_cancel or 0), int(n_settle or 0))

    except Exception as e:
        _log_event("ERROR", "live_router", f"sweep_close_finished_markets error: {e}")
        return (0, 0)




# Public status helper for orchestrator
def get_bet_status(bet_id: str) -> str:
    if not bet_id:
        return "UNKNOWN"
    try:
        app_key, token = _keys()
        resp = _list_current(app_key, token, str(bet_id))
        orders = (resp.get("result", {}) or {}).get("currentOrders") or []
        if not orders:
            return "EXECUTION_COMPLETE"
        o = orders[0]
        matched = float(o.get("sizeMatched") or 0.0)
        status  = str(o.get("orderStatus") or o.get("status") or "").upper()
        if matched > 0.0 or "EXECUTION_COMPLETE" in status:
            return "EXECUTION_COMPLETE"
        if "EXECUTABLE" in status:
            return "EXECUTABLE"
        if "CANCELLED" in status:
            return "CANCELLED"
        return status or "UNKNOWN"
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
                target_ticks
            FROM orders
            WHERE customerOrderRef=?
              AND role='PARENT'
              AND entry_status='MATCHED'
            LIMIT 1
        """, (str(parent_cor),)).fetchone()

        if not parent:
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

        hedge_stake = calc_greenup_stake(
            parent_side,
            float(parent["entry_odds"]),
            float(parent["entry_stake"]),
            hedge_odds
        )

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
        return int(cur.lastrowid)

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
知道 and exposure has not yet been released, release it here.

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
                bank_state.on_parent_closed(
                    engine=r["engine"],
                    entry_odds=float(r["entry_odds"]),
                    entry_stake=float(r["entry_stake"]),
                )

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
    parent_persistence: str = "PERSIST",
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
    entry_odds = _round_odds(float(entry_odds))
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

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py : place_parent_and_hedge()
# 🔎 SEARCH FOR: "if _plan is not None:"  (the COMPAT GLUE block)
# ⛏ ACTION: Insert THIS block immediately AFTER the COMPAT GLUE normalisation
# 📆 PATCHED: 2026-01-22 — STOPLOSS Immediate Child Executor for v7
# ============================================================================

    # --------------------------------------------------------------
    # STOPLOSS (W-engine) — IMMEDIATE FLATTEN EXIT
    # --------------------------------------------------------------
    # STOPLOSS (W-engine) — IMMEDIATE FLATTEN EXIT
    if _plan and str(_plan.get("engine")).upper() == "OVERWATCHER" \
              and str(_plan.get("type")).upper() == "STOPLOSS":

        parent_cor = _ctx.get("customerOrderRef") or _plan.get("customerOrderRef")
        if not parent_cor:
            parent_cor = _ref("SLP")

        child_id = _place_stoploss_child_now(
            parent_cor=parent_cor,
            market_id=str(_plan.get("marketId")),
            selection_id=str(_plan.get("selectionId")),
            exit_side="BACK" if (_plan.get("side") or "").upper() == "LAY" else "LAY",
            exit_odds=float(_plan.get("px")),
            parent_stake=float(_plan.get("entry_stake") or _plan.get("stake") or 0.0),
            run_id=_ctx.get("run_id")
        )

        if not child_id:
            return None, "STOPLOSS_FAILED"

        return None, parent_cor


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
    app_key, token = _keys()

    engine = (
        (_plan or {}).get("engine")
        or (_ctx  or {}).get("engine")
    )

    # Router does NOT hard-gate on engine.
    # Engine is a BankState/accounting concern handled upstream in placement.
    # Betfair execution must proceed regardless.


    # 3) place parent -----------------------------------------------------------
    bf_parent_id, detail = None, {}
# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: if _is_manual_parent(parent_row):
# 🧩 ACTION: FIX VARIABLE + EARLY RETURN
# 📆 PATCHED: 2026-01-08 — activate manual parent exposure bypass
# ============================================================================

    if _is_manual_parent(row):
        return None, parent_ref

# === PATCH END ==============================================================

    try:
        # 1️⃣ Reserve FIRST
        bank_state.on_parent_placed(
            engine=engine,
            side=side,
            entry_odds=float(entry_odds),
            entry_stake=float(stake),
        )
        bf_parent_id, detail = _place(app_key, token, market_id, selection_id, side,
                                      float(entry_odds), float(stake), parent_ref,
                                      persistence=parent_persistence)
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
            if not _poll_matched(app_key, token, bf_parent_id, timeout_s=90, interval_s=2.0):
                try: _cancel(app_key, token, bf_parent_id)
                except Exception: pass
                if parent_persistence.upper() == "LAPSE":
                    try:
                        con = _orders_conn()
                        _q_retry(con, """
                            UPDATE orders
                               SET entry_status='CANCELLED',
                                   closed_at=COALESCE(closed_at, datetime('now','utc'))
                             WHERE customerOrderRef=? AND COALESCE(entry_status,'')<>'MATCHED'
                        """, (parent_ref,))
                        con.commit(); con.close()
                    except Exception: pass
                return
            # parent is matched on exchange; stamp it in DB so re-hedge can rescue if needed
            try:
                _orders_update_parent_matched(parent_ref, bf_parent_id)
                _orders_probe(parent_ref, note="parent_matched")
                _log_event_safe("INFO", "live_router",
                                f"[BG] parent matched mid={market_id} sid={selection_id} ref={parent_ref}")
            except Exception as e:
                _log_event_safe("WARN", "live_router",
                                f"[BG] could not mark parent matched ref={parent_ref}: {e}")

            # --------------------------------------------------
            # CHILD lifecycle: QUEUE → ROUTER WORKER HANDOFF
            # --------------------------------------------------
            try:
                # 1️⃣ Compute hedge parameters (already correct)
                ticks = max(1, abs(int(hedge_ticks)))
                from engines.price_math import odds_plus_ticks as _odds_plus_ticks
                parent_side = side.upper()

                if parent_side == "LAY":
                    hedge_side = "BACK"
                    hedge_odds = odds_plus_ticks(entry_odds, +ticks)   # ✔ HIGHER
                else:
                    hedge_side = "LAY"
                    hedge_odds = odds_plus_ticks(entry_odds, -ticks)   # ✔ LOWER


                hedge_odds = _round_odds(float(hedge_odds))
                hedge_stake = (
                    float(hedge_stake_override)
                    if hedge_stake_override is not None
                    else calc_greenup_stake(
                        side,
                        float(entry_odds),
                        float(stake),
                        float(hedge_odds),
                    )
                )

                # 2️⃣ Insert CHILD row in QUEUED state (DB-first, minimal)
                child_id = _orders_insert_child_queued(
                    parent_cor=parent_ref,
                    market_id=market_id,
                    selection_id=selection_id,
                    side=hedge_side,
                    odds=hedge_odds,
                    stake=hedge_stake,
                    source=source,
                    exit_kind="HEDGE",
                )

                if not child_id:
                    _log_event_safe(
                        "ERROR",
                        "live_router",
                        f"[BG] child queue failed parent_ref={parent_ref}"
                    )
                    return

                # 3️⃣ Hand off CHILD execution to ROUTER WORKER (authoritative)
              
                direction = "LAY->BACK" if side.upper() == "LAY" else "BACK->LAY"

                enqueue_router_child(
                    plan={
                        "role": "CHILD",
                        "child_id": child_id,
                        "parent_cor": parent_ref,
                        "marketId": market_id,
                        "selectionId": selection_id,
                        "side": hedge_side,
                        "px": hedge_odds,
                        "size": hedge_stake,
                        "direction": direction,
                        "engine": engine,
                        "source": source,
                        "exit_kind": "HEDGE",
                    },
                    ctx={
                        "run_id": run_id,
                        "engine": engine,
                        "mode": "LIVE",
                    }
                )

                _log_event_safe(
                    "INFO",
                    "live_router",
                    f"[CHILD→ROUTER] queued child_id={child_id} parent_ref={parent_ref}"
                )

            except Exception as e:
                _log_event_safe(
                    "ERROR",
                    "live_router",
                    f"[BG] child handoff failed parent_ref={parent_ref}: {e}"
                )

        except Exception as e:
            _log_event_safe("ERROR", "live_router", f"bg hedge error ref={parent_ref}: {e}")

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

            hedge_stake = calc_greenup_stake(
                parent_side,
                entry_odds,
                entry_stake,
                hedge_odds
            )

            child_id = _orders_insert_child_queued(
                parent_cor=str(r["cor"]),
                market_id=str(r["marketId"]),
                selection_id=str(r["selectionId"]),
                side=hedge_side,
                odds=float(hedge_odds),
                stake=float(hedge_stake),
                source=source,
                exit_kind="HEDGE",
            )

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

                # 🔑 CONTINUE LIFECYCLE → ROUTER
                enqueue_router_child(
                    plan={
                        "parent_cor": parent_cor,
                    },
                    ctx={}
                )

                _log_event(
                    "INFO",
                    "live_router",
                    f"[SYNC] flipped parent_ref={parent_cor} to MATCHED"
                )

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
        for r in rows:
            _q_retry(cur, """
                UPDATE orders
                   SET entry_status='CANCELLED',
                       closed_at=COALESCE(closed_at, datetime('now','utc')),
                       error=COALESCE(error, 'cleanup_orphan'),
                       mode='LIVE'
                 WHERE id=?
            """, (r["id"],))
            _log_event("WARN", "live_router",
                       f"[CLEANUP] orphan parent cancelled ref={r['customerOrderRef']}")
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



