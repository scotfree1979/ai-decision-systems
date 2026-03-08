from __future__ import annotations

import json, uuid, datetime, sqlite3
from typing import Optional
# --- helpers (keep exactly as-is, except open_auto_db) ---
from engines.decision_engine.decide_once.helpers import (
    status_once,
    q_retry as _q,
)

# --- authoritative DB opener for execution paths ---
from engines.config_paths import open_auto_db as _adb



# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: top-level (module scope)
# 🧩 ACTION: ADD (verbatim relocation of execution worker from LiveRouter)
# 📆 PATCHED: 2025-12-17 — Placement-owned execution worker (no behaviour change)
# ======================================================================================================

import threading
import queue
import time
import traceback

# ------------------------------------------------------------------------------
# Placement Execution Queue
# ------------------------------------------------------------------------------
# NOTE:
# This queue was previously owned by LiveRouter.
# It is relocated here verbatim to restore correct execution ownership.
# ------------------------------------------------------------------------------
from engines.decision_engine.decide_once.placement_queues import (
    PLACEMENT_INPUT_QUEUE, 
    PLACEMENT_EXEC_QUEUE
)



# ------------------------------------------------------------------------------
# Placement Execution Worker
# ------------------------------------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: def _placement_worker_loop():
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-01-17 — Fix placement worker unreachable SELECT
#
# ROOT CAUSE (PROVEN):
# - placement worker filtered on `o.error IS NULL`
# - QUEUED rows legitimately have error populated from prior attempts
# - Result: ZERO rows ever selected → place_parent_and_hedge never called
#
# FIX (HARD RULE):
# - Lifecycle authority is entry_status ONLY
# - error is terminal metadata, NOT a selection gate
# - Each parent is attempted ONCE
#
# INVARIANTS:
# - QUEUED → PLACING → (PLACED | FAILED)
# - FAILED / CANCELLED / MATCHED are never re-read
# ======================================================================================================
# placement.py (module scope)
PLACEMENT_METRICS = {
    "exec_taken": 0,
    "router_called": 0,
    "router_exception": 0,
}

def _placement_worker_loop(*, run_id: str, poll_sleep: float = 0.2):
    """
    Placement worker (DB-first, authoritative).

    Responsibilities:
    - Read QUEUED parents from DB (current run only)
    - Order deterministically (route → bus_stop → engine → opened_at)
    - Atomically claim one row (QUEUED → PLACING)
    - Reload FULL ctx for that parent
    - Call place_parent_and_hedge(parent_id, run_id, ctx)
    """

    import time
    from engines.config_paths import open_auto_db




    from engines.live.ctx_loader import load_ctx_for_parent  # helper that rebuilds full ctx

    print(f"[PLACEMENT][WORKER] started (run_id={run_id})", flush=True)

    while True:
        con = None
        try:
            # Open DB writer (needed for atomic claim)
            con = open_auto_db(rw=True)
 

            # Select next executable parent (ORDERED)
            row = con.execute(
                """
                SELECT id
                FROM orders
                WHERE
                    role = 'PARENT'
                    AND entry_status = 'QUEUED'
                    AND run_id = ?
                ORDER BY
                    route_id ASC,
                    bus_stop ASC,
                    CASE engine
                        WHEN 'MSC_INPLAY'       THEN 1
                        WHEN 'LEGACY'           THEN 2
                        WHEN 'MSC_RISK'         THEN 3
                        WHEN 'MSC_EXPLORATORY'  THEN 4
                        ELSE 99
                    END ASC,
                    datetime(opened_at) ASC
                LIMIT 1
                """,
                (str(run_id),)
            ).fetchone()

            if not row:
                con.close()
                time.sleep(poll_sleep)
                continue

            parent_id = int(row[0])

            # Atomic claim
            cur = con.execute(
                """
                UPDATE orders
                   SET entry_status = 'PLACING'
                 WHERE id = ?
                   AND entry_status = 'QUEUED'
                """,
                (parent_id,)
            )

            if cur.rowcount != 1:
                con.commit()
                con.close()
                continue

            con.commit()
            con.close()

        except Exception as e:
            try:
                if con:
                    con.rollback()
                    con.close()
            except Exception:
                pass
            print(f"[PLACEMENT][ERR] DB failure: {e}", flush=True)
            time.sleep(0.5)
            continue

        # Reload FULL ctx and execute via router
        try:
            ctx = load_ctx_for_parent(parent_id)

            con = open_auto_db(rw=False)
            row = con.execute(
                "SELECT customerOrderRef FROM orders WHERE id = ?",
                (parent_id,)
            ).fetchone()
            con.close()

            if not row or not row[0]:
                raise RuntimeError(f"Missing customerOrderRef for parent_id={parent_id}")

            parent_ref = row[0]

            import engines.live.live_router as live_router

            live_router.place_parent_and_hedge(
                parent_ref=parent_ref,
                run_id=run_id,
                _ctx=ctx,
            )

        except Exception as e:
            import traceback

            print(
                "\n[PLACEMENT][ERR] ROUTER EXECUTION FAILED\n"
                f"parent_id={parent_id}\n"
                f"exception_type={type(e).__name__}\n"
                f"exception_msg={e}\n"
                "---------------- TRACEBACK ----------------",
                flush=True
            )

            traceback.print_exc()

            # Optional but extremely useful: dump ctx snapshot safely
            try:
                print(
                    "\n[PLACEMENT][CTX SNAPSHOT]\n"
                    f"{json.dumps(ctx, default=str, indent=2)}\n",
                    flush=True
                )
            except Exception:
                print("[PLACEMENT][CTX SNAPSHOT FAILED]", flush=True)


# ------------------------------------------------------------------------------
# Worker bootstrap
# ------------------------------------------------------------------------------
_PLACEMENT_WORKER_THREAD: threading.Thread | None = None

def start_placement_worker(*, run_id: str):
    """
    Start placement execution worker (idempotent).
    """
    print("[PLACEMENT][TRACE] start_placement_worker() CALLED", flush=True)

    global _PLACEMENT_WORKER_THREAD

    if any(
        t.name == "PlacementWorker" and t.is_alive()
        for t in threading.enumerate()
    ):
        return

    t = threading.Thread(
        target=_placement_worker_loop,
        kwargs={"run_id": str(run_id)},
        name="PlacementWorker",
        daemon=True,
    )

    t.start()
    _PLACEMENT_WORKER_THREAD = t

    print(f"[PLACEMENT] execution worker started (run_id={run_id})")


# ------------------------------------------------------------
# 0) Canonical MID/SID resolution (EARLY — required by gates)
# ------------------------------------------------------------
def _canon_ids(d):
    if not isinstance(d, dict):
        return None, None
    mid = d.get("marketId") or d.get("market_id") or d.get("mid")
    sid = d.get("selectionId") or d.get("selection_id") or d.get("sid")
    return (str(mid) if mid is not None else None,
            str(sid) if sid is not None else None)

# ------------------------------------------------------------------------------
# Public enqueue API (used by BUS / router adapters)
# ------------------------------------------------------------------------------
def placement_affordable(plan, ctx):
    engine = plan["engine"]
    required = compute_required_exposure(plan)

    ok = bank_state.can_place(engine, plan)
    return ok, required


# =====================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: def enqueue_for_placement(name: str, plan: dict, ctx: dict):
# 📆 PATCHED: 2025-12-29 — Minimal canonicalization at placement boundary (DB-first)
#
# PURPOSE:
# - Placement enqueue is the ownership boundary
# - Canonicalize *shape only* (not logic)
# - Immediately create PARENT / QUEUED row
# - Eliminate downstream blockers permanently
# =====================================================================================

def enqueue_for_placement(name: str, plan: dict, ctx: dict):
    """
    Placement enqueue (FINAL, MINIMAL).

    Responsibility:
    - Canonicalize shape
    - Insert PARENT row with entry_status='QUEUED'
    - NOTHING ELSE
    """

    # --------------------------------------------------
    # Shape-only canonicalisation
    # --------------------------------------------------
    plan = dict(plan or {})
    ctx  = dict(ctx or {})

    plan["marketId"]    = plan.get("marketId")    or ctx.get("marketId")
    plan["selectionId"] = plan.get("selectionId") or ctx.get("selectionId")

    if not plan["marketId"] or not plan["selectionId"]:
        print("[PLACEMENT][DROP] missing marketId/selectionId")
        return

    engine = ctx.get("engine") or plan.get("engine")
    if not engine:
        raise RuntimeError("[PLACEMENT] invariant violation: engine missing")

    engine = str(engine)
    plan["engine"] = engine
    ctx["engine"]  = engine

    run_id = ctx.get("run_id")
    if not run_id:
        raise RuntimeError("[PLACEMENT] invariant violation: run_id missing")

    # --------------------------------------------------
    # DB-FIRST INSERT (AUTHORITATIVE)
    # --------------------------------------------------
    try:
        parent_id = _insert_pending_parent(
            run_id=run_id,
            market_id=str(plan["marketId"]),
            selection_id=str(plan["selectionId"]),
            side=plan["side"],
            entry_odds=plan.get("px"),
            entry_stake=plan.get("size"),
            cor=plan["customerOrderRef"],
            engine=plan["engine"],
            source=plan["letter"],
            ctx=ctx,
            plan=plan,
        )

        if not parent_id:
            print("[PLACEMENT][DROP] parent insert failed")
            return

        # For observability only (NOT execution)
        plan["parent_id"] = parent_id

    except Exception as e:
        print(f"[PLACEMENT][DROP] insert error: {e}")
        return

# ---------- tiny safe getters
def _rget(row, key, default=None):
    try:
        if hasattr(row, "keys"):
            return row[key] if key in row.keys() else default
        if isinstance(row, dict):
            return row.get(key, default)
    except Exception:
        pass
    return default

def _i(v, d=0):
    try: return int(v)
    except Exception: return int(d)

def _f(v, d=0.0):
    try: return float(v)
    except Exception: return float(d)

# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: def _next_trade_index(
# 📆 PATCHED: 2025-11-29 — replace raw sqlite3.connect() with DAL-safe _adb()
# ===========================================================================

def _next_trade_index(mid: str, sid: str, letter: str) -> int:
    """DAL-safe: return next trade_index (per marketId, selectionId, letter)."""
    con = None
    try:
        from engines.config_paths import auto_conn
        con = _adb(rw=True)
        row = con.execute("""
            SELECT COUNT(*) AS n
              FROM orders
             WHERE marketId=? AND selectionId=? AND UPPER(source)=UPPER(?)
               AND date(opened_at)=date('now','utc')
        """, (str(mid), str(sid), str(letter))).fetchone()
        return int(row["n"] or 0) + 1
    except Exception:
        return 1
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

# === PATCH END ==============================================================



# Plan ledger hooks (guarded)
try:
    from engines.mastery.plan_ledger import mark_open_parent, mark_child
except Exception:
    def mark_open_parent(*_a, **_k): pass
    def mark_child(*_a, **_k): pass


# ---------- schema probes
def _orders_has_status() -> bool:
    con = None
    try:
        from engines.config_paths import auto_conn
        con = _adb(rw=True)

        for r in con.execute("PRAGMA table_info(orders)"):
            name = r["name"] if hasattr(r, "keys") else r[1]
            if str(name).lower() == "status":
                return True
        return False
    except Exception:
        return False
    finally:
        try:
            if con: con.close()
        except Exception:
            pass


def _fetch_px_from_odds_current(mid: str, sid: str) -> Optional[float]:
    """
    Resolve latest price from AUTO.odds_current for today.
    Schema (confirmed): day, marketId, selectionId, ltp, updated_ts.
    """
    con = None
    try:
        from engines.config_paths import auto_conn
        con = _adb(rw=True)
        r = con.execute("""
            SELECT ltp
              FROM odds_current
             WHERE day IN (date('now'), date('now','utc'))
               AND marketId=? AND selectionId=?
             ORDER BY datetime(updated_ts) DESC
             LIMIT 1
        """, (str(mid), str(sid))).fetchone()
        if r and r["ltp"] is not None:
            return float(r["ltp"])
        return None
    except Exception:
        return None
    finally:
        try: con.close()
        except Exception: pass

def _fetch_px_from_inbound(mid: str, sid: str) -> Optional[float]:
    """
    Resolve price from AUTO.inbound_oc_cache (oc1 → anchor_odd → oc1_band tail).
    Schema (confirmed): selectionId, oc1, anchor_odd, oc1_band_json, marketId, id, last_sync_ts.
    """
    con = None
    try:
        from engines.config_paths import auto_conn
        con = _adb(rw=True)
        r = con.execute("""
            SELECT oc1, anchor_odd, oc1_band_json
              FROM inbound_oc_cache
             WHERE marketId=? AND selectionId=?
             ORDER BY id DESC
             LIMIT 1
        """, (str(mid), str(sid))).fetchone()
        if not r:
            return None
        if r["oc1"] is not None:
            return float(r["oc1"])
        if r["anchor_odd"] is not None:
            return float(r["anchor_odd"])
        bj = r["oc1_band_json"]
        if bj:
            try:
                arr = json.loads(bj) or []
                if arr:
                    return float(arr[-1])
            except Exception:
                return None
        return None
    except Exception:
        return None
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===

def _orders_cols() -> dict:
    con = None
    try:
        from engines.config_paths import auto_conn
        con = _adb(rw=True)
        out = {}
        for r in con.execute("PRAGMA table_info(orders)"):
            name = r["name"] if hasattr(r, "keys") else r[1]
            notnull = bool(r["notnull"] if hasattr(r, "keys") else r[3])
            out[str(name)] = notnull
        return out
    except Exception:
        return {}
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

# ---------- decisions
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: ^def _write_decision\(
# 📆 PATCHED: 2025-11-19 — use direct writer to AUTOSCALP_GUI for decisions
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _write_decision(*, run_id, mid, sid, outcome, why, letter,
                    proposed_odds=None, proposed_stake=None,
                    order_id=None, meta: dict | None = None):
    decided_at = datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    m = dict(meta or {})
    m.update({"placement_outcome": outcome, "why": why})
    con = None
    try:
        # 🔁 Direct writer (avoids readonly DAL path)
        con = _adb(ro=False)
        con.execute("""
            INSERT INTO decisions(
                run_id, marketId, selectionId, decided_at,
                signal_type, blueprint_match, confidence, scalp_direction,
                proposed_odds, proposed_stake, notes, meta_json, order_id, why
            ) VALUES (?,?,?,?,NULL,NULL,NULL,NULL,?,?,?,?,?,?)
        """, (
            run_id,
            str(mid),
            str(sid),
            decided_at,
            proposed_odds,
            proposed_stake,
            "",
            json.dumps(m, separators=(",",":")),
            order_id,
            str(why or "")
        ))
        con.commit()
    except Exception as e:
        try:
            if con: con.rollback()
        finally:
            print(f"[DECIDE][ERR] decisions insert failed: {e}")
    finally:
        try:
            if con: con.close()
        except Exception:
            pass


def log_decision_skip(*, run_id, marketId, selectionId, letter, why, order_id: int | None = None):
    _write_decision(run_id=run_id, mid=marketId, sid=selectionId,
                    outcome="not_PLACED", why=why, letter=letter,
                    order_id=order_id)

# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: def _insert_pending_parent
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2025-12-17 — Canonical-schema, non-blocking parent preclaim
# ======================================================================================================
def _insert_pending_parent(
    *,
    run_id: int,
    market_id: str,
    selection_id: str,
    side: str,
    entry_odds: float,
    entry_stake: float,
    cor: str,
    engine: str,
    source: str,
    mode: str = "LIVE",
    stoploss_mode: str = "BALANCED",
    plan_id: str | None = None,
    ctx: dict | None = None,
    plan: dict | None = None,
) -> int | None:
    """
    Insert a PARENT row into orders (idempotent).

    Contract (STRICT):
    - BUS guarantees all required fields.
    - Placement does NO recovery, NO probing, NO enrichment.
    - customerOrderRef is authoritative and MUST be idempotent.
    """

    # -------------------------------
    # NORMALISE INPUTS (NO MUTATION)
    # -------------------------------
    plan = plan or {}
    ctx  = ctx or {}
    bet_type = str(plan.get("bet_type") or "").upper() or None
    # -------------------------------
    # REQUIRED INVARIANT
    # -------------------------------
    if "target_ticks" not in plan:
        raise RuntimeError(
            f"[PLACEMENT] invariant violation: target_ticks missing for parent {cor}"
        )

    target_ticks = int(plan["target_ticks"])
    if target_ticks <= 0:
        raise RuntimeError(
            f"[PLACEMENT] invariant violation: target_ticks <= 0 for parent {cor}"
        )


    mid    = str(market_id)
    sid    = str(selection_id)
    letter = str(source)[:1].upper()

    # -------------------------------
    # OPEN WRITER (CANONICAL AUTO DB)
    # -------------------------------
    con = _adb(ro=False)
    cur = con.cursor()

    try:
        # -------------------------------
        # TRADE INDEX (PER RUNNER / LETTER)
        # -------------------------------
        trade_index = _next_trade_index(mid, sid, letter)
        plan["trade_index"] = trade_index

        # -------------------------------
        # INSERT PARENT (IDEMPOTENT)
        # -------------------------------
        try:
            # FULL lifecycle exposure (parent + child)
            required_exposure = float(plan["required_exposure"])

            cur.execute(
                """
                INSERT INTO orders (
                    customerOrderRef,
                    run_id,
                    mode,
                    marketId,
                    selectionId,
                    side,
                    entry_odds,
                    entry_stake,
                    target_ticks,
                    required_exposure,
                    route_id,
                    bus_stop,
                    tick_id,
                    entry_status,
                    role,
                    source,
                    engine,
                    bet_type,
                    stoploss_mode,
                    notes,
                    opened_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?, ?, ?, ?, ?, ?
                )
                """,
                (
                    str(cor),
                    str(run_id),
                    str(mode),
                    str(mid),
                    str(sid),
                    str(side),
                    float(entry_odds),
                    float(entry_stake),
                    target_ticks,
                    required_exposure,
                    plan.get("route_id"),
                    plan.get("bus_stop"),
                    plan.get("tick_id"),
                    "QUEUED",
                    "PARENT",
                    str(letter),
                    str(engine),
                    bet_type,
                    str(stoploss_mode).upper(),
                    f"{letter}{trade_index:02d}",
                    datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
                )
            )

            pending_id = int(cur.lastrowid)

        except sqlite3.IntegrityError:
            # --------------------------------------------------
            # 🔁 IDEMPOTENT REUSE (EXPECTED + CORRECT)
            # --------------------------------------------------
            row = cur.execute(
                """
                SELECT id
                  FROM orders
                 WHERE customerOrderRef = ?
                   AND role = 'PARENT'
                 LIMIT 1
                """,
                (str(cor),)
            ).fetchone()

            if not row:
                raise  # true corruption — invariant violation

            pending_id = int(row["id"])

        con.commit()
        return pending_id

    except Exception as e:
        con.rollback()
        print(f"[PLACEMENT][PRECLAIM][FAIL] {e}")
        return None

    finally:
        try:
            con.close()
        except Exception:
            pass


def _promote_pending_to_queued(pending_id: int) -> None:
    con = _adb(ro=False)
    try:
        con.execute(
            """
            UPDATE orders
               SET entry_status = 'QUEUED'
             WHERE id = ?
               AND entry_status = 'PENDING'
            """,
            (int(pending_id),)
        )
        con.commit()
    finally:
        try:
            con.close()
        except Exception:
            pass



def _cancel_stale_parents(mid: str, sid: str, *, older_than_sec: int = 70) -> None:
    from engines.config_paths import auto_conn
    con = _adb(rw=True)
    try:
        con.execute("""
          UPDATE orders
             SET entry_status='FAILED', closed_at=datetime('now','utc'),
                 notes = TRIM(COALESCE(notes,'') || ' stale')
           WHERE marketId=? AND selectionId=?
             AND role IN ('PARENT','parent')   -- be tolerant on value
             AND hedge_of IS NULL
             AND entry_status='PENDING'
             AND entry_bet_id IS NULL
             AND datetime(opened_at) <= datetime('now','utc', ?)
        """, (str(mid), str(sid), f"-{int(max(15, older_than_sec))} seconds"))
        con.commit()
    except Exception:
        try: con.rollback()
        except Exception: pass
    finally:
        try: con.close()
        except Exception: pass


# ---------- main entry
# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: ^def place_from_plan\(name: str, plan: dict, ctx: dict\)
# 🛠 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2025-12-30 — Restore LEGACY inline execution + MSC enqueue path
#
# PURPOSE
# -------
# This function restores the ONLY execution path that has ever placed bets (LEGACY),
# while cleanly adding a separate MSC path that routes via the placement queue.
#
# Execution model:
#
#   LEGACY
#     → place_parent_and_hedge()  (INLINE — proven working path)
#
#   MSC_*
#     → enqueue_for_placement()
#     → PlacementWorker → LiveRouter
#
# Both paths:
#   • share canonicalisation
#   • share affordability gate
#   • preserve historical behaviour
#
# ======================================================================================================

def place_from_plan(name: str, plan: dict, ctx: dict) -> Optional[int]:
    """
    Placement entrypoint.

    Parents are DB-first and worker-owned.
    This function MUST NOT place parents directly.
    """

    # Canonical IDs
    mid, sid = _canon_ids(plan)
    if not mid or not sid:
        return None

    plan = dict(plan or {})
    ctx  = dict(ctx or {})

    engine = plan.get("engine") or ctx.get("engine")
    if not engine:
        return None

    engine = str(engine).upper()
    plan["engine"] = engine
    ctx["engine"]  = engine

    # Normalize direction → side
    direction = str(plan.get("direction") or "LAY->BACK").upper()
    plan["side"] = "LAY" if direction.startswith("LAY") else "BACK"



    # 🔑 SINGLE ACTION
    enqueue_for_placement(name, plan, ctx)
    return None



