# CAPS MODULE (minimal, schema-aware)
from __future__ import annotations

import sqlite3
from typing import Optional, Tuple, Dict

# ---- DB helpers --------------------------------------------------------------

def _db_path() -> str:
    try:
        from engines.config_paths import autoscalp_db
        return autoscalp_db()
    except Exception:
        from engines.config_paths import autoscalp_db
        return autoscalp_db()

def _orders_has_status() -> bool:
    """True if orders has a 'status' column (newer schema)."""
    from engines.decision_engine.decide_once.helpers import open_auto_db as _adb
    con = None
    try:
        con = _adb(); con.row_factory = sqlite3.Row
        cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
        return "status" in cols
    except Exception:
        return False
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

# === DROP IN: engines/caps.py ================================================

from typing import Tuple, Optional, Dict, Any
import sqlite3, time

# You likely already have this in helpers; keep a local safe version.
def _q_retry(con: sqlite3.Connection, sql: str, params: tuple = (), tries: int = 6, delay_s: float = 0.08):
    last = None
    for i in range(max(1, tries)):
        try:
            cur = con.cursor(); cur.execute(sql, params); return cur
        except sqlite3.OperationalError as e:
            last = e
            if "locked" in str(e).lower() and i < tries - 1:
                time.sleep(delay_s * (i + 1)); continue
            raise
    raise last  # pragma: no cover

def _tbl_cols(con: sqlite3.Connection, tbl: str) -> set[str]:
    try:
        return { (r[1] if isinstance(r, tuple) else r["name"]) for r in _q_retry(con, f"PRAGMA table_info({tbl})").fetchall() }
    except Exception:
        return set()

def _has_tbl(con: sqlite3.Connection, name: str) -> bool:
    return bool(_q_retry(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

def _parent_pred(cols: set[str]) -> str:
    # Prefer role, else link‑based inference
    if "role" in cols:
        return "role='PARENT'"
    if "hedge_of" in cols:
        return "(hedge_of IS NULL OR hedge_of='')"
    if "parent_id" in cols:
        return "(parent_id IS NULL)"
    return "1=1"  # worst‑case

def _child_pred(cols: set[str]) -> str:
    if "role" in cols:
        return "role='CHILD'"
    if "hedge_of" in cols:
        return "(hedge_of IS NOT NULL AND hedge_of<>'')"
    if "parent_id" in cols:
        return "(parent_id IS NOT NULL)"
    return "0=1"

def _status_expr(cols: set[str], which: str) -> str:
    # returns a safe UPPER() expression or 'NULL'
    if which in cols:
        return f"UPPER(COALESCE({which},''))"
    return "NULL"

def is_100_matched(con: sqlite3.Connection, parent_id: int) -> bool:
    """
    True if a given parent has exit matched child recorded.
    Works with either role/hedge_of/parent_id schemas.
    """
    cols = _tbl_cols(con, "orders")
    if not cols: return False
    # child link
    if "hedge_of" in cols:
        link = "hedge_of"
        q = f"SELECT 1 FROM orders WHERE {link}=? AND {_status_expr(cols,'entry_status')}='MATCHED' AND {_status_expr(cols,'exit_status')}='MATCHED' LIMIT 1"
        row = _q_retry(con, q, (parent_id,)).fetchone()
        return bool(row)
    if "parent_id" in cols:
        link = "parent_id"
        q = f"SELECT 1 FROM orders WHERE {link}=? AND {_status_expr(cols,'entry_status')}='MATCHED' AND {_status_expr(cols,'exit_status')}='MATCHED' LIMIT 1"
        row = _q_retry(con, q, (parent_id,)).fetchone()
        return bool(row)
    # last‑ditch: if we cannot link, require parent itself closed w/ matched exit (not ideal)
    q = f"SELECT 1 FROM orders WHERE id=? AND {_status_expr(cols,'exit_status')}='MATCHED' LIMIT 1"
    row = _q_retry(con, q, (parent_id,)).fetchone()
    return bool(row)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/caps.py
# 🔎 SEARCH (regex): ^def is_100_matched\(con: sqlite3\.Connection, parent_id: int\) -> bool:
#    INSERT the following block **immediately below** that function
# 📆 PATCHED: 2025-09-29T14:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def is_100_matched_with_kind(con: sqlite3.Connection, parent_id: int) -> tuple[bool, str]:
    """
    100% classifier:
      • Returns (True, 'HEDGE'|'STOPLOSS'|'UNKNOWN') if parent fully closed.
      • Returns (False, 'NONE') if not 100% yet.
    Heuristics (in priority order):
      1) CHILD.exit_kind if present
      2) realized_pnl sign (>=0 → HEDGE, <0 → STOPLOSS)
      3) odds geometry:
         - LAY parent: BACK exit @ higher odds → HEDGE, else STOPLOSS
         - BACK parent: LAY exit @ lower  odds → HEDGE, else STOPLOSS
    """
    try:
        cols = _tbl_cols(con, "orders")
        if not cols:
            return (False, "NONE")

        # fetch parent core fields
        row = _q_retry(con, """
            SELECT id, UPPER(COALESCE(role,'')) AS role,
                   UPPER(COALESCE(entry_status,'')) AS e_stat,
                   UPPER(COALESCE(exit_status,''))  AS x_stat,
                   UPPER(COALESCE(side,'')) AS side,
                   entry_odds, exit_odds,
                   realized_pnl
            FROM orders WHERE id=? LIMIT 1
        """, (int(parent_id),)).fetchone()
        if not row:
            return (False, "NONE")

        # not fully closed?
        if (row["x_stat"] or "") != "MATCHED":
            return (False, "NONE")

        # 1) look at the last child linked to this parent (if any)
        child = _q_retry(con, """
            SELECT UPPER(COALESCE(exit_kind,'')) AS exit_kind,
                   UPPER(COALESCE(side,''))      AS side,
                   entry_odds
            FROM orders
            WHERE role='CHILD' AND hedge_of=?
            ORDER BY id DESC
            LIMIT 1
        """, (int(parent_id),)).fetchone()

        if child and (child["exit_kind"] or "") in ("HEDGE", "STOPLOSS"):
            return (True, child["exit_kind"])

        # 2) realized P&L sign
        try:
            rp = float(row["realized_pnl"] if row["realized_pnl"] is not None else 0.0)
            if rp > 1e-9:
                return (True, "HEDGE")
            if rp < -1e-9:
                return (True, "STOPLOSS")
        except Exception:
            pass

        # 3) geometry fallback
        try:
            parent_side = (row["side"] or "").upper()
            E = float(row["entry_odds"] or 0.0)
            X = float(row["exit_odds"]  or 0.0)
            if E > 0.0 and X > 0.0:
                if parent_side == "LAY":
                    return (True, "HEDGE" if X > E + 1e-9 else "STOPLOSS")
                else:  # BACK
                    return (True, "HEDGE" if X < E - 1e-9 else "STOPLOSS")
        except Exception:
            pass

        return (True, "UNKNOWN")
    except Exception:
        return (False, "NONE")


def is_parent_100_with_kind_by_cor(con: sqlite3.Connection, parent_cor: str) -> tuple[bool, str]:
    """
    Convenience: resolve parent_id by customerOrderRef then delegate to is_100_matched_with_kind.
    """
    try:
        row = _q_retry(con, "SELECT id FROM orders WHERE customerOrderRef=? LIMIT 1", (str(parent_cor),)).fetchone()
        if not row:
            return (False, "NONE")
        return is_100_matched_with_kind(con, int(row["id"]))
    except Exception:
        return (False, "NONE")


def _open_parents_for_letter(con: sqlite3.Connection, letter: str, mode: Optional[str]) -> list[Dict[str,Any]]:
    cols = _tbl_cols(con, "orders")
    if not cols: return []
    where_parent = _parent_pred(cols)
    where_mode   = ("AND UPPER(COALESCE(mode,''))=UPPER(?)" if ("mode" in cols and mode) else "")
    where_letter = ("AND UPPER(COALESCE(source,'')) LIKE UPPER(?)" if "source" in cols else "")
    params: list = []
    sql = f"""
      SELECT id, marketId, selectionId, COALESCE(source,'') AS src,
             { _status_expr(cols,'entry_status') } AS e_stat,
             { _status_expr(cols,'exit_status') }  AS x_stat
        FROM orders
       WHERE {where_parent}
         AND { _status_expr(cols,'entry_status') }='MATCHED'
         AND ({ _status_expr(cols,'exit_status') } IS NULL OR { _status_expr(cols,'exit_status') }<>'MATCHED')
         {where_mode} {where_letter}
       ORDER BY id ASC
    """
    if ("mode" in cols and mode): params.append(mode)
    if "source" in cols:          params.append(f"{letter}%")
    rows = [dict(zip([c[0] for c in _q_retry(con, "PRAGMA table_info(orders)").fetchall()], []))]  # dummy for IDEs
    rows = [ { "id": r[0], "marketId": str(r[1]), "selectionId": str(r[2]), "src": r[3], "e_stat": r[4], "x_stat": r[5]} 
             for r in _q_retry(con, sql, tuple(params)).fetchall() ]
    return rows

def letter_can_place(con: sqlite3.Connection, *, letter: str, mode: str = "LIVE", max_active: int = 1) -> Tuple[bool, str]:
    """
    Enforce: do not place a new parent for a letter if there exists any
    parent with entry MATCHED but child not MATCHED (i.e., not 100% yet).
    """
    try:
        open_parents = _open_parents_for_letter(con, letter, mode)
        if not open_parents:
            return (True, "")
        # any not 100%?
        for p in open_parents:
            if not is_100_matched(con, int(p["id"])):
                return (False, f"letter_slot_busy:{letter}")
        # all 100% (should be closed already)… allow
        return (True, "")
    except Exception as e:
        # fail‑open to avoid total stalls, but tag reason
        return (True, f"caps_err:{type(e).__name__}")
# === END: engines/caps.py =====================================================


# ---- Open counts -------------------------------------------------------------
# === PATCH START ===
# 📍 TARGET: engines/caps.py:open_parents_letter
# 🔎 SEARCH: def open_parents_letter(
# 📆 PATCHED: 2025-11-21 — DAL RW for orders lookup

from engines.config_paths import auto_conn as _auto_conn

def open_parents_letter(market: str, sel: str, letter: str) -> int:
    """
    Count open PARENT orders today for (market, selection, letter).
    Uses DAL-safe RW connection to autoscalp_gui.db.
    """
    has_status = _orders_has_status()
    if has_status:
        open_pred = "((status IN ('PENDING','PLACED')) AND (closed_at IS NULL OR closed_at=''))"
        not_cancel = "COALESCE(status, entry_status, '') NOT IN ('CANCELLED','VOIDED','LAPSED')"
        child_match = "COALESCE(exit_status, entry_status, status, '')"
    else:
        open_pred = "((entry_status IN ('PENDING','PLACED')) AND (closed_at IS NULL OR closed_at=''))"
        not_cancel = "COALESCE(entry_status, '') NOT IN ('CANCELLED','VOIDED','LAPSED')"
        child_match = "COALESCE(exit_status, entry_status, '')"

    sql = f"""
      WITH parents AS (
        SELECT id
        FROM orders
        WHERE marketId=? AND selectionId=? AND source=? AND hedge_of IS NULL
          AND {not_cancel}
          AND {open_pred}
          AND date(opened_at)=date('now','utc')
      ),
      hedged AS (
        SELECT DISTINCT parentId FROM (
          SELECT id AS parentId
          FROM orders
          WHERE marketId=? AND selectionId=? AND source=? AND hedge_of IS NULL
            AND closed_at IS NOT NULL
            AND date(opened_at)=date('now','utc')
          UNION
          SELECT hedge_of AS parentId
          FROM orders
          WHERE marketId=? AND selectionId=? AND source=? AND hedge_of IS NOT NULL
            AND UPPER({child_match}) IN ('EXECUTION_COMPLETE','EXECUTED','MATCHED')
            AND date(opened_at)=date('now','utc')
        )
      )
      SELECT COUNT(*) AS n_open
      FROM parents p
      LEFT JOIN hedged h ON h.parentId = p.id
      WHERE h.parentId IS NULL
    """

    params = (
        str(market), str(sel), str(letter),
        str(market), str(sel), str(letter),
        str(market), str(sel), str(letter),
    )

    con = _auto_conn(rw=True); con.row_factory = sqlite3.Row
    try:
        return int(con.execute(sql, params).fetchone()["n_open"])
    finally:
        con.close()
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/caps.py:total_parents_letter
# 📆 PATCHED: 2025-11-21 — DAL RW

from engines.config_paths import auto_conn as _auto_conn

def total_parents_letter(market: str, sel: str, letter: str) -> int:
    has_status = _orders_has_status()
    not_cancel = "COALESCE(status, entry_status, '') NOT IN ('CANCELLED','VOIDED','LAPSED')" if has_status \
                 else "COALESCE(entry_status, '') NOT IN ('CANCELLED','VOIDED','LAPSED')"

    sql = f"""
      SELECT COUNT(*) AS n
      FROM orders
      WHERE marketId=? AND selectionId=? AND source=? AND hedge_of IS NULL
        AND date(opened_at)=date('now','utc')
        AND {not_cancel}
    """

    con = _auto_conn(rw=True); con.row_factory = sqlite3.Row
    try:
        return int(con.execute(sql, (str(market), str(sel), str(letter))).fetchone()["n"])
    finally:
        con.close()
# === PATCH END ===


def pass_no_for(market: str, sel: str, letter: str, per_letter_cap: int = 3) -> int:
    """
    Deterministic pass number for (market, sel, letter) today:
    pass = floor(total/ cap) + 1
    """
    total = total_parents_letter(market, sel, letter)
    return (total // max(1, per_letter_cap)) + 1

# ---- CAP gate ---------------------------------------------------------------
# === PATCH START ===
# 📍 TARGET: engines/caps.py:cap_ok
# 📆 PATCHED: 2025-11-21 — DAL RW

from engines.config_paths import auto_conn as _auto_conn

def cap_ok(market_id: str, selection_id: str, letter: str, *, side: str | None = None):
    """
    Simple per-runner, per-letter open cap (DAL-safe).
    """
    has_status = _orders_has_status()
    if has_status:
        open_pred = "((status IN ('PENDING','PLACED')) AND (closed_at IS NULL OR closed_at=''))"
    else:
        open_pred = "((entry_status IN ('PENDING','PLACED')) AND (closed_at IS NULL OR closed_at=''))"

    con = _auto_conn(rw=True); con.row_factory = sqlite3.Row
    try:
        row = con.execute(f"""
            SELECT
              SUM(CASE WHEN source=? THEN 1 ELSE 0 END) AS open_per_letter,
              COUNT(*) AS open_total
            FROM orders
            WHERE {open_pred}
              AND marketId=? AND selectionId=?
        """, (str(letter), str(market_id), str(selection_id))).fetchone()

        open_per_letter = int(row["open_per_letter"] or 0)
        open_total = int(row["open_total"] or 0)

        ok = (open_per_letter < 3)
        reason = "cap_ok" if ok else "cap_block: per-letter limit reached"
        metrics = {"open_per_letter": open_per_letter, "open_total": open_total}
        return ok, reason, metrics
    finally:
        con.close()
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/caps.py:cap_ok_v7
# 📆 PATCHED: 2025-11-21 — DAL RW

from engines.config_paths import auto_conn as _auto_conn

def cap_ok_v7(market_id: str, selection_id: str, letter: str, *, mode: str = "LIVE") -> tuple[bool, str, dict]:
    db = _db_path()
    con = _auto_conn(rw=True); con.row_factory = sqlite3.Row
    try:
        has_v7 = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v_orders_v7'"
        ).fetchone())

        if has_v7:
            row = con.execute("""
                SELECT COUNT(*) AS open_per_letter
                  FROM v_orders_v7
                 WHERE marketId=? AND selectionId=? AND letter=?
                   AND (exit_status IS NULL OR exit_status<>'MATCHED')
                   AND entry_status IN ('PLACED','PENDING','MATCHED')
            """, (str(market_id), str(selection_id), str(letter))).fetchone()

            open_per_letter = int(row["open_per_letter"] or 0)
            ok = (open_per_letter < 3)
            reason = "cap_ok_v7" if ok else "cap_block: per-letter limit reached"
            return ok, reason, {"open_per_letter": open_per_letter}

        return cap_ok(market_id, selection_id, letter, side=None)
    except Exception as e:
        return True, f"cap_bridge_err:{type(e).__name__}", {}
    finally:
        con.close()
# === PATCH END ===

# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/caps.py
# 🔎 SEARCH: def cap_ok_v8(
# 📆 PATCHED: 2025-12-02 — expand cap check to MSC letters D/J/V
# ---------------------------------------------------------------------------

def cap_ok_v8(market_id: str, selection_id: str, letter: str,
              *, mode: str = "LIVE", cap_limit: int = 3) -> tuple[bool,str,dict]:

    # normalise + accept MSC letters
    letter = str(letter).upper()
    if letter not in ("A","B","C","D","E","F","G","H","I","J","K","L","P","R","S","T","V","X","Z"):
        return True, "cap_skip_unknown_letter", {}

    con = _auto_conn(rw=True); con.row_factory = sqlite3.Row
    try:
        q = f"""
        SELECT COUNT(*) AS active
          FROM orders p
         WHERE role='PARENT'
           AND UPPER(COALESCE(entry_status,'')) IN ('PLACED','MATCHED')
           AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
           AND NOT EXISTS (
                 SELECT 1 FROM orders c
                  WHERE c.hedge_of=p.id
                    AND UPPER(COALESCE(c.exit_status,''))='MATCHED'
             )
           AND UPPER(COALESCE(source,'')) LIKE UPPER(?)
           AND date(p.opened_at)=date('now','utc')
           AND UPPER(COALESCE(p.mode,''))=UPPER(?)
           AND p.marketId=? AND p.selectionId=?
        """

        row = con.execute(q, (letter, mode, str(market_id), str(selection_id))).fetchone()
        active = int(row["active"] or 0)

        ok = active < cap_limit
        reason = "cap_ok" if ok else f"cap_block {letter}: {active}/{cap_limit}"
        return ok, reason, {"active": active, "cap_limit": cap_limit}

    except Exception as e:
        return True, f"cap_v8_err:{type(e).__name__}", {}

    finally:
        con.close()

# === PATCH END ==============================================================




