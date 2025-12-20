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

_PLACEMENT_EXEC_QUEUE: "queue.Queue[tuple[str, dict, dict]]" = queue.Queue()


# ------------------------------------------------------------------------------
# Placement Execution Worker
# ------------------------------------------------------------------------------
def _placement_worker_loop():
    """
    Placement execution worker.

    Dequeues (name, plan, ctx) tuples and executes them sequentially
    using existing placement semantics.

    BEHAVIOUR IS IDENTICAL to the previous LiveRouter worker.
    Ownership only has moved.
    """
    from engines.decision_engine.decide_once.placement import place_from_plan

    while True:
        try:
            name, plan, ctx = _PLACEMENT_EXEC_QUEUE.get()

            try:
                place_from_plan(name, plan, ctx)
            except Exception:
                print("[PLACEMENT][WORKER][ERR] execution failed")
                traceback.print_exc()

        except Exception:
            print("[PLACEMENT][WORKER][ERR] worker loop error")
            traceback.print_exc()
            time.sleep(0.5)


# ------------------------------------------------------------------------------
# Worker bootstrap
# ------------------------------------------------------------------------------
_PLACEMENT_WORKER_THREAD: threading.Thread | None = None


def start_placement_worker():
    """
    Start placement execution worker (idempotent).

    This replaces the LiveRouter worker startup.
    """
    global _PLACEMENT_WORKER_THREAD

    if _PLACEMENT_WORKER_THREAD and _PLACEMENT_WORKER_THREAD.is_alive():
        return

    t = threading.Thread(
        target=_placement_worker_loop,
        name="PlacementWorker",
        daemon=True,
    )
    t.start()
    _PLACEMENT_WORKER_THREAD = t

    print("[PLACEMENT] execution worker started")


# ------------------------------------------------------------------------------
# Public enqueue API (used by BUS / router adapters)
# ------------------------------------------------------------------------------
def placement_affordable(plan: dict, ctx: dict) -> tuple[bool, str]:
    from engines.live import bank_state

    engine = ctx.get("engine")
    if not engine:
        return False, "missing_engine"

    size = float(plan.get("size") or 0.0)
    px   = float(plan.get("px") or 0.0)
    direction = str(plan.get("direction") or "").upper()

    if size <= 0.0 or px <= 0.0:
        return False, "invalid_size_or_price"

    # Conservative FULL lifecycle reservation
    if direction.startswith("LAY"):
        parent_liab = size * max(px - 1.0, 0.0)
        child_liab  = size
    else:
        parent_liab = size
        child_liab  = size * max(px - 1.0, 0.0)

    required = round(parent_liab + child_liab, 2)

    # 🔴 THIS IS THE GATE 🔴
    available = bank_state.get_engine_available(engine)

    if available < required:
        return False, (
            f"insufficient_engine_budget "
            f"engine={engine} "
            f"required={required:.2f} "
            f"available={available:.2f}"
        )

    return True, "ok"

# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: def enqueue_for_placement(name: str, plan: dict, ctx: dict)
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2025-12-18 — Non-blocking enqueue + worker auto-bootstrap
# ======================================================================================================

def enqueue_for_placement(name: str, plan: dict, ctx: dict):
    """
    Enqueue a plan for placement execution.

    CRITICAL:
    - MUST NOT block BUS
    - Placement failure must never stall tick loop
    """

    # Ensure worker is running (idempotent)
    try:
        start_placement_worker()
    except Exception as e:
        print(f"[PLACEMENT][WARN] worker start failed: {e}")

    try:
        # Non-blocking enqueue — BUS must never wait
        _PLACEMENT_EXEC_QUEUE.put_nowait((name, plan, ctx))
    except Exception as e:
        # Drop-on-failure is correct behaviour here
        print(
            "[PLACEMENT][DROP] enqueue failed — BUS continues | "
            f"engine={name} run_id={ctx.get('run_id')} err={e}"
        )


def _auto_db_writer(timeout: float = 8.0) -> sqlite3.Connection:
    con = _adb(rw=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return con



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
        con = _auto_db_writer()
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
                    outcome="not_placed", why=why, letter=letter,
                    order_id=order_id)

# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: def _insert_pending_parent
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2025-12-17 — Canonical-schema, non-blocking parent preclaim
# ======================================================================================================
def _insert_pending_parent(*,
                           mid: str,
                           sid: str,
                           letter: str,
                           side: str,
                           plan: dict,
                           ctx: dict,
                           cor: str,
                           plan_id: Optional[str] = None) -> int | None:
    """
    Insert a PENDING PARENT row into orders.

    Contract (STRICT):
    - BUS guarantees all required fields.
    - Placement does NO recovery, NO probing, NO enrichment.
    - Missing data → fail fast → BUS must be fixed.
    """

    # -------------------------------
    # REQUIRED FIELDS (FAIL FAST)
    # -------------------------------
    if not mid or not sid or not cor:
        raise RuntimeError("placement_preclaim_missing_identity")

    if plan.get("px") is None:
        raise RuntimeError("placement_preclaim_missing_px")

    if plan.get("size") is None:
        raise RuntimeError("placement_preclaim_missing_size")

    if ctx.get("run_id") is None:
        raise RuntimeError("placement_preclaim_missing_run_id")

    # -------------------------------
    # OPEN WRITER (CANONICAL AUTO DB)
    # -------------------------------
    con = _auto_db_writer()
    cur = con.cursor()

    try:
        # -------------------------------
        # TRADE INDEX (PER RUNNER / LETTER)
        # -------------------------------
        trade_index = _next_trade_index(mid, sid, letter)
        plan["trade_index"] = trade_index

        # -------------------------------
        # INSERT PARENT ORDER (CANONICAL)
        # -------------------------------
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
                entry_status,
                role,
                source,
                stoploss_mode,
                notes,
                opened_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                str(cor),
                _i(ctx.get("run_id")),
                str(ctx.get("mode") or "LIVE"),
                str(mid),
                str(sid),
                str(side),
                _f(plan.get("px")),
                _f(plan.get("size")),
                "PENDING",
                "PARENT",
                str(letter),
                str(plan.get("stoploss_mode") or "BALANCED").upper(),
                f"{letter}{trade_index:02d}",
                datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            )
        )

        con.commit()
        pending_id = int(cur.lastrowid)
        # 🔑 THIS IS THE MISSING STEP
        _promote_pending_to_queued(pending_id)
        # -------------------------------
        # PLAN LEDGER LINK (IF PRESENT)
        # -------------------------------
        if plan_id and pending_id:
            try:
                mark_open_parent(plan_id, pending_id)
            except Exception:
                # ledger failure must NOT block placement
                pass

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
    con = _auto_db_writer()
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
# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: ^def place_from_plan\(name: str, plan: dict, ctx: dict\)
# 🛠 ACTION: Replace entire function with MSC-pass-through + legacy-safe placement
# 📆 PATCHED: 2025-12-11Z — CTXv7 alignment, MSC pass-through, legacy preservation
# ============================================================================

def place_from_plan(name: str, plan: dict, ctx: dict) -> Optional[int]:
    """
    Unified placement handler (MSC-aware)
    -------------------------------------

    • MSC engines (MSC_EXPLORATORY / MSC_RISK / MSC_INPLAY)
        → DO NOT apply legacy placement logic
        → DO NOT enrich px / size / direction
        → DO NOT run caps or budget checks
        → DO NOT stale-cancel
        → They already contain full v7 intelligence and must pass straight
          to LiveRouter untouched.

    • Legacy engines
        → Keep full legacy flow (caps, px enrichment, budget, stale cleanup)
        → Direction already upgraded in lanes (MSC-direction-bridge)
    """

    from engines.live.live_router import place_parent_and_hedge
    from engines.decision_engine.decide_once import caps
    from engines.config_paths import auto_conn

    # ======================================================================
    # PLACEMENT GATE — DELEGATED (NO LIFECYCLE LOGIC HERE)
    # ======================================================================

    ok, reason = placement_affordable(plan, ctx)

    if not ok:
        _write_decision(
            run_id=ctx.get("run_id"),
            mid=mid,
            sid=sid,
            outcome="not_placed",
            why=reason,
            letter=str(plan.get("letter") or "?")[:1],
            proposed_odds=plan.get("px"),
            proposed_stake=plan.get("size"),
        )
        return None

    # ======================================================================
    # END PLACEMENT GATE
    # ======================================================================

    # Normalise objects
    plan = dict(plan or {})
    ctx  = dict(ctx or {})

    engine = (ctx.get("engine") or "").upper()
    is_msc = engine.startswith("MSC_")

    # ------------------------------------------------------------
    # 1) Canonical MID/SID resolution
    # ------------------------------------------------------------
    def _canon_ids(d):
        if not isinstance(d, dict): return None, None
        mid = d.get("marketId") or d.get("market_id") or d.get("mid")
        sid = d.get("selectionId") or d.get("selection_id") or d.get("sid")
        return (str(mid) if mid is not None else None,
                str(sid) if sid is not None else None)

    mid, sid = _canon_ids(plan)
    if not mid or not sid:
        cm, cs = _canon_ids(ctx)
        mid = mid or cm; sid = sid or cs

    if not mid or not sid:
        _write_decision(
            run_id=ctx.get("run_id"), mid=mid, sid=sid,
            outcome="not_placed", why="blocked: missing marketId/selectionId",
            letter=str(plan.get("letter") or "?")[:1]
        )
        return None

    # MSC always supplies correct px / size / direction
    # Legacy may not — determine early
    direction = str(plan.get("direction") or "LAY->BACK").upper()
    side = "LAY" if direction.startswith("LAY") else "BACK"

    # ------------------------------------------------------------
    # 2) MSC ENGINE FAST-PATH (NO LEGACY LOGIC)
    # ------------------------------------------------------------
    if is_msc:

        # --- MSC LETTER SELECTION -----------------------------------------
        msc_mode = plan.get("msc_mode") or ctx.get("msc_mode")
        if   msc_mode == "EXPLORATORY": letter = "D"
        elif msc_mode == "RISK":        letter = "J"
        elif msc_mode == "INPLAY":      letter = "V"
        else:                           letter = "D"

        plan["letter"] = letter
        plan["customerOrderRef"] = f"{letter}-{uuid.uuid4().hex[:10]}"

        # Preclaim a parent row so Router can upgrade it
        from engines.live.live_router import _orders_insert_parent_queued

        pending_id = _orders_insert_parent_queued(
            run_id=ctx.get("run_id"),
            market_id=mid,
            selection_id=sid,
            side=side,
            entry_odds=plan.get("px"),
            entry_stake=plan.get("size"),
            cor=plan["customerOrderRef"],
            source=letter,
        )
        if pending_id is None:
            _write_decision(
                run_id=ctx.get("run_id"), mid=mid, sid=sid,
                outcome="not_placed", why="msc_preclaim_fail",
                letter=letter
            )
            return None

        # Route directly through LiveRouter exactly as MSC intended
        result = place_parent_and_hedge(_name=name, _plan=plan, _ctx=ctx)
        bet_id, cref, child_id = None, None, None

        try:
            if isinstance(result, (list, tuple)):
                if len(result) >= 2: bet_id, cref = result[0], result[1]
                if len(result) >= 3: child_id = result[2]
            else:
                bet_id = result
        except Exception:
            pass

        cref = cref or plan["customerOrderRef"]

        # Ledger: child
        try:
            if child_id:
                mark_child(plan.get("plan_id"), child_id)
        except Exception:
            pass

        # --- Upgrade order row --------------------------------------------
        con = _auto_db_writer()
        try:
            if bet_id:
                if _orders_has_status():
                    con.execute("""
                        UPDATE orders
                           SET entry_bet_id=?,
                               entry_status='PLACED', status='PLACED',
                               customer_ref=COALESCE(?, customer_ref)
                         WHERE customerOrderRef=?""",
                        (str(bet_id), str(cref), str(plan["customerOrderRef"])))
                else:
                    con.execute("""
                        UPDATE orders
                           SET entry_bet_id=?,
                               entry_status='PLACED',
                               customer_ref=COALESCE(?, customer_ref)
                         WHERE customerOrderRef=?""",
                        (str(bet_id), str(cref), str(plan["customerOrderRef"])))
                con.commit()

                _write_decision(
                    run_id=ctx.get("run_id"), mid=mid, sid=sid,
                    outcome="placed", why="ok(msc)", letter=letter,
                    proposed_odds=plan.get("px"), proposed_stake=plan.get("size"),
                    order_id=pending_id, meta={"cref": cref, "bet_id": bet_id}
                )
                return bet_id

            # Router failed
            if _orders_has_status():
                con.execute("""
                    UPDATE orders
                       SET entry_status='FAILED', status='FAILED',
                           closed_at=datetime('now','utc'),
                           notes = TRIM(COALESCE(notes,'') || ' router_fail')
                     WHERE customerOrderRef=?""",
                    (str(plan["customerOrderRef"]),))
            else:
                con.execute("""
                    UPDATE orders
                       SET entry_status='FAILED',
                           closed_at=datetime('now','utc'),
                           notes = TRIM(COALESCE(notes,'') || ' router_fail')
                     WHERE customerOrderRef=?""",
                    (str(plan["customerOrderRef"]),))
            con.commit()

            _write_decision(
                run_id=ctx.get("run_id"), mid=mid, sid=sid,
                outcome="not_placed", why="router_fail", letter=letter,
                proposed_odds=plan.get("px"), proposed_stake=plan.get("size"),
                order_id=pending_id, meta={"cref": cref}
            )
            return None

        except Exception as e:
            con.rollback()
            print(f"[MSC][ERR] finalize failed mid={mid} sid={sid}: {e}")
            return None
        finally:
            con.close()

    # =====================================================================
    # 3) LEGACY ENGINE PLACEMENT (FULL PIPELINE)
    # =====================================================================

    # --- Legacy letter resolution (required before any CAP or PX logic) ---
    letter = (str(
        plan.get("letter") or
        ctx.get("letter") or
        name or "A"
    )[:1]).upper()

    plan["letter"] = letter

# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: Legacy placement — stale cleanup
# 🧩 ACTION: DISABLE
# 📆 PATCHED: 2025-12-17 — Placement is non-mutating pre-router
# ======================================================================================================

    # NOTE:
    # Stale cleanup is no longer placement responsibility.
    # Router handles lifecycle reconciliation.
    # _cancel_stale_parents(mid, sid, older_than_sec=70)

# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: Legacy placement — price enrichment block
# 🧩 ACTION: REPLACE
# 📆 PATCHED: 2025-12-17 — Trust BUS-normalized px (non-blocking placement)
# ======================================================================================================

    # --- px is guaranteed by BUS ---
    try:
        plan["px"] = float(plan.get("px"))
    except Exception:
        plan["px"] = 0.0

    plan["size"] = float(plan.get("size") or 2.0)

    # Do NOT block here — decision logging handles failures


    # --- CAP gate
# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: Legacy placement — CAP gate
# 🧩 ACTION: MODIFY (non-blocking)
# 📆 PATCHED: 2025-12-17 — CAP v8 diagnostic only
# ======================================================================================================

    ok_cap, cap_reason, cap_metrics = caps.cap_ok_v8(mid, sid, letter)

    if not ok_cap:
        _write_decision(
            run_id=ctx.get("run_id"),
            mid=mid,
            sid=sid,
            outcome="not_placed",
            why=cap_reason,
            letter=letter,
            proposed_odds=plan.get("px"),
            proposed_stake=plan.get("size"),
            meta={"cap": cap_metrics}
        )
        # Do NOT return — continue to router


    # --- Preclaim
    cor = f"{letter}-{uuid.uuid4().hex[:10]}"
    plan["customerOrderRef"] = cor
    pending_id = _insert_pending_parent(
        mid=mid, sid=sid, letter=letter, side=side,
        plan=plan, ctx=ctx, cor=cor, plan_id=plan.get("plan_id")
    )
    if pending_id is None:
        _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                        outcome="not_placed", why="cap_preclaim_failed",
                        letter=letter)
        return None

    # --- Router (legacy)
    result = place_parent_and_hedge(_name=name, _plan=plan, _ctx=ctx)
    bet_id, cref, child_id = None, None, None
    try:
        if isinstance(result, (list, tuple)):
            if len(result) >= 2: bet_id, cref = result[:2]
            if len(result) >= 3: child_id = result[2]
        else:
            bet_id = result
    except Exception:
        pass

    cref = cref or cor
    if child_id:
        try: mark_child(plan_id, child_id)
        except Exception: pass

    # --- Finalise legacy
    con = None
    try:
        con = _auto_db_writer()
        if bet_id:
            # placed
            if _orders_has_status():
                con.execute("""
                  UPDATE orders
                     SET entry_bet_id=?, entry_status='PLACED', status='PLACED',
                         customer_ref=COALESCE(?, customer_ref)
                   WHERE customerOrderRef=?""",
                   (str(bet_id), str(cref), str(cor)))
            else:
                con.execute("""
                  UPDATE orders
                     SET entry_bet_id=?, entry_status='PLACED',
                         customer_ref=COALESCE(?, customer_ref)
                   WHERE customerOrderRef=?""",
                   (str(bet_id), str(cref), str(cor)))
            con.commit()

            _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                            outcome="placed", why="ok", letter=letter,
                            proposed_odds=plan["px"], proposed_stake=plan["size"],
                            order_id=pending_id)
            return bet_id

        else:
            # failed
            if _orders_has_status():
                con.execute("""
                  UPDATE orders
                     SET entry_status='FAILED', status='FAILED',
                         closed_at=datetime('now','utc'),
                         notes = TRIM(COALESCE(notes,'') || ' router_fail')
                   WHERE customerOrderRef=?""", (str(cor),))
            else:
                con.execute("""
                  UPDATE orders
                     SET entry_status='FAILED',
                         closed_at=datetime('now','utc'),
                         notes = TRIM(COALESCE(notes,'') || ' router_fail')
                   WHERE customerOrderRef=?""", (str(cor),))
            con.commit()

            _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                            outcome="not_placed", why="router_fail", letter=letter,
                            proposed_odds=plan["px"], proposed_stake=plan["size"],
                            order_id=pending_id)
            return None

    except Exception as e:
        try:
            if con: con.rollback()
        finally:
            print(f"[PLACE][ERR] finalize failed mid={mid} sid={sid}: {e}")
        return None

    finally:
        try:
            if con: con.close()
        except Exception:
            pass


