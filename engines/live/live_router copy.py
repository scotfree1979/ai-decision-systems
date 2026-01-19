#!/usr/bin/env python3
# engines/live/live_router.py
from __future__ import annotations

import json, time, threading, random, sqlite3
from datetime import datetime, timezone
from typing import Optional, Tuple
from engines import price_math as pm
from engines.config_paths import auto_conn as _auto_conn
import requests
from engines.config_paths import auto_conn as _cp_auto_conn, q_retry as _cp_q_retry, autoscalp_db, connect_db
import inspect, sys, hashlib

print("LIVE_ROUTER __file__ =", __file__)
print("LIVE_ROUTER sys.path[0:5] =", sys.path[:5])

try:
    src = inspect.getsource(inspect.currentframe())
    print("LIVE_ROUTER source hash =", hashlib.sha256(src.encode()).hexdigest())
except Exception as e:
    print("LIVE_ROUTER source hash FAILED:", e)


# DB connection for AUTOSCALP orders table (GUI DB)
def _orders_conn():
    # Uses the canonical path from engines.config_paths.autoscalp_db()
    return _db()
# --- stake + mastery helpers (LIVE) ---------------------------------
def _fetch_live_bank(default: float = 0.0) -> float:
    """
    Read today's LIVE bank from GUI dashboard_tiles; fallback to default if missing.
    """
    try:
        con = _auto_conn(autoscalp_db(), timeout=6)
        con.row_factory = sqlite3.Row
        row = _q_retry(con, "SELECT bank FROM dashboard_tiles WHERE day=date('now') AND UPPER(mode)='LIVE' LIMIT 1"
        ).fetchone()
        con.close()
        return float(row["bank"] or 0.0) if row else float(default)
    except Exception:
        return float(default)

# --- BEGIN DB SHIM (no-callsite changes needed) -------------------------------
from engines.config_paths import (
    auto_conn as __cp_auto_conn,
    q_retry   as __cp_q_retry,
    autoscalp_db, connect_db
)

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

# ─────────────────────────────────────────────────────────────────────────────
# DB helpers (AutoScalp GUI DB, WAL, retries)
# ─────────────────────────────────────────────────────────────────────────────
import sqlite3
from engines.config_paths import autoscalp_db

# unified GUI DB connector (no name collision)
def _db() -> sqlite3.Connection:
    con = _cp_auto_conn()
    try:
        con.row_factory = sqlite3.Row
        _cp_q_retry(con, "PRAGMA journal_mode=WAL;")
        _cp_q_retry(con, "PRAGMA busy_timeout=8000;")
        _cp_q_retry(con, "PRAGMA synchronous=NORMAL;")
        _cp_q_retry(con, "PRAGMA read_uncommitted=1;")
    except Exception:
        pass
    return con

def _q(con: sqlite3.Connection, sql: str, params: tuple = ()):
    return _cp_q_retry(con, sql, params)

# use _db() for every orders/GUI DB open
def _orders_conn(): return _db()
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

def _stake_live(letter: str, *, phase: str = "PRE") -> tuple[float, str]:
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
        _q_retry(bdb, "INSERT INTO mastery_events(event_type, details_json, created_at) "
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

# ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: router housekeeping / reconciliation loop
# 📆 PATCHED: 2026-01-10 — add PRE-OFF unmatched parent timeout release
# ============================================================

from datetime import datetime, timezone, timedelta

UNMATCHED_PARENT_TIMEOUT_SECONDS = 300  # 5 minutes


def _release_unmatched_parents_by_age(conn, now_utc):
    """
    Cancel PRE-OFF parents that remain PLACED + UNMATCHED for >= 5 minutes.
    Ownership:
      - Cancellation + DB mutation: LiveRouter
      - Exposure release: BankState
    Idempotent:
      - Skips any parent with exit_status already set.
    """

    # --- select candidates (PRE-OFF only, unmatched, still open) ---
    rows = conn.execute(
        """
        SELECT
            id,
            marketId,
            selectionId,
            opened_at,
            entry_status,
            entry_matched_stake,
            entry_bet_id
        FROM orders
        WHERE role='PARENT'
          AND entry_status='PLACED'
          AND exit_status IS NULL
          AND (entry_matched_stake IS NULL OR entry_matched_stake=0)
          AND date(opened_at) = date('now','utc')
        """
    ).fetchall()

    for r in rows:
        parent_id, mid, sid, opened_at, entry_status, matched_stake, bet_id = r

        # --- defensive parsing ---
        try:
            opened_ts = datetime.fromisoformat(opened_at.replace("Z", "+00:00"))
        except Exception:
            # malformed timestamp → skip (do not risk cancelling incorrectly)
            continue

        age_sec = (now_utc - opened_ts).total_seconds()
        if age_sec < UNMATCHED_PARENT_TIMEOUT_SECONDS:
            continue

        # --- PRE-OFF guard: skip if market already in-play ---
        mkt = conn.execute(
            "SELECT isInplay FROM bf_market_book WHERE marketId=?",
            (mid,)
        ).fetchone()
        if mkt and int(mkt[0]) == 1:
            continue

        # --- cancel on Betfair (fail-fast, no retries) ---
        try:
            app_key, token = _keys()
            _cancel(app_key, token, str(bet_id))

        except Exception:
            # cancellation failure is terminal for this pass; do not mutate DB
            continue

        # --- DB mutation (terminal) ---
        conn.execute(
            """
            UPDATE orders
            SET
              exit_status='CANCELLED',
              exit_kind='TIMEOUT_UNMATCHED',
              closed_at=?
            WHERE id=?
              AND exit_status IS NULL
            """,
            (now_utc.isoformat(), parent_id)
        )

        # --- exposure release (mandatory) ---
        try:
            bank_state.release_parent(parent_id)
        except Exception:
            # exposure safety: log but continue (DB already reflects cancellation)
            pass

        # --- observability ---
        _emit_router_event(
            level="INFO",
            code="PARENT_TIMEOUT",
            message=f"PARENT_TIMEOUT_CANCELLED parent_id={parent_id} age_sec={int(age_sec)} marketId={mid} selectionId={sid}"
        )

# ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🧩 ACTION: ADD — cancel stale PLACING parents
# 📆 PATCHED: 2026-01-12 — release stuck PLACING exposure
# ============================================================

PLACING_TIMEOUT_SECONDS = 20

def _release_stale_placing(conn):
    """
    Cancel LIVE orders stuck in PLACING beyond timeout.
    This is the PRIMARY exposure leak fix.
    """

    now = datetime.now(timezone.utc)

    rows = conn.execute("""
        SELECT
            id,
            customerOrderRef,
            entry_bet_id,
            opened_at
        FROM orders
        WHERE mode='LIVE'
          AND UPPER(entry_status)='PLACING'
          AND opened_at IS NOT NULL
          AND date(opened_at) = date('now','utc')

          AND (
                strftime('%s','now')
                - strftime('%s', opened_at)
              ) >= ?
    """, (PLACING_TIMEOUT_SECONDS,)).fetchall()

    if not rows:
        return 0

    cancelled = 0

    for r in rows:
        oid   = r["id"]
        cor   = r["customerOrderRef"]
        betid = r["entry_bet_id"]

        # --- best-effort Betfair cancel ---
        if betid:
            try:
                app_key, token = _keys()
                _cancel(app_key, token, str(betid))
            except Exception:
                pass

        # --- DB mutation (terminal) ---
        conn.execute("""
            UPDATE orders
               SET entry_status='CANCELLED',
                   exit_status='CANCELLED',
                   exit_kind='STALE_PLACING',
                   closed_at=datetime('now','utc')
             WHERE id=?
               AND UPPER(entry_status)='PLACING'
        """, (oid,))

        # --- exposure release (MANDATORY) ---
        try:
            from engines.live import bank_state
            bank_state.release_parent(oid)
        except Exception:
            pass

        # --- observability ---
        try:
            _emit_router_event(
                level="WARN",
                code="STALE_PLACING_CANCEL",
                message=f"PLACING timeout cancelled id={oid} ref={cor}"
            )
        except Exception:
            pass

        cancelled += 1

    return cancelled


# ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: router housekeeping / reconciliation loop
# 📆 PATCHED: 2026-01-10 — cancel UNMATCHED parents on IN-PLAY transition
# ============================================================

from datetime import datetime, timezone

def _release_unmatched_parents_on_inplay(conn, now_utc):
    """
    Cancel parents that are still PLACED + UNMATCHED when their
    (marketId, selectionId) enters the IN-PLAY scope bucket.

    Ownership:
      - Detection: Scope (read-only)
      - Cancellation + DB mutation: LiveRouter
      - Exposure release: BankState

    Idempotent:
      - Skips any parent with exit_status already set.
    """

    # --- obtain current scope snapshot (read-only) ---
    # Assumes canonical scope state is already built and available
    # via the same object BUS uses. This function must not rebuild scope.
    try:
        from engines.decision_engine.scope import get_scope_state  # canonical accessor
        scope = get_scope_state() or {}
        in_play = set(scope.get("in_play") or [])
    except Exception:
        # If scope is unavailable, do nothing (safe no-op)
        return

    if not in_play:
        return

    # --- select candidates: PLACED, UNMATCHED, still open ---
    rows = conn.execute(
        """
        SELECT
            id,
            marketId,
            selectionId,
            entry_bet_id
        FROM orders
        WHERE role='PARENT'
          AND entry_status='PLACED'
          AND exit_status IS NULL
          AND (entry_matched_stake IS NULL OR entry_matched_stake=0)
          AND date(opened_at) = date('now','utc')
        """
    ).fetchall()

    for r in rows:
        parent_id, mid, sid, bet_id = r
        if not bet_id:
            continue

        # --- check scope transition ---
        if (str(mid), str(sid)) not in in_play:
            continue

        # --- cancel on Betfair (fail-fast) ---
        try:
            app_key, token = _keys()
            _cancel(app_key, token, str(bet_id))
        except Exception:
            continue

        # --- DB mutation (terminal) ---
        conn.execute(
            """
            UPDATE orders
            SET
              exit_status='CANCELLED',
              exit_kind='INPLAY_UNMATCHED',
              closed_at=?
            WHERE id=?
              AND exit_status IS NULL
            """,
            (now_utc.isoformat(), parent_id)
        )

        # --- exposure release (mandatory) ---
        try:
            from engines.live import bank_state
            bank_state.release_parent(parent_id)
        except Exception:
            pass

        # --- observability ---
        try:
            _emit_router_event(
                level="INFO",
                code="PARENT_INPLAY_CANCEL",
                message=(
                    f"INPLAY_UNMATCHED_CANCEL parent_id={parent_id} "
                    f"marketId={mid} selectionId={sid}"
                )
            )
        except Exception:
            pass


# ============================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _router_housekeeping_tick\(
# 📆 PATCHED: 2026-01-10 — invoke IN-PLAY unmatched cancel
# ============================================================

def _router_housekeeping_tick():
    """
    Existing housekeeping tick. Extended to cancel UNMATCHED parents
    on IN-PLAY transition, using scope state.
    """
    conn = _orders_conn()
    now_utc = datetime.now(timezone.utc)

    # --- existing housekeeping calls ---
    _sync_all_matches(conn)
    _sync_hedge_matches(conn)

    # --- existing PRE-OFF timeout rule ---
    _release_unmatched_parents_by_age(conn, now_utc)

    # --- NEW: IN-PLAY transition rule ---
    _release_unmatched_parents_on_inplay(conn, now_utc)

    # 🔥 NEW — kill stuck PLACING
    _release_stale_placing(conn)

    conn.commit()


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
        def _add(col, ddl): 
            if col not in cols: _q_retry(cur, f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
        _add("role",      "TEXT")          # 'PARENT'|'CHILD'
        _add("hedge_of",  "INTEGER")       # child -> parent id
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

# Make UTC explicitly timezone-aware for consistency
def _utcnow_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^import requests$
# --- PATCH START: add just below imports --------------------------------
# DB connection for AUTOSCALP orders table (GUI DB)
def _orders_conn():
    # Uses the canonical path from engines.config_paths.autoscalp_db()
    return _db()# --- PATCH END -----------------------------------------------------------

# === TRIPLE-HEADER PATCH ======================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _orders_conn():
# ============================================================================

# helper: resolve/create FK id in runs for a given string run_id
def _run_fk_id(run_id: str | None, mode: str = "LIVE") -> int:
    """
    Convert human run_id (e.g., 'LIVE-20250912-174250') into FK int (runs.id).
    We store the human id in runs.notes to make this idempotent.
    """
    rid_txt = (run_id or "").strip()
    con = _orders_conn()
    try:
        cur = con.cursor()
        # ensure table exists (compatible with your current schema)
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
        # find existing by notes
        row = _q_retry(cur, "SELECT id FROM runs WHERE notes=? LIMIT 1", (rid_txt,)).fetchone()
        if row:
            return int(row[0])
        # insert new
        _q_retry(cur, "INSERT INTO runs(started_at, mode, notes) VALUES(datetime('now','utc'), ?, ?)",
                 (str(mode or "LIVE"), rid_txt))
        con.commit()
        return int(cur.lastrowid)
    finally:
        try: con.close()
        except Exception: pass
# === END PATCH ================================================================


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

def _round_odds(
    odds: float | None,
    *,
    parent_id: int | None = None,
) -> float:
    """
    DB-FIRST odds normalisation.

    Rules:
    - Never raises
    - If odds is None → rehydrate from DB (parent_id required)
    - If DB also missing → HARD FLOOR 1.01
    - Always returns a valid Betfair ladder price
    """

    # --------------------------------------------------
    # 1️⃣ DB-first rehydration if missing
    # --------------------------------------------------
    if odds is None:
        if parent_id is not None:
            try:
                con = _orders_conn()
                row = con.execute(
                    "SELECT entry_odds FROM orders WHERE id=?",
                    (int(parent_id),)
                ).fetchone()
                con.close()
                if row and row[0] is not None:
                    odds = row[0]
            except Exception:
                pass

    # --------------------------------------------------
    # 2️⃣ Absolute safety floor
    # --------------------------------------------------
    try:
        x = float(odds)
    except Exception:
        x = 1.01

    if x < 1.01:
        x = 1.01

    # --------------------------------------------------
    # 3️⃣ Snap to ladder
    # --------------------------------------------------
    try:
        return float(pm.snap_to_tick(x))
    except Exception:
        t = _tick(x)
        return float(f"{round(round(x / t) * t, 2):.2f}")


def _calc_hedge_stake(parent_side: str, entry_odds: float, parent_stake: float, hedge_odds: float) -> float:
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
        q = "SELECT v FROM app_kv WHERE LOWER(k) IN ({}) ORDER BY ts DESC LIMIT 1".format(
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

def _keys() -> Tuple[str, str]:
    """
    Unified Betfair credential resolver:
      1) OS env (BETFAIR_* or plain APP_KEY / SESSION_TOKEN)
      2) GUI DB (app_kv) common key names
      3) daily_config constants
      4) upgrade_import_patch.get_session_token() as a last resort
    Raises RuntimeError if either is missing.
    """
    # 1) environment
    app_key = (
        os.environ.get("BETFAIR_APP_KEY")
        or os.environ.get("APP_KEY")
        or os.environ.get("X_APPLICATION")
        or None
    )
    token = (
        os.environ.get("BETFAIR_SESSION_TOKEN")
        or os.environ.get("SESSION_TOKEN")
        or os.environ.get("X_AUTHENTICATION")
        or None
    )

    # 2) GUI app_kv (autoscalp_gui.db)
    if not app_key:
        app_key = _kv_get_one(["app_key", "APP_KEY", "bf_app_key", "BF_APP_KEY", "betfair_app_key"])
    if not token:
        token = _kv_get_one([
            "session_token","SESSION_TOKEN","bf_session","BF_SESSION",
            "betfair_session_token","X-Authentication","X_AUTHENTICATION","auth_token","token"
        ])

    # 3) daily_config fallback
    if not app_key:
        try:
            import engines.daily_config as dc
            app_key = getattr(dc, "APP_KEY", None)
        except Exception:
            pass
    if not token:
        try:
            import engines.daily_config as dc
            get_tok = getattr(dc, "get_session_token", None)
            token = get_tok() if callable(get_tok) else None
        except Exception:
            pass

    # 4) final shim fallback (older GUI step stored via upgrade_import_patch)
    if not token:
        try:
            from engines.upgrade_import_patch import get_session_token  # type: ignore
            token = get_session_token()
        except Exception:
            pass

    if not app_key or not token:
        raise RuntimeError("Betfair credentials not available (APP_KEY / SESSION_TOKEN).")

    # opportunistically export to env for any downstream users in this process
    os.environ.setdefault("BETFAIR_APP_KEY", app_key)
    os.environ.setdefault("BETFAIR_SESSION_TOKEN", token)

    try:
        _log_event("INFO", "live_router",
                   f"creds resolved app={_mask(app_key)} tok={_mask(token)} (env/db/DC)")
    except Exception:
        pass

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

def _place(*, app_key: str, token: str, parent_ref: str):
    """
    Place a PARENT order on Betfair.

    DB-FIRST INVARIANT:
    - parent_ref == customerOrderRef
    - ALL execution data is loaded from the ORDERS table
    - No positional arguments
    - No raw sqlite3.connect
    """

    from engines.config_paths import connect_orders_db
    from engines import price_math as pm

    # --------------------------------------------------
    # 1️⃣ Load authoritative parent row (ORDERS DB)
    # --------------------------------------------------
    con = connect_orders_db(ro=True)
    try:
        row = con.execute(
            """
            SELECT
                marketId,
                selectionId,
                side,
                entry_odds,
                entry_stake
            FROM orders
            WHERE customerOrderRef = ?
              AND role = 'PARENT'
              AND entry_status = 'PLACING'
            """,
            (str(parent_ref),)
        ).fetchone()
    finally:
        con.close()

    if not row:
        raise RuntimeError(
            f"PLACE invariant violated: parent row missing for customerOrderRef={parent_ref}"
        )

    market_id    = row["marketId"]
    selection_id = row["selectionId"]
    side         = row["side"]
    odds         = row["entry_odds"]
    stake        = row["entry_stake"]

    # --------------------------------------------------
    # 2️⃣ Hard invariants (fail fast, no recovery)
    # --------------------------------------------------
    if market_id is None or selection_id is None:
        raise RuntimeError(
            f"PLACE invariant violated: marketId/selectionId NULL for {parent_ref}"
        )

    if odds is None or stake is None:
        raise RuntimeError(
            f"PLACE invariant violated: odds/stake NULL for {parent_ref}"
        )

    # --------------------------------------------------
    # 3️⃣ Build Betfair payload (DB → payload)
    # --------------------------------------------------
    params = {
        "marketId": str(market_id),
        "customerRef": str(parent_ref),
        "instructions": [{
            "selectionId": int(selection_id),
            "side": side.upper(),
            "orderType": "LIMIT",
            "limitOrder": {
                "size": float(stake),
                "price": _round_odds(odds, parent_id=parent_id),
                "persistenceType": "PERSIST",
            },
            "customerOrderRef": str(parent_ref),
        }]
    }


    # --------------------------------------------------
    # 4️⃣ RPC call
    # --------------------------------------------------
    resp = _rpc(app_key, token, "placeOrders", params)

    res    = resp.get("result") or {}
    status = res.get("status", "")
    ir     = (res.get("instructionReports") or [{}])[0] or {}
    bet_id = ir.get("betId")

    return (
        bet_id if status == "SUCCESS" and bet_id else None,
        {
            "status": status,
            "report": ir,
            "raw": resp,
        }
    )


def _list_current(app_key: str, token: str, bet_id: str) -> dict:
    return _rpc(app_key, token, "listCurrentOrders", {"betIds": [bet_id]})

def _cancel(app_key: str, token: str, bet_id: str) -> None:
    try:
        _rpc(app_key, token, "cancelOrders", {"betIds": [bet_id]})
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
            _router_housekeeping_tick()   # 🔥 THIS IS THE MISSING CALL
        except Exception as e:
            _log_event("ERROR", "live_router", f"housekeeping error: {e}")

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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_insert_parent_queued\(
# --- PATCH START: replace function ------------------------------------
def _orders_insert_parent_queued(run_id, market_id, selection_id, side, entry_odds, entry_stake, cor, *, source="LEGACY_STRATEGY"):
    """
    Upsert LIVE parent row as 'queued' with strategy source.
    """
    _ensure_orders_schema()
    fk = _run_fk_id(run_id, mode="LIVE")
    con = _orders_conn(); cur = con.cursor()
    try:
        _q_retry(cur, """
            INSERT INTO orders (
                customerOrderRef, run_id, mode, marketId, selectionId,
                side, entry_odds, entry_stake, entry_status, opened_at,
                role, source
            ) VALUES (?, ?, 'LIVE', ?, ?, ?, ?, ?, 'queued', ?, 'PARENT', ?)
            ON CONFLICT(customerOrderRef) DO UPDATE SET
                run_id=COALESCE(orders.run_id, excluded.run_id),
                mode='LIVE',
                marketId=COALESCE(excluded.marketId, orders.marketId),
                selectionId=COALESCE(excluded.selectionId, orders.selectionId),
                side=excluded.side,
                entry_odds=excluded.entry_odds,
                entry_stake=excluded.entry_stake,
                entry_status=COALESCE(orders.entry_status, 'queued'),
                opened_at=COALESCE(orders.opened_at, excluded.opened_at),
                role='PARENT',
                source=COALESCE(orders.source, excluded.source)
        """, (str(cor), int(fk), str(market_id), str(selection_id),
              side.upper(), float(entry_odds), float(entry_stake),
              _utcnow_str(), str(source)))
        con.commit()
        _orders_probe(cor, note="queued")
    except Exception as e:
        _log_event("ERROR", "live_router", f"orders upsert queued failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# ======================================================================
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: def _orders_update_parent_placed
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-01-14 — hard invariant: PLACED requires bet_id
#
# INVARIANT:
# - entry_status='PLACED' is ILLEGAL without a real Betfair bet_id
# - Violations are logged and downgraded to FAILED
# ======================================================================

def _orders_update_parent_placed(cor, bet_id):
    """
    Mark parent as PLACED **only if** a valid Betfair bet_id exists.

    HARD INVARIANT:
    - bet_id MUST be non-null
    - Otherwise this function MUST NOT write PLACED
    """

    if not bet_id:
        # 🔥 HARD STOP — this is a logic violation, not a soft failure
        _log_event(
            "ERROR",
            "live_router",
            f"[INVARIANT] attempted PLACED without bet_id ref={cor}"
        )
        _orders_update_parent_failed(cor, "NO_BET_ID_FROM_BETFAIR")
        return

    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()

    try:
        # Resolve run FK
        rid_row = _q_retry(
            cur,
            "SELECT id FROM runs WHERE mode='LIVE' ORDER BY datetime(started_at) DESC LIMIT 1"
        ).fetchone()
        fk = int(rid_row[0]) if rid_row else _run_fk_id("LIVE-AUTO", mode="LIVE")

        # Ensure skeleton exists
        _q_retry(cur, """
            INSERT INTO orders (customerOrderRef, run_id, mode, entry_status, opened_at, role)
            VALUES (?, ?, 'LIVE', 'QUEUED', ?, 'PARENT')
            ON CONFLICT(customerOrderRef) DO NOTHING
        """, (str(cor), int(fk), _utcnow_str()))

        # LEGAL transition → PLACED
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='PLACED',
                   entry_bet_id=?,
                   mode='LIVE',
                   role='PARENT',
                   run_id=COALESCE(run_id, ?)
             WHERE customerOrderRef=?
        """, (str(bet_id), int(fk), str(cor)))

        con.commit()
        _orders_probe(cor, note="placed", bet_id=bet_id)

    except Exception as e:
        _log_event(
            "ERROR",
            "live_router",
            f"orders update placed failed ref={cor}: {e}"
        )
    finally:
        try: con.close()
        except Exception: pass




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
# 🔎 SEARCH: ^def _orders_update_parent_matched\(
# --- PATCH START: replace function ------------------------------------
def _orders_update_parent_matched(cor):
    """Parent entry filled."""
    _ensure_orders_schema()
    con = _orders_conn(); cur = con.cursor()
    try:
        _q_retry(cur, """
            UPDATE orders
               SET entry_status='matched', mode='LIVE', role='PARENT'
             WHERE customerOrderRef=?
        """, (str(cor),))
        con.commit()
        _orders_probe(cor, note="parent_matched")
    except Exception as e:
        _log_event("ERROR", "live_router", f"orders update parent matched failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass
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
            SELECT side, entry_odds, entry_stake
            FROM orders
            WHERE customerOrderRef=? AND role='PARENT'
        """, (str(cor),)).fetchone()
        if not parent:
            return
        parent_side, parent_odds, parent_stake = parent
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
             WHERE hedge_of = (SELECT id FROM orders WHERE customerOrderRef=?)
        """, (realized, realized, str(cor)))
        con.commit()
        _orders_probe(cor, note="child_matched")
    except Exception as e:
        _log_event("ERROR", "live_router", f"orders update child matched failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def _orders_update_hedge_matched\(
# --- PATCH START: replace function ------------------------------------
def _orders_update_hedge_matched(*, cor: str, exit_side: str, exit_odds: float, exit_stake: float) -> None:
    _ensure_orders_schema()
    con = _orders_conn(); con.row_factory = sqlite3.Row
    try:
        cur = con.cursor()
        p = _q_retry(cur, """SELECT id, side, entry_odds, entry_stake, marketId, selectionId,
                                  COALESCE(source,'') AS source, COALESCE(run_id,'') AS run_id
                            FROM orders WHERE customerOrderRef=? LIMIT 1""", (str(cor),)).fetchone()
        if not p:
            _log_event("ERROR","live_router", f"hedge_matched: parent missing ref={cor}")
            return
        entry_side = (p["side"] or "").upper()
        E, S  = float(p["entry_odds"] or 0.0), float(p["entry_stake"] or 0.0)
        H, S2 = float(exit_odds or 0.0), float(exit_stake or 0.0)

        if entry_side == "LAY":
            win  = S2*(H-1.0) - S*(E-1.0)
            lose = S - S2
        else:
            win  = (E-1.0)*S - (H-1.0)*S2
            lose = -S + S2
        realized = round(min(win, lose), 2)

        _q_retry(cur, """
            UPDATE orders
               SET exit_status='matched',
                   exit_odds=?, exit_stake=?,
                   closed_at=COALESCE(closed_at, datetime('now','utc')),
                   realized_pnl=?, net_pl=COALESCE(net_pl,0.0)+?, mode='LIVE'
             WHERE customerOrderRef=?
        """, (H, S2, realized, realized, str(cor)))
        con.commit()
        _orders_probe(cor, note="hedge_matched")
        # Mastery: record good trade outcome
        try:
            _mastery_log("trade_outcome", {
                "result": "good",
                "market": str(p["marketId"]), "runner": str(p["selectionId"]),
                "entry_odds": E, "entry_stake": S,
                "exit_odds": H, "exit_stake": S2,
                "realized": realized, "source": str(p["source"] or "")
            })
        except Exception:
            pass


        # strategies_performance EXIT (LIVE)
        try:
            from engines.config_paths import autoscalp_db as _adb_path  # just to ensure import OK
            _ = _adb_path()
            _sp_log_exit_live(run_id=p["run_id"], strategy=p["source"] or "LEGACY",
                              mid=str(p["marketId"]), sid=str(p["selectionId"]), pnl=realized)
        except Exception:
            pass
    except Exception as e:
        _log_event("ERROR", "live_router", f"orders update hedge matched failed ref={cor}: {e}")
    finally:
        try: con.close()
        except Exception: pass

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
# --- PATCH START: add helper below parent helpers ----------------------
def _orders_insert_child_live(parent_cor: str, *, market_id: str, selection_id: str,
                              side: str, odds: float, stake: float,
                              bet_id: str, source: str = "LEGACY_STRATEGY") -> Optional[int]:
    """
    Create a CHILD row linked to the parent (hedge_of=parent.id), mark live.
    Returns child id.
    """
    _ensure_orders_schema()
    con = _orders_conn(); con.row_factory = sqlite3.Row
    try:
        parent = _q_retry(con, "SELECT id, run_id FROM orders WHERE customerOrderRef=? LIMIT 1", (str(parent_cor),)).fetchone()
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
              entry_bet_id, role, hedge_of, source
            ) VALUES (?, ?, 'LIVE', ?, ?, ?, ?, ?, 'live', ?, ?, 'CHILD', ?, ?)
        """, (
            _ref("CHILD"), int(fk), str(market_id), str(selection_id),
            side.upper(), float(odds), float(stake), _utcnow_str(), str(bet_id or ""), pid, str(source)
        ))
        child_id = cur.lastrowid
        _q_retry(cur, """
            UPDATE orders
               SET exit_bet_id = ?,
                   exit_odds   = ?,
                   exit_stake  = ?
             WHERE id = ?
        """, (str(bet_id or ""), float(odds), float(stake), pid))
        con.commit()
        return int(child_id)
    except Exception as e:
        _log_event("ERROR", "live_router", f"insert_child error parent_ref={parent_cor}: {e}")
        return None
    finally:
        try: con.close()
        except Exception: pass

# --- PATCH END ----------------------------------------------------------

def _sweep_close_finished_markets(grace_min: int = 15) -> tuple[int, int]:
    """
    Belt-and-braces closer:
      - For markets with off_at_utc <= now - grace_min, cancel any LIVE QUEUED/PLACED entries.
      - For those markets, mark still-open parents as SETTLED (so they stop counting as 'open risk').
    Returns (n_cancelled, n_settled).
    """
    try:
        # 1) which markets are 'finished' (bets.db schedule, UTC)
        bdb = connect_db(ro=True)
        bdb.row_factory = sqlite3.Row
        rows = _q_retry(bdb, "SELECT marketId FROM markets_schedule "
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

        # 2) cancel QUEUED/PLACED (unmatched liability)
        n_cancel = _q_retry(con, f"UPDATE orders SET entry_status='cancelled', "
            f"    closed_at=COALESCE(closed_at, datetime('now','utc')), "
            f"    mode='LIVE' "
            f"WHERE mode='LIVE' AND marketId IN ({qph}) "
            f"  AND UPPER(COALESCE(entry_status,'')) IN ('QUEUED','PLACED')",
            tuple(mids)
        ).rowcount

        # 3) mark unmatched-hedge parents as SETTLED (to drop from 'open risk')
        n_settle = _q_retry(con, f"UPDATE orders SET exit_status='settled', "
            f"    closed_at=COALESCE(closed_at, datetime('now','utc')), "
            f"    mode='LIVE' "
            f"WHERE mode='LIVE' AND marketId IN ({qph}) "
            f"  AND COALESCE(role, CASE WHEN hedge_of IS NULL OR hedge_of='' THEN 'PARENT' ELSE 'CHILD' END)='PARENT' "
            f"  AND UPPER(COALESCE(entry_status,''))='MATCHED' "
            f"  AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')",
            tuple(mids)
        ).rowcount

        con.commit(); con.close()
        if n_cancel or n_settle:
            _log_event("INFO", "live_router",
                       f"_sweep_close_finished_markets: cancelled={n_cancel} settled={n_settle} mids={len(mids)}")
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

# live_router.py (module scope)
ROUTER_METRICS = {
    "entered": 0,
    "gate_reached": 0,
    "gate_blocked": 0,
    "betfair_called": 0,
}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py
# 🔎 SEARCH: ^def place_parent_and_hedge\(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-01-18 — Parent placement only (no child logic)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def place_parent_and_hedge(
    *,
    parent_ref: str,
    run_id: str | None = None,
    _ctx: dict | None = None,
) -> tuple[None, str]:

    """
    ROUTER — PARENT PLACEMENT ONLY

    - No child logic
    - No hedge logic
    - No background threads
    - No legacy kwargs
    - DB is already authoritative
    """
    ROUTER_METRICS["entered"] += 1

    from engines.config_paths import open_auto_db
    from engines.live import bank_state

    # --------------------------------------------------
    # 1️⃣ Load parent row (authoritative)
    # --------------------------------------------------
    con = open_auto_db(rw=False)
    row = con.execute(
        """
        SELECT
            customerOrderRef,
            marketId,
            selectionId,
            side,
            entry_odds,
            entry_stake,
            engine,
            required_exposure,
            id
        FROM orders
        WHERE customerOrderRef = ?
          AND role = 'PARENT'
          AND entry_status = 'PLACING'
        """,
        (str(parent_ref),)
    ).fetchone()
    con.close()

    if not row:
        _orders_update_parent_failed(parent_ref, "PARENT_ROW_MISSING")
        return None, ""

    (
        parent_ref,
        market_id,
        selection_id,
        side,
        entry_odds,
        stake,
        engine,
        required_exposure,
        parent_id,
    ) = row

    # --------------------------------------------------
    # 2️⃣ Exposure gate (DB-first, authoritative)
    # --------------------------------------------------

    try:
        con = open_auto_db(rw=False)
        row = con.execute(
            "SELECT required_exposure FROM orders WHERE id=?",
            (int(parent_id),)
        ).fetchone()
        con.close()
    except Exception:
        _orders_update_parent_failed_by_id(parent_id, "EXPOSURE_LOOKUP_FAILED")
        return None, parent_ref

    if not row or row[0] is None:
        _orders_update_parent_failed_by_id(parent_id, "REQUIRED_EXPOSURE_MISSING")
        return None, parent_ref

    required_exposure = float(row[0])

    if not bank_state.can_place(engine, required_exposure):
        _orders_update_parent_failed_by_id(parent_id, "INSUFFICIENT_EXPOSURE")
        return None, parent_ref

    ROUTER_METRICS["gate_reached"] += 1


    # --------------------------------------------------
    # 3️⃣ Place on Betfair
    # --------------------------------------------------
    ROUTER_METRICS["betfair_called"] += 1
    app_key, token = _keys()

    bf_bet_id, detail = _place(
        app_key=app_key,
        token=token,
        parent_ref=parent_ref,
    )


    if not bf_bet_id:
        err = (
            (detail.get("instructionReports") or [{}])[0]
            .get("errorCode", "BETFAIR_REJECTED")
        )
        _orders_update_parent_failed_by_id(parent_id, err)
        return None, parent_ref

    # --------------------------------------------------
    # 4️⃣ Mark PLACED
    # --------------------------------------------------
    _orders_update_parent_placed(parent_ref, bf_bet_id)

    # --------------------------------------------------
    # 5️⃣ Reserve exposure
    # --------------------------------------------------
    try:
        bank_state.on_parent_placed(
            engine=str(engine),
            parent_id=int(parent_id),
        )
    except Exception as e:
        _orders_update_parent_failed_by_id(
            parent_id,
            f"EXPOSURE_RESERVE_FAILED:{e}",
        )
        return None, parent_ref

        ROUTER_METRICS.setdefault("placed", 0)
        ROUTER_METRICS["placed"] += 1

    return None, parent_ref


# ------------------------------------------------------------------
# LEGACY: historical inline BG hedge logic (no longer executed)
# Child placement is now handled by router child worker / rehedge loop
# Kept temporarily for reference and comparison
# ------------------------------------------------------------------




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
               AND entry_status='MATCHED'
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
            hedge_odds = _round_odds(hedge_odds, parent_id=r["id"])
            hedge_stake = _calc_hedge_stake(side_p, eo, st, hedge_odds)

            href = _ref(hedge_side)
            h_bet_id, _ = _place(app_key, token, mid, sid, hedge_side, hedge_odds, hedge_stake, href, persistence="PERSIST")
            if not h_bet_id:
                continue

            _orders_insert_child_live(parent_cor=str(r["cor"]), market_id=mid, selection_id=sid,
                                      side=hedge_side, odds=hedge_odds, stake=hedge_stake,
                                      bet_id=h_bet_id, source=src)
            fixed += 1
        except Exception as e:
            _log_event("ERROR", "live_router", f"rehedge error: {e}")

    return fixed


# --- PATCH END ----------------------------------------------------------

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/live_router.py  (just below imports)
# --- PATCH START: path logger ------------------------------------------
_DB_PATH_LOGGED = False
def _log_db_path_once():
    global _DB_PATH_LOGGED
    if _DB_PATH_LOGGED: return
    try:
        _log_event("INFO", "live_router", f"orders DB path = {autoscalp_db()}")
    except Exception:
        pass
    _DB_PATH_LOGGED = True
# --- PATCH END ----------------------------------------------------------

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

# force final binding after full module load
globals()["place_parent_and_hedge"] = place_parent_and_hedge


4020 lines.

Okay, so there are 4,020 lines, right, so then if this code is 3980 to 420, it should be at the bottom of the file.

Now, can you confirm that the said and the actual file don't match?

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

# force final binding after full module load
globals()["place_parent_and_hedge"] = place_parent_and_hedge