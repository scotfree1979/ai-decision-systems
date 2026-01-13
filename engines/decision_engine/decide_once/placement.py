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
# ======================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: def _placement_worker_loop():
# 🧩 ACTION: ADD (DB-queue consumption before in-memory queue)
# 📆 PATCHED: 2025-12-30 — Restore DB → Placement execution bridge
#
# RATIONALE:
# Parents are correctly inserted as entry_status='QUEUED'.
# The placement worker must consume DB-queued parents one-by-one.
# Without this, execution never begins.
# ======================================================================================================

def _placement_worker_loop():
    from engines.live.live_router import place_parent_and_hedge
    from engines.config_paths import open_auto_db
    import sqlite3, time, traceback

    while True:
        try:
            # --------------------------------------------------
            # 1️⃣ DB-FIRST: consume ONE queued parent
            # --------------------------------------------------
            con = open_auto_db(rw=True)
            con.row_factory = sqlite3.Row

            # Attach BETS DB (required for marketStartTime)
            try:
                from engines.config_paths import bets_db
                con.execute(
                    f"ATTACH DATABASE '{bets_db()}' AS bets"
                )
            except Exception:
                pass

            row = con.execute(
                """
                SELECT
                    o.customerOrderRef,
                    o.marketId,
                    o.selectionId,
                    o.side,
                    o.entry_odds,
                    o.entry_stake,
                    o.engine,
                    o.source,
                    o.run_id,
                    o.required_exposure
                FROM orders o
                LEFT JOIN bets.bets b
                  ON b.marketId = o.marketId
                WHERE o.role = 'PARENT'
                  AND o.entry_status = 'QUEUED'
                ORDER BY
                    CASE o.engine
                        WHEN 'MSC_INPLAY' THEN 1
                        WHEN 'MSC_RISK' THEN 2
                        WHEN 'LEGACY' THEN 3
                        WHEN 'MSC_EXPLORATORY' THEN 4
                        ELSE 9
                    END,
                    ABS(
                        strftime('%s', b.marketStartTime) -
                        strftime('%s', 'now')
                    ) ASC,
                    o.opened_at ASC
                LIMIT 1
                """
            ).fetchone()

            if row:
                try:
                    from engines.decision_engine.decide_once.scope import _SCOPE_STATE
                    in_play_markets = set(_SCOPE_STATE.get("in_play", []))
                    if row["marketId"] in in_play_markets:
                        con.close()
                        time.sleep(0.05)
                        continue
                except Exception:
                    pass

            if row:
                # Mark as PLACING immediately to avoid double-pick
                con.execute(
                    """
                    UPDATE orders
                       SET entry_status='PLACING'
                     WHERE customerOrderRef=?
                    """,
                    (row["customerOrderRef"],)
                )
                con.commit()
                con.close()

                from engines.live.bank_state import on_parent_placed

                # Execute parent (THIS MUST STAY FIRST)
                bet_id = place_parent_and_hedge(
                    market_id=row["marketId"],
                    selection_id=row["selectionId"],
                    side=row["side"],
                    entry_odds=row["entry_odds"],
                    stake=row["entry_stake"],
                    source=row["source"],
                    run_id=row["run_id"],
                    parent_persistence="LAPSE",
                    _name="PLACEMENT_WORKER",
                    _plan={
                        "customerOrderRef": row["customerOrderRef"],
                        "engine": row["engine"],
                    },
                    _ctx={
                        "customerOrderRef": row["customerOrderRef"],
                        "engine": row["engine"],
                    },
                )

                # Loop immediately (one-by-one semantics)
                continue

            con.close()

            # --------------------------------------------------
            # 2️⃣ FALLBACK: in-memory queue (unchanged)
            # --------------------------------------------------
            name, plan, ctx = _PLACEMENT_EXEC_QUEUE.get()
            place_from_plan(name, plan, ctx)

        except Exception:
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
def placement_affordable(plan: dict, ctx: dict) -> tuple[bool, str, float]:
    from engines.live import bank_state

    engine = ctx.get("engine")
    if not engine:
        raise RuntimeError("PLACEMENT INVARIANT VIOLATION: engine missing")

    try:
        stake = float(plan.get("size") or 0.0)
        odds  = float(plan.get("px") or 0.0)
        side  = str(plan.get("side") or "").upper()

        if stake <= 0 or odds <= 0:
            return False, "invalid_stake_or_odds", 0.0

        if side == "LAY":
            parent_liab = stake * max(odds - 1.0, 0.0)
            child_liab  = stake
        else:
            parent_liab = stake
            child_liab  = stake * max(odds - 1.0, 0.0)

        required = parent_liab + child_liab
        available = bank_state.get_engine_available(engine)

        if available < required:
            return False, "insufficient_engine_budget", required

        return True, "ok", required

    except Exception as e:
        return False, f"gate_error:{e}", 0.0


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
    Placement enqueue (canonical boundary).

    CONTRACT:
    - Shape-only canonicalization
    - DB-first parent preclaim
    - No enrichment, no gating, no decisions
    """

    # ------------------------------------------------------------------
    # Ensure worker is running (idempotent)
    # ------------------------------------------------------------------
    try:
        start_placement_worker()
    except Exception as e:
        print(f"[PLACEMENT][WARN] worker start failed: {e}")

    # ------------------------------------------------------------------
    # 🔑 MINIMAL CANONICALIZATION (SHAPE ONLY)
    # ------------------------------------------------------------------
    plan = dict(plan or {})
    ctx  = dict(ctx or {})

    # IDs
    plan["marketId"]    = plan.get("marketId")    or ctx.get("marketId")
    plan["selectionId"] = plan.get("selectionId") or ctx.get("selectionId")

    if not plan["marketId"] or not plan["selectionId"]:
        print("[PLACEMENT][DROP] missing marketId/selectionId")
        return

    # Engine (authoritative from BUS)
    engine = plan.get("engine")
    if not engine:
        print("[PLACEMENT][DROP] missing engine")
        return
    plan["engine"] = str(engine)

    # Letter / source (audit + DB)
    letter = (
        plan.get("letter")
        or ctx.get("letter")
        or plan["engine"][:1]
    )
    plan["letter"] = str(letter)[:1].upper()

    # Direction → side
    direction = str(plan.get("direction") or "LAY->BACK").upper()
    plan["side"] = "LAY" if direction.startswith("LAY") else "BACK"

    # px / size (shape only)
    try:
        plan["px"] = float(plan.get("px"))
        plan["size"] = float(plan.get("size"))
    except Exception:
        print("[PLACEMENT][DROP] invalid px/size")
        return

    # customerOrderRef (stable identity)
    if not plan.get("customerOrderRef"):
        plan["customerOrderRef"] = f"{plan['letter']}-{uuid.uuid4().hex[:10]}"

    engine = plan.get("engine") or ctx.get("engine")
    if not engine:
        print("[PLACEMENT][DROP] missing engine at placement boundary")
        return

    plan["engine"] = engine
    ctx["engine"] = engine

    # ------------------------------------------------------------------
    # 🔒 AFFORDABILITY GATE (THIS IS THE FIX)
    # ------------------------------------------------------------------
    ok, _, required = placement_affordable(plan, ctx)
    if not ok:
        return

    plan["required_exposure"] = required
    # ------------------------------------------------------------------
    # 🔑 DB-FIRST PARENT PRECLAIM (AUTHORITATIVE)
    # ------------------------------------------------------------------
    try:
        pending_id = _insert_pending_parent(
            run_id=ctx.get("run_id"),
            market_id=str(plan["marketId"]),
            selection_id=str(plan["selectionId"]),
            side=plan["side"],
            entry_odds=plan["px"],
            entry_stake=plan["size"],
            cor=plan["customerOrderRef"],
            engine=plan["engine"],
            source=plan["letter"],
            ctx=ctx,
            plan=plan,
        )


        if pending_id is None:
            print("[PLACEMENT][DROP] parent preclaim failed")
            return

        plan["parent_id"] = pending_id

    except Exception as e:
        print(f"[PLACEMENT][DROP] preclaim error: {e}")
        return

    # ------------------------------------------------------------------
    # NON-BLOCKING EXECUTION QUEUE
    # ------------------------------------------------------------------
    try:
        _PLACEMENT_EXEC_QUEUE.put_nowait((name, plan, ctx))
    except Exception as e:
        print(f"[PLACEMENT][DROP] enqueue failed after preclaim: {e}")


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
    con = _auto_db_writer()
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
            if side.upper() == "LAY":
                parent_liab = float(entry_stake) * max(float(entry_odds) - 1.0, 0.0)
                child_liab  = float(entry_stake)
            else:
                parent_liab = float(entry_stake)
                child_liab  = float(entry_stake) * max(float(entry_odds) - 1.0, 0.0)

            required_exposure = round(parent_liab + child_liab, 2)

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
                    entry_status,
                    role,
                    source,
                    engine,
                    stoploss_mode,
                    notes,
                    opened_at
                )
                VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'QUEUED', 'PARENT',
                    ?, ?, ?, ?, ?
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
                    float(plan["required_exposure"]),
                    str(letter),
                    str(engine),
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

    # Affordability gate (shared)
    ok, _, required = placement_affordable(plan, ctx)
    if not ok:
        return None

    plan["required_exposure"] = required

    # 🔑 SINGLE ACTION
    enqueue_for_placement(name, plan, ctx)
    return None



