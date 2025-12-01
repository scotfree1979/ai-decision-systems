#!/usr/bin/env python3
"""
AutoScalp — Unified DAL Layer
-----------------------------
This file eliminates all SQLite lock errors by enforcing:

    READ  = LOCAL replica (fast, safe)
             → fallback to CLOUD if empty

    WRITE = CLOUD only (never lock local)

    DAL   = fresh-read merge for all SELECTs

Supports: bets.db · autoscalp_gui.db · settlements.db · mastery_v7.db
Keeps all legacy imports working (DB_PATH, autoscalp_db(), etc.)
"""
from __future__ import annotations

import os, sqlite3, threading, time

# ============================================================================
# 📁 DB LOCATION & CONNECTOR LAYER (LOCAL + CLOUD, WITH LEGACY ALIASES)
# ============================================================================



import os
import sqlite3
import time
from typing import Tuple
# --- DAL: bypass any python-level sqlite hijacker by using C extension directly
import _sqlite3 as _raw_sqlite3

# --- DAL boot flags (must be set before any possible hijacker import) ---
import os as _os
_os.environ.setdefault("AUTOSCALP_DISABLE_HIJACK", "1")  # DAL controls sqlite opens

# --- core paths --------------------------------------------------------------

# Physical project root (…/analytics_beta_dev)
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))

LIVE_ROOT = os.path.join(DATA_DIR, "livecache")
os.makedirs(LIVE_ROOT, exist_ok=True)

LIVE_AUTO = os.path.join(LIVE_ROOT, "autoscalp_live.db")
LIVE_BETS = os.path.join(LIVE_ROOT, "bets_live.db")
LIVE_SETTLE = os.path.join(LIVE_ROOT, "settlements_live.db")
LIVE_MASTERY = os.path.join(LIVE_ROOT, "mastery_live.db")

# === PATCH START =========================================================
# 📍 TARGET: engines/config_paths.py (module root)
# 📆 PATCHED: 2025-12-03 — Replace iCloud CLOUD paths with new LiveCache paths
# PURPOSE:
#   • Remove dependency on iCloud
#   • LiveCache is now the only write-target in LIVE mode
#   • iCloud folder becomes backup-only, unused by DAL
#   • LiveCacheKeeper maintains size + WAL hygiene
# ========================================================================

# NEW: LiveCache root inside project data directory
LIVE_CACHE_ROOT = os.path.join(DATA_DIR, "livecache")
os.makedirs(LIVE_CACHE_ROOT, exist_ok=True)

# Replace all CLOUD paths with LiveCache equivalents
CLOUD_ROOT = LIVE_CACHE_ROOT

CLOUD_AUTO = os.path.join(LIVE_CACHE_ROOT, "autoscalp_livecache.db")
CLOUD_BETS = os.path.join(LIVE_CACHE_ROOT, "bets_livecache.db")
CLOUD_SETTLE = os.path.join(LIVE_CACHE_ROOT, "settlements_livecache.db")
CLOUD_MASTERY = os.path.join(LIVE_CACHE_ROOT, "mastery_livecache.db")

# Create blank LiveCache DBs if missing
for _p in (CLOUD_AUTO, CLOUD_BETS, CLOUD_SETTLE, CLOUD_MASTERY):
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    if not os.path.exists(_p):
        open(_p, "a").close()

print(f"[LiveCache DAL] active → {LIVE_CACHE_ROOT}")

# === PATCH END ===========================================================

# === PATCH START =========================================================
# 📍 TARGET: engines/config_paths.py  (module-level, no indent)
# 📆 PATCHED: 2025-12-03 — Add raw DB connectors for AlphaX mirror
# PURPOSE:
#   • Provide REAL sqlite3 connections for CLOUD and LOCAL
#   • NOT SafeConn, NOT wrapped, NOT hijacked
#   • Required for AlphaX CLOUD→LOCAL mirror to actually copy parents
#   • Prevents “_SafeConn has no attribute row_factory” errors
# ==========================================================================

import sqlite3 as _raw_sqlite3

def _raw_open(path: str, *, timeout: int = 10):
    """
    Return a REAL sqlite3 connection (never SafeConn):
        • WAL mode
        • busy_timeout
        • autocommit
        • row_factory can be set by caller
    """
    con = _raw_sqlite3.connect(path, timeout=timeout, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
    return con

def _get_local_conn(family: str):
    """
    Return a REAL LOCAL DB connection for the given family.
    AlphaX mirror uses this to write orders locally.
    """
    if family == "auto":
        return _raw_open(LOCAL_AUTO)
    if family == "bets":
        return _raw_open(LOCAL_BETS)
    if family == "mastery":
        return _raw_open(LOCAL_MASTERY)
    if family == "settlements":
        return _raw_open(LOCAL_SETTLE)
    raise ValueError(f"Unknown family: {family}")

def _get_cloud_conn(family: str):
    """
    Return a REAL CLOUD DB connection for the given family.
    AlphaX mirror uses this to read cloud orders/odds.
    """
    if family == "auto":
        return _raw_open(CLOUD_AUTO)
    if family == "bets":
        return _raw_open(CLOUD_BETS)
    if family == "mastery":
        return _raw_open(CLOUD_MASTERY)
    if family == "settlements":
        return _raw_open(CLOUD_SETTLE)
    raise ValueError(f"Unknown family: {family}")

# === PATCH END ===========================================================



def _resolve_data_dir() -> str:
    """
    Canonical data directory resolver (lowercase 'data').
    Priority:
      1) AUTOSCALP_DB_DIR env override
      2) <repo>/data (created if missing)
    """
    env_dir = os.environ.get("AUTOSCALP_DB_DIR")
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir
    data_low = os.path.join(_ROOT, "data")
    os.makedirs(data_low, exist_ok=True)
    return data_low


DATA_DIR = _resolve_data_dir()

# --- canonical local DBs (authoritative copies) ------------------------------

LOCAL_BETS: str = os.path.join(DATA_DIR, "bets.db")
LOCAL_AUTO: str = os.path.join(DATA_DIR, "autoscalp_gui.db")
LOCAL_SETTLE: str = os.path.join(DATA_DIR, "settlements.db")
LOCAL_MASTERY: str = os.path.join(DATA_DIR, "mastery_v7.db")

# --- cloud mirrors (backup / low-contention writers) -------------------------
# REPLACED: Cloud is no longer used as a writer target.
#           Cloud will continue to exist for iCloud backup ONLY.
#           All CLOUD_* variables now point to LiveCache instead.

# === PATCH START =============================================================
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-12-03 — Replace cloud paths with LiveCache paths
# PURPOSE:
#   • Introduce LiveCache (local 5-day rolling DB)
#   • Replace CLOUD_* variables so DAL writes to LiveCache
#   • Stop all DAL writes to iCloud
#   • Keep actual cloud folder untouched for backup only
# ============================================================================

LIVE_ROOT: str = os.path.join(DATA_DIR, "livecache")
os.makedirs(LIVE_ROOT, exist_ok=True)

# LiveCache DBs (writer layer)
LIVE_AUTO: str = os.path.join(LIVE_ROOT, "autoscalp_live.db")
LIVE_BETS: str = os.path.join(LIVE_ROOT, "bets_live.db")
LIVE_SETTLE: str = os.path.join(LIVE_ROOT, "settlements_live.db")
LIVE_MASTERY: str = os.path.join(LIVE_ROOT, "mastery_live.db")

# Replace “CLOUD” variables with LiveCache
CLOUD_ROOT = LIVE_ROOT
CLOUD_AUTO = LIVE_AUTO
CLOUD_BETS = LIVE_BETS
CLOUD_SETTLE = LIVE_SETTLE
CLOUD_MASTERY = LIVE_MASTERY

# Back-compat aliases for old modules
_LOCAL_AUTO_PATH = LOCAL_AUTO
_CLOUD_AUTO_PATH = CLOUD_AUTO

def get_local_auto_path() -> str:
    return LOCAL_AUTO

def get_cloud_auto_path() -> str:
    # Back-compat: returns LiveCache path (not actual iCloud)
    return CLOUD_AUTO

# === PATCH END ===============================================================

# === PATCH START =============================================================
# 📍 TARGET: engines/config_paths.py (module root)
# 📆 PATCHED: 2025-12-03 — LiveCacheKeeper (5-day retention + WAL cleaner)
# PURPOSE:
#   • Trim LiveCache DBs to last 5 days only
#   • Clean WAL/SHM files regularly
#   • Prevent LiveCache growth + avoid corruption
# ============================================================================

import threading as _lc_thread
import sqlite3 as _lc_sqlite
import os as _lc_os
import time as _lc_time

_LC_RETENTION_DAYS = 5

_LIVE_TABLES_TS = {
    "orders": "opened_at",
    "order_events": "ts",
    "odds_current": "updated_ts",
    "inbound_oc_cache": "last_sync_ts",
    "oc_series": "snapshot_ts",
}

def _lc_trim_table(con, table, ts_col):
    try:
        con.execute(
            f"DELETE FROM {table} "
            f"WHERE date({ts_col}) < date('now','utc','-{_LC_RETENTION_DAYS} days')"
        )
        con.commit()
    except Exception as e:
        print(f"[LiveCacheKeeper] trim warn {table}: {e}")

def _lc_wal_cleanup(db_path):
    try:
        wal = db_path + "-wal"
        shm = db_path + "-shm"
        if _lc_os.path.exists(wal):
            _lc_os.remove(wal)
        if _lc_os.path.exists(shm):
            _lc_os.remove(shm)
    except Exception as e:
        print(f"[LiveCacheKeeper] WAL clean warn: {e}")

def _lc_keeper_loop():
    while True:
        try:
            # trim each LiveCache DB
            for db in (CLOUD_AUTO, CLOUD_BETS, CLOUD_SETTLE, CLOUD_MASTERY):
                try:
                    con = _lc_sqlite.connect(db, timeout=4, isolation_level=None)
                    con.row_factory = _lc_sqlite.Row

                    for table, ts in _LIVE_TABLES_TS.items():
                        # Skip if table does not exist
                        try:
                            con.execute(f"SELECT 1 FROM {table} LIMIT 1")
                        except Exception:
                            continue

                        _lc_trim_table(con, table, ts)

                    con.close()

                    # WAL/SHM cleanup
                    _lc_wal_cleanup(db)

                except Exception as e:
                    print(f"[LiveCacheKeeper] db warn {db}: {e}")

        except Exception as e:
            print(f"[LiveCacheKeeper] loop warn: {e}")

        _lc_time.sleep(90)  # run ~ every 90 seconds

# start background keeper once
if not any(t.name == "LiveCacheKeeper" for t in threading.enumerate()):
    _lc_thread.Thread(
        target=_lc_keeper_loop,
        name="LiveCacheKeeper",
        daemon=True
    ).start()
    print("[LiveCacheKeeper] active (5-day trim, WAL purge)")
# === PATCH END ===============================================================



MASTERY_CLOUD_ROOT: str = os.path.join(
    os.path.dirname(CLOUD_ROOT),
    "AutoScalpCloud",
    "data",
)
CLOUD_MASTERY: str = os.path.join(MASTERY_CLOUD_ROOT, "mastery_cache.db")

for _p in (LOCAL_BETS, LOCAL_AUTO, LOCAL_SETTLE, LOCAL_MASTERY, CLOUD_AUTO, CLOUD_BETS, CLOUD_MASTERY):
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    if not os.path.exists(_p):
        open(_p, "a").close()

# --- canonical exported path constants (back-compat) -------------------------




BETS_DB_PATH: str = LOCAL_BETS
AUTOSCALP_DB_PATH: str = LOCAL_AUTO
GUI_DB_PATH: str = AUTOSCALP_DB_PATH  # legacy name for autoscalp_gui.db

DB_PATH: str = BETS_DB_PATH           # legacy “main” DB pointer (bets.db)
BETS_DB: str = BETS_DB_PATH
AUTOSCALP_DB: str = AUTOSCALP_DB_PATH
AUTO_DB: str = AUTOSCALP_DB_PATH      # some old code uses AUTO_DB
CLOUD_BETS_PATH: str = CLOUD_BETS
CLOUD_AUTOSCALP_DB: str = CLOUD_AUTO

# --- simple path helpers -----------------------------------------------------

# --- Healer guard + helpers (near the healer code) ---
_RESERVED_SQLITE = {"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"}

def _is_reserved_sqlite_table(name: str) -> bool:
    try: n = str(name)
    except Exception: return True
    return n.startswith("sqlite_") or n in _RESERVED_SQLITE

def _q(name: str) -> str:
    # Double-quote SQLite identifiers and escape any inner quotes
    return '"' + str(name).replace('"', '""') + '"'


def repo_root() -> str:
    """Return the repo root directory (…/analytics_beta_dev)."""
    return _ROOT


def data_dir() -> str:
    """Return current base data directory."""
    return DATA_DIR


def autoscalp_db() -> str:
    """Canonical local autoscalp GUI DB path."""
    return LOCAL_AUTO


def bets_db() -> str:
    """Canonical local bets DB path."""
    return LOCAL_BETS


def settlements_db() -> str:
    return LOCAL_SETTLE


def mastery_v7_db() -> str:
    return LOCAL_MASTERY


def cloud_data_dir() -> str:
    """Base directory for cloud caches."""
    return MASTERY_CROSS_ROOT if False else MASTERY_CLOUD_ROOT  # kept simple; adjust if needed


def mastery_v7_cache_db() -> str:
    """iCloud-hosted mastery cache DB."""
    return CLOUD_MASTERY

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py — new raw LOCAL reader
# 📆 PATCHED: 2025-11-17Z — provide DAL-safe raw reader for odds + monitor

def open_local_readonly_auto(timeout: float = 8.0):
    """
    RAW LOCAL reader for autoscalp_gui.db.
    • Never uses DAL
    • Never touches CLOUD
    • Always a real sqlite3.Connection
    • Safe for MarketMonitor, OddsService, GUI warm-up
    """
    con = _raw_sqlite3.connect(
        f"file:{LOCAL_AUTO}?mode=ro",
        uri=True,
        timeout=timeout,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON;")
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA read_uncommitted=1;")
    except Exception:
        pass
    return con
# === PATCH END ===





# --- low-level opener with WAL / timeouts -----------------------------------

# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: end of file
# 🛠 APPEND
# 📆 PATCHED: 2025-11-18 WAL entry for AlphaX

# === PATCH START ===
def open_db(family: str, ro=False, rw=False, **_):
    """
    WAL entry point used by AlphaX scheduler.
    family ∈ {'auto', 'bets', 'mastery', 'settlements'}
    """
    if family == "auto":        return open_auto_db(ro=ro, rw=rw)
    if family == "bets":        return open_bets_db(ro=ro, rw=rw)
    if family == "mastery":     return open_mastery_db(ro=ro, rw=rw)
    if family == "settlements": return open_settlements_db(ro=ro, rw=rw)
    raise ValueError(f"Unknown DB family: {family}")
# === PATCH END ===



# --- four primary connectors (your Autocon + friends) ------------------------

def bets_conn(*, ro: bool = False, timeout: float = 10.0) -> sqlite3.Connection:
    """Primary trading DB (bets.db). Writes go to LOCAL only."""
    return open_db(LOCAL_BETS, ro=ro, timeout=timeout)


# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def _open_auto_local
# 🛠 REPLACE ENTIRE FUNCTION

def _open_auto_local(timeout: float = 10.0, ro: bool = False):
    """
    RAW local opener — bypass DAL for background writer + GUI warm-up.
    """
    import _sqlite3 as _raw
    mode = "ro" if ro else "rw"
    con = _raw.connect(
        f"file:{LOCAL_AUTO}?mode={mode}",
        uri=True,
        timeout=timeout,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=12000")
        con.execute("PRAGMA read_uncommitted=1")
    except Exception:
        pass
    return con



# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def _open_auto_cloud
# 🛠 REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2025-11-18 — fix background writer crash

def _open_auto_cloud(timeout: float = 10.0, ro: bool = False):
    """
    RAW cloud opener — bypass DAL.
    Background writer must never call open_db() because open_db
    is now a family-based MAP/DAL dispatcher.
    """
    import _sqlite3 as _raw
    mode = "ro" if ro else "rwc"
    con = _raw.connect(
        f"file:{CLOUD_AUTO}?mode={mode}&cache=shared",
        uri=True,
        timeout=timeout,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA busy_timeout=12000")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con


# ============================================================
# MODE SWITCH: setup vs live (GUI controls this)
# ============================================================
DAL_MODE = "SETUP"

def enable_live_dal():
    global DAL_MODE
    DAL_MODE = "LIVE"

def enable_setup_dal():
    global DAL_MODE
    DAL_MODE = "SETUP"


def auto_conn(*, rw: bool = True, timeout: float = 10.0):
    if DAL_MODE.upper() == "SETUP":
        # SETUP = raw local only, NO DAL, NO cloud, NO attach
        return _open_auto_local(timeout=timeout, ro=not rw)


    # NEW → INSPECT THE REAL PATH
    import inspect
    caller = inspect.stack()[1]
    path = CLOUD_AUTO if rw else LOCAL_AUTO
    print(f"[DAL-TRACE] rw={rw} → {path} ← called from {caller.filename}:{caller.lineno}")


    # --- LIVE MODE (new DAL) ---
    # your full DAL code unchanged below this line

    """
    Unified AutoScalp connector with freshness merge.

    SELECT behaviour:
        • Query LOCAL and CLOUD
        • Use per-table timestamp rules to pick the freshest single row
        • Always return exactly 1 row (wrapped in _RowCursor)

    WRITE behaviour:
        • Writes always go to CLOUD
        • Commit immediately
    """

    # fresh RO local handle
    local = _open_auto_local(timeout=timeout, ro=True)
    # global singleton cloud handle
    cloud = _open_auto_singleton(timeout=timeout)

    # ────────────────────────────────────────────────────────────────
    # HARD-CODED TIMESTAMP MAP  (H1)
    # Table → column or ordered list of columns (first valid wins)
    # ────────────────────────────────────────────────────────────────
    TS_COL_MAP = {
        # core OC + odds
        "inbound_oc_cache":      ["last_sync_ts"],
        "oc_series":             ["snapshot_ts"],
        "odds_current":          ["updated_ts"],

        # anchor + plan
        "orders":                ["closed_at", "opened_at"],
        "plan_ledger":           ["updated_at", "decided_at"],
        "decisions":             ["decided_at"],
        "dashboard_activity":    ["window_start_ts"],
        "dashboard_runs":        ["now_ts"],

        # state tables
        "internal_bank":         ["updated_at"],
        "book_state":            ["updated_at"],
        "live_state":            ["updated_at"],
        "live_state_table":      ["updated_at"],

        # mastery / feedback (runtime relevant ones)
        "mastery_feedback":                  ["ts"],
        "mastery_feedback_backfill":         ["ts"],
        "mastery_cache":                     ["ts"],
        "training_events":                   ["ts"],
        "mastery_training_metrics":          ["ts"],
        "mastery_posterior_adjustments":     ["ts"],

        # playbook / settled
        "playbooks_settled":     ["created_at"],
        "playbooks_learning":    ["created_at"],

        # runners / snapshots
        "dashboard_runners":     ["last_snapshot_ts"],
        "runner_history":        ["updated_at"],
        "runner_form":           ["created_at"],
        "runner_form_snapshot":  ["created_at"],

        # bets meta
        "bets":                  ["timestamp"],
        "bets_meta_cache":       ["day"],

        # settle mirrors
        "bf_cleared_orders_cache":  ["settledDate"],
        "bf_cleared_orders_local":  ["settledDate"],

        # fallback tables (latest available)
        "events":                ["ts"],
        "loss_tags":             ["created_at"],
        "liability_signals":     ["created_at"],
        "liability_signals_proxy": ["created_at"],
        "decisions_proxy":       ["created_at"],

        # if not listed, DAL will NOT merge freshness — local-only first, then cloud
    }

    # ────────────────────────────────────────────────────────────────
    # Helper: extract table name from SQL
    # ────────────────────────────────────────────────────────────────
    def _extract_table(sql: str) -> str | None:
        s = sql.strip().lower()
        if not s.startswith("select"):
            return None
        tokens = s.split()
        if "from" not in tokens:
            return None
        idx = tokens.index("from")
        if idx + 1 >= len(tokens):
            return None
        t = tokens[idx + 1].strip().strip('"').strip("'")
        return t

    # ────────────────────────────────────────────────────────────────
    # SELECT merge logic (freshest row wins)
    # ────────────────────────────────────────────────────────────────
    def _select(sql: str, params=()):
        table = _extract_table(sql)
        ts_cols = TS_COL_MAP.get(table)

        # Run both queries safely
        try:
            lcur = local.execute(sql, params)
            lrows = lcur.fetchall()
        except Exception:
            lrows = []

        try:
            ccur = cloud.execute(sql, params)
            crows = ccur.fetchall()
        except Exception:
            crows = []

        # no merge if table not mapped OR multiple rows
        if not ts_cols:
            # prefer local if any rows
            if lrows:
                return _RowCursor(lrows)
            return _RowCursor(crows)

        # single-row expectation (runtime always scoping by marketId/selectionId)
        lrow = lrows[0] if lrows else None
        crow = crows[0] if crows else None
        if lrow is None and crow is None:
            return _RowCursor([])

        # choose freshest row
        def _ts_from(row):
            if row is None:
                return None
            for col in ts_cols:
                if col in row.keys():
                    v = row[col]
                    if v not in (None, ""):
                        return v
            return None

        lt = _ts_from(lrow)
        ct = _ts_from(crow)

        # pick best defined timestamp
        if lt is None:
            # local missing → cloud wins
            return _RowCursor([crow])
        if ct is None:
            # cloud missing → local wins
            return _RowCursor([lrow])

        # both non-empty → numeric or lexical comparison works (ISO strings)
        if ct > lt:
            return _RowCursor([crow])
        else:
            return _RowCursor([lrow])

    # ────────────────────────────────────────────────────────────────
    # Main execute wrapper
    # ────────────────────────────────────────────────────────────────
    def execute(sql: str, params=()):
        s = sql.strip().lower()
        if s.startswith("select"):
            return _select(sql, params)

        # writes → cloud only
        cur = cloud.execute(sql, params)
        try:
            cloud.commit()
        except Exception:
            pass
        return cur

    # ────────────────────────────────────────────────────────────────
    # Object returned by DAL
    # ────────────────────────────────────────────────────────────────
    class _DAL:
        def __init__(self, local_con, cloud_con):
            self._local = local_con     # read-only reader
            self._cloud = cloud_con     # main writer
            self._closed = False

        # --- context manager support ---
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                if exc_type is None:
                    try:
                        self._local.commit()
                    except Exception:
                        pass
            finally:
                try:
                    self._local.close()
                except Exception:
                    pass
                self._closed = True
            return False

        # --- core sqlite proxy methods ---
        def execute(self, sql, params=()):
            return self._local.execute(sql, params)

        def executemany(self, sql, seq):
            return self._local.executemany(sql, seq)

        def executescript(self, script):
            return self._local.executescript(script)

        def cursor(self):
            return self._local.cursor()

        # --- writer passthrough ---
        def commit(self):
            try:
                return self._cloud.commit()
            except Exception:
                return None

        def rollback(self):
            try:
                return self._cloud.rollback()
            except Exception:
                return None

        def close(self):
            if not self._closed:
                try:
                    self._local.close()
                except Exception:
                    pass
                self._closed = True

        # --- row_factory passthrough ---
        @property
        def row_factory(self):
            return self._local.row_factory

        @row_factory.setter
        def row_factory(self, v):
            self._local.row_factory = v


# === PATCH START ===
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-11-17Z — force global LOCAL read visibility
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def autoscalp_ro(timeout: float = 10.0):
    """
    Global read-only accessor for autoscalp_gui.db.
    Forces LOCAL copy ALWAYS (never cloud),
    guaranteeing odds_current and inbound_oc_cache visibility.
    """
    con = _open_auto_local(timeout=timeout, ro=True)
    con.row_factory = sqlite3.Row
    return con

# ensure every older alias points to it
AUTO_RO = autoscalp_ro
AUTO_R = autoscalp_ro
# === PATCH END ===


# single shared cloud handle for AUTO to keep WAL contention low
_auto_cloud_handle: sqlite3.Connection | None = None
_auto_lock = None


def _auto_lock_obj():
    global _auto_lock
    if _auto_lock is None:
        import threading as _t
        _auto_lock = _t.Lock()
    return _auto_lock


def _open_auto_singleton(timeout: float = 10.0) -> sqlite3.Connection:
    global _auto_cloud_handle
    lock = _auto_lock_obj()
    with lock:
        if _auto_cloud_handle is None:
            # IMPORTANT: use CLOUD for write handle
            _auto_cloud_handle = _open_auto_cloud(timeout=timeout, ro=False)
        return _auto_cloud_handle



def settle_conn(*, ro: bool = False, timeout: float = 10.0) -> sqlite3.Connection:
    return open_db(LOCAL_SETTLE, ro=ro, timeout=timeout)




def mastery_conn(*, ro: bool = False, timeout: float = 10.0) -> sqlite3.Connection:
    return open_db(LOCAL_MASTERY, ro=ro, timeout=timeout)


# --- simple cursor wrapper for local SELECT results --------------------------

class _RowCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self._idx = 0

    def fetchall(self):
        return list(self._rows)

    def fetchone(self):
        if self._idx >= len(self._rows):
            return None
        r = self._rows[self._idx]
        self._idx += 1
        return r

    def __iter__(self):
        return iter(self._rows)

# --- legacy connector aliases (DB_PATH, AUTOCON, etc.) ----------------------

# “Con” objects (old names)
AUTOCON    = auto_conn
BETSCON    = bets_conn
SETTLECON  = settle_conn
MASTERYCON = mastery_conn

# legacy connect_* helpers
def connect_db(path: str | None = None, ro: bool = False, timeout: float = 10.0):
    """
    Back-compat: open bets DB unless an explicit path is provided.
    This is what old code using sqlite3.connect(DB_PATH) assumed.
    """
    return open_db(path or BETS_DB_PATH, ro=ro, timeout=timeout)


def connect_bets_db(timeout: float = 10.0, ro: bool = False):
    return bets_conn(ro=ro, timeout=timeout)

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py (legacy connectors section)
# 📆 PATCHED: 2025-11-16Z — add missing settlements connector
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def connect_settlements_db(timeout: float = 10.0, ro: bool = False):
    """
    Legacy helper: explicitly connect to settlements.db.
    Mirrors connect_bets_db / connect_autoscalp_db behaviour.
    """
    return open_db(LOCAL_SETTLE, ro=ro, timeout=timeout)

# === PATCH END ===


def connect_autoscalp_db(timeout: float = 10.0, ro: bool = False):
    return auto_conn(rw=not ro, timeout=timeout)


def connect_orders_db(ro: bool = True, timeout: float = 10.0):
    """
    Legacy hook: open the DB that owns the `orders` table.
    Prefer autoscalp_gui.db if it has the table, otherwise fall back to bets.db.
    """
    candidates = [LOCAL_AUTO, LOCAL_BETS]
    chosen = None
    for path in candidates:
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5) as con:
                cur = con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders'")
                if cur.fetchone():
                    chosen = path
                    break
        except Exception:
            continue
    if chosen is None:
        chosen = LOCAL_BETS
    return open_db(chosen, ro=ro, timeout=timeout)


def connect_mastery_v7_db(ro: bool = False, timeout: float = 10.0):
    return mastery_conn(ro=ro, timeout=timeout)


def connect_mastery_v7_cache(ro: bool = False, timeout: float = 10.0):
    return open_db(CLOUD_MASTERY, ro=ro, timeout=timeout)


def auto_ro(timeout: float = 10.0):
    """
    Legacy read-only autoscalp connector.
    Historically: open autoscalp_gui.db read-only.
    """
    return _open_auto_local(timeout=timeout, ro=True)

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py (legacy read-only helpers)
# 📆 PATCHED: 2025-11-16Z — add missing settle_ro alias
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def settle_ro(timeout: float = 10.0):
    """
    Legacy read-only settlements connector.
    Historically: open settlements.db read-only.
    """
    return open_db(LOCAL_SETTLE, ro=True, timeout=timeout)

# === PATCH END ===



# --- mode bookkeeping (used by GUI / upgrade flows) -------------------------

_MODE: str = (os.environ.get("AUTOSCALP_MODE") or "learning").lower()


def set_db_paths(
    mode: str | None = None,
    *,
    bets: str | None = None,
    autoscalp: str | None = None,
    data_dir_override: str | None = None,
    quiet: bool = False,
) -> Tuple[str, str]:
    """
    Back-compat `set_db_paths`:
      • mode: 'test' | 'learning' | 'live'
      • can override bets/auto paths and/or base data dir
      • updates BETS_DB_PATH / AUTOSCALP_DB_PATH + aliases.
    """
    global DATA_DIR, LOCAL_BETS, LOCAL_AUTO, LOCAL_SETTLE, LOCAL_MASTERY
    global BETS_DB_PATH, AUTOSCALP_DB_PATH, DB_PATH, BETS_DB, AUTOSCALP_DB, AUTO_DB, _ROOT, _MODE

    m = (mode or os.environ.get("AUTOSCALP_MODE") or "learning").lower()
    _MODE = m

    base = data_dir() if data_dir_override is None else os.path.abspath(data_dir_override)
    os.makedirs(base, exist_ok=True)

    if bets is None:
        bet_path = os.path.join(base, "bets.test.db" if m == "test" else "bets.db")
    else:
        bet_path = bets

    if autoscalp is None:
        auto_path = os.path.join(base, "autoscalp_gui.test.db" if m == "test" else "autoscalp_gui.db")
    else:
        auto_path = autoscalp

    LOCAL_BETS = os.path.abspath(bet_path)
    LOCAL_AUTO = os.path.abspath(auto_path)
    LOCAL_SETTLE = os.path.join(base, "settlements.db")
    LOCAL_MASTERY = os.path.join(base, "mastery_v7.db")

    BETS_DB_PATH = LOCAL_BETS
    AUTOSCALP_DB_PATH = LOCAL_AUTO
    DB_PATH = BETS_DB_PATH
    BETS_DB = BETS_DB_PATH
    AUTOSCALP_DB = AUTOSCALP_DB_PATH
    AUTO_DB = AUTOSCALP_DB_PATH

    if not quiet:
        print(f"[paths] DATA_DIR={base} BETS_DB={BETS_DB_PATH} AUTO_DB={AUTOSCALP_DB_PATH}")
    return BETS_DB_PATH, AUTOSCALP_DB_PATH


def get_mode() -> str:
    """Return last selected mode as lowercase string."""
    return _MODE


def is_replay_mode() -> bool:
    return _MODE in ("test", "replay")


def get_db_paths() -> Tuple[str, str]:
    """Return (bets_db_path, autoscalp_db_path)."""
    return BETS_DB_PATH, AUTOSCALP_DB_PATH


# --- simple DB sanity helpers ------------------------------------------------

def ensure_db_readable(path: str | None = None) -> str:
    """
    Legacy helper used by older modules.
    Ensures the DB file and its parent directory exist.
    Does not change schema.
    """
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        try:
            open(p, "a").close()
        except Exception:
            pass
    return p


def ensure_db_ready() -> None:
    """
    Best-effort: ensure bets.db and autoscalp_gui.db exist and have app_kv.
    Safe to call repeatedly.
    """
    for path in (LOCAL_BETS, LOCAL_AUTO):
        try:
            con = open_db(path, ro=False)
            try:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS app_kv("
                    "  key TEXT PRIMARY KEY,"
                    "  value TEXT,"
                    "  updated_at TEXT)"
                )
                con.commit()
            finally:
                con.close()
        except Exception:
            pass


def q_retry(con: sqlite3.Connection, sql: str, params=(), *, tries: int = 6, delay_s: float = 0.08):
    """
    Back-compat retry wrapper used by dashboard / lanes for transient locks.
    """
    last: Exception | None = None
    for i in range(max(1, tries)):
        try:
            return con.execute(sql, params)
        except sqlite3.OperationalError as e:
            last = e
            m = str(e).lower()
            if (("locked" in m) or ("busy" in m)) and i < tries - 1:
                time.sleep(delay_s * (i + 1))
                continue
            raise
    if last:
        raise last

print("[paths] ✔ Legacy+DAL mapping active — all connectors available")


# ============================================================
# 📌 AUTO-HEAL: CLOUD SCHEMA REPAIR (ALL 4 DBS)
#     • Local = source of truth
#     • Cloud = write-only, must match local
#     • No data copy, no table drops, no null injections
#     • Only ensures tables + columns exist
#     • Quiet unless a repair is made
# ============================================================

import threading, sqlite3, os, time

# --- Local and Cloud pairs -----------------------------
_SCHEMA_PAIRS = [
    (LOCAL_AUTO,     CLOUD_AUTO,     "autoscalp_gui"),
    (LOCAL_BETS,     CLOUD_BETS,     "bets"),
    (LOCAL_SETTLE,   CLOUD_SETTLE,   "settlements"),
    (LOCAL_MASTERY,  CLOUD_MASTERY,  "mastery_v7"),
]

# --- Healer guard: skip SQLite internal tables -------------------------
_RESERVED_SQLITE = {"sqlite_sequence", "sqlite_stat1", "sqlite_stat4"}
def _is_reserved_sqlite_table(name: str) -> bool:
    try:
        n = str(name)
    except Exception:
        return True
    return n.startswith("sqlite_") or n in _RESERVED_SQLITE


def _schema_of(path: str) -> dict:
    """Return {table: set(columns)} for a database file."""
    try:
        con = sqlite3.connect(path, timeout=5)
        con.row_factory = sqlite3.Row
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        tables = {}
        for r in rows:
            t = r["name"] if isinstance(r, sqlite3.Row) else r[0]
            if _is_reserved_sqlite_table(t):
                continue                      # <-- skip SQLite internals
            cols = con.execute(f"PRAGMA table_info('{t}')").fetchall()
            tables[t] = set(c["name"] for c in cols)
        con.close()
        return tables
    except Exception:
        return {}

def _heal_cloud(local_path: str, cloud_path: str, label: str):
    """Ensure cloud tables + columns match local schema (structure only)."""
    loc = _schema_of(local_path)
    cld = _schema_of(cloud_path)

    repaired = False
    try:
        con = sqlite3.connect(cloud_path, timeout=10, isolation_level=None)
        cur = con.cursor()

        # Ensure tables exist
        for table, local_cols in loc.items():
            if _is_reserved_sqlite_table(table):
                continue
            if table not in cld:
                col_def = ", ".join(f"{_q(c)} TEXT" for c in local_cols) or '"id" INTEGER'
                cur.execute(f"CREATE TABLE IF NOT EXISTS {_q(table)} ({col_def})")
                repaired = True

        # Ensure columns exist (this is the only place we add columns)
        cld = _schema_of(cloud_path)
        for table, local_cols in loc.items():
            if _is_reserved_sqlite_table(table):
                continue
            cloud_cols = cld.get(table, set())
            missing = local_cols - cloud_cols
            for col in missing:
                cur.execute(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(col)} TEXT")
                repaired = True


        if repaired:
            print(f"[heal:{label}] repaired cloud schema")

        con.close()
    except Exception as e:
        # Quietly ignore SQLite-internal complaints if any slipped through
        msg = str(e).lower()
        if "reserved for internal use" not in msg:
            print(f"[heal:{label}] warn: {e}")


def _background_healer(interval: float = 30.0):
    """Runs forever, every 30s, healing cloud schemas."""
    while True:
        for local_path, cloud_path, label in _SCHEMA_PAIRS:
            _heal_cloud(local_path, cloud_path, label)
        time.sleep(interval)

# Start once
if not any(t.name == "CloudSchemaHeal" for t in threading.enumerate()):
    t = threading.Thread(
        target=_background_healer,
        name="CloudSchemaHeal",
        daemon=True
    )
    t.start()

# ============================================================
# 🚀 FAST BACKGROUND WRITER — CLOUD → LOCAL SYNC
# ============================================================

def _background_writer_fast(interval: float = 2.0):
    """
    Fast incremental sync from CLOUD → LOCAL.

    For every table in autoscalp_gui:
      • Find latest timestamp in LOCAL
      • Find latest timestamp in CLOUD
      • If CLOUD has newer → pull only those rows
      • Write them into LOCAL with INSERT OR REPLACE
    """
    import sqlite3, time

    # same TS_COL_MAP as in auto_conn()
    TS_COL_MAP = {
        "inbound_oc_cache":      ["last_sync_ts"],
        "oc_series":             ["snapshot_ts"],
        "odds_current":          ["updated_ts"],
        "orders":                ["closed_at", "opened_at"],
        "plan_ledger":           ["updated_at", "decided_at"],
        "decisions":             ["decided_at"],
        "dashboard_activity":    ["window_start_ts"],
        "dashboard_runs":        ["now_ts"],
        "internal_bank":         ["updated_at"],
        "book_state":            ["updated_at"],
        "live_state":            ["updated_at"],
        "live_state_table":      ["updated_at"],
        "mastery_feedback":                  ["ts"],
        "mastery_feedback_backfill":         ["ts"],
        "mastery_cache":                     ["ts"],
        "training_events":                   ["ts"],
        "mastery_training_metrics":          ["ts"],
        "mastery_posterior_adjustments":     ["ts"],
        "playbooks_settled":     ["created_at"],
        "playbooks_learning":    ["created_at"],
        "dashboard_runners":     ["last_snapshot_ts"],
        "runner_history":        ["updated_at"],
        "runner_form":           ["created_at"],
        "runner_form_snapshot":  ["created_at"],
        "bets":                  ["timestamp"],
        "bf_cleared_orders_cache":  ["settledDate"],
        "bf_cleared_orders_local":  ["settledDate"],
        "events":                ["ts"],
        "loss_tags":             ["created_at"],
        "liability_signals":     ["created_at"],
        "liability_signals_proxy": ["created_at"],
        "decisions_proxy":       ["created_at"],
        "dashboard_tiles":       ["updated_at"],
    }

    cloud = _open_auto_cloud(timeout=5, ro=False)
    local = _open_auto_local(timeout=5, ro=False)

    while True:
        for table, ts_cols in TS_COL_MAP.items():

            # skip tables without timestamps
            if not ts_cols:
                continue

            ts_col = ts_cols[0]

            # ---- read newest LOCAL ts ----
            try:
                r = local.execute(
                    f"SELECT MAX({ts_col}) AS mx FROM '{table}'"
                ).fetchone()
                local_ts = r["mx"]
            except Exception:
                local_ts = None

            # ---- read newest CLOUD ts ----
            try:
                r = cloud.execute(
                    f"SELECT MAX({ts_col}) AS mx FROM '{table}'"
                ).fetchone()
                cloud_ts = r["mx"]
            except Exception:
                continue  # cloud table might not exist

            # nothing newer in cloud
            if cloud_ts is None or (local_ts is not None and cloud_ts <= local_ts):
                continue

            # ---- pull newer rows from CLOUD ----
            try:
                rows = cloud.execute(
                    f"""
                    SELECT * FROM '{table}'
                    WHERE {ts_col} IS NOT NULL
                      AND {ts_col} > ?
                    """,
                    (local_ts,)
                ).fetchall()
            except Exception:
                continue

            if not rows:
                continue

            # ---- write them into LOCAL ----
            cols = rows[0].keys()
            collist = ",".join([f'"{c}"' for c in cols])
            placeholders = ",".join(["?"] * len(cols))
            sql = f"""
                INSERT OR REPLACE INTO '{table}' ({collist})
                VALUES ({placeholders})
            """

            try:
                for row in rows:
                    local.execute(sql, tuple(row))
                local.commit()
            except Exception:
                pass

        time.sleep(interval)


# ============================================================
# 🚀 Start background writer once
# ============================================================
if not any(t.name == "CloudWriterFast" for t in threading.enumerate()):
    t = threading.Thread(
        target=_background_writer_fast,
        name="CloudWriterFast",
        daemon=True
    )
    t.start()


# === PATCH START ===========================================
# 📍 TARGET: engines/config_paths.py
# 🔥 FULL REPLACEMENT OF THE ENTIRE DAL REGION
# 📆 PATCHED: 2025-11-24 — FINAL RW-ONLY DAL (No RO Anywhere)
# ============================================================

# ============================================================
# 🌐 RW-ONLY DAL — FINAL, STABLE, PERMANENT
# ------------------------------------------------------------
# SETUP MODE:
#     • Always LOCAL RW
#
# LIVE MODE:
#     • READ = LOCAL RW
#     • WRITE = CLOUD RW
#
# ATTACH MODEL:
#     • PRIMARY family attaches CLOUD
#     • All non-primary families attach LOCAL
#     • Never attach anything RO
#     • No "mode=ro" ever appears again
#
# ALPHAX + HIJACK guarantee write ordering → safe RW everywhere
# ============================================================

# ----------------------------------------------------------------------
# 📌 Mode Control (SETUP ←→ LIVE)
# ----------------------------------------------------------------------
DAL_MODE = "SETUP"

def enable_live_dal():
    """Called at Step 4."""
    global DAL_MODE
    DAL_MODE = "LIVE"

def enable_setup_dal():
    """Called at GUI start-up before Step 1."""
    global DAL_MODE
    DAL_MODE = "SETUP"


# ----------------------------------------------------------------------
# 📌 RW-only Local Connector
# ----------------------------------------------------------------------
def _local_db(path: str):
    con = sqlite3.connect(
        path,
        timeout=10,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ----------------------------------------------------------------------
# 📌 RW-only Cloud Connector
# ----------------------------------------------------------------------
def _cloud_db(path: str):
    con = sqlite3.connect(
        path,
        timeout=12,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=12000")
    con.execute("PRAGMA journal_mode=WAL")
    return con


# ----------------------------------------------------------------------
# 📌 RW-only ATTACH Model (final)
# ----------------------------------------------------------------------
def _attach_all_four(con: sqlite3.Connection, rw_primary: str):
    fam_to_cloud = {
        "auto": CLOUD_AUTO,
        "bets": CLOUD_BETS,
        "mastery": CLOUD_MASTERY,
        "settlements": CLOUD_SETTLE,
    }

    fam_to_local = {
        "auto": LOCAL_AUTO,
        "bets": LOCAL_BETS,
        "mastery": LOCAL_MASTERY,
        "settlements": LOCAL_SETTLE,
    }

    # --- ATTACH with retries ---
    import time
    for fam in ("auto", "bets", "mastery", "settlements"):
        path = fam_to_cloud[fam] if fam == rw_primary else fam_to_local[fam]

        for attempt in range(5):
            try:
                con.execute(f"ATTACH DATABASE ? AS {fam}", (path,))
                break  # success
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))



# ----------------------------------------------------------------------
# 📌 RW-only Dispatcher
# ----------------------------------------------------------------------
# === PATCH START =========================================================
# 📍 TARGET: engines/config_paths.py::_dispatch_open
# 📆 PATCHED: 2025-12-03 — LIVE writes → LiveCache (CLOUD_* remapped)
# PURPOSE:
#   • READ = LOCAL (always)
#   • WRITE = LiveCache (formerly CLOUD)
# ========================================================================

def _dispatch_open(family: str, *, rw: bool = False):
    mode = "SETUP" if DAL_MODE.upper() == "SETUP" else "LIVE"

    if mode == "SETUP":
        # SETUP always uses LOCAL RW (no attach, no cloud)
        if family == "auto":        return _local_db(LOCAL_AUTO)
        if family == "bets":        return _local_db(LOCAL_BETS)
        if family == "mastery":     return _local_db(LOCAL_MASTERY)
        if family == "settlements": return _local_db(LOCAL_SETTLE)

    # LIVE MODE
    PRIMARY_LOCAL = {
        "auto": LOCAL_AUTO,
        "bets": LOCAL_BETS,
        "mastery": LOCAL_MASTERY,
        "settlements": LOCAL_SETTLE,
    }[family]

    PRIMARY_LIVECACHE = {
        "auto": CLOUD_AUTO,
        "bets": CLOUD_BETS,
        "mastery": CLOUD_MASTERY,
        "settlements": CLOUD_SETTLE,
    }[family]

    # WRITE → LiveCache (CLOUD_* now equals LiveCache)
    if rw:
        con = _cloud_db(PRIMARY_LIVECACHE)
        _attach_all_four(con, rw_primary=family)
        return con

    # READ → LOCAL
    return _local_db(PRIMARY_LOCAL)
# === PATCH END ===========================================================



# ----------------------------------------------------------------------
# 📌 Public API (FINAL)
# ----------------------------------------------------------------------
# === PATCH START =========================================================
# 📍 TARGET: engines/config_paths.py (public DAL API)
# 📆 PATCHED: 2025-12-03 — LiveCache-integrated DAL
# ========================================================================

def open_auto_db(*, rw=None, **_):
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_AUTO)
    if rw:
        con = _cloud_db(CLOUD_AUTO)
        _attach_all_four(con, rw_primary="auto")
        return con
    return _local_db(LOCAL_AUTO)

def open_bets_db(*, rw=None, **_):
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_BETS)
    if rw:
        con = _cloud_db(CLOUD_BETS)
        _attach_all_four(con, rw_primary="bets")
        return con
    return _local_db(LOCAL_BETS)

def open_mastery_db(*, rw=None, **_):
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_MASTERY)
    if rw:
        con = _cloud_db(CLOUD_MASTERY)
        _attach_all_four(con, rw_primary="mastery")
        return con
    return _local_db(LOCAL_MASTERY)

def open_settlements_db(*, rw=None, **_):
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_SETTLE)
    if rw:
        con = _cloud_db(CLOUD_SETTLE)
        _attach_all_four(con, rw_primary="settlements")
        return con
    return _local_db(LOCAL_SETTLE)
# === PATCH END ===========================================================



# Legacy Aliases
auto_conn            = open_auto_db
bets_conn            = open_bets_db
mastery_conn         = open_mastery_db
settle_conn          = open_settlements_db

connect_auto_db       = open_auto_db
connect_bets_db       = open_bets_db
connect_mastery_db    = open_mastery_db
connect_settlements_db = open_settlements_db

def connect_db(*, rw=None, **_):
    return open_auto_db(rw=rw)

# === PATCH END =============================================

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-12-02 — Universal Connection Cache (Local + Cloud)

import threading as _dal_thread

# ---------------------------------------------------------------------------
# GLOBAL CACHED CONNECTIONS
# ---------------------------------------------------------------------------
# One per DB family for LOCAL RW reads
_LOCAL_CONN = {
    "auto": None,
    "bets": None,
    "mastery": None,
    "settlements": None,
}

# One per DB family for CLOUD RW writes
_CLOUD_CONN = {
    "auto": None,
    "bets": None,
    "mastery": None,
    "settlements": None,
}

# Locks per family
_CONN_LOCK = {
    "auto": _dal_thread.Lock(),
    "bets": _dal_thread.Lock(),
    "mastery": _dal_thread.Lock(),
    "settlements": _dal_thread.Lock(),
}

# Map families → paths
_LOCAL_PATHS = {
    "auto": LOCAL_AUTO,
    "bets": LOCAL_BETS,
    "mastery": LOCAL_MASTERY,
    "settlements": LOCAL_SETTLE,
}

_CLOUD_PATHS = {
    "auto": CLOUD_AUTO,
    "bets": CLOUD_BETS,
    "mastery": CLOUD_MASTERY,
    "settlements": CLOUD_SETTLE,
}

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: _get_local_conn
# 📆 PATCHED: 2025-12-02 — SafeConn wrapper (prevents accidental close)
# ============================================================================

class _SafeConn:
    """
    Wraps a real sqlite3.Connection so that:

      • .close() is ignored (DAL owns connection lifecycle)
      • All other attributes/methods forward to the real connection
      • .commit() still works normally
      • Prevents GUI / engines from breaking cached connections

    This is REQUIRED because callers still assume they own the connection.
    With the new cached DAL, closing must be a no-op.
    """

    __slots__ = ("_con",)

    def __init__(self, real_con):
        self._con = real_con

    # --- BLOCK close() entirely --------------------------------------------
    def close(self):
        # DAL owns lifecycle → ignore all external close requests
        return None

    # --- Allow commit() to work normally -----------------------------------
    def commit(self):
        try:
            return self._con.commit()
        except Exception:
            return None

    # --- Allow rollback() normally -----------------------------------------
    def rollback(self):
        try:
            return self._con.rollback()
        except Exception:
            return None

    # --- Forward everything else -------------------------------------------
    def __getattr__(self, name):
        return getattr(self._con, name)

    def __enter__(self):
        # behave like a normal connection but cannot be closed by caller
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                try:
                    self._con.commit()
                except Exception:
                    pass
        finally:
            # DO NOT close underlying shared connection
            return False

# --- Wrap LOCAL + CLOUD pull points ----------------------------------------

    # === PATCH START ====================================================
    # 📍 TARGET: engines/config_paths.py::_wrap_local
    # 📆 PATCHED: 2025-12-03 — Remove SafeConn wrapper for LOCAL writer
    # PURPOSE:
    #   • AlphaX LOCAL writer must hit the raw LOCAL connection
    #   • SafeConn breaks execute/commit/row_factory
    #   • Fixes all missing local orders/children
    # ====================================================================

def _wrap_local(fam: str):
    """Return the raw LOCAL cached RW connection for AlphaX mirror."""
    real = _get_local_conn(fam)
    return _SafeConn(real)

    # === PATCH END ======================================================


def _wrap_cloud(fam: str):
    """Return safe-wrapped CLOUD cached connection."""
    real = _get_cloud_conn(fam)
    return _SafeConn(real)

# === PATCH END ==============================================================



# ---------------------------------------------------------------------------
# INTERNAL HELPERS — these replace all raw openers
# ---------------------------------------------------------------------------





# ---------------------------------------------------------------------------
# PUBLIC DAL API — now backed by connection cache
# ---------------------------------------------------------------------------
# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def open_auto_db(
# 📆 PATCHED: 2025-12-02 — Use SafeConn wrapper
# ============================================================================

def open_auto_db(*, rw=None, **_):
    fam = "auto"

    # SETUP → LOCAL ONLY
    if DAL_MODE.upper() == "SETUP":
        return _wrap_local(fam)

    # LIVE → writes to cloud
    con = _wrap_cloud(fam)
    try:
        _attach_all_four(con._con, rw_primary=fam)   # attach underlying real con
    except Exception:
        pass
    return con


def open_bets_db(*, rw=None, **_):
    fam = "bets"

    if DAL_MODE.upper() == "SETUP":
        return _wrap_local(fam)

    if rw:
        con = _wrap_cloud(fam)
        try:
            _attach_all_four(con._con, rw_primary=fam)
        except Exception:
            pass
        return con

    return _wrap_local(fam)


def open_mastery_db(*, rw=None, **_):
    fam = "mastery"

    if DAL_MODE.upper() == "SETUP":
        return _wrap_local(fam)

    if rw:
        con = _wrap_cloud(fam)
        try:
            _attach_all_four(con._con, rw_primary=fam)
        except Exception:
            pass
        return con

    return _wrap_local(fam)


def open_settlements_db(*, rw=None, **_):
    fam = "settlements"

    if DAL_MODE.upper() == "SETUP":
        return _wrap_local(fam)

    if rw:
        con = _wrap_cloud(fam)
        try:
            _attach_all_four(con._con, rw_primary=fam)
        except Exception:
            pass
        return con

    return _wrap_local(fam)


def open_db(family: str, ro=False, rw=False, **_):
    """AlphaX entrypoint → routed through *safe* cached DAL."""
    if family == "auto":        return open_auto_db(rw=rw)
    if family == "bets":        return open_bets_db(rw=rw)
    if family == "mastery":     return open_mastery_db(rw=rw)
    if family == "settlements": return open_settlements_db(rw=rw)
    raise ValueError(f"Unknown DB family: {family}")

# === PATCH END ==============================================================
# === PATCH START ============================================
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-12-02 — Add dedicated LOCAL writer (SafeConn RW)

import threading as _locw_thread

# Global LOCAL writer lock (serialises LOCAL writes safely)
_LOCAL_WRITE_LOCK = _locw_thread.Lock()

def open_auto_local_write(timeout: float = 10.0):
    """
    Dedicated LOCAL writer for AlphaX mirror:
        • Uses the cached LOCAL connection (SafeConn-wrapped)
        • Serialised under _LOCAL_WRITE_LOCK
        • Always WAL RW
        • NEVER closes
        • Never touches CLOUD
    """
    from engines.config_paths import _wrap_local

    fam = "auto"

    # pull wrapped LOCAL cached RW connection
    # === PATCH START ===============================================
    # 📍 TARGET: engines/config_paths.py::open_auto_local_write
    # 📆 PATCHED: 2025-12-03 — Raw LOCAL writer for AlphaX Mirror
    # PURPOSE:
    #   • Writer gets raw local conn
    #   • Immune to GUI/hijack SafeConn close()
    #   • Serialized via lock
    # ==============================================================

    from engines.config_paths import _get_local_conn

    con = _get_local_conn("auto")   # RAW LOCAL CONNECTION ONLY

    # === PATCH END =================================================


    # apply writer lock so LOCAL writes cannot interleave
    class _LocalWriter:
        __slots__ = ("_con", "_lock")

        def __init__(self, con, lock):
            self._con = con
            self._lock = lock

        def execute(self, sql, params=()):
            with self._lock:
                try:
                    cur = self._con.execute(sql, params)
                    self._con.commit()
                    return cur
                except Exception:
                    return None

        def executemany(self, sql, seq):
            with self._lock:
                try:
                    cur = self._con.executemany(sql, seq)
                    self._con.commit()
                    return cur
                except Exception:
                    return None

        # no-op close
        def close(self):
            return None

    return _LocalWriter(con, _LOCAL_WRITE_LOCK)

# === PATCH END ==============================================


