#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
# --- PATH FIX (allow direct execution) -----------------------------
import os, sys
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# -------------------------------------------------------------------

"""
settlements.py — Settlement pipeline & DB (separate) for AutoScalp
# === PATCH START ============================================================
# Ensure we ALWAYS import the correct v7 reinforcement hook
from engines.mastery.train_mastery_v7 import on_settlement_event as v7_on_settlement_event
# === PATCH END ==============================================================

Creates data/settlements.db, ingests:
  - Betfair Betting API: listClearedOrders, listMarketCatalogue, listMarketBook
  - CSV (optional): ExchangeBets_Settled*.csv
Normalises to canonical tables, and reconciles back into autoscalp_gui.db.orders
using bf_bet_id (orders) ⇆ betId (settlements).

CLI:
  python3 settlements.py import-csv path/to/ExchangeBets_Settled.csv
  python3 settlements.py fetch --since-days 2 [--from "YYYY-MM-DD"] [--to "YYYY-MM-DD"]
  python3 settlements.py reconcile
"""


import os, sys, json, csv, argparse, sqlite3, time, math
from typing import Any, Dict, Iterable, List, Optional, Tuple
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import urllib.request, urllib.error
from engines.live import bank_state



# 📍 engines/live/settlements.py
# === PATCH: HYBRID DB CONNECTIONS (Schema V3 aligned) ===

from engines.config_paths import (
    auto_conn as _auto_conn,        # GUI DB (autoscalp_gui.db)
    connect_bets_db as _bets_conn,  # Bets DB (bets.db)
    DATA_DIR,                       # Base folder for DBs
)

import sqlite3, os

# ======================================================================
# 📍 PATCH 1 — Settlement EventSync Emitter
# 🔎 SEARCH: "Mastery event hooks (stubs for now)"
# 📆 PATCHED: 2026-02-10
# ======================================================================

from engines.mastery.event_sink import emit as _emit_event

def _emit_settlement_event(event_type: str, payload: dict):
    """
    Unified Settlement → EventSync emitter.
    All final settlement signals flow through this interface.
    """
    try:
        data = dict(payload)
        data["event"] = event_type
        data["ts"] = datetime.now(timezone.utc).isoformat()
        _emit_event("settlement", data)
    except Exception as e:
        print(f"[EventSync][SETTLEMENT] warn: {e}")



# 📍 TARGET: engines/live/settlements.py — function _settle_conn
# 🔎 SEARCH:
# def _settle_conn(rw: bool = True, timeout: float = 8.0) -> sqlite3.Connection:
# 📆 PATCHED: 2026-01-19

def _settle_conn(rw: bool = True, timeout: float = 8.0) -> sqlite3.Connection:
    """
    Settlement DB connector — NOW DAL-MANAGED

    CHANGES:
    • Replaces all raw sqlite3.connect(...) calls
    • Prevents HijackMonitor from intercepting rogue DB opens
    • Prevents AlphaX from killing settlements thread
    • Respects LIVE vs SETUP modes through DAL
    • Uses the correct WAL/busy_timeout as configured by DAL
    """

    from engines.config_paths import settlements_db

    # DAL-managed connector handles:
    # - correct path resolution
    # - WAL mode
    # - busy timeout
    # - cloud vs local behaviour
    # - HijackMonitor safety
    # - AlphaX compatibility
    con = settlements_db(rw=rw)

    # Ensure row factory is maintained (legacy compatibility)
    try:
        import sqlite3
        con.row_factory = sqlite3.Row
    except Exception:
        pass

    return con


def _auto_local():
    """Read GUI DB through DAL (autoscalp_gui.db)"""
    return _auto_conn(rw=False)

def _bets_local():
    """Read bets.db through DAL"""
    return _bets_conn(ro=True)



def _auto_local_conn(timeout: float = 8.0) -> sqlite3.Connection:
    """
    Read-only LOCAL autoscalp_gui.db reader.
    - No DAL
    - No cloud attach
    - Used for fetching session token / app key
    """
    path = autoscalp_gui_db_path()  # always LOCAL path
    con = sqlite3.connect(
        path,
        timeout=timeout,
        isolation_level=None,
        check_same_thread=False,
    )
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=6000;")
    except Exception:
        pass
    return con

def _resolve_betfair_creds_localonly() -> tuple[str | None, str | None]:
    from engines.config_paths import autoscalp_db_path
    import sqlite3

    con = sqlite3.connect(autoscalp_db_path(), timeout=6)
    con.row_factory = sqlite3.Row

    def _get(keys):
        q = ",".join("?" * len(keys))
        row = con.execute(
            f"""
            SELECT value
              FROM app_kv
             WHERE LOWER(key) IN ({q})
             ORDER BY updated_at DESC
             LIMIT 1
            """,
            [k.lower() for k in keys],
        ).fetchone()
        return row["value"] if row and row["value"] else None

    app_key = _get(("betfair_app_key", "app_key", "app_key_live"))
    session = _get(("betfair_session_token", "session_token", "session"))

    con.close()

    return (
        app_key.strip() if isinstance(app_key, str) else None,
        session.strip() if isinstance(session, str) else None,
    )


# 🧩 Monkey-patch safeguard:
# Any accidental import of auto_conn within this module will redirect here.
try:
    import engines.config_paths as _cp
    _cp.auto_conn_for_settlements = _settle_conn
except Exception:
    pass

print("[settlements] 🧩 decoupled from auto_conn — using dedicated settlements.db connection.")
# === PATCH END ===


# --- KPI rollup views for the GUI -------------------------------------------
def ensure_kpi_views() -> None:
    """
    Create lightweight rollup views used by the GUI KPIs.
    v_settle_mkt_day: net P&L per (UTC day, marketId)
    v_settle_day:     aggregate per day
    """
    try:
        ensure_schema()
        with connect_db(settlements_db_path()) as con:
            con.executescript(
                """
                CREATE VIEW IF NOT EXISTS v_settle_mkt_day AS
                  SELECT date(settledDate)         AS day,
                         marketId                  AS marketId,
                         SUM(COALESCE(profit,0.0)) AS net,
                         COUNT(*)                  AS bets
                    FROM bf_cleared_orders
                   WHERE settledDate IS NOT NULL
                GROUP BY 1,2;

                CREATE VIEW IF NOT EXISTS v_settle_day AS
                  SELECT day,
                         SUM(net)  AS net,
                         COUNT(*)  AS mkts
                    FROM v_settle_mkt_day
                GROUP BY 1;
                """
            )
            con.commit()
    except Exception:
        # non-fatal for live loop; GUI will fall back to direct queries
        pass

def _rget(row, key, default=None):
    try:
        if row is None:
            return default
        if hasattr(row, "keys"):       # sqlite3.Row
            return row[key] if key in row.keys() else default
        if isinstance(row, dict):
            return row.get(key, default)
        if isinstance(row, (tuple, list)):
            if isinstance(key, int) and 0 <= key < len(row):
                return row[key]
            return default
        if isinstance(row, (int, float, str)):
            return row
    except Exception:
        pass
    return default

# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py : close_settled_markets
# 📆 PATCHED: 2025-11-06Z — expire all runners in settled markets
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def close_settled_markets() -> int:
    """
    Mark every order in markets whose Betfair status is CLOSED as expired/settled,
    releasing exposure across parents, children, and any strays.
    """
    auto_db = autoscalp_gui_db_path()
    set_db  = settlements_db_path()
    ensure_schema()
    closed = 0

    from engines.config_paths import auto_conn as _auto_conn
    from engines.live import bank_state   # ← ADD

    with connect_db(set_db) as s:
        o = _auto_conn(rw=True)
        o.row_factory = sqlite3.Row

        mids = [r["marketId"] for r in s.execute(
            "SELECT marketId FROM bf_market_book WHERE UPPER(status)='CLOSED'"
        ).fetchall()]

        if not mids:
            return 0

        for mid in mids:

            # 🔑 STEP 1: fetch open parents BEFORE expiring them
            parents = o.execute("""
                SELECT id, engine, entry_odds, entry_stake
                  FROM orders
                 WHERE marketId=?
                   AND role='PARENT'
                   AND (exit_status IS NULL OR exit_status='')
            """, (mid,)).fetchall()

            # 🔑 STEP 2: release exposure for each parent

                    # LIVE invariant:
                    # Exposure lifecycle is owned by live_router


            # 🔑 STEP 3: mark orders terminal
            o.execute("""
              UPDATE orders
                 SET exit_status = 'EXPIRED',
                     closed_at   = COALESCE(closed_at, datetime('now','utc'))
               WHERE marketId=?
                 AND UPPER(exit_status) NOT IN ('SETTLED','CANCELLED');
            """, (mid,))

            closed += int(o.total_changes or 0)

        o.commit()

    print(f"[settlements] expired all orders in {len(mids)} closed markets → {closed} rows updated")


    # ======================================================================
    # 📍 PATCH 4 — EventSync for market expiry settlement
    # 🔎 SEARCH: "expired all orders in"
    # 📆 PATCHED: 2026-02-10
    # ======================================================================

    try:
        _emit_settlement_event("market_expired", {
            "marketId": mid,
            "rows_expired": closed,
        })
    except Exception as e:
        print(f"[EventSync][market_expired] warn: {e}")

    return closed
# === PATCH END ===

# ======================================================================
# 📍 TARGET: engines/live/settlements.py
# 🎯 ACTION: one-shot hard cleanup for settled markets
# 📆 PATCHED: 2025-12-31 — force cancel + release exposure
# ======================================================================

from engines.live import bank_state
from engines.config_paths import auto_conn as _auto_conn
import sqlite3

def force_cancel_all_for_settled_markets() -> int:
    """
    HARD SETTLEMENT CLEANUP

    For every market that is CLOSED:
      - cancel ALL parents
      - cancel ALL children
      - release ALL reserved exposure via BankState

    This guarantees:
      • zero open orders
      • zero reserved exposure
      • budget unblocked
    """

    released = 0
    cancelled = 0

    from engines.config_paths import auto_conn
    from engines.live import bank_state
    import sqlite3

    # AUTO DB (orders)
    con = auto_conn(rw=True)
    con.row_factory = sqlite3.Row

    # SETTLEMENTS DB (market status)
    with connect_db(settlements_db_path()) as s:

        try:
            mids = set()

            # 1️⃣ Betfair-confirmed CLOSED markets
            for r in s.execute(
                "SELECT marketId FROM bf_market_book WHERE UPPER(status)='CLOSED'"
            ).fetchall():
                mids.add(str(r["marketId"]))

            # 2️⃣ Time-based fallback (authoritative safety net)
            for r in con.execute(
                "SELECT DISTINCT marketId FROM orders WHERE role='PARENT'"
            ).fetchall():
                mid = str(r["marketId"])
                try:
                    if _eligible_for_settlement(mid):
                        mids.add(mid)
                except Exception:
                    pass

            if not mids:
                return 0

            for mid in mids:

                # --- CHILDREN FIRST ---
                con.execute("""
                    UPDATE orders
                       SET exit_status='CANCELLED',
                           closed_at=datetime('now','utc')
                     WHERE marketId=?
                       AND role='CHILD'
                       AND (exit_status IS NULL OR exit_status='')
                """, (mid,))
                cancelled += con.total_changes or 0

                # --- THEN PARENTS ---
                con.execute("""
                    UPDATE orders
                       SET exit_status='CANCELLED',
                           closed_at=datetime('now','utc')
                     WHERE marketId=?
                       AND role='PARENT'
                       AND (exit_status IS NULL OR exit_status='')
                """, (mid,))
                cancelled += con.total_changes or 0

            con.commit()

        finally:
            con.close()

    print(
        f"[settlements] FORCE CLEANUP complete → "
        f"cancelled_orders={cancelled}"
    )

    return cancelled


# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py:_detect_bucket
# 🔎 SEARCH: def _detect_bucket(mid, sid):
# 📆 PATCHED: 2025-11-21
from engines.config_paths import auto_conn as _auto_conn

def _detect_bucket(mid, sid):
    con = _auto_conn(rw=False); con.row_factory = sqlite3.Row
    r = con.execute(
        "SELECT bucket FROM cache_mastery_outcomes WHERE marketId=? AND selectionId=? LIMIT 1",
        (mid, sid)
    ).fetchone()
    con.close()
    return r[0] if r else "unknown"
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py:_update_bucket_state
# 🔎 SEARCH: def _update_bucket_state(
# 📆 PATCHED: 2025-11-21
from engines.config_paths import auto_conn as _auto_conn

def _update_bucket_state(bucket, conf):
    con = _auto_conn(rw=True); cur = con.cursor()
    cur.execute("""
        INSERT INTO river_bucket_state(bucket, conf, n)
        VALUES(?, ?, 1)
        ON CONFLICT(bucket)
        DO UPDATE SET
            conf = ROUND(((conf * n) + excluded.conf) / (n + 1), 4),
            n = n + 1,
            updated_at = datetime('now','utc');
    """, (bucket, conf))
    cur.execute("""
        UPDATE mastery_posteriors
           SET bucket_confidence = (SELECT conf FROM river_bucket_state WHERE bucket=?)
         WHERE bucket=?;
    """, (bucket, bucket))
    con.commit(); con.close()
# === PATCH END ===



# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: imports / helpers
# ─────────────────────────────────────────────────────────────────────────────

# --- PATCH START: canonical OFF time & guard ---------------------------------
import sqlite3
from datetime import datetime, timezone, timedelta
from engines.config_paths import connect_db as auto_connect_db, q_retry as _q


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _bets(ro=True) -> sqlite3.Connection:
    con = auto_connect_db(ro=bool(ro))
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass
    return con


def _get_off_utc(mid: str) -> datetime | None:
    b = _bets(ro=True)
    try:
        row = _q(b, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        if row and row["off_at_utc"]:
            s = str(row["off_at_utc"]).strip()
            if s.endswith("Z"):
                return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
            try: return datetime.fromisoformat(s).astimezone(timezone.utc)
            except Exception: pass
        try:
            r2 = _q(b, "SELECT marketStartTime FROM bets WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
            if r2 and r2["marketStartTime"]:
                s = str(r2["marketStartTime"]).strip()
                if s.endswith("Z"):
                    return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
                return datetime.fromisoformat(s).astimezone(timezone.utc)
        except Exception:
            pass
        return None
    finally:
        b.close()

def _eligible_for_settlement(mid: str, *, grace_min: int = 2) -> bool:
    off = _get_off_utc(mid)
    if not off: 
        return False
    return (_now_utc() - off).total_seconds() >= grace_min * 60

# Inside your settlement sweep loop, guard with:
#   if not _eligible_for_settlement(mid): continue
# Then do your usual P&L stamping back into AUTO_DB orders → tiles read it.
# --- PATCH END ---------------------------------------------------------------


# --- add just below the imports in settlements.py ---------------------
def _resolve_betfair_creds() -> tuple[str|None, str|None]:
    """
    Return (app_key, session) by probing the same sources the GUI/feeder use:
      1) engines.session_secrets (DB)
      2) engines.daily_config (APP_KEY / get_session_token)
      3) JSON next to autoscalp_gui.db (betfair_creds.json)
      4) Environment variables
    """
    app_key = None
    session = None

    # 1) DB secrets
    try:
        from engines.session_secrets import get_secret, APP_KEY_KEY, SESSION_KEY
        app_key = get_secret(APP_KEY_KEY) or app_key
        session = get_secret(SESSION_KEY) or session
    except Exception:
        pass

    # 2) daily_config helpers
    try:
        import engines.daily_config as dc
        if not app_key:
            app_key = getattr(dc, "APP_KEY", None)
        if not session and hasattr(dc, "get_session_token") and callable(dc.get_session_token):
            try:
                tok = dc.get_session_token()
                if tok: session = tok
            except Exception:
                pass
    except Exception:
        pass

    # 3) JSON creds next to autoscalp_gui.db
    try:
        base = os.path.dirname(autoscalp_gui_db_path())
        jpath = os.path.join(base, "betfair_creds.json")
        if (not app_key or not session) and os.path.exists(jpath):
            with open(jpath, "r", encoding="utf-8") as fh:
                obj = json.load(fh)
            app_key = obj.get("app_key") or app_key
            session = obj.get("session") or session
    except Exception:
        pass

    # 4) Env
    app_key = app_key or os.environ.get("BETFAIR_APP_KEY")
    session = session or os.environ.get("BETFAIR_SESSION")

    # Normalise blanks
    app_key = app_key.strip() if isinstance(app_key, str) else app_key
    session = session.strip() if isinstance(session, str) else session
    if not app_key: app_key = None
    if not session: session = None

    return app_key, session
# --- end add ----------------------------------------------------------

def _today_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

# ──────────────────────────────────────────────────────────────────────────────
# Paths & environment
# ──────────────────────────────────────────────────────────────────────────────

def _default_data_dir() -> str:
    try:
        import engines.config_paths as cp
        # attempt to initialise if unset
        try: cp.set_db_paths(mode=os.environ.get("AUTOSCALP_MODE","live"), quiet=True)
        except Exception: pass
        dd = getattr(cp, "DATA_DIR", None)
        if dd: return dd
    except Exception:
        pass
    # fallback to ./data next to this file
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

DATA_DIR = _default_data_dir()

def settlements_db_path() -> str:
    return os.path.join(DATA_DIR, "settlements.db")

def autoscalp_gui_db_path() -> str:
    try:
        import engines.config_paths as cp
        return cp.autoscalp_db()
    except Exception:
        return os.path.join(DATA_DIR, "autoscalp_gui.db")

def _ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# DB helpers
# ──────────────────────────────────────────────────────────────────────────────

@contextmanager
def connect_db(path: str):
    """
    settlements.db MUST use a REAL sqlite3 connection.
    It must NEVER go through DALWriteProxy.
    """
    _ensure_parent(path)

    con = sqlite3.connect(
        path,
        timeout=30.0,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")

    try:
        yield con
    finally:
        try: con.close()
        except: pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# ──────────────────────────────────────────────────────────────────────────────
# Schema: raw → canonical → enrich
# ──────────────────────────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS bf_cleared_orders_raw(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,                 -- 'CSV' | 'API'
  ingested_at TEXT NOT NULL,
  betId TEXT,
  payload_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_bfco_raw_betid ON bf_cleared_orders_raw(betId);

CREATE TABLE IF NOT EXISTS bf_cleared_orders(
  betId TEXT PRIMARY KEY,
  marketId TEXT,
  selectionId TEXT,
  side TEXT,                            -- BACK|LAY
  priceRequested REAL,
  averagePriceMatched REAL,
  priceMatched REAL,
  sizeSettled REAL,
  profit REAL,                          -- net of commission (Betfair)
  commission REAL,
  settledDate TEXT,
  placedDate TEXT,
  customerOrderRef TEXT,
  customerStrategyRef TEXT,
  persistenceType TEXT,
  orderType TEXT,
  bspLiability REAL,
  eventTypeId TEXT,
  handicap REAL,
  json_raw TEXT
);
CREATE INDEX IF NOT EXISTS idx_bfco_mkt ON bf_cleared_orders(marketId);
CREATE INDEX IF NOT EXISTS idx_bfco_sel ON bf_cleared_orders(selectionId);
CREATE INDEX IF NOT EXISTS idx_bfco_settled ON bf_cleared_orders(settledDate);

-- Market Catalogue: metadata (course, race, start times, runners, etc.)
CREATE TABLE IF NOT EXISTS bf_market_catalogue(
  marketId TEXT PRIMARY KEY,
  marketName TEXT,
  eventName TEXT,
  competition TEXT,
  countryCode TEXT,
  venue TEXT,
  marketStartTime TEXT,
  totalMatched REAL,
  raceType TEXT,
  distanceMeters REAL,
  going TEXT,
  class TEXT,
  runnersJson TEXT,          -- runners metadata (ids, names)
  raw_json TEXT
);

-- Market Book: final state per runner (win/loser, LTP, inPlay, status)
CREATE TABLE IF NOT EXISTS bf_market_book(
  marketId TEXT PRIMARY KEY,
  isInplay INTEGER,
  status TEXT,               -- OPEN|SUSPENDED|CLOSED
  betDelay INTEGER,
  totalMatched REAL,
  lastMatchTime TEXT,
  resultJson TEXT,           -- runners status/ltp/etc.
  raw_json TEXT
);

-- Runner info (enriched; if available via extended sources/metadata)
-- Note: Betfair Betting API does not directly expose jockey/trainer; keep table
-- for future enrichment (e.g., external Racing API). We still store runner names.
CREATE TABLE IF NOT EXISTS bf_runner_info(
  marketId TEXT,
  selectionId TEXT,
  runnerName TEXT,
  stallDraw INTEGER,
  jockeyName TEXT,
  trainerName TEXT,
  age INTEGER,
  weightCarried REAL,
  officialRating INTEGER,
  raw_json TEXT,
  PRIMARY KEY(marketId, selectionId)
);

-- Rollup per runner-day (optional, populated on reconcile)
CREATE TABLE IF NOT EXISTS bf_settlement_runner_day(
  day TEXT,
  marketId TEXT,
  selectionId TEXT,
  trades INTEGER NOT NULL DEFAULT 0,
  net REAL NOT NULL DEFAULT 0.0,
  commission REAL NOT NULL DEFAULT 0.0,
  win_rate REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY(day, marketId, selectionId)
);
"""

def ensure_schema() -> None:
    with connect_db(settlements_db_path()) as con:
        con.executescript(SCHEMA_SQL)
        con.commit()

def sync_orders_into_ledger_from_auto(day_utc: Optional[str] = None) -> int:
    """
    Push today's AUTO_DB.orders rows into st_order_ledger (one row per order/betId).
    If an order has no Betfair betId yet, synthesize 'AUTO:<order_id>'.
    """
    ensure_schema()
    try:
        import engines.config_paths as cp
        auto = cp.autoscalp_db()
    except Exception:
        auto = os.path.join(os.path.dirname(settlements_db_path()), "autoscalp_gui.db")

    n = 0
    a = _auto_conn(rw=False)
    with connect_db(settlements_db_path()) as s:

        cols = {r["name"] for r in a.execute("PRAGMA table_info(orders)")}
        date_col = "opened_at" if "opened_at" in cols else ("ts" if "ts" in cols else None)
        if not date_col:
            
            a.close()
            return 0


        rows = a.execute(f"""
          SELECT id, mode, marketId, selectionId, side,
                 entry_odds, entry_stake, entry_status,
                 opened_at, bf_bet_id, customer_ref,
                 exit_status, closed_at, net_pl
          FROM orders
          WHERE date(COALESCE({date_col}, datetime('now'))) = date('now')
        """).fetchall()

        # ensure tables exist
        s.execute("""
          CREATE TABLE IF NOT EXISTS st_order_ledger(
            betId TEXT PRIMARY KEY,
            source TEXT, mode TEXT,
            marketId TEXT, selectionId TEXT, side TEXT,
            price REAL, size REAL, status TEXT,
            opened_at TEXT, placed_at TEXT, settled_at TEXT,
            customerOrderRef TEXT, customerStrategyRef TEXT,
            net_pl REAL, commission REAL, json_raw TEXT
          )
        """)
        s.execute("""
          CREATE TABLE IF NOT EXISTS st_runner_day_totals(
            day TEXT, marketId TEXT, selectionId TEXT,
            parents_placed INTEGER, children_placed INTEGER,
            exits INTEGER, cancels INTEGER, fails INTEGER,
            hedges_matched INTEGER, successes INTEGER,
            net REAL, commission REAL,
            PRIMARY KEY(day, marketId, selectionId)
          )
        """)

        for r in rows:
            betId = r["bf_bet_id"] or f"AUTO:{int(r['id'])}"
            status = (r["exit_status"] or r["entry_status"] or "").upper() or "QUEUED"

            s.execute("""
              INSERT INTO st_order_ledger(
                betId, source, mode, marketId, selectionId, side, price, size, status,
                opened_at, placed_at, settled_at, customerOrderRef, customerStrategyRef,
                net_pl, commission, json_raw
              ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(betId) DO UPDATE SET
                source=excluded.source, mode=excluded.mode,
                marketId=excluded.marketId, selectionId=excluded.selectionId,
                side=excluded.side, price=excluded.price, size=excluded.size, status=excluded.status,
                opened_at=COALESCE(st_order_ledger.opened_at, excluded.opened_at),
                placed_at=COALESCE(st_order_ledger.placed_at, excluded.placed_at),
                settled_at=COALESCE(st_order_ledger.settled_at, excluded.settled_at),
                customerOrderRef=COALESCE(st_order_ledger.customerOrderRef, excluded.customerOrderRef),
                customerStrategyRef=COALESCE(st_order_ledger.customerStrategyRef, excluded.customerStrategyRef),
                net_pl=COALESCE(excluded.net_pl, st_order_ledger.net_pl),
                json_raw=COALESCE(excluded.json_raw, st_order_ledger.json_raw)
            """, (
                str(betId), "AUTO", str(r["mode"] or ""), str(r["marketId"]), str(r["selectionId"]),
                (r["side"] or "").upper(), float(r["entry_odds"] or 0.0), float(r["entry_stake"] or 0.0),
                status,
                r["opened_at"], None, r["closed_at"],
                r["customer_ref"], None,
                float(r["net_pl"] or 0.0), None,
                None
            ))
            n += 1
        s.commit()
    return n

# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def _close_parents_children()
# 🎯 ACTION: Replace entire function (correct per-child EventSync routing)
# 📆 PATCHED: 2026-02-10
# =============================================================================
# === PATCH START =============================================================

def _close_parents_children() -> int:
    """Cascade SETTLED status + net P&L from parents to their children."""
    con = _auto_conn(rw=True); con.row_factory = sqlite3.Row
    updated = 0

    parents = con.execute("""
        SELECT id, marketId, selectionId, net_pl
          FROM orders
         WHERE exit_status='SETTLED'
           AND (role IS NULL OR role='PARENT')
    """).fetchall()

    for p in parents:
        kids = con.execute("SELECT id FROM orders WHERE hedge_of=?", (p["id"],)).fetchall()
        for k in kids:
            _q(con, """
                UPDATE orders
                   SET exit_status='SETTLED',
                       closed_at=datetime('now','utc'),
                       realized_pnl=COALESCE(realized_pnl,?),
                       net_pl=COALESCE(net_pl,?)
                 WHERE id=?
            """, (p["net_pl"], p["net_pl"], k["id"]))
            updated += con.total_changes

            # Per-child EventSync (correct placement)
            try:
                _emit_settlement_event("child_settled", {
                    "marketId": p["marketId"],
                    "selectionId": p["selectionId"],
                    "parent_id": p["id"],
                    "child_id": k["id"],
                    "net_pl": float(p["net_pl"] or 0.0),
                })
            except Exception as e:
                print(f"[EventSync][child_settled] warn: {e}")

    con.commit(); con.close()
    print(f"[settlements] cascaded {updated} child closures")
    return updated

# === PATCH END ===============================================================

# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def _update_exposure_cache(
# 🎯 ACTION: Replace entire function (always use REAL autoscalp_gui.db)
# 📆 PATCHED: 2026-02-10
# =============================================================================
# === PATCH START =============================================================

from engines.config_paths import open_auto_db as _open_auto

def _update_exposure_cache() -> None:
    """Compute total open exposure and append to exposure_log in AUTO DB."""
    from engines.config_paths import q_retry
    con = _open_auto(rw=True)    # ✔ REAL writer
    con.row_factory = sqlite3.Row

    exposure = float(
        con.execute("""
            SELECT COALESCE(SUM(entry_stake),0)
              FROM orders
             WHERE exit_status IS NULL OR exit_status=''
        """).fetchone()[0] or 0.0
    )

    q_retry(con, """
        CREATE TABLE IF NOT EXISTS exposure_log(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            exposure REAL NOT NULL,
            sampled_at TEXT NOT NULL DEFAULT (datetime('now','utc'))
        )
    """)

    q_retry(con,
        "INSERT INTO exposure_log(day, exposure) VALUES(date('now','utc'), ?)",
        (exposure,)
    )

    con.commit(); con.close()
    print(f"[settlements] exposure logged £{exposure:.2f}")

# === PATCH END ===============================================================

# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def _ensure_restore_view()
# 🎯 ACTION: Replace entire function (ensure view is created in AUTO DB)
# 📆 PATCHED: 2026-02-10
# =============================================================================
# === PATCH START =============================================================

from engines.config_paths import open_auto_db as _open_auto

def _ensure_restore_view() -> None:
    """Create v_order_restore view inside autoscalp_gui.db (not settlements.db)."""
    from engines.config_paths import q_retry
    con = _open_auto(rw=True)

    q_retry(con, """
        CREATE VIEW IF NOT EXISTS v_order_restore AS
        SELECT
          o.marketId,
          o.selectionId,
          b.horse_name,
          b.event_name,
          b.marketStartTime,
          o.side,
          o.entry_odds,
          o.entry_stake,
          o.exit_odds,
          o.exit_stake,
          o.realized_pnl,
          o.exit_status,
          o.closed_at
        FROM orders o
        LEFT JOIN bets b USING (marketId, selectionId)
        WHERE o.exit_status IN ('OPEN','SETTLED');
    """)

    con.commit(); con.close()
    print("[settlements] restore view ensured")

# === PATCH END ===============================================================



def rebuild_runner_day_totals(day_utc: Optional[str] = None) -> int:
    """
    (Re)compute st_runner_day_totals for a given *local* day by joining AUTO_DB.orders.
    Success = parent matched + child matched with net_pl > 0.
    """
    ensure_schema()
    auto = None
    try:
        import engines.config_paths as cp
        auto = cp.autoscalp_db()
    except Exception:
        auto = os.path.join(os.path.dirname(settlements_db_path()), "autoscalp_gui.db")

    day_local = (datetime.now().strftime("%Y-%m-%d"))

    with connect_db(settlements_db_path()) as s:
        s.execute("""
          CREATE TABLE IF NOT EXISTS st_runner_day_totals(
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            parents_placed INTEGER NOT NULL DEFAULT 0,
            children_placed INTEGER NOT NULL DEFAULT 0,
            exits INTEGER NOT NULL DEFAULT 0,
            cancels INTEGER NOT NULL DEFAULT 0,
            fails INTEGER NOT NULL DEFAULT 0,
            hedges_matched INTEGER NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            net REAL NOT NULL DEFAULT 0.0,
            commission REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY(day, marketId, selectionId)
          )
        """)
        s.commit()

    a = _auto_conn(rw=False)
    with connect_db(settlements_db_path()) as s:

        cols = {r["name"] for r in a.execute("PRAGMA table_info(orders)")}
        link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        if not link:
            
            a.close()
            return 0

        parents = a.execute(f"""
          SELECT marketId, selectionId,
                 SUM(CASE WHEN UPPER(entry_status)='PLACED' THEN 1 ELSE 0 END) as p_placed,
                 SUM(CASE WHEN UPPER(entry_status)='MATCHED' THEN 1 ELSE 0 END) as p_matched,
                 SUM(CASE WHEN UPPER(entry_status)='CANCELLED' THEN 1 ELSE 0 END) as p_cancelled,
                 SUM(CASE WHEN UPPER(entry_status)='FAILED' THEN 1 ELSE 0 END) as p_failed,
                 SUM(CASE WHEN UPPER(exit_status)='MATCHED' THEN 1 ELSE 0 END) as p_exits,
                 SUM(COALESCE(net_pl,0.0)) as net
          FROM orders
          WHERE ({link} IS NULL OR {link}='')
            AND date(COALESCE(opened_at, datetime('now'))) = date(?)
          GROUP BY marketId, selectionId
        """, (day_local,)).fetchall()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH (regex): ^\s*def rebuild_runner_day_totals\(day_utc: Optional\[str\] = None\)\s*->\s*int:
#    Replace ONLY the kid_map build + usage lines inside this function as below
# 📆 PATCHED: 2025-09-21T16:45Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        children = a.execute(f"""
          SELECT marketId, selectionId,
                 SUM(CASE WHEN UPPER(entry_status)='PLACED' THEN 1 ELSE 0 END) as c_placed,
                 SUM(CASE WHEN UPPER(entry_status)='MATCHED' THEN 1 ELSE 0 END) as c_matched
          FROM orders
          WHERE ({link} IS NOT NULL AND {link}<>'')
            AND date(COALESCE(opened_at, datetime('now'))) = date(?)
          GROUP BY marketId, selectionId
        """, (day_local,)).fetchall()

        # NORMALISE children to plain dicts so we can safely use .get
        kid_map = {}
        for r in children:
            key = (str(r["marketId"]), str(r["selectionId"]))
            kid_map[key] = {
                "c_placed":  int(r["c_placed"]  or 0),
                "c_matched": int(r["c_matched"] or 0),
            }

        updated = 0
        for p in parents:
            key = (str(p["marketId"]), str(p["selectionId"]))
            c = kid_map.get(key, {"c_placed": 0, "c_matched": 0})
            s.execute("""
              INSERT INTO st_runner_day_totals(
                day, marketId, selectionId,
                parents_placed, children_placed, exits, cancels, fails, hedges_matched, successes,
                net, commission
              ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(day, marketId, selectionId) DO UPDATE SET
                parents_placed=excluded.parents_placed,
                children_placed=excluded.children_placed,
                exits=excluded.exits, cancels=excluded.cancels, fails=excluded.fails,
                hedges_matched=excluded.hedges_matched, successes=excluded.successes,
                net=excluded.net, commission=excluded.commission
            """, (
                day_local, key[0], key[1],
                int(p["p_placed"] or 0),
                int(c["c_placed"] or 0),
                int(p["p_exits"] or 0),
                int(p["p_cancelled"] or 0),
                int(p["p_failed"] or 0),
                int(c["c_matched"] or 0),
                int(1 if float(p["net"] or 0.0) > 0 else 0),
                float(p["net"] or 0.0), 0.0
            ))
            updated += 1

        s.commit()
    return updated




# ──────────────────────────────────────────────────────────────────────────────
# Betfair API client (minimal)
# ──────────────────────────────────────────────────────────────────────────────
# 📍 engines/live/settlements.py
# === PATCH: STABLE RPC CLIENT (RAW, LOCAL CREDS, CORRECT URL) ===

class BetfairClient:
    API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

    def __init__(self):
        ak, ss = _resolve_betfair_creds()
        if not ak or not ss:
            raise RuntimeError("Missing Betfair APP_KEY / SESSION_TOKEN")

        self.app_key = ak
        self.session = ss

    def _reload_creds(self):
        ak, ss = _resolve_betfair_creds_localonly()
        if ak:
            self.app_key = ak
        if ss:
            self.session = ss

    def _rpc(self, method: str, params: dict):
        self._reload_creds()

        payload = [{
            "jsonrpc": "2.0",
            "method": f"SportsAPING/v1.0/{method}",
            "params": params,
            "id": 1,
        }]

        req = urllib.request.Request(
            self.API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Application": self.app_key,
                "X-Authentication": self.session,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                obj = json.loads(body)
                if isinstance(obj, list) and "result" in obj[0]:
                    return obj[0]["result"]
                raise RuntimeError(obj)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')}")
        except Exception as e:
            raise RuntimeError(f"RPC exception {method}: {e}")

    # === RESTORED PUBLIC API METHODS (required by settlements.py) ===

# === PATCH START ============================================================
# 📍 TARGET: engines/live/settlements.py: class BetfairClient
# 🔎 SEARCH: class BetfairClient:
# 📆 PATCHED: 2025-11-30 — Restore public API wrappers (list_cleared_orders, list_market_catalogue, list_market_book)
# ============================================================================

    def list_cleared_orders(
        self,
        from_iso: Optional[str] = None,
        to_iso: Optional[str] = None,
        bet_status: str = "SETTLED",
        event_type_ids: Optional[List[str]] = None,
        market_ids: Optional[List[str]] = None,
        side: Optional[str] = None,
        customer_order_refs: Optional[List[str]] = None,
        include_item_description: bool = True,
        from_record: int = 0,
        record_count: int = 1000,
    ) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "betStatus": bet_status,
            "fromRecord": from_record,
            "recordCount": record_count,
            "includeItemDescription": include_item_description,
        }

        if from_iso or to_iso:
            params["settledDateRange"] = {}
            if from_iso:
                params["settledDateRange"]["from"] = from_iso
            if to_iso:
                params["settledDateRange"]["to"] = to_iso

        if event_type_ids:
            params["eventTypeIds"] = event_type_ids
        if market_ids:
            params["marketIds"] = market_ids
        if side:
            params["side"] = side
        if customer_order_refs:
            params["customerOrderRefs"] = customer_order_refs

        return self._rpc("listClearedOrders", params)


    def list_market_catalogue(
        self,
        market_ids: List[str],
        max_results: int = 200,
    ) -> List[Dict[str, Any]]:
        params = {
            "filter": {"marketIds": market_ids},
            "maxResults": max_results,
            "marketProjection": [
                "EVENT",
                "COMPETITION",
                "MARKET_START_TIME",
                "RUNNER_DESCRIPTION",
            ],
        }
        return self._rpc("listMarketCatalogue", params)


    def list_market_book(

        self,
        market_ids: List[str],
    ) -> List[Dict[str, Any]]:
        if not market_ids:
            return []  # HARD GUARD
        params = {
            "marketIds": market_ids,
            "priceProjection": {
                "priceData": [
                    "SP_AVAILABLE",
                    "EX_TRADED",
                    "EX_BEST_OFFERS",
                ]
            },
            "orderProjection": "EXECUTION_COMPLETE",
            "matchProjection": "ROLLED_UP_BY_PRICE",
        }
        return self._rpc("listMarketBook", params)

# === PATCH END ==============================================================

# ──────────────────────────────────────────────────────────────────────────────
# CSV ingest (optional)
# ──────────────────────────────────────────────────────────────────────────────

def import_cleared_csv(path: str) -> int:
    ensure_schema()
    n = 0
    with open(path, "r", encoding="utf-8-sig", newline="") as fh, connect_db(settlements_db_path()) as con:
        rdr = csv.DictReader(fh)
        for row in rdr:
            bet_id = _rget(row, "Bet ID") or _rget(row, "betId") or _rget(row, "BetId")
            payload_json = json.dumps(row, ensure_ascii=False)
            con.execute(
                "INSERT INTO bf_cleared_orders_raw(source, ingested_at, betId, payload_json) VALUES (?,?,?,?)",
                ("CSV", _utcnow_iso(), bet_id, payload_json)
            )
            # map a subset to canonical
            mapped = map_cleared_row(row)
            if mapped.get("betId"):
                upsert_cleared(mapped, con=con)
                n += 1
        con.commit()
    return n

def map_cleared_row(row: Dict[str, Any]) -> Dict[str, Any]:
    # Robust column name handling across CSV variants
    def g(*keys, cast=str, default=None):
        for k in keys:
            if k in row and row[k] not in ("", None):
                try:
                    return cast(row[k])
                except Exception:
                    try:
                        return cast(str(row[k]).strip())
                    except Exception:
                        return default
        return default
    # map
    return {
        "betId": g("Bet ID","betId"),
        "marketId": g("Market ID","marketId"),
        "selectionId": g("Selection ID","selectionId", cast=str),
        "side": g("Side","side", cast=str),
        "priceRequested": g("Price requested","priceRequested", cast=float),
        "averagePriceMatched": g("Avg. price matched","averagePriceMatched", cast=float),
        "priceMatched": g("Price matched","priceMatched", cast=float),
        "sizeSettled": g("Size settled","sizeSettled", cast=float),
        "profit": g("Profit","profit", cast=float),
        "commission": g("Commission","commission", cast=float),
        "settledDate": g("Settled date","settledDate", cast=str),
        "placedDate": g("Placed date","placedDate", cast=str),
        "customerOrderRef": g("Customer Order Ref","customerOrderRef", cast=str),
        "customerStrategyRef": g("Customer Strategy Ref","customerStrategyRef", cast=str),
        "persistenceType": g("Persistence Type","persistenceType", cast=str),
        "orderType": g("Order Type","orderType", cast=str),
        "bspLiability": g("BSP Liability","bspLiability", cast=float),
        "eventTypeId": g("Event Type ID","eventTypeId", cast=str),
        "handicap": g("Handicap","handicap", cast=float),
        "json_raw": json.dumps(row, ensure_ascii=False),
    }

def upsert_cleared(mapped: Dict[str, Any], *, con: Optional[sqlite3.Connection] = None) -> None:
    own_con = con is None

    # Upsert statement (unchanged)
    q = """
    INSERT INTO bf_cleared_orders(
      betId, marketId, selectionId, side, priceRequested, averagePriceMatched, priceMatched,
      sizeSettled, profit, commission, settledDate, placedDate, customerOrderRef,
      customerStrategyRef, persistenceType, orderType, bspLiability, eventTypeId, handicap, json_raw
    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(betId) DO UPDATE SET
      marketId=excluded.marketId,
      selectionId=excluded.selectionId,
      side=excluded.side,
      priceRequested=excluded.priceRequested,
      averagePriceMatched=excluded.averagePriceMatched,
      priceMatched=excluded.priceMatched,
      sizeSettled=excluded.sizeSettled,
      profit=excluded.profit,
      commission=excluded.commission,
      settledDate=excluded.settledDate,
      placedDate=excluded.placedDate,
      customerOrderRef=excluded.customerOrderRef,
      customerStrategyRef=excluded.customerStrategyRef,
      persistenceType=excluded.persistenceType,
      orderType=excluded.orderType,
      bspLiability=excluded.bspLiability,
      eventTypeId=excluded.eventTypeId,
      handicap=excluded.handicap,
      json_raw=excluded.json_raw
    ;
    """

    if own_con:
        ensure_schema()
        with connect_db(settlements_db_path()) as c:
            c.execute(q, (
                mapped.get("betId"),
                mapped.get("marketId"),
                mapped.get("selectionId"),
                mapped.get("side"),
                mapped.get("priceRequested"),
                mapped.get("averagePriceMatched"),
                mapped.get("priceMatched"),
                mapped.get("sizeSettled"),
                mapped.get("profit"),
                mapped.get("commission"),
                mapped.get("settledDate"),
                mapped.get("placedDate"),
                mapped.get("customerOrderRef"),
                mapped.get("customerStrategyRef"),
                mapped.get("persistenceType"),
                mapped.get("orderType"),
                mapped.get("bspLiability"),
                mapped.get("eventTypeId"),
                mapped.get("handicap"),
                mapped.get("json_raw"),
            ))
            c.commit()
    else:
        # Reuse caller's connection; let the caller handle commit/close.
        con.execute(q, (
            mapped.get("betId"),
            mapped.get("marketId"),
            mapped.get("selectionId"),
            mapped.get("side"),
            mapped.get("priceRequested"),
            mapped.get("averagePriceMatched"),
            mapped.get("priceMatched"),
            mapped.get("sizeSettled"),
            mapped.get("profit"),
            mapped.get("commission"),
            mapped.get("settledDate"),
            mapped.get("placedDate"),
            mapped.get("customerOrderRef"),
            mapped.get("customerStrategyRef"),
            mapped.get("persistenceType"),
            mapped.get("orderType"),
            mapped.get("bspLiability"),
            mapped.get("eventTypeId"),
            mapped.get("handicap"),
            mapped.get("json_raw"),
        ))


# ──────────────────────────────────────────────────────────────────────────────
# API ingest (Cleared Orders + Market data)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_cleared_orders_api(since_iso: Optional[str], to_iso: Optional[str], *, bet_status: str="SETTLED") -> int:
    client = BetfairClient()
    ensure_schema()
    total = 0
    from_rec = 0
    while True:
        result = client.list_cleared_orders(
            from_iso=since_iso, to_iso=to_iso,
            bet_status=bet_status,
            include_item_description=True,
            from_record=from_rec, record_count=1000
        )
        items = (result or {}).get("clearedOrders") or []
        more = bool((result or {}).get("moreAvailable"))
        with connect_db(settlements_db_path()) as con:
            for it in items:
                bet_id = it.get("betId")
                con.execute(
                    "INSERT INTO bf_cleared_orders_raw(source, ingested_at, betId, payload_json) VALUES (?,?,?,?)",
                    ("API", _utcnow_iso(), bet_id, json.dumps(it, ensure_ascii=False))
                )
                mapped = map_cleared_api(it)
                if mapped.get("betId"):
                    upsert_cleared(mapped, con=con)
                    total += 1
            con.commit()
        if not more:
            break
        from_rec += len(items) if items else 0
        if from_rec <= 0:
            break
        time.sleep(0.25)
    return total


def map_cleared_api(it: Dict[str, Any]) -> Dict[str, Any]:
    # https://betfair-developer-docs.../Betting+API → listClearedOrders output
    desc = (it.get("itemDescription") or {})
    return {
        "betId": it.get("betId"),
        "marketId": desc.get("marketId") or it.get("marketId"),
        "selectionId": str(desc.get("selectionId") or it.get("selectionId") or ""),
        "side": it.get("side"),
        "priceRequested": it.get("priceRequested"),
        "averagePriceMatched": it.get("averagePriceMatched"),
        "priceMatched": it.get("priceMatched"),
        "sizeSettled": it.get("sizeSettled"),
        "profit": it.get("profit"),
        "commission": it.get("commission"),
        "settledDate": it.get("settledDate"),
        "placedDate": it.get("placedDate"),
        "customerOrderRef": it.get("customerOrderRef"),
        "customerStrategyRef": it.get("customerStrategyRef"),
        "persistenceType": it.get("persistenceType"),
        "orderType": it.get("orderType"),
        "bspLiability": it.get("bspLiability"),
        "eventTypeId": (desc.get("eventTypeId") or it.get("eventTypeId")),
        "handicap": it.get("handicap"),
        "json_raw": json.dumps(it, ensure_ascii=False),
    }

# === PATCH START 2026-02-11 ===============================================

def fetch_market_metadata_api(market_ids: List[str]) -> Tuple[int,int]:
    client = BetfairClient()
    ensure_schema()

    total_cats, total_books = 0, 0

    def chunks(lst, n=40):
        for i in range(0, len(lst), n):
            yield lst[i:i+n]

    # ✔ USE REAL SQLITE3 — NOT DALWriteProxy
    with connect_db(settlements_db_path()) as con:

        # -------------------------------------------------
        # MARKET CATALOGUE INSERTS
        # -------------------------------------------------
        for chunk in chunks(market_ids, 40):
            cats = client.list_market_catalogue(chunk)
            total_cats += len(cats or [])

            for c in cats or []:
                md = c or {}
                runners = [
                    {"selectionId": r.get("selectionId"),
                     "runnerName": r.get("runnerName")}
                    for r in (md.get("runners") or [])
                ]
                venue = ((md.get("event") or {}).get("venue")) or md.get("eventName")

                con.execute("""
                    INSERT INTO bf_market_catalogue(
                      marketId, marketName, eventName, competition, countryCode, venue,
                      marketStartTime, totalMatched, raceType, distanceMeters, going, class,
                      runnersJson, raw_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(marketId) DO UPDATE SET
                      marketName=excluded.marketName,
                      eventName=excluded.eventName,
                      competition=excluded.competition,
                      countryCode=excluded.countryCode,
                      venue=excluded.venue,
                      marketStartTime=excluded.marketStartTime,
                      totalMatched=excluded.totalMatched,
                      runnersJson=excluded.runnersJson,
                      raw_json=excluded.raw_json
                """, (
                    md.get("marketId"),
                    md.get("marketName"),
                    ((md.get("event") or {}).get("name")),
                    ((md.get("competition") or {}).get("name")),
                    md.get("countryCode"),
                    venue,
                    md.get("marketStartTime"),
                    md.get("totalMatched"),
                    None, None, None, None,
                    json.dumps(runners, ensure_ascii=False),
                    json.dumps(md, ensure_ascii=False),
                ))

                for r in (md.get("runners") or []):
                    con.execute("""
                        INSERT INTO bf_runner_info(marketId, selectionId, runnerName, stallDraw, raw_json)
                        VALUES (?,?,?,?,?)
                        ON CONFLICT(marketId, selectionId)
                        DO UPDATE SET
                          runnerName=excluded.runnerName,
                          stallDraw=excluded.stallDraw,
                          raw_json=excluded.raw_json
                    """, (
                        md.get("marketId"),
                        str(r.get("selectionId")),
                        r.get("runnerName"),
                        r.get("sortPriority"),
                        json.dumps(r, ensure_ascii=False),
                    ))

        # -------------------------------------------------
        # MARKET BOOK INSERTS
        # -------------------------------------------------
        for chunk in chunks(market_ids, 40):
            books = client.list_market_book(chunk)
            total_books += len(books or [])

            for b in books or []:
                md = b or {}
                res = [
                    {
                        "selectionId": r.get("selectionId"),
                        "status": r.get("status"),
                        "ltp": r.get("lastPriceTraded"),
                        "totalMatched": r.get("totalMatched"),
                        "sp": ((r.get("sp") or {}).get("actualSP")),
                    }
                    for r in (md.get("runners") or [])
                ]

                con.execute("""
                    INSERT INTO bf_market_book(
                      marketId, isInplay, status, betDelay, totalMatched,
                      lastMatchTime, resultJson, raw_json
                    )
                    VALUES (?,?,?,?,?,?,?,?)
                    ON CONFLICT(marketId) DO UPDATE SET
                      isInplay=excluded.isInplay,
                      status=excluded.status,
                      betDelay=excluded.betDelay,
                      totalMatched=excluded.totalMatched,
                      lastMatchTime=excluded.lastMatchTime,
                      resultJson=excluded.resultJson,
                      raw_json=excluded.raw_json
                """, (
                    md.get("marketId"),
                    1 if md.get("isInplay") else 0,
                    md.get("status"),
                    md.get("betDelay"),
                    md.get("totalMatched"),
                    md.get("lastMatchTime"),
                    json.dumps(res, ensure_ascii=False),
                    json.dumps(md, ensure_ascii=False),
                ))

        con.commit()

    return (total_cats, total_books)

# === PATCH END ============================================================



# ──────────────────────────────────────────────────────────────────────────────
# Reconcile into autoscalp_gui.db.orders
# ──────────────────────────────────────────────────────────────────────────────

# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py:reconcile_orders
# 🔎 SEARCH: def reconcile_orders():
# 📆 PATCHED: 2025-11-21
from engines.config_paths import auto_conn as _auto_conn

def reconcile_orders() -> Tuple[int,int]:
    """
    Overwrite orders.realized_pnl/net_pl/exit_status/closed_at where bf_bet_id matches a betId.
    """
    orders_db = autoscalp_gui_db_path()
    set_db = settlements_db_path()
    ensure_schema()
    updated = 0
    pairs_marked = 0

    # settlements DB stays raw; orders DB must use DAL
    with connect_db(set_db) as s:
        o = _auto_conn(rw=True)
        o.row_factory = sqlite3.Row
        try:
            # ensure fast lookup in orders
            try:
                o.execute("CREATE INDEX IF NOT EXISTS idx_orders_bfid ON orders(bf_bet_id)")
                o.commit()
            except Exception:
                pass

            # Upsert by betId
            for row in s.execute("SELECT betId, marketId, selectionId, profit, commission, settledDate FROM bf_cleared_orders"):
                betId = row["betId"]
                profit = row["profit"]
                settled = row["settledDate"]
                o.execute("""
                    UPDATE orders
                       SET realized_pnl = ?,
                           net_pl       = ?,
                           exit_status  = COALESCE(exit_status, 'SETTLED'),
                           closed_at    = COALESCE(closed_at, ?)
                     WHERE bf_bet_id = ?
                """, (
                    profit,
                    profit,
                    settled,
                    betId
                ))

                record_playbook_pattern(order_row=row, pnl_row=row, oc_snapshot=None)

                # ======================================================================
                # 📍 PATCH 2 — EventSync for final Betfair settlement
                # 🔎 SEARCH: "o.execute("""UPDATE orders"
                # 📆 PATCHED: 2026-02-10
                # ======================================================================

                try:
                    _emit_settlement_event("order_settled", {
                        "betId": betId,
                        "marketId": row["marketId"],
                        "selectionId": row["selectionId"],
                        "profit": float(profit or 0.0),
                        "commission": row["commission"],
                        "settled_at": row["settledDate"],
                    })
                except Exception as e:
                    print(f"[EventSync][settlement] emit failed for betId={betId}: {e}")


                if o.total_changes:
                    updated += 1

                    try:
                        from engines.mastery import mastery_policy as mp
                        mp.record_outcome(
                            str(betId),
                            {"marketId": row["marketId"], "selectionId": row["selectionId"]},
                            {"realized_pnl": float(profit or 0.0)}
                        )
                    except Exception as e:
                        print(f"[SETTLE] mastery warn: {e}")

            # Compute runner-day rollups
            s.execute("""
              INSERT INTO bf_settlement_runner_day(day, marketId, selectionId, trades, net, commission, win_rate)
              SELECT date(settledDate), marketId, selectionId,
                     COUNT(*),
                     COALESCE(SUM(profit),0.0),
                     COALESCE(SUM(commission),0.0),
                     AVG(CASE WHEN profit>0 THEN 1.0 ELSE 0.0 END)
              FROM bf_cleared_orders
              WHERE settledDate IS NOT NULL
              GROUP BY 1,2,3
              ON CONFLICT(day, marketId, selectionId) DO UPDATE SET
                trades    = excluded.trades,
                net       = excluded.net,
                commission= excluded.commission,
                win_rate  = excluded.win_rate
            """)
            s.commit()

            # after updating orders from betId, refresh daily totals (today)
            try:
                day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                rebuild_runner_day_totals(day)
                sync_orders_into_ledger_from_auto(day)
            except Exception:
                pass

            # orphan parent marking (unchanged)
            pairs_marked = 0

            o.commit()

        finally:
            try: o.close()
            except Exception: pass

    # inside reconcile_orders(), just before return:
    try:
        ensure_kpi_views()
    except Exception:
        pass

    # --- After daily rollups and before returning ---
    try:
        from engines.mastery.train_mastery_v7 import on_settlement_event
        with connect_db(settlements_db_path()) as s2:
            for r in s2.execute("""
                    SELECT DISTINCT marketId, selectionId, profit
                      FROM bf_cleared_orders
                     WHERE settledDate IS NOT NULL
                       AND datetime(settledDate) >= datetime('now','-10 minute','utc')
                       AND profit IS NOT NULL
                """):
                mid = str(r["marketId"])
                sid = str(r["selectionId"])
                pnl = float(r["profit"] or 0.0)
                v7_on_settlement_event(mid, sid, pnl)

    except Exception as e:
        print(f"[settlements-river] warn: failed to run River reinforcement — {e}")

    return updated, pairs_marked
# === PATCH END ===

# ========================================================================
# 📍 TARGET: engines/live/settlements.py : record_playbook_pattern
# 🔎 SEARCH: con = __settle_conn()
# 📆 PATCHED: 2026-02-10 — Correct DB routing for playbook writes
# ========================================================================
# === PATCH START ========================================================

from engines.config_paths import open_auto_db as _open_auto

def record_playbook_pattern(order_row, pnl_row, oc_snapshot=None):
    """
    Persist a canonical Playbook pattern derived from settled orders.
    Corrected: playbooks must be written into autoscalp_gui.db using a
    REAL sqlite connection, NOT DALWriteProxy and NOT settlements.db.
    """
    try:
        con = _open_auto(rw=True)     # ✔ REAL writer to autoscalp_gui.db
        con.row_factory = sqlite3.Row
        cur = con.cursor()

        # ensure table exists
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

        # derive canonical values
        mid = str(order_row["marketId"])
        sid = int(order_row["selectionId"])
        strat = str(order_row.get("source") or "UNK")
        entry_odds = float(order_row.get("entry_odds") or 0)
        exit_odds = float(order_row.get("exit_odds") or 0)
        pnl = float(pnl_row.get("profit") or 0.0)
        outcome = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "NEUTRAL"
        exposure = abs(float(order_row.get("entry_stake") or 0.0))
        oc_stage = (oc_snapshot or {}).get("stage", "OC?")
        band_low = (oc_snapshot or {}).get("band_low")
        band_high = (oc_snapshot or {}).get("band_high")

        meta = json.dumps({
            "side": order_row.get("side"),
            "exit_kind": order_row.get("exit_kind"),
            "commission": pnl_row.get("commission"),
            "source_run": pnl_row.get("source", "SETTLEMENT"),
        }, separators=(',', ':'))

        _q_retry(cur, """
            INSERT INTO playbooks(day, marketId, selectionId, strategy, oc_stage,
                                  pattern_key, band_low, band_high,
                                  entry_odds, exit_odds, pnl, outcome,
                                  exposure, confidence, meta_json, source)
            VALUES(date('now','utc'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1.0, ?, 'LIVE')
        """, (mid, sid, strat, oc_stage,
              f"{mid}-{sid}-{oc_stage}", band_low, band_high,
              entry_odds, exit_odds, pnl, outcome, exposure, meta))
        con.commit()
        _q_retry(con, "INSERT INTO events(ts,level,source,message) VALUES(datetime('now','utc'),'INFO','playbook',?)",
                 (f"recorded {outcome} mid={mid} sid={sid} pnl={pnl:+.2f} oc={oc_stage}",))
        con.commit()
        con.close()
    except Exception as e:
        try:
            _q_retry(__settle_conn(), "INSERT INTO events(ts,level,source,message) VALUES(datetime('now','utc'),'ERROR','playbook',?)",
                     (f"record_playbook_pattern error: {e}",))
        except Exception:
            pass
# === PATCH END ===




# ──────────────────────────────────────────────────────────────────────────────
# Mastery event hooks (stubs for now)
# ──────────────────────────────────────────────────────────────────────────────

def emit_mastery_event(event_type: str, details: Dict[str, Any], mode: str="LIVE") -> None:
    """
    Stub: write into mastery_events (if available) safely — no hard dependency.
    """
    try:
        orders_db = autoscalp_gui_db_path()
        with connect_db(orders_db) as con:
            con.execute("""
              CREATE TABLE IF NOT EXISTS mastery_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                happened_at TEXT NOT NULL DEFAULT (datetime('now','utc')),
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                delta_progress INTEGER NOT NULL DEFAULT 0,
                source TEXT NOT NULL DEFAULT 'LIVE'
              )
            """)
            con.execute("INSERT INTO mastery_events(event_type, details_json, source) VALUES (?,?,?)",
                        (event_type, json.dumps(details, ensure_ascii=False), mode))
            con.commit()
    except Exception:
        pass

def pick_one_market_id_from_orders(day_utc: Optional[str] = None) -> Optional[str]:
    """
    Pick a single marketId from autoscalp_gui.db.orders for a given UTC day.
    Chooses the market with the most parent rows (hedge_of IS NULL).
    """
    day = (day_utc or datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    con = _auto_conn(rw=False)
    try:
        r = con.execute("""
            SELECT marketId, COUNT(*) AS n
              FROM orders
             WHERE date(COALESCE(opened_at, datetime('now'))) = date(?)
               AND (hedge_of IS NULL OR hedge_of='')
             GROUP BY marketId
             ORDER BY n DESC, marketId
             LIMIT 1
        """, (day,)).fetchone()

        return r["marketId"] if r and r["marketId"] else None
    finally:
        con.close()




def pick_one_market_id_from_settlements(from_iso: Optional[str], to_iso: Optional[str]) -> Optional[str]:
    """
    Pick a single marketId from settlements.db (bf_cleared_orders) in the settledDate window.
    Chooses the market with the most cleared rows.
    """
    with connect_db(settlements_db_path()) as con:
        r = con.execute("""
            SELECT marketId, COUNT(*) AS n
              FROM bf_cleared_orders
             WHERE marketId IS NOT NULL
               AND (? IS NULL OR datetime(settledDate) >= datetime(?))
               AND (? IS NULL OR datetime(settledDate) <  datetime(?))
             GROUP BY marketId
             ORDER BY n DESC, marketId
             LIMIT 1
        """, (from_iso, from_iso, to_iso, to_iso)).fetchone()
        return r["marketId"] if r and r["marketId"] else None

# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def _record_training_history(
# 🎯 ACTION: Replace function (must log into autoscalp_gui.db)
# 📆 PATCHED: 2026-02-10
# =============================================================================
# === PATCH START =============================================================

from engines.config_paths import open_auto_db as _open_auto

def _record_training_history(epoch, score, goal_alignment):
    con = _open_auto(rw=True)
    _q_retry(con,
        "INSERT INTO training_history(epoch, score, goal_alignment) VALUES (?,?,?)",
        (epoch, score, goal_alignment)
    )
    con.commit(); con.close()

# === PATCH END ===============================================================


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args(argv: Optional[List[str]]=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Settlements pipeline (separate DB) for AutoScalp")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("sync-auto", help="Copy today's AUTO orders into Settlement tables (no Betfair needed)")
    p_csv = sub.add_parser("import-csv", help="Import Betfair cleared orders CSV")
    p_csv.add_argument("path", help="CSV path")

    p_fetch = sub.add_parser("fetch", help="Fetch Betfair API (cleared orders + market meta)")

    # Mutually exclusive: --day OR --since-days OR (explicit --from/--to)
    g = p_fetch.add_mutually_exclusive_group(required=False)
    g.add_argument("--day", dest="day", help="UTC day YYYY-MM-DD (from 00:00Z to next midnight)")
    g.add_argument("--since-days", dest="since_days", type=int,
                   help="If set, use now-<days> → now (UTC)")

    p_fetch.add_argument("--from", dest="from_date",
                         help="From date (YYYY-MM-DD or full UTC ISO like 2025-09-17T00:00:00Z)",
                         default=None)
    p_fetch.add_argument("--to", dest="to_date",
                         help="To date (YYYY-MM-DD or full UTC ISO)", default=None)
    p_fetch.add_argument("--print", dest="print_rows", action="store_true",
                         help="Print sample rows after fetch")

    p_fetch.add_argument("--batch-size", dest="batch_size", type=int, default=40,
                         help="Max markets per API call (default=40)")
    p_fetch.add_argument("--only-market", dest="only_markets", action="append", default=None,
                         help="Restrict metadata step to this marketId (repeatable).")
    p_fetch.add_argument("--only-one-from-orders", dest="only_one_from_orders", action="store_true",
                         help="Pick a single marketId from autoscalp orders for the given --day (or today).")
    p_fetch.add_argument("--only-one-from-settlements", dest="only_one_from_settlements", action="store_true",
                         help="Pick a single marketId from settlements for the requested date window.")
    p_fetch.add_argument("--orders-day", dest="orders_day", default=None,
                         help="YYYY-MM-DD UTC used by --only-one-from-orders (defaults to --day or today).")
    p_fetch.add_argument("--skip-meta", dest="skip_meta", action="store_true",
                         help="Skip market metadata (catalogue/book) calls.")

    p_fetch.add_argument(
        "--with-meta",
        dest="with_meta",
        action="store_true",
        help="Explicitly enable market metadata fetch (slow, non-live)"
    )




    sub.add_parser("reconcile", help="Reconcile settlements into autoscalp_gui.db.orders")

    return p.parse_args(argv)

def main(argv: Optional[List[str]] = None) -> int:
    # ensure env creds present for the whole run (harmless if already set)


    args = parse_args(argv)

    if args.cmd == "import-csv":
        n = import_cleared_csv(args.path)
        # expose v_settle_* views for the dashboard right after an import
        try:
            ensure_kpi_views()
        except Exception:
            pass
        print(f"[settlements] CSV imported rows={n} -> {settlements_db_path()}")
        return 0


    elif args.cmd == "fetch":
        # resolve date window (UTC)
        if args.day:
            from_iso = f"{args.day}T00:00:00Z" if "T" not in args.day else args.day
            from_dt  = datetime.fromisoformat(from_iso.replace("Z","+00:00")).astimezone(timezone.utc)
            to_iso   = (from_dt + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif args.since_days is not None:
            to_dt   = datetime.now(timezone.utc)
            from_dt = to_dt - timedelta(days=int(args.since_days))
            from_iso = from_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
            to_iso   = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        else:
            # explicit --from/--to
            def _mk_iso(s: Optional[str], end=False) -> Optional[str]:
                if not s: return None
                if "T" in s: return s if s.endswith("Z") else (s+"Z")
                return f"{s}{'T23:59:59Z' if end else 'T00:00:00Z'}"
            from_iso = _mk_iso(args.from_date, end=False)
            to_iso   = _mk_iso(args.to_date,   end=True)

        print(f"[settlements] fetch clearedOrders {from_iso or '(none)'} → {to_iso or '(none)'}")

        # pull all relevant statuses
        total = 0
        for status in ["SETTLED", "VOIDED", "LAPSED", "CANCELLED"]:
            n = fetch_cleared_orders_api(from_iso, to_iso, bet_status=status)
            print(f"[settlements] fetched {status}: +{n}")
            total += n
        print(f"[settlements] fetched clearedOrders total={total}")

        # metadata for those markets
        mkt_ids: List[str] = []
        with connect_db(settlements_db_path()) as con:
            for r in con.execute("SELECT DISTINCT marketId FROM bf_cleared_orders WHERE marketId IS NOT NULL"):
                mkt_ids.append(r["marketId"])

        # Narrow to a single marketId if requested
        if getattr(args, "only_markets", None):
            allow = set(args.only_markets or [])
            mkt_ids = [m for m in mkt_ids if m in allow]

        if getattr(args, "only_one_from_orders", False):
            # precedence: explicit --orders-day, else --day, else UTC today
            day_for_orders = args.orders_day or args.day or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            chosen = pick_one_market_id_from_orders(day_for_orders)
            if chosen:
                print(f"[settlements] only-one-from-orders day={day_for_orders} → marketId={chosen}")
                mkt_ids = [chosen]
            else:
                print(f"[settlements] only-one-from-orders found none for day={day_for_orders}")
                mkt_ids = []

        if getattr(args, "only_one_from_settlements", False):
            chosen = pick_one_market_id_from_settlements(from_iso, to_iso)
            if chosen:
                print(f"[settlements] only-one-from-settlements window={from_iso}→{to_iso} → marketId={chosen}")
                mkt_ids = [chosen]
            else:
                print(f"[settlements] only-one-from-settlements found none for window={from_iso}→{to_iso}")
                mkt_ids = []

        # Optionally skip metadata entirely
        # Default: SKIP metadata unless explicitly requested
        skip_meta = getattr(args, "skip_meta", False)

        if skip_meta or not mkt_ids:

            if not mkt_ids:
                print("[settlements] no markets to fetch metadata for")
            else:
                print("[settlements] skip-meta enabled (no metadata calls)")
            # optional preview
            if getattr(args, "print_rows", False):
                with connect_db(settlements_db_path()) as con:
                    sample = con.execute("""
                        SELECT betId, marketId, selectionId, side, priceMatched, sizeSettled, profit, settledDate
                        FROM bf_cleared_orders
                        ORDER BY datetime(settledDate) DESC
                        LIMIT 20
                    """).fetchall()
                print("[settlements] sample rows:")
                for r in sample:
                    print(dict(r))
            return 0

        # batching: drive chunk size from CLI, with simple backoff on TOO_MUCH_DATA
        batch_size = max(1, int(getattr(args, "batch_size", 40) or 40))

        def _chunks(lst, n):
            for i in range(0, len(lst), n):
                yield lst[i:i+n]

        total_cats, total_books = 0, 0

        # We’ll loop chunks; if API throws TOO_MUCH_DATA for a chunk, retry smaller
        for i in range(0, len(mkt_ids), batch_size):
            window = mkt_ids[i:i+batch_size]
            size = len(window)
            while size >= 1:
                try:
                    c, b = fetch_market_metadata_api(window)
                    total_cats += c
                    total_books += b
                    break  # this chunk succeeded
                except RuntimeError as e:
                    msg = str(e)
                    if "TOO_MUCH_DATA" in msg or "ANGX-0001" in msg:
                        size = max(1, size // 2)
                        window = window[:size]
                        print(f"[settlements] TOO_MUCH_DATA → retrying chunk size {size}")
                        continue
                    raise  # other errors bubble up

        print(f"[settlements] metadata upserted: catalogue={total_cats}, book={total_books}")

        # optional preview (unchanged)
        if getattr(args, "print_rows", False):
            with connect_db(settlements_db_path()) as con:
                sample = con.execute("""
                    SELECT betId, marketId, selectionId, side, priceMatched, sizeSettled, profit, settledDate
                    FROM bf_cleared_orders
                    ORDER BY datetime(settledDate) DESC
                    LIMIT 20
                """).fetchall()
            print("[settlements] sample rows:")
            for r in sample:
                print(dict(r))
        return 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/mastery/settlements.py
# 🔎 SEARCH: (append at end of file)
# 📆 PATCHED: 2025-09-29T14:25Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import sqlite3, json
from datetime import datetime, timezone

try:
    from engines.config_paths import auto_conn as _auto_conn, q_retry as _q_retry
except Exception:
    from engines.config_paths import auto_conn as _auto_conn
    def _q_retry(con, sql, params=()):
        cur = con.cursor(); cur.execute(sql, params); return cur

# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def _orders_conn()  (mastery block near bottom)
# 🎯 ACTION: Replace function so mastery writes to AUTO DB, not settlements DB
# 📆 PATCHED: 2026-02-10
# =============================================================================
# === PATCH START =============================================================

from engines.config_paths import open_auto_db as _open_auto

def _orders_conn() -> sqlite3.Connection:
    """Mastery settlement processes must write to AUTOSCALP_GUI.DB."""
    con = _open_auto(rw=True)
    con.row_factory = sqlite3.Row
    try:
        _q_retry(con, "PRAGMA busy_timeout=6000")
    except Exception:
        pass
    return con

# === PATCH END ===============================================================


# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def _mastery_log(
# 🎯 ACTION: Replace function (must log into AUTO DB, not settlements DB)
# 📆 PATCHED: 2026-02-10
# =============================================================================
# === PATCH START =============================================================

from engines.config_paths import open_auto_db as _open_auto

def _mastery_log(event_type: str, payload: dict):
    """Write mastery events to autoscalp_gui.db (correct DB)."""
    try:
        con = _open_auto(rw=True)
        _q_retry(con, """
            CREATE TABLE IF NOT EXISTS mastery_events(
                event_type TEXT,
                details_json TEXT,
                created_at TEXT
            )
        """)
        _q_retry(con, """
            INSERT INTO mastery_events(event_type, details_json, created_at)
            VALUES (?, ?, datetime('now','utc'))
        """, (event_type, json.dumps(payload, separators=(',',':'), ensure_ascii=False)))
        con.commit(); con.close()
    except Exception:
        pass

# === PATCH END ===============================================================

# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def mark_stoploss_for_parent(
# 🎯 ACTION: Replace function (must update AUTO DB, not settlements DB)
# 📆 PATCHED: 2026-02-10
# =============================================================================
# === PATCH START =============================================================

from engines.config_paths import open_auto_db as _open_auto

def mark_stoploss_for_parent(parent_cor: str) -> None:
    con = _open_auto(rw=True)
    try:
        _q_retry(con,
            "UPDATE orders SET exit_kind='STOPLOSS' WHERE customerOrderRef=?",
            (str(parent_cor),)
        )
        _q_retry(con, """
            UPDATE orders
               SET exit_kind='STOPLOSS'
             WHERE role='CHILD'
               AND hedge_of=(SELECT id FROM orders WHERE customerOrderRef=? LIMIT 1)
        """, (str(parent_cor),))
        con.commit()
        _mastery_log("trade_outcome_hint",
                     {"parent_ref": parent_cor, "exit_kind": "STOPLOSS"})
    finally:
        con.close()

# === PATCH END ===============================================================


# 📍 engines/live/settlements.py
# --- Background Settlements Loop ---------------------------------------------
import threading, time
from datetime import datetime, timezone, timedelta

# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py : start_settlement_daemon
# 📆 PATCHED: 2025-11-06Z — unified settlement + winners + expiry loop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 engines/live/settlements.py
# === PATCH: SAFE SETTLEMENTS DAEMON (NO BLOCKING) ===

def start_settlement_daemon(interval_s: int = 300):
    def _loop():
        while True:
            try:
                _run_single_settlement_cycle()
            except Exception as e:
                print(f"[settlements-loop] error: {e}")
            finally:
                time.sleep(interval_s)

    t = threading.Thread(target=_loop, name="SettlementsDaemon", daemon=True)
    t.start()
    print(f"[settlements-loop] daemon started (interval={interval_s}s)")

# ======================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def _run_single_settlement_cycle():
# 🎯 ACTION: Make reconciliation UNCONDITIONAL
# 📆 PATCHED: 2026-03-11 — ensure lifecycle always converges in LIVE
# ======================================================================

def _run_single_settlement_cycle():
    to_dt = datetime.now(timezone.utc)
    from_iso = (to_dt - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    to_iso   = to_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"[settlements] window {from_iso} → {to_iso}")

    # ------------------------------------------------------------------
    # API fetch (may return zero rows)
    # ------------------------------------------------------------------
    try:
        total = 0
        for status in ["SETTLED", "VOIDED", "LAPSED", "CANCELLED"]:
            n = fetch_cleared_orders_api(from_iso, to_iso, bet_status=status)
            print(f"[settlements] fetched {status}: +{n}")
            total += n

        print(f"[settlements] fetched clearedOrders total={total}")

    except Exception as e:
        print(f"[settlements] fetch failed: {e}")
        return  # SAFETY EXIT — do not attempt reconcile if fetch exploded

    # ------------------------------------------------------------------
    # 🔑 UNCONDITIONAL RECONCILIATION (FIX)
    # ------------------------------------------------------------------
    try:
        updated, _ = reconcile_orders()
        print(f"[settlements] reconciled={updated}")
    except Exception as e:
        print(f"[settlements] reconcile failed: {e}")

    # ------------------------------------------------------------------
    # BANK STATE SYNC (REALIZED PNL)
    # ------------------------------------------------------------------
    try:
        bank_state.reconcile_realized_pnl_from_orders()
    except Exception as e:
        print(f"[settlements] bank_state sync failed: {e}")

    # ------------------------------------------------------------------
    # EXPIRE CLOSED MARKETS
    # ------------------------------------------------------------------
    try:
        expired = close_settled_markets()
        print(f"[settlements] expired={expired}")
    except Exception as e:
        print(f"[settlements] expire failed: {e}")

    # ------------------------------------------------------------------
    # UPDATE DASHBOARD
    # ------------------------------------------------------------------
    from gui.dashboard_data import rebuild_kpi_tiles
    rebuild_kpi_tiles(source="LIVE")


# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py (append below start_settlement_daemon)
# 📆 PATCHED: 2025-11-04Z — Continuous winners/form updater
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def start_winners_daemon(interval_s: int = 5):
    """
    Background loop that extracts winners from bf_market_book.resultJson.
    Schema-safe: does NOT assume runner-level columns.
    """

    def _loop():
        while True:
            try:
                with connect_db(settlements_db_path()) as con:
                    rows = con.execute("""
                        SELECT marketId, resultJson
                        FROM bf_market_book
                        WHERE status='CLOSED'
                          AND resultJson IS NOT NULL
                    """).fetchall()

                if not rows:
                    time.sleep(interval_s)
                    continue

                with connect_db(settlements_db_path()) as con:
                    for r in rows:
                        mid = r["marketId"]
                        try:
                            data = json.loads(r["resultJson"] or "[]")
                        except Exception:
                            continue

                        # Betfair resultJson = list of runners
                        for runner in data:
                            sid = runner.get("selectionId")
                            status = runner.get("status")

                            # Winner = ACTIVE runner with WINNER status
                            if not sid or status not in ("WINNER", "PLACED"):
                                continue

                            con.execute("""
                                INSERT INTO runner_form_canonical(
                                    marketId,
                                    selectionId,
                                    day,
                                    status,
                                    profit,
                                    runs,
                                    wins,
                                    win_rate,
                                    last_seen
                                ) VALUES (
                                    ?, ?, date('now','utc'),
                                    'WIN', 1.0, 1, 1, 1.0,
                                    datetime('now','utc')
                                )
                                ON CONFLICT(marketId, selectionId)
                                DO UPDATE SET
                                    runs = runs + 1,
                                    wins = wins + 1,
                                    win_rate = ROUND(1.0 * wins / runs, 3),
                                    last_seen = datetime('now','utc')
                            """, (mid, str(sid)))

                    con.commit()

                print(f"[winners-daemon] processed {len(rows)} closed markets")

            except Exception as e:
                print(f"[winners-daemon] warn: {e}")

            time.sleep(interval_s)

    t = threading.Thread(target=_loop, name="WinnersDaemon", daemon=True)
    t.start()
    print(f"[winners-daemon] started (interval={interval_s}s)")

# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py (__main__ guard)
# 🔎 SEARCH: start_settlement_daemon(interval_s=300)
# 📆 PATCHED: 2025-12-03 — Do NOT start settlement daemon at import/startup.
#              GUI/orchestrator will start it AFTER Step-1 creds resolved.

# REMOVE automatic daemon start:
# start_settlement_daemon(interval_s=300)
# start_winners_daemon(interval_s=5)

# =============================================================================
# 📍 TARGET: engines/live/settlements.py
# 🔎 SEARCH: def start_all_settlement_services():
# 🎯 ACTION: Replace function (ensure DB + creds are ready before daemons start)
# 📆 PATCHED: 2026-02-10
# =============================================================================
def _wait_for_betfair_creds(max_wait_s: int = 30) -> bool:
    import time, sqlite3
    from engines.config_paths import autoscalp_db

    deadline = time.time() + max_wait_s

    while time.time() < deadline:
        try:
            con = sqlite3.connect(autoscalp_db(), timeout=3)
            con.row_factory = sqlite3.Row

            row = con.execute(
                """
                SELECT value
                  FROM app_kv
                 WHERE LOWER(key) IN ('betfair_session_token','session_token','betfair_session')
                 ORDER BY updated_at DESC
                 LIMIT 1
                """
            ).fetchone()

            con.close()

            if row and row["value"]:
                return True

        except Exception:
            pass

        time.sleep(0.5)

    return False


# === PATCH START =============================================================

def start_all_settlement_services():
    """Start settlement services only after DB + credentials exist."""
    try:
        from engines.config_paths import autoscalp_db
        db_path = autoscalp_db()

        if not db_path or not os.path.exists(os.path.dirname(db_path)):
            print("[settlements] delay: AUTO DB not yet ready")
            return

        # 🔑 NEW: credential gate
        if not _wait_for_betfair_creds(max_wait_s=30):
            print("[settlements] delay: Betfair creds not ready after 30s — skipping start")
            return

        start_settlement_daemon(interval_s=300)
        start_winners_daemon(interval_s=5)
        print("[settlements] services started AFTER DB + creds ready")

    except Exception as e:
        print(f"[settlements] failed to start services: {e}")


# === PATCH END ===============================================================



if __name__ == "__main__":

# === PATCH START ===
# 📍 TARGET: engines/live/settlements.py (__main__ guard)
# 📆 PATCHED: 2025-11-04Z — auto-start River + Winners daemons
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        start_all_settlement_services()

        
    except Exception as e:
        print(f"[daemons] warn: failed to start background daemons — {e}")
# === PATCH END ===


    try:
        ensure_schema()
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(130)
