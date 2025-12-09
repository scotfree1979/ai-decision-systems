#!/usr/bin/env python3
# engines/live/live_router.py
from __future__ import annotations

import json, time, threading, random, sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple
from engines import price_math as pm
# === PATCH START: use LiveCache-only DB connector for router ===
from engines.config_paths import auto_conn_live as _auto_conn
# === PATCH END ===
import uuid
import requests
from engines.config_paths import auto_conn as _cp_auto_conn, q_retry as _cp_q_retry, autoscalp_db, connect_db
from engines.math.dynamic_stake_v7 import calc_dynamic_stake, calc_greenup_stake

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
              SUM(CASE WHEN role='PARENT' AND entry_status='matched' 
                       AND (exit_status IS NULL OR exit_status<>'matched')
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

def _compute_letter_exposure(mid: str, sid: str, letter: str):
    """
    Compute per-letter exposure for MSC/Legacy engines.
    """
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        row = _q_retry(con, """
            SELECT 
              SUM(CASE WHEN role='PARENT' AND entry_status='matched'
                       AND (exit_status IS NULL OR exit_status<>'matched')
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
# === PATCH START ============================================
# 📍 TARGET: engines/live/live_router.py : _orders_conn()
# 📆 PATCHED: 2025-12-10 — route all LIVE writes to dual-writer
# =============================================================

from engines.config_paths import auto_conn_live

def _orders_conn():
    """
    LiveRouter MUST write to dual-writer:
        LOCAL + LiveCache simultanously.
    auto_conn_live(rw=True) provides this.
    """
    return auto_conn_live(rw=True)

# === PATCH END ==============================================


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
    LiveRouter MUST write to the LiveCache DB (autoscalp_livecache.db).
    This connector (_auto_conn) is imported as:
        from engines.config_paths import auto_conn_live as _auto_conn
    The previous implementation incorrectly routed through auto_conn(),
    causing all writes to go to LOCAL or nowhere.
    """
    # ✔ FIXED — use LiveCache writer
    con = _auto_conn(rw=True)

    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA busy_timeout=8000;")
    except Exception:
        pass

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

def _engine_from_source(source: str) -> str:
    """
    Convert a source/letter into an engine bucket.
    Defaults to LEGACY for all non-MSC letters.
    """
    if not source:
        return "LEGACY"
    L = str(source).upper()[:1]      # take the first letter only
    return ENGINE_MAP.get(L, "LEGACY")
# === PATCH END ============================================================



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

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🆕 ADD: place_from_bus()
# 📆 PATCHED: 2026-02-13
# ============================================================================

def place_from_bus(plan: dict, ctx: dict):
    """
    Direct BUS→Router placement.
    All engines pass through here.
    No Lanes, no Placement stage.
    `plan` contains engine, px, size, direction.
    """
# === PATCH START ============================================================
# 📍 TARGET: live_router.place_from_bus
# 📆 PATCHED: 2026-02-14 — Shadow Mode Switch
# ============================================================================

    import os
    SHADOW = os.environ.get("AUTOSCALP_SHADOW", "0") == "1"

    if SHADOW:
        print(f"[ROUTER][SHADOW] {direction} {size}@{odds} mid={mid} sid={sid}")
        return None

# === PATCH END ================================================================

    mid = str(plan.get("marketId"))
    sid = str(plan.get("selectionId"))
    odds = float(plan.get("px") or 0)
    size = float(plan.get("size") or 0)
    direction = plan.get("direction")

    # create order reference
    cref = f"{plan.get('engine','?')}-{uuid.uuid4().hex[:10]}"

    # insert via existing helper
    from engines.live.live_router import _orders_insert_parent_queued
    parent_id = _orders_insert_parent_queued(
        run_id=ctx.get("run_id"),
        market_id=mid,
        selection_id=sid,
        side="LAY" if direction.startswith("LAY") else "BACK",
        entry_odds=odds,
        entry_stake=size,
        cor=cref,
        source=plan.get("engine"),
    )

    # push into main router path
    _place(_name=plan.get("engine"), _plan={
        "px": odds,
        "size": size,
        "direction": direction,
        "customerOrderRef": cref,
        "marketId": mid,
        "selectionId": sid
    }, _ctx=ctx)

# === PATCH END ================================================================



# ── DB bootstrap (orders + events) ───────────────────────────────────────────
_ORDERS_SCHEMA_OK = False
def _ensure_orders_schema() -> None:
    """Idempotently ensure orders/events exist with linkable parent/child + strategy columns."""
    global _ORDERS_SCHEMA_OK
    if _ORDERS_SCHEMA_OK:
        return
    con = _db()    
    try:
        cur = con.cursor()
        # events sink (used by _log_event/_orders_probe)
        _q_retry(cur, """
            CREATE TABLE IF NOT EXISTS events(
              ts TEXT, level TEXT, source TEXT, message TEXT
            )
        """)
        # base orders table (will be evolved below)
        _q_retry(cur, """
            CREATE TABLE IF NOT EXISTS orders(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              customerOrderRef TEXT UNIQUE,
              mode TEXT,
              run_id INTEGER,
              marketId TEXT,
              selectionId TEXT,
              side TEXT,
              -- entry leg
              entry_odds REAL,
              entry_stake REAL,
              entry_status TEXT,
              entry_bet_id TEXT,
              opened_at TEXT,
              -- exit leg (hedge or flatten)
              exit_status TEXT,
              exit_bet_id TEXT,
              exit_odds REAL,
              exit_stake REAL,
              closed_at TEXT,
              -- PnL
              realized_pnl REAL,
              net_pl REAL,
              -- misc
              error TEXT
            )
        """)
        cols = {r[1] for r in _q_retry(cur, "PRAGMA table_info(orders)")}

        # NEW COLUMN
        if "stop_loss_px" not in cols:
            _q_retry(cur, "ALTER TABLE orders ADD COLUMN stop_loss_px REAL")

        def _add(col, ddl): 
            if col not in cols: _q_retry(cur, f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
        _add("role",      "TEXT")          # 'PARENT'|'CHILD'
        _add("hedge_of",  "INTEGER")       # child -> parent id
        _add("exit_kind", "TEXT")        # 'HEDGE' | 'STOPLOSS' | 'FLATTEN' (optional)
        _add("source",    "TEXT")          # strategy tag e.g. LEGACY_STRATEGY
        # helpful indexes
        _q_retry(cur, "CREATE INDEX IF NOT EXISTS idx_orders_mode_opened ON orders(mode, opened_at)")
        _q_retry(cur, "CREATE INDEX IF NOT EXISTS idx_orders_status      ON orders(entry_status, exit_status)")
        _q_retry(cur, "CREATE INDEX IF NOT EXISTS idx_orders_role        ON orders(role)")
        _q_retry(cur, "CREATE INDEX IF NOT EXISTS idx_orders_link        ON orders(hedge_of)")
        con.commit()
        _ORDERS_SCHEMA_OK = True
    finally:
        con.close()

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

# Call this once after schema ensure
try:
    _repair_orphan_run_ids(mode="LIVE", tag="LIVE-AUTO")
except Exception:
    pass


# Make UTC explicitly timezone-aware for consistency
def _utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")




# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _run_fk_id\(run_id: str \| None, mode: str = "LIVE"\):
# 📆 PATCHED: 2025-09-29T11:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _run_fk_id(run_id: str | None, mode: str = "LIVE") -> int:
    """
    Convert human run_id (e.g., 'LIVE-20250912-174250') into FK int (runs.id).
    Falls back to 'LIVE-AUTO' if run_id is missing.
    """
    rid_txt = (run_id or "").strip() or f"{mode.upper()}-AUTO"
    con = _orders_conn()
    try:
        cur = con.cursor()
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
        row = _q_retry(cur, "SELECT id FROM runs WHERE notes=? LIMIT 1", (rid_txt,)).fetchone()
        if row:
            return int(row[0])
        _q_retry(cur, "INSERT INTO runs(started_at, mode, notes) VALUES(datetime('now','utc'), ?, ?)",
                 (str(mode or "LIVE"), rid_txt))
        con.commit()
        return int(cur.lastrowid)
    finally:
        try: con.close()
        except Exception: pass

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
        _log_event("INFO", "live_router", f"orders DB path = {autoscalp_db()}")
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


def _poll_matched(app_key: str, token: str, bet_id: str, timeout_s: int = 90, interval_s: float = 2.0) -> bool:
    """
    Poll Betfair until this betId is matched (sizeMatched>0 OR orderStatus=EXECUTION_COMPLETE).
    If listCurrentOrders returns empty, we DO NOT assume matched; we just keep polling until timeout.
    """
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            cur = _list_current(app_key, token, bet_id)
            orders = (cur.get("result", {}) or {}).get("currentOrders") or []
            if orders:
                o = orders[0]
                matched = float(o.get("sizeMatched") or 0.0)
                status  = str(o.get("orderStatus") or o.get("status") or "").upper()
                if matched > 0.0 or "EXECUTION_COMPLETE" in status:
                    return True
            # if empty → we don't know yet; keep polling
        except Exception:
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

# Re-hedge background loop (singleton)
_REHEDGE_THREAD = None

def _rehedge_loop(period_s: float = 10.0, default_ticks: int = 1):
    while True:
        try:
            ensure_hedges_for_open_parents(max_to_fix=50, default_ticks=default_ticks)
        except Exception as e:
            _log_event("ERROR", "live_router", f"rehedge loop error: {e}")

        try:
            _sync_parent_matches(limit=50)
        except Exception as e:
            _log_event("ERROR", "live_router", f"sync_parent_matches error: {e}")

        try:
            _sync_all_matches(limit=100)   # ← NEW: sweep stuck 'placed' to 'matched'
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
    *, source="LEGACY_STRATEGY", stop_loss_px=None):
    """
    Upsert LIVE parent row as 'queued'.
    Stores stop_loss_px for Overwatcher STOPLOSS engine.
    """

    # === PATCH 3.2 START — EventSync: parent_queued =======================
    mid = str(market_id)
    sid = str(selection_id)
    letter = str(source)[:1].upper()

    before_exposure = _compute_exposure(mid, sid)
    before_letter = _compute_letter_exposure(mid, sid, letter)

    _emit_router_event("parent_queued", {
        "marketId": mid,
        "selectionId": sid,
        "cor": cor,
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
                ?, ?, 'LIVE', ?, ?, ?, ?, ?, 'queued', ?,
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
                entry_status  = COALESCE(orders.entry_status, 'queued'),
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
            source,
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
# 🔎 SEARCH: ^def _orders_update_parent_placed\(
# --- PATCH START: replace function ------------------------------------
def _orders_update_parent_placed(cor, bet_id):
    """Mark parent placed and record entry_bet_id (create skeleton row if missing)."""
    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        # latest LIVE run row as fallback FK if textual run_id isn't directly available here
        rid_row = _q_retry(cur, "SELECT id FROM runs WHERE mode='LIVE' ORDER BY datetime(started_at) DESC LIMIT 1").fetchone()
        fk = int(rid_row[0]) if rid_row else _run_fk_id("LIVE-AUTO", mode="LIVE")

        # skeleton WITH run_id to satisfy NOT NULL + FK
        _q_retry(cur, """
            INSERT INTO orders (customerOrderRef, run_id, mode, entry_status, opened_at, role)
            VALUES (?, ?, 'LIVE', 'queued', ?, 'PARENT')
            ON CONFLICT(customerOrderRef) DO NOTHING
        """, (str(cor), int(fk), _utcnow_str()))

        _q_retry(cur, """
            UPDATE orders
               SET entry_status='placed',
                   entry_bet_id=?,
                   mode='LIVE',
                   role='PARENT',
                   run_id=COALESCE(run_id, ?)
             WHERE customerOrderRef=? 
        """, (str(bet_id or ""), int(fk), str(cor)))
        con.commit()
        _orders_probe(cor, note="placed")
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
        _log_event("ERROR", "live_router", f"orders update placed failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass

# --- PATCH END ----------------------------------------------------------



def _orders_update_parent_failed(cor, error_msg):
    con = _con(); cur = con.cursor()
    try:
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='failed', error=?
             WHERE customerOrderRef=? AND mode='LIVE'
        """, (str(error_msg)[:240], cor))
        con.commit()
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_update_parent_matched
# 📆 PATCHED: 2025-10-03T12:05Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _orders_update_parent_matched(cor: str, bet_id: str | None = None):
    """Parent entry filled (and stamp matched odds/stake if betId known)."""
    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='matched',
                   mode='LIVE',
                   role='PARENT'
             WHERE customerOrderRef=?
        """, (str(cor),))
        con.commit()
        _orders_probe(cor, note="parent_matched")
        # NEW → log playbook entry after match
        _record_playbook_entry(cor)
        # NEW: stamp avg matched odds/stake if betId is available
        if bet_id:
            try:
                app_key, token = _keys()
                avg_odds, matched_size = _fetch_avg_match(app_key, token, str(bet_id))
                if avg_odds > 0.0 and matched_size > 0.0:
                    _q_retry(cur, """
                        UPDATE orders
                           SET entry_matched_odds=?,
                               entry_matched_stake=?
                         WHERE customerOrderRef=?
                    """, (avg_odds, matched_size, str(cor)))
                    con.commit()
            except Exception as e:
                _log_event("WARN","live_router",f"parent_matched: could not fetch odds/stake ref={cor} err={e}")
    except Exception as e:
        _log_event("ERROR", "live_router", f"orders update parent matched failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass

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
               SET entry_status='matched',
                   closed_at=COALESCE(closed_at, datetime('now','utc')),
                   role='CHILD',
                   realized_pnl=?,
                   net_pl=?
             WHERE hedge_of = ?
        """, (realized, realized, pid))
        con.commit()

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
    Finalize hedge exit: compute realized PnL, stamp exit row, fire EventSync.
    """
    _ensure_orders_schema()
    con = _orders_conn(); con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        p = _q_retry(cur, """
            SELECT id, side, entry_odds, entry_stake, marketId, selectionId,
                   COALESCE(source,'') AS source
              FROM orders
             WHERE customerOrderRef=? LIMIT 1
        """, (str(cor),)).fetchone()
        if not p:
            _log_event("ERROR","live_router",f"hedge_matched: parent missing ref={cor}")
            return

        pid        = int(p["id"])
        entry_side = (p["side"] or "").upper()
        E, S       = float(p["entry_odds"] or 0.0), float(p["entry_stake"] or 0.0)
        H, S2      = float(exit_odds or 0.0),       float(exit_stake or 0.0)

        # PnL
        if entry_side == "LAY":
            win  = S2*(H-1.0) - S*(E-1.0)
            lose = S - S2
        else:
            win  = (E-1.0)*S - (H-1.0)*S2
            lose = -S + S2
        realized = round(min(win, lose), 2)

        # update parent
        _q_retry(cur, """
            UPDATE orders
               SET exit_status='matched',
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
                "result": ("good" if kind == "HEDGE" else "stoploss" if kind == "STOPLOSS" else "unknown"),
                "exit_kind": kind,
                "market": str(p["marketId"]), "runner": str(p["selectionId"]),
                "entry_odds": E, "entry_stake": S,
                "exit_odds": H,  "exit_stake": S2,
                "realized": realized, "source": str(p["source"] or "")
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
            "AND entry_status='matched' AND (exit_status IS NULL OR exit_status <> 'matched')",
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
               SET entry_status='cancelled',
                   closed_at=COALESCE(closed_at, ?),
                   error=COALESCE(error, ?),
                   mode='LIVE'
             WHERE customerOrderRef=?
        """, (_utcnow_str(), reason[:200], str(cor)))
        con.commit()
    finally:
        con.close()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_insert_child_live\(parent_cor:
#    REPLACE the function with the version below (adds exit_kind column)
# 📆 PATCHED: 2025-09-29T14:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _orders_insert_child_live(parent_cor: str, *, market_id: str, selection_id: str,
                              side: str, odds: float, stake: float,
                              bet_id: str, source: str = "LEGACY_STRATEGY",
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
            ) VALUES (?, ?, 'LIVE', ?, ?, ?, ?, ?, 'live', datetime('now','utc'),
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

from engines.mastery.event_sink import emit as _emit_event

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
        _emit_event("settlement", payload)
    except Exception as e:
        print(f"[EventSync][router_exit] warn: {e}")

# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _place_stoploss_child_now(
# 📆 PATCHED: 2026-02-10 — Settlement EventSync B: STOPLOSS_EXIT
# ============================================================================

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

    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row

        parent = _q_retry(con, """
            SELECT id, run_id
              FROM orders
             WHERE customerOrderRef=? LIMIT 1
        """, (str(parent_cor),)).fetchone()
        if not parent:
            con.close()
            return None

        pid  = int(parent["id"])
        fk   = int(parent["run_id"] or 0)
        S2   = float(parent_stake)
        H    = float(exit_odds)

        app_key, token = _keys()
        cref = _ref("SL")
        bet_id, _ = _place(app_key, token,
                           market_id, selection_id,
                           exit_side, H, S2,
                           cref, persistence="LAPSE")

        if not bet_id:
            con.close()
            return None

        cur = con.cursor()
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
              'matched', datetime('now','utc'), ?,
              'CHILD', ?, 'S', 'STOPLOSS'
            )
        """, (cref, fk,
              str(market_id), str(selection_id),
              exit_side.upper(), H, S2,
              bet_id, pid))

        child_id = int(cur.lastrowid)

        # stamp parent
        _q_retry(cur, """
            UPDATE orders
               SET exit_status='matched',
                   exit_kind='STOPLOSS',
                   exit_odds=?, exit_stake=?,
                   closed_at=datetime('now','utc')
             WHERE customerOrderRef=?
        """, (H, S2, str(parent_cor)))
        con.commit()
        con.close()

        # === EVENTSYNC: B — STOPLOSS EXIT ==================================
        try:
            _emit_settlement_router_event(
                "STOPLOSS_EXIT",
                cor=parent_cor,
                market_id=market_id,
                selection_id=selection_id,
                exit_odds=H,
                exit_stake=S2,
                realized=None   # parent realized PnL is finalized later in reconcile
            )
        except Exception as e:
            _log_event("WARN","live_router",f"EventSync stoploss exit failed: {e}")
        # ====================================================================

        return child_id

    except Exception as e:
        _log_event("ERROR","live_router",f"_place_stoploss_child_now failed ref={parent_cor}: {e}")
        return None

# === PATCH END ==============================================================




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
                entry_status='cancelled',
                closed_at=COALESCE(closed_at, datetime('now','utc')),
                mode='LIVE'
            WHERE mode='LIVE'
              AND marketId IN ({qph})
              AND UPPER(COALESCE(entry_status,'')) IN ('QUEUED','PLACED')
        """, tuple(mids)).rowcount

        # 3) SETTLE OPEN PARENTS
        n_settle = _q_retry(con, f"""
            UPDATE orders SET
                exit_status='settled',
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
def _orders_insert_child_queued(parent_cor: str, *,
                                market_id: str, selection_id: str,
                                side: str, odds: float, stake: float,
                                source: str = "LEGACY_STRATEGY",
                                exit_kind: str = "HEDGE") -> Optional[int]:
    """
    Insert CHILD row in queued state. Returns child id.
    exit_kind: 'HEDGE' (planned), 'STOPLOSS' (risk stop), 'FLATTEN' (manual).
    """
    letter = "S" if exit_kind.upper() == "STOPLOSS" else "H"
    source = letter  # ensures book_state and playbooks carry H or S

    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        parent = _q_retry(cur, "SELECT id, run_id FROM orders WHERE customerOrderRef=? LIMIT 1",
                          (str(parent_cor),)).fetchone()
        if not parent:
            _log_event("ERROR", "live_router", f"insert_child: parent not found ref={parent_cor}")
            return None
        pid = int(parent["id"])
        fk  = int(parent["run_id"]) if parent["run_id"] is not None else _run_fk_id("LIVE-AUTO", mode="LIVE")
        cref = _ref("CHILD")
        _q_retry(cur, """
            INSERT INTO orders(
              customerOrderRef, run_id, mode, marketId, selectionId,
              side, entry_odds, entry_stake, entry_status, opened_at,
              role, hedge_of, source, exit_kind
            ) VALUES (?, ?, 'LIVE', ?, ?, ?, ?, ?, 'queued', datetime('now','utc'),
                      'CHILD', ?, ?, ?)
        """, (cref, fk, str(market_id), str(selection_id),
              side.upper(), float(odds), float(stake), pid, str(source), str(exit_kind).upper()))
        cid = int(cur.lastrowid)
        con.commit()
        return cid
    except Exception as e:
        _log_event("ERROR", "live_router", f"insert_child_queued error parent_ref={parent_cor}: {e}")
        return None
    finally:
        try: con.close()
        except Exception: pass



def _orders_update_child_placed(child_id: int, bet_id: str) -> None:
    """Mark child as placed with entry_bet_id."""
    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='placed',
                   entry_bet_id=?,
                   mode='LIVE',
                   role='CHILD'
             WHERE id=?
        """, (str(bet_id), int(child_id)))
        con.commit()
    except Exception as e:
        _log_event("ERROR", "live_router", f"child update placed failed id={child_id}: {e}")
    finally:
        try: con.close()
        except Exception: pass
# --- PATCH END ----------------------------------------------------
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_update_child_placed\(child_id: int, bet_id: str\) -> None:
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

ODDS_CAP_LIMIT = 25.0  # hard ceiling for any placement

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def place_parent_and_hedge\(
# --- REPLACE THE WHOLE FUNCTION WITH THIS -----------------------------------
# --- REPLACE the function header with this (note the extra params) ---
def place_parent_and_hedge(
    *,
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
    child_persistence: str  = "LAPSE",
    max_inplay_seconds: int = 0,
    hedge_stake_override: float | None = None,
    # ⬇️ compat path used by placement.py
    _name: str | None = None,
    _plan: dict | None = None,
    _ctx: dict | None = None,
) -> tuple[Optional[str], str]:

# === PATCH START ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def place_parent_and_hedge(
# 📆 PATCHED: 2026-02-12 — allow Bus direct placement
# ============================================================================

# Bus direct placement is already compatible with place_parent_and_hedge.
# Add a safety guard so that legacy placement module is not required.

    if _name.startswith("MSC_") or _name == "OVERWATCHER" or _name == "LEGACY":
        # fast-path accepted
        pass
# === PATCH END ================================================================


    # --- COMPAT GLUE: accept (_name, _plan, _ctx) from placement.py -----------
    if _plan is not None:
        p = dict(_plan)
        c = dict(_ctx or {})

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
    if _plan and str(_plan.get("engine")).upper() == "OVERWATCHER" \
              and str(_plan.get("type")).upper() == "STOPLOSS":

        parent_cor = _ctx.get("customerOrderRef") or _plan.get("customerOrderRef")
        if not parent_cor:
            parent_cor = _ref("SLP")  # fallback, extremely rare

        # Load parent
        con_sl = _orders_conn(); con_sl.row_factory = sqlite3.Row
        parent = _q_retry(con_sl,
            "SELECT id, side, entry_stake, marketId, selectionId "
            "FROM orders WHERE customerOrderRef=? LIMIT 1",
            (str(parent_cor),)
        ).fetchone()

        if not parent:
            con_sl.close()
            return None, "STOPLOSS_NO_PARENT"

        pid        = int(parent["id"])
        p_side     = str(parent["side"]).upper()
        p_stake    = float(parent["entry_stake"] or 0.0)
        p_mid      = str(parent["marketId"])
        p_sid      = str(parent["selectionId"])

        # STOPLOSS exit direction
        exit_side = "BACK" if p_side == "LAY" else "LAY"
        exit_odds = float(_plan.get("px") or 0.0)
        exit_stake = p_stake  # 1:1 flatten

        # -----------------------------------------
        # CANCEL ANY EXISTING HEDGE CHILDREN
        # -----------------------------------------
        try:
            cur_sl = con_sl.cursor()
            hrows = _q_retry(cur_sl, """
                SELECT id FROM orders
                 WHERE hedge_of=? AND role='CHILD'
                   AND exit_kind='HEDGE'
                   AND (exit_status IS NULL OR exit_status<>'matched')
            """, (pid,)).fetchall()

            for hr in hrows:
                _q_retry(cur_sl,
                    "UPDATE orders SET exit_status='cancelled', "
                    "closed_at=datetime('now','utc'), exit_kind='SL_Cancel_H' "
                    "WHERE id=?", (int(hr["id"]),)
                )
            con_sl.commit()
        except Exception:
            pass

        # -----------------------------------------
        # IMMEDIATE STOPLOSS CHILD PLACEMENT
        # -----------------------------------------
        try:
            app_key, token = _keys()
            cref = _ref("SL")

            # Betfair order
            bet_id, detail = _place(
                app_key, token,
                p_mid, p_sid,
                exit_side,
                float(exit_odds),
                float(exit_stake),
                cref,
                persistence="LAPSE"
            )

            if not bet_id:
                con_sl.close()
                return None, "STOPLOSS_BETFAIL"

            # Insert DB child row (matched immediately)
            cur_sl = con_sl.cursor()
            _q_retry(cur_sl, """
                INSERT INTO orders(
                  customerOrderRef, run_id, mode,
                  marketId, selectionId,
                  side, entry_odds, entry_stake,
                  entry_status, opened_at,
                  entry_bet_id,
                  role, hedge_of, source, exit_kind, engine
                ) VALUES (
                  ?, ?, 'LIVE',
                  ?, ?,
                  ?, ?, ?,
                  'matched', datetime('now','utc'),
                  ?,
                  'CHILD', ?, 'S', 'STOPLOSS', 'OVERWATCHER'
                )
            """, (
                cref, parent.get("run_id") or _run_fk_id("LIVE-AUTO", mode="LIVE"),
                p_mid, p_sid,
                exit_side, float(exit_odds), float(exit_stake),
                bet_id,
                pid
            ))

            child_id = int(cur_sl.lastrowid)

            # Update parent
            _q_retry(cur_sl, """
                UPDATE orders
                   SET exit_status='matched',
                       exit_kind='STOPLOSS',
                       exit_odds=?, exit_stake=?,
                       closed_at=datetime('now','utc')
                 WHERE id=?
            """, (float(exit_odds), float(exit_stake), pid))

            con_sl.commit()
            con_sl.close()

            _log_event("INFO", "live_router",
                       f"[SL][EXEC] parent={parent_cor} child_id={child_id} "
                       f"side={exit_side} odds={exit_odds} stake={exit_stake}")

            return bet_id, cref

        except Exception as e:
            try: con_sl.close()
            except: pass
            _log_event("ERROR", "live_router",
                       f"[SL][ERR] router_stoploss_exec_failed ref={parent_cor} err={e}")
            return None, f"STOPLOSS_ERR:{e}"

# === PATCH END ============================================================



    # --- dynamic stake logic ---------------------------------------------------
    letter = _letter_from_source(source)
    phase  = (locals().get("phase") or _infer_phase_from_schedule(str(market_id))).upper()
    dyn_stake, dyn_why = calc_dynamic_stake(letter, phase=phase)
    use_dynamic = (
        USE_ROUTER_DYNAMIC_STAKE
        or (letter in USE_ROUTER_DYNAMIC_STAKE_LETTERS)
        or (stake is None or (isinstance(stake, (int,float)) and float(stake) <= 0.0))
    )
    try:
        stake = float(dyn_stake if use_dynamic else float(stake))
    except Exception:
        stake = float(dyn_stake)

    if (letter == "A" and USE_ROUTER_DYNAMIC_STAKE_A) or (stake <= 0.0):
        stake = dyn_stake

        # === BANKSTATE GATING ============================================
        try:
            from engines.live import bank_state

            # resolve engine bucket
            eng = _engine_from_source(source)

            # static pot (used for dynamic stake sizing)
            live_bank = bank_state.get_engine_pot(eng)

            # required stake for this order (dyn or plan)
            stake_required = float(stake)

            # is there enough AVAILABLE pot right now?
            if not bank_state.can_place(eng, stake_required):
                msg = (f"[BUDGET] block: engine={eng} "
                       f"need={stake_required:.2f} "
                       f"avail={bank_state.get_engine_available(eng):.2f}")
                _log_event("WARN", "live_router", msg)
                return None, msg

        except Exception as e:
            _log_event("ERROR", "live_router",
                       f"budget_gate_error src={source} err={e}")
        # ==================================================================


    try:
        _log_event("INFO","live_router",
                   f"stake_resolve letter={letter} phase={phase} stake={stake:.2f} "
                   f"{'dyn' if use_dynamic else 'plan'} | "+dyn_why)

    except Exception:
        pass

    try:
        # Show letter numbering for debug clarity
        idx = _active_parents_count_per_letter(market_id, selection_id, letter) + 1
        _log_event("INFO","live_router",f"[CAP] next slot mid={market_id} sid={selection_id} letter={letter}{idx}")
    except Exception:
        pass


    # Log stake decision for Mastery
    _mastery_log("stake_decision", {
        "letter": letter, "phase": phase, "market": str(market_id), "runner": str(selection_id),
        "entry_odds": float(entry_odds), "stake": float(stake), "why": dyn_why, "source": str(source),
    })


    # --- CAP gate --------------------------------------------------------------
    try:
        from engines.caps import cap_ok_v8
        ok_cap, why, metrics = cap_ok_v8(market_id, selection_id, letter)
        if not ok_cap:
            msg = f"[LIVE CAP] mid={market_id} sid={selection_id} blocked letter={letter} reason={why} metrics={metrics}"
            _log_event("WARN", "live_router", msg)
            return None, msg
    except Exception as e:
        _log_event("WARN", "live_router", f"cap check error fallback: {e}")


    app_key, token = _keys()
    try:
        parent_ref = (
            (_plan or {}).get("customerOrderRef")
            or (_ctx or {}).get("customerOrderRef")
            or _ref(side.upper())
        )
    except Exception:
        parent_ref = _ref(side.upper())

    # --- ODDS CAP GUARD -------------------------------------------------------
    if entry_odds is None or float(entry_odds) > ODDS_CAP_LIMIT:
        try:
            # emit structured learning event so Mastery can record it
            event_sink.on_decision({
                "type": "ignored_zone",
                "reason": f"odds>{ODDS_CAP_LIMIT}",
                "marketId": str(market_id),
                "selectionId": str(selection_id),
                "odds": float(entry_odds or 0.0),
                "stake": float(stake or 0.0),
                "source": str(source),
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })
            _log_event(
                "WARN", "live_router",
                f"[ODDS_CAP] blocked placement mid={market_id} sid={selection_id} "
                f"odds={entry_odds} src={source}"
            )
        except Exception as e:
            _log_event("ERROR", "live_router", f"odds_cap emit failed: {e}")
        # skip placing this trade entirely
        return None, f"ODDS>{ODDS_CAP_LIMIT}"
# === PATCH END ===

    # 1) queue parent -----------------------------------------------------------
    try:
        _orders_insert_parent_queued(run_id, market_id, selection_id, side, float(entry_odds), float(stake),
                                     parent_ref, source=source)
        _orders_probe(parent_ref, note="queued")
    except Exception as e:
        _log_event("ERROR", "live_router", f"PARENT queue error ref={parent_ref}: {e}")

    # 2) place parent -----------------------------------------------------------
    bf_parent_id, detail = None, {}
    try:
        bf_parent_id, detail = _place(app_key, token, market_id, selection_id, side,
                                      float(entry_odds), float(stake), parent_ref,
                                      persistence=parent_persistence)
        if bf_parent_id:
            _orders_update_parent_placed(parent_ref, bf_parent_id)
            _orders_probe(parent_ref, note="placed", bet_id=bf_parent_id)
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
        return None, parent_ref

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
                               SET entry_status='cancelled',
                                   closed_at=COALESCE(closed_at, datetime('now','utc'))
                             WHERE customerOrderRef=? AND COALESCE(entry_status,'')<>'matched'
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


            # hedge planning
            try:
                ticks = max(1, abs(int(hedge_ticks)))
                from engines.price_math import odds_plus_ticks as _odds_plus_ticks
                if side.upper() == "LAY":
                    hedge_side = "BACK"
                    hedge_odds = _odds_plus_ticks(float(entry_odds), -ticks)
                else:
                    hedge_side = "LAY"
                    hedge_odds = _odds_plus_ticks(float(entry_odds), +ticks)
                hedge_odds = _round_odds(float(hedge_odds))
                hedge_stake = (
                    float(hedge_stake_override) if hedge_stake_override is not None
                    else calc_greenup_stake(side, float(entry_odds), float(stake), float(hedge_odds))
                )
            except Exception as e:
                _log_event_safe("ERROR", "live_router", f"hedge planning error ref={parent_ref}: {e}")
                return

            # --- PATCH START: robust child placement with retries --------------
            hedge_ref = _ref(hedge_side)
            try:
                # Insert child row in queued state
                child_id = _orders_insert_child_queued(
                    parent_cor=parent_ref,
                    market_id=market_id,
                    selection_id=selection_id,
                    side=hedge_side,
                    odds=float(hedge_odds),
                    stake=float(hedge_stake),
                    source=source,
                )

                deadline = time.time() + max(0, _secs_to_off(market_id) - 180)  # cutoff T-3m
                bf_child_id, hdetail = None, {}

                while time.time() < deadline and not bf_child_id:
                    _log_event_safe("INFO", "live_router",
                        f"[BG] child_place_attempt parent_ref={parent_ref} child_ref={hedge_ref} "
                        f"side={hedge_side} odds={hedge_odds} stake={hedge_stake}")

                    try:
                        app_key, token = _keys()  # 🔑 refresh each attempt
                        bf_child_id, hdetail = _place(
                            app_key, token,
                            market_id, selection_id,
                            hedge_side, float(hedge_odds), float(hedge_stake),
                            hedge_ref, persistence=child_persistence
                        )
                    except Exception as e:
                        hdetail = {"errorCode": str(e)}
                        _log_event_safe("ERROR", "live_router",
                            f"[BG] child_place_exception parent_ref={parent_ref} err={e}")

                    if not bf_child_id:
                        time.sleep(5)
                        continue

                if bf_child_id and child_id:
                    _log_event_safe("INFO", "live_router",
                        f"[BG] child_placed parent_ref={parent_ref} child_id={child_id} betId={bf_child_id}")
                    _orders_update_child_placed(child_id, bf_child_id)
                    _ledger_link_child_by_parent_cor(parent_ref, child_id)
                else:
                    if child_id:
                        con = _orders_conn(); cur = con.cursor()
                        err_txt = (hdetail.get("errorCode") if hdetail else "NO_RESPONSE")
                        _q_retry(cur,
                            "UPDATE orders SET entry_status='failed', error=? WHERE id=?",
                            (str(err_txt), int(child_id))
                        )
                        con.commit(); con.close()

                    from engines.mastery import event_sink
                    event_sink.on_decision({
                        "type": "child_placement_failed",
                        "parent_ref": parent_ref,
                        "marketId": market_id,
                        "selectionId": selection_id,
                        "odds": hedge_odds,
                        "stake": hedge_stake,
                        "reason": hdetail.get("errorCode") if hdetail else "unknown",
                    })

                    _orders_probe(parent_ref, note="child_fail")
                    _log_event_safe("ERROR", "live_router",
                        f"[BG] HEDGE final fail parent_ref={parent_ref} child_ref={hedge_ref} detail={hdetail}")

            except Exception as _e:
                _log_event_safe("ERROR", "live_router",
                    f"[BG] child insert/link failed ref={parent_ref}: {_e}")
            # --- PATCH END ------------------------------------------------------

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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def ensure_hedges_for_open_parents\(
# --- PATCH START: replace function ------------------------------------
def ensure_hedges_for_open_parents(*, max_to_fix: int = 20, default_ticks: int = 1) -> int:
    """
    For every matched PARENT without a matched/live CHILD, place CHILD and link it.
    """
    fixed = 0
    _ensure_orders_schema()
    con = _orders_conn(); con.row_factory = sqlite3.Row
    try:
        rows = _q_retry(con, """
            SELECT id, customerOrderRef AS cor, marketId, selectionId, side, entry_odds, entry_stake,
                   COALESCE(source,'LEGACY_STRATEGY') AS source
              FROM orders
             WHERE mode='LIVE'
               AND role='PARENT'
               AND entry_status='matched'
               AND (exit_status IS NULL OR UPPER(exit_status) <> 'MATCHED')
               AND NOT EXISTS (
                   SELECT 1 FROM orders c
                    WHERE c.role='CHILD' AND c.hedge_of=orders.id
                      AND UPPER(COALESCE(c.entry_status,'')) IN ('LIVE','MATCHED')
               )
             ORDER BY opened_at DESC
             LIMIT ?
        """, (int(max_to_fix),)).fetchall()
    except Exception as e:
        _log_event("ERROR", "live_router", f"rehedge scan failed: {e}")
        try: con.close()
        except Exception: pass
        return 0
    finally:
        try: con.close()
        except Exception: pass

    if not rows:
        return 0

    try:
        app_key, token = _keys()
    except Exception as e:
        _log_event("ERROR", "live_router", f"rehedge auth error: {e}")
        return 0

    for r in rows:
        try:
            mid, sid = str(r["marketId"]), str(r["selectionId"])
            side_p = (r["side"] or "").upper()
            eo, st = float(r["entry_odds"] or 0.0), float(r["entry_stake"] or 0.0)
            src    = str(r["source"] or "LEGACY_STRATEGY")
            tks    = max(1, int(default_ticks))
            hedge_side = "BACK" if side_p == "LAY" else "LAY"
            hedge_odds = pm.walk_ticks(eo, tks, direction=("up" if side_p == "LAY" else "down"))
            hedge_odds = _round_odds(float(hedge_odds))
            hedge_stake = calc_greenup_stake(side_p, eo, st, hedge_odds)

            href = _ref(hedge_side)
            h_bet_id, _ = _place(app_key, token, mid, sid, hedge_side, hedge_odds, hedge_stake, href, persistence="LAPSE")

            if not h_bet_id:
                continue

            _orders_insert_child_live(parent_cor=str(r["cor"]), market_id=mid, selection_id=sid,
                                      side=hedge_side, odds=hedge_odds, stake=hedge_stake,
                                      bet_id=h_bet_id, source=src)
            # 🔗 Link plan_ledger.child_order_id (second line of defense)
            try:
                # We need the child id we just inserted; requery by (role='CHILD', hedge_of=parent.id)
                con2 = _orders_conn(); con2.row_factory = sqlite3.Row
                rowc = _q_retry(con2, """
                    SELECT c.id
                      FROM orders p
                      JOIN orders c ON c.hedge_of = p.id AND c.role='CHILD'
                     WHERE p.customerOrderRef=? 
                     ORDER BY c.id DESC LIMIT 1
                """, (str(r["cor"]),)).fetchone()
                con2.close()
                if rowc:
                    _ledger_link_child_by_parent_cor(str(r["cor"]), int(rowc["id"]))
            except Exception:
                pass

            fixed += 1
            # 📊 update vSERF/BookState
            try:
                _update_book_state(run_id="LIVE")
            except Exception: pass

        except Exception as e:
            _log_event("ERROR", "live_router", f"rehedge error: {e}")

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
            SELECT id, customerOrderRef, entry_bet_id, side, entry_odds, entry_stake
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

    if not rows: return 0

    for r in rows:
        try:
            status = get_bet_status(str(r["entry_bet_id"]))
            if status == "EXECUTION_COMPLETE":
                _orders_update_parent_matched(str(r["customerOrderRef"]))
                _log_event("INFO", "live_router",
                           f"[SYNC] flipped parent_ref={r['customerOrderRef']} to MATCHED")
                fixed += 1
        except Exception as e:
            _log_event("ERROR", "live_router",
                       f"sync_parent_matches error parent_ref={r['customerOrderRef']}: {e}")
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
               AND entry_status='matched'
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
                       "WHERE marketId=? AND entry_status='matched' AND (closed_at IS NULL OR closed_at='')",
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
                   SET entry_status='cancelled',
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

