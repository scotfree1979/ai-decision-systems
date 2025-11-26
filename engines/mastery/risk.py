from __future__ import annotations
# === TRIPLE-HEADER PATCH ======================================================
# 📍 TARGET: engines/mastery/risk.py
# 🔎 SEARCH: def derive_stops(post: Dict[str, Any]) -> Tuple[int, int]:
# ============================================================================

# === DROP IN: engines/risk.py ================================================

from typing import Any, Dict, Tuple, Optional, List
import sqlite3, time, json
from datetime import datetime, timezone

# === PATCH START ===
# 📍 TARGET: engines/mastery/risk.py
# 🔎 SEARCH: import sqlite3
# ⛏️ ACTION: add ensure_book_state_table() right after imports

def _ensure_book_state_table(con: sqlite3.Connection) -> None:
    """
    Ensure the dashboard_book_state table exists.
    Columns: day (PK), open_parents, open_children, liability, updated_at
    """
    con.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_book_state(
          day TEXT PRIMARY KEY,
          open_parents INTEGER DEFAULT 0,
          open_children INTEGER DEFAULT 0,
          liability REAL DEFAULT 0.0,
          updated_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)
    con.commit()
# === PATCH END ===


# ---- sqlite helpers ----------------------------------------------------------
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
    raise last

def _has(con: sqlite3.Connection, tbl: str) -> bool:
    return bool(_q_retry(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)).fetchone())

def _cols(con: sqlite3.Connection, tbl: str) -> set[str]:
    try: return { (r[1] if isinstance(r,tuple) else r["name"]) for r in _q_retry(con, f"PRAGMA table_info({tbl})").fetchall() }
    except Exception: return set()

def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# ---- app_kv for internal bank + flags ---------------------------------------
def ensure_app_kv(con: sqlite3.Connection) -> None:
    _q_retry(con, """
      CREATE TABLE IF NOT EXISTS app_kv(
        k TEXT PRIMARY KEY,
        v TEXT,
        updated_ts TEXT
      )
    """); con.commit()

def get_kv(con: sqlite3.Connection, k: str, default: Optional[str] = None) -> Optional[str]:
    row = _q_retry(con, "SELECT v FROM app_kv WHERE k=?", (k,)).fetchone()
    return (row[0] if row else default)

def set_kv(con: sqlite3.Connection, k: str, v: str) -> None:
    _q_retry(con, "INSERT INTO app_kv(k,v,updated_ts) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, updated_ts=excluded.updated_ts",
             (k, v, now_iso()))
    con.commit()

# Public helpers for the router/feeder
def set_insufficient_funds_since(con: sqlite3.Connection) -> None:
    ensure_app_kv(con); set_kv(con, "insuf_since_ts", now_iso())

def clear_insufficient_funds(con: sqlite3.Connection) -> None:
    ensure_app_kv(con); set_kv(con, "insuf_since_ts", "")

def insuf_active(con: sqlite3.Connection) -> bool:
    ensure_app_kv(con); return bool(get_kv(con, "insuf_since_ts", ""))

def ensure_internal_bank(con: sqlite3.Connection, *, seed_amount: Optional[float] = None) -> None:
    """
    Create bank if missing (e.g., at first launch each day).
    Caller can seed with live balance.
    """
    ensure_app_kv(con)
    if get_kv(con, "internal_bank", None) is None:
        set_kv(con, "internal_bank", str(float(seed_amount or 0.0)))

def read_internal_bank(con: sqlite3.Connection) -> float:
    ensure_app_kv(con)
    try: return float(get_kv(con, "internal_bank", "0.0") or 0.0)
    except Exception: return 0.0

def bump_internal_bank(con: sqlite3.Connection, delta: float) -> None:
    v = read_internal_bank(con) + float(delta or 0.0)
    set_kv(con, "internal_bank", f"{v:.2f}")

# ---- book_state (dashboard) --------------------------------------------------
def ensure_book_state_schema(con: sqlite3.Connection) -> None:
    _q_retry(con, """
      CREATE TABLE IF NOT EXISTS dashboard_book_state(
        day TEXT PRIMARY KEY,
        open_parents INTEGER,
        open_children INTEGER,
        open_liability REAL,
        open_by_letter_json TEXT,
        open_by_market_json TEXT,
        top_risk_json TEXT,
        last_refreshed_ts TEXT
      )
    """); con.commit()

def _s(colset: set[str], name: str, default: str = "NULL") -> str:
    # safe column expression
    return name if name in colset else default

def _liability_expr(cols: set[str]) -> str:
    # prefer explicit liability; else derive coarse from odds/stake if present
    if "entry_liability" in cols: return "COALESCE(entry_liability,0.0)"
    if {"entry_odds","entry_stake"}.issubset(cols):
        return "CASE WHEN UPPER(COALESCE(side,''))='LAY' THEN (COALESCE(entry_odds,0)-1.0)*COALESCE(entry_stake,0) ELSE COALESCE(entry_stake,0) END"
    return "0.0"

def snapshot_book_state(con: sqlite3.Connection, *, mode: str = "LIVE", max_top: int = 8) -> Dict[str, Any]:
    """
    Compute open parent/child counts and liabilities, grouped by letter & market.
    """
    out = dict(open_parents=0, open_children=0, open_liability=0.0,
               open_by_letter={}, open_by_market={}, top_risk=[])
    if not _has(con, "orders"): return out

    cols = _cols(con, "orders")
    if not cols: return out
    liab = _liability_expr(cols)
    ts_open = "opened_at" if "opened_at" in cols else None

    # parents = matched entry & (no matched exit)
    parent_pred = "role='PARENT'" if "role" in cols else ("(hedge_of IS NULL OR hedge_of='')" if "hedge_of" in cols else ("(parent_id IS NULL)" if "parent_id" in cols else "1=1"))
    mode_pred   = "AND UPPER(COALESCE(mode,''))=UPPER(?)" if "mode" in cols else ""
    q = f"""
      SELECT COALESCE(source,'?') AS letter, COALESCE(marketId,'?') AS mid,
             SUM({liab}) AS liab,
             COUNT(*) AS n
        FROM orders
       WHERE {parent_pred}
         AND UPPER(COALESCE(entry_status,''))='MATCHED'
         AND (UPPER(COALESCE(exit_status,''))<>'MATCHED' OR exit_status IS NULL)
         {mode_pred}
       GROUP BY letter, mid
    """
    params = (mode,) if ("mode" in cols) else ()
    rows = _q_retry(con, q, params).fetchall()

    by_letter: Dict[str, float] = {}
    by_market: Dict[str, float] = {}
    total_open = 0
    for letter, mid, li, n in rows:
        by_letter[str(letter)[:1]] = by_letter.get(str(letter)[:1], 0.0) + float(li or 0.0)
        by_market[str(mid)]        = by_market.get(str(mid),        0.0) + float(li or 0.0)
        total_open += int(n or 0)

    out["open_parents"] = total_open
    out["open_liability"] = sum(by_letter.values())
    out["open_by_letter"] = by_letter
    out["open_by_market"] = by_market

    # children working
    child_pred = "role='CHILD'" if "role" in cols else ("(hedge_of IS NOT NULL AND hedge_of<>'')" if "hedge_of" in cols else ("(parent_id IS NOT NULL)" if "parent_id" in cols else "0=1"))
    q2 = f"""
      SELECT COUNT(*) FROM orders
       WHERE {child_pred}
         AND UPPER(COALESCE(entry_status,'')) IN ('STAGED','PLACED','LIVE')
         {mode_pred}
    """
    out["open_children"] = int(_q_retry(con, q2, params).fetchone()[0] or 0)

    # top risk list (largest liabilities)
    pairs = sorted(by_market.items(), key=lambda kv: -kv[1])[:max_top]
    out["top_risk"] = [{"marketId": mid, "liability": float(v)} for mid, v in pairs]
    return out

def update_book_state_tick(con: sqlite3.Connection, *, mode: str = "LIVE") -> Dict[str, Any]:
    ensure_book_state_schema(con)
    _ensure_book_state_table(con)
    snap = snapshot_book_state(con, mode=mode)
    _q_retry(con, """
      INSERT INTO dashboard_book_state(day, open_parents, open_children, open_liability,
                                       open_by_letter_json, open_by_market_json, top_risk_json, last_refreshed_ts)
      VALUES(date('now','utc'), ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(day) DO UPDATE SET
        open_parents=excluded.open_parents,
        open_children=excluded.open_children,
        open_liability=excluded.open_liability,
        open_by_letter_json=excluded.open_by_letter_json,
        open_by_market_json=excluded.open_by_market_json,
        top_risk_json=excluded.top_risk_json,
        last_refreshed_ts=excluded.last_refreshed_ts
    """, (int(snap["open_parents"]), int(snap["open_children"]), float(snap["open_liability"]),
          json.dumps(snap["open_by_letter"]), json.dumps(snap["open_by_market"]), json.dumps(snap["top_risk"]), now_iso()))
    con.commit()
    return snap

# ---- funds/int‑bank gating ---------------------------------------------------
def funds_gate(con: sqlite3.Connection, *, plan_px: float, plan_stake: float, hedge_ticks: int = 1,
               min_avail_ok: float = 50.0, bank_cap_pct: float = 0.15) -> Tuple[bool, str]:
    """
    Hard gate before placing a parent.
    - require available funds >= min_avail_ok
    - projected open liability (parent) must keep total open <= bank_cap_pct of INTERNAL bank
    """
    ensure_app_kv(con); ensure_book_state_schema(con)
    # available funds (cached elsewhere can write here; fall back to zero)
    try:
        avail = float(get_kv(con, "bf_available", "0.0") or 0.0)
    except Exception:
        avail = 0.0
    if avail < float(min_avail_ok or 0.0):
        return (False, f"avail_below_{min_avail_ok:.0f}")

    # projected parent liability (worst‑case for lay; else stake)
    parent_liab = max(0.0, (plan_px - 1.0) * plan_stake)
    bank = read_internal_bank(con)
    snap = snapshot_book_state(con)
    if bank > 0.0:
        if (float(snap["open_liability"]) + parent_liab) > (bank_cap_pct * bank):
            return (False, f"book_cap_{bank_cap_pct:.0%}")
    return (True, "")
# === END: engines/risk.py =====================================================


# Killswitch: orphan logic can be globally disabled without deleting code
try:
    from engines.orphan_killswitch import orphan_enabled
except Exception:
    def orphan_enabled() -> bool:  # fail-closed (disabled)
        return False
# === PATCH END ===

def pretrade_ok(ctx: Dict[str, Any], post: Dict[str, Any]) -> Tuple[bool, str]:
    if not ctx.get("sigma_ok", True):
        return False, "sigma_guard"
    if not ctx.get("liquidity_ok", True):
        return False, "liquidity_guard"
    if not ctx.get("two_chapters_ok", True):
        return False, "chapters_guard"
    if ctx.get("cooldown_until"):
        return False, "cooldown"
    return True, "ok"

def derive_stops(post: Dict[str, Any]) -> Tuple[int, int]:
    stops = post.get("stops", {}) if post else {}
    hard = int(max(1, int(stops.get("hard_stop_ticks", 2))))
    timeout = int(stops.get("timeout_sec", 45))
    return hard, timeout

# --- NEW: CAP enforcement (per runner, per letter) + orphan guard -------------

try:
    from engines.config_paths import auto_conn, q_retry
except Exception:
    import sqlite3, os
    def auto_conn():
        ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
        con = sqlite3.connect(os.path.join(ROOT, "data", "autoscalp_gui.db"),
                              timeout=3.0, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con
    def q_retry(con, sql, params=()):
        return con.execute(sql, params)

def _open_parent_count_letter(mid: str, sid: str, letter: str, *, mode: str = "LIVE") -> int:
    """
    Count currently-open PARENT positions for a (market, selection, letter).
    Open := (role='PARENT' OR hedge_of IS NULL/empty) AND (closed_at IS NULL OR closed_at='')
    Letter matches orders.source first character (robust to long names).
    """
    con = auto_conn()
    try:
        row = q_retry(con, """
            SELECT COUNT(*) AS n
              FROM orders
             WHERE mode=?
               AND marketId=? AND selectionId=?
               AND (role='PARENT' OR COALESCE(hedge_of,'')='')
               AND (closed_at IS NULL OR closed_at='')
               AND UPPER(SUBSTR(COALESCE(source,''),1,1)) = UPPER(?)
        """, (str(mode), str(mid), str(sid), str(letter or '')[:1])).fetchone()
        return int(row["n"] if row else 0)
    finally:
        try: con.close()
        except Exception: pass

def _has_orphan_no_child_match(mid: str, sid: str, letter: str, *, mode: str = "LIVE") -> bool:
    """
    Orphan guard: any OPEN parent for (mid,sid,letter) with NO matched child.
    Matched child := role='CHILD', hedge_of=parent COR, entry_status='matched' (case-insensitive).
    """
    # KILLSWITCH: completely disable orphan detection when not enabled
    if not orphan_enabled():
        return False
    con = auto_conn()
    try:
        row = q_retry(con, """
            SELECT COUNT(*) AS bad
              FROM orders p
             WHERE p.mode=?
               AND p.marketId=? AND p.selectionId=?
               AND (p.role='PARENT' OR COALESCE(p.hedge_of,'')='')
               AND (p.closed_at IS NULL OR p.closed_at='')
               AND UPPER(SUBSTR(COALESCE(p.source,''),1,1)) = UPPER(?)
               AND NOT EXISTS (
                     SELECT 1
                       FROM orders c
                      WHERE c.mode = p.mode
                        AND c.role = 'CHILD'
                        AND c.hedge_of = p.customerOrderRef
                        AND UPPER(COALESCE(c.entry_status,'')) = 'MATCHED'
               )
        """, (str(mode), str(mid), str(sid), str(letter or '')[:1])).fetchone()
        return int(row["bad"] if row else 0) > 0
    finally:
        try: con.close()
        except Exception: pass

def can_open_scalp(market_id: str, selection_id: str, *,
                   max_per_runner: int,
                   run_id: str,
                   family_letter: str) -> Tuple[bool, str]:
    """
    Cap: ≤ max_per_runner OPEN parents for (market, selection, letter).
    Slot frees when hedge matches and parent is closed (closed_at set by close path).
    Catch-all: if any OPEN parent has NO matched child, block placements (prevents compounding risk).
    Returns (ok, reason).
    """
    # (inside can_open_scalp or equivalent CAP gate function)
    if orphan_enabled() and _has_orphan_no_child_match(market_id, selection_id, family_letter, mode="LIVE"):
        return False, "orphan_parent_no_child_match"

    open_n = _open_parent_count_letter(market_id, selection_id, family_letter, mode="LIVE")
    if open_n >= int(max_per_runner):
        return False, f"max_per_runner_per_letter({max_per_runner}) open={open_n}"

    return True, ""
# === END PATCH ================================================================
