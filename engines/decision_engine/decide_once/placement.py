from __future__ import annotations

import json, uuid, datetime, sqlite3
from typing import Optional
from engines.decision_engine.decide_once.helpers import open_auto_db as _adb, status_once, q_retry as _q


from engines.decision_engine.decide_once.helpers import (
    open_auto_db as _adb,
    status_once,
    q_retry as _q,   # if needed
)
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 📆 PATCHED: 2025-10-18Z — fix undefined budget_manager reference
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.risk import budget_manager
# === PATCH END ===
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 ANCHOR: just below the existing imports (after sqlite3/json/datetime)
# 📆 PATCHED: 2025-11-19 — direct writer for AUTOSCALP GUI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _auto_db_writer(timeout: float = 8.0) -> sqlite3.Connection:
    """
    Direct writable connection to AUTOSCALP_GUI (autoscalp_gui.db),
    bypassing DAL/auto_conn. Used only for orders/decisions where
    DAL has been giving us read-only connections.
    """
    from engines.config_paths import autoscalp_db
    path = autoscalp_db()
    con = sqlite3.connect(path, timeout=timeout, isolation_level=None)
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

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 📆 PATCHED: 2025-10-29Z — assign trade_index per (market, selection, letter)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.v7_shims import v7_order_shim  # ensure this import exists (safe no-op if already loaded)

def _next_trade_index(mid: str, sid: str, letter: str) -> int:
    """Return next trade_index (per marketId, selectionId, letter)."""
    try:
        import sqlite3
        from engines.config_paths import autoscalp_db
        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
        row = con.execute("""
            SELECT COUNT(*) AS n
              FROM orders
             WHERE marketId=? AND selectionId=? AND UPPER(source)=UPPER(?)
               AND date(opened_at)=date('now','utc')
        """, (str(mid), str(sid), str(letter))).fetchone()
        con.close()
        return int(row["n"] or 0) + 1
    except Exception:
        return 1
# === PATCH END ===


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
        con = _adb(); con.row_factory = sqlite3.Row
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
        con = _adb(); con.row_factory = sqlite3.Row
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
        con = _adb(); con.row_factory = sqlite3.Row
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
        con = _adb(); con.row_factory = sqlite3.Row
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

# ---------- preclaim PARENT row
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: ^def _insert_pending_parent\(
# 📆 PATCHED: 2025-11-19 — use direct writer, keep _orders_cols for schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _insert_pending_parent(*, mid: str, sid: str, letter: str, side: str,
                           plan: dict, ctx: dict, cor: str,
                           plan_id: Optional[str] = None) -> int | None:
    """
    Insert a PENDING PARENT row into orders and, if available, mark the plan_ledger parent.
    Returns the new orders.id (pending parent) or None on failure.
    """
    con = None
    try:
        cols = _orders_cols()
        if not cols:
            raise RuntimeError("orders missing")

        cref_col = "customerOrderRef" if "customerOrderRef" in cols else ("customer_ref" if "customer_ref" in cols else None)
        if not cref_col:
            raise RuntimeError("orders missing customerOrderRef/customer_ref")

        # 🔁 Use a direct writable connection to AUTOSCALP_GUI for the insert
        con = _auto_db_writer()
        cur = con.cursor()

        fields, params = [], []
        def add(col, val):
            if col in cols:
                fields.append(col); params.append(val)

        add(cref_col, str(cor))
        add("marketId", str(mid))
        add("selectionId", str(sid))

        # orders.run_id is INTEGER; tolerate strings by best-effort coercion
        if ctx.get("run_id") is not None:
            add("run_id", _i(ctx.get("run_id")))

        add("mode", str(ctx.get("mode") or "LIVE"))
        add("side", str(side))
        add("entry_odds", _f(plan.get("px"), 0.0))
        add("entry_stake", _f(plan.get("size"), 0.0))
        add("entry_status", "PENDING")
        if "status" in cols:
            add("status", "PENDING")
        add("role", "PARENT")

        # --- assign trade index for this market/runner/letter ---
        trade_index = _next_trade_index(mid, sid, letter)
        plan["trade_index"] = trade_index

        # Tag notes with per-runner sequence for audit
        if "notes" in cols:
            add("notes", f"{letter}{trade_index:02d}")

        add("source", str(letter))
        add("opened_at", datetime.datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"))

        # ensure this stays as “pre-claim” marker
        if "notes" in cols and "notes" not in fields:
            add("notes", f"pre-claim {letter}")

        sql = f"INSERT INTO orders ({', '.join(fields)}) VALUES ({', '.join(['?']*len(fields))})"
        cur.execute(sql, tuple(params))
        con.commit()
        pending_id = int(cur.lastrowid)

        # Mark the ledger now that we have the parent order id
        try:
            if plan_id and pending_id:
                mark_open_parent(plan_id, pending_id)
        except Exception:
            pass

        return pending_id

    except Exception as e:
        try:
            if con: con.rollback()
        finally:
            print(f"[CAP][preclaim] failed: {e}")
        return None
    finally:
        try:
            if con: con.close()
        except Exception:
            pass



def _cancel_stale_parents(mid: str, sid: str, *, older_than_sec: int = 70) -> None:
    con = _adb(); con.row_factory = sqlite3.Row
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH (regex): ^def place_from_plan\(name: str, plan: dict, ctx: dict\) -> Optional\[int\]:
# ⛏️ ACTION: replace the entire function with the block below
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def place_from_plan(name: str, plan: dict, ctx: dict) -> Optional[int]:
    """
    Minimal placement path:
      - trust plan (ids, price, size, direction)
      - enforce per-letter CAP
      - rotation: block same (mid,sid,letter) if an open parent exists
      - preclaim PENDING -> call live router -> PLACED/FAILED
      - always write decisions
    """
    from engines.live.live_router import place_parent_and_hedge
    from engines.decision_engine.decide_once import caps
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: def place_from_plan(
# ⛏️ ACTION: insert early guards

    # scope sanity: do not place without odds or outside of scope
    px = plan.get("px")
    if px is None or float(px) <= 0:
        # last-ditch: try odds_current
        try:
            con = _adb(ro=True); con.row_factory = sqlite3.Row
            row = _q(con, """
                SELECT ltp
                  FROM odds_current
                 WHERE marketId=? AND selectionId=?
              ORDER BY updated_ts DESC LIMIT 1
                 """, (str(plan.get("marketId")), str(plan.get("selectionId")))).fetchone()
            con.close()
            if row and row["ltp"]:
                plan["px"] = float(row["ltp"])
        except Exception:
            pass
    if float(plan.get("px") or 0) <= 0:
        _write_decision and _write_decision(run_id=ctx.get("run_id"), marketId=str(plan.get("marketId")),
            selectionId=str(plan.get("selectionId")), outcome="not_placed", why="blocked: missing px",
            letter=str(plan.get("letter") or "?")[:1], proposed_odds=None, proposed_stake=plan.get("size"), order_id=None)
        return None
# === PATCH END ===


    plan = dict(plan or {}); ctx = dict(ctx or {})
    plan_id = plan.get("plan_id")  # <- comes from mastery ledger

    # --- IDs / letter / side
    def _canon_ids(d):
        if not isinstance(d, dict): return None, None
        mid = d.get("marketId") or d.get("market_id") or d.get("mid")
        sid = d.get("selectionId") or d.get("selection_id") or d.get("sid")
        return (str(mid) if mid is not None else None, str(sid) if sid is not None else None)

    mid, sid = _canon_ids(plan)
    if mid is None or sid is None:
        cm, cs = _canon_ids(ctx)
        mid = mid or cm; sid = sid or cs

    # clear stale parents from previous passes (optional safety)
    try:
        _cancel_stale_parents(mid, sid, older_than_sec=70)
    except Exception:
        pass

    letter = (str(plan.get("letter") or ctx.get("letter") or name or "A")[:1]).upper()
    direction = str(plan.get("direction") or "LAY->BACK").upper()
    side = "LAY" if direction.startswith("LAY") else "BACK"

    # --- fundamentals
    if not mid or not sid:
        _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                        outcome="not_placed", why="blocked: missing marketId/selectionId",
                        letter=letter)
        return None

    # 🔁 ENRICH PX FROM DB IF MISSING ----------------------------------------
    # plan["px"] may be absent/0 if upstream ctx didn't carry odds; resolve from DB.
    try:
        px0 = plan.get("px") or plan.get("entry_odds")
        px = float(px0) if px0 is not None else 0.0
    except Exception:
        px = 0.0

    if px <= 0.0:
        # 1) odds_current (today)
        px_oc = _fetch_px_from_odds_current(mid, sid)
        if isinstance(px_oc, float) and px_oc > 0.0:
            plan["px"] = px = float(px_oc)
        else:
            # 2) inbound_oc_cache fallback
            px_ib = _fetch_px_from_inbound(mid, sid)
            if isinstance(px_ib, float) and px_ib > 0.0:
                plan["px"] = px = float(px_ib)

    plan["size"] = _f(plan.get("size"), 2.0)
    plan["px"]   = _f(plan.get("px"), _f(plan.get("entry_odds"), 0.0))

    if plan["px"] <= 0.0:
        # 📋 Detailed debug so we know *why* we still have no px
        debug_meta = {
            "why": "blocked: missing px",
            "mid": str(mid), "sid": str(sid), "letter": letter,
            "ctx_odds": ctx.get("odds"), "ctx_ltp": ctx.get("ltp"),
            "plan_px": plan.get("px"), "entry_odds": plan.get("entry_odds"),
            "oc_probe": bool(_fetch_px_from_odds_current(mid, sid) is not None),
            "inbound_probe": bool(_fetch_px_from_inbound(mid, sid) is not None),
        }
        _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                        outcome="not_placed", why="blocked: missing px",
                        letter=letter, proposed_odds=None, proposed_stake=plan.get("size"),
                        meta=debug_meta)
        return None
    # ------------------------------------------------------------------------

    plan["size"] = _f(plan.get("size"), 2.0)
    plan["px"]   = _f(plan.get("px"), _f(plan.get("entry_odds"), 0.0))
    if plan["px"] <= 0.0:
        _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                        outcome="not_placed", why="blocked: missing px",
                        letter=letter)
        return None

    # --- CAP gate
    ok_cap, cap_reason, cap_metrics = caps.cap_ok(mid, sid, letter, side=side)
    if not ok_cap:
        _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                        outcome="not_placed", why=cap_reason, letter=letter,
                        proposed_odds=plan["px"], proposed_stake=plan["size"],
                        meta={"cap": cap_metrics})
        return None

    # --- Rotation (handled earlier in lanes; no hard block here)

    # --- Preclaim PENDING parent (now passes plan_id so helper can mark ledger)
    cor = f"{letter}-{uuid.uuid4().hex[:10]}"
    plan["customerOrderRef"] = cor
    pending_id = _insert_pending_parent(
        mid=mid, sid=sid, letter=letter, side=side,
        plan=plan, ctx=ctx, cor=cor, plan_id=plan_id
    )
    if pending_id is None:
        _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                        outcome="not_placed", why="cap_preclaim_failed",
                        letter=letter)
        return None

    # Optional pass stamp
    try:
        plan["pass_no"] = int(caps.pass_no_for(mid, sid, letter))
    except Exception:
        pass

    # ensure hedge distance is present (router will place child at parent ± ticks)
    if "hedge_ticks" not in plan or not plan["hedge_ticks"]:
        plan["hedge_ticks"] = int(plan.get("target_ticks") or 1)

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 📆 PATCHED: 2025-10-18T12:05Z — fix stray return indentation inside place_from_plan()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Budget enforcement check
        try:
            if not budget_manager.authorise(plan):
                _write_decision(
                    run_id=ctx.get("run_id"),
                    mid=mid,
                    sid=sid,
                    outcome="not_placed",
                    why="blocked: over liability limit",
                    letter=letter,
                    proposed_odds=plan["px"],
                    proposed_stake=plan["size"],
                    meta={"budget": "liability > limit"},
                )
                print(f"[BUDGET] Blocked mid={mid} sid={sid} – liability over limit")
                return None
        except Exception as e:
            print(f"[BUDGET] warn: enforcement check failed mid={mid} sid={sid}: {e}")


    # --- Route to live placement
    # NOTE: place_parent_and_hedge currently returns (parent_bet_id, cref). If/when it returns a child id, call mark_child().
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/placement.py
# 🔎 SEARCH: bet_id, cref = place_parent_and_hedge(_name=name, _plan=plan, _ctx=ctx)
# ⛏️ ACTION: replace that single line with this block

    result = place_parent_and_hedge(_name=name, _plan=plan, _ctx=ctx)
    bet_id, cref, child_id = None, None, None

    # Backward-compat: router may return (parent_id, cref) OR (parent_id, cref, child_id)
    try:
        if isinstance(result, (list, tuple)):
            if len(result) >= 2:
                bet_id, cref = result[0], result[1]
            if len(result) >= 3:
                child_id = result[2]
        else:
            bet_id = result
    except Exception:
        pass

    cref = cref or cor

    # If router gave us a child ID, mark it in the plan ledger
    try:
        if child_id:
            mark_child(plan_id, child_id)
    except Exception as e:
        print(f"[PLACE][WARN] mark_child failed mid={mid} sid={sid}: {e}")
# === PATCH END ===

    cref = cref or cor

    # --- Upgrade order row based on exchange result
    con = None
    try:
        # 🔁 Use direct writer to AUTOSCALP_GUI to avoid readonly DAL issues
        con = _auto_db_writer()
        if bet_id:
            # ✅ we have an exchange order – upgrade to PLACED and attach bet id
            if _orders_has_status():
                con.execute("""
                  UPDATE orders
                     SET entry_bet_id=?,
                         entry_status='PLACED', status='PLACED',
                         customer_ref=COALESCE(?, customer_ref)
                   WHERE customerOrderRef=?""",
                   (str(bet_id), str(cref or cor), str(cor)))
            else:
                con.execute("""
                  UPDATE orders
                     SET entry_bet_id=?,
                         entry_status='PLACED',
                         customer_ref=COALESCE(?, customer_ref)
                   WHERE customerOrderRef=?""",
                   (str(bet_id), str(cref or cor), str(cor)))
            con.commit()
            _write_decision(run_id=ctx.get("run_id"), mid=mid, sid=sid,
                            outcome="placed", why="ok", letter=letter,
                            proposed_odds=plan["px"], proposed_stake=plan["size"],
                            order_id=pending_id, meta={"cref": cref or cor, "bet_id": bet_id})
            return bet_id  # success
        else:
            # ❌ no bet id returned – mark FAILED (do NOT call this 'PLACED')
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
                            order_id=pending_id, meta={"cref": cref or cor})
            return None
    except Exception as e:
        try:
            if con: con.rollback()
        finally:
            print(f"[PLACE][ERR] finalize failed: {e}")
        return None
    finally:
        try:
            if con: con.close()
        except Exception:
            pass


