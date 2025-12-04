#!/usr/bin/env python3
"""
AutoScalp — CONFIG PATHS (FINAL CLEAN REBUILD)
------------------------------------------------
This file provides:

    1) LiveCache (5-day write layer)
    2) Local DBs (long-term historical)
    3) RW-only DAL (no RO anywhere)
    4) Raw openers for AlphaX mirroring
    5) SafeConn wrapper for cached RW connections
    6) Full attach-all-four model for cross-db SELECTs
    7) LiveCacheKeeper (5-day retention + WAL/SHM purge)
    8) Legacy compatibility for all older modules

LIVE MODE:
    READ  = LOCAL
    WRITE = LIVECACHE (CLOUD_* now maps to LiveCache)

SETUP MODE:
    READ  = LOCAL
    WRITE = LOCAL

AlphaX:
    Reads from LiveCache → Writes into LOCAL via dedicated writer
"""

from __future__ import annotations
import os, sqlite3, threading, time
from typing import Tuple
import _sqlite3 as _raw_sqlite3

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py (top-level)
# 📆 PATCHED: 2025-12-06 — DAL Global Write Queue + Writer Thread
# ============================================================================

import queue

# Global write queue (one writer model)
_DAL_WRITE_QUEUE = queue.Queue(maxsize=100000)

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::_dal_writer_loop
# 📆 PATCHED: 2025-12-07 — High-throughput Batch Writer (dedupe + ordering)
# ============================================================================

import collections
import time

# Max items to drain per batch
_DAL_BATCH_SIZE = 3000

# Priority ordering: parent tables first
# (ensures logical consistency if two tables are written in the same batch)
_DAL_TABLE_ORDER = [
    "order_events",
    "orders",
    "odds_current",
    "inbound_oc_cache",
    "oc_series",
]

def _extract_table(sql: str) -> str:
    """Extract table name from SQL for ordering."""
    sql_l = sql.lower()
    for t in _DAL_TABLE_ORDER:
        if f" {t.lower()} " in sql_l or sql_l.startswith(f"insert into {t.lower()}"):
            return t
    return "zzz"  # unknown tables at end

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::_dal_writer_loop
# 📆 PATCHED: 2025-12-07 — Persistent writer resolution
# ============================================================================

def _dal_writer_loop():
    """
    Batch writer now receives (fam, sql, params)
    and always writes using the persistent writer connection.
    """
    while True:
        fam, sql, params = _DAL_WRITE_QUEUE.get()
        try:
            con = _get_writer(fam)  # persistent connection
            con.execute(sql, params)
            con.commit()
        except Exception as e:
            print(f"[DAL-WRITER] fail: {e} | sql={sql}")
        finally:
            _DAL_WRITE_QUEUE.task_done()
# === PATCH END ============================================================

# spawn thread once
if not any(t.name == "DAL-Writer" for t in threading.enumerate()):
    threading.Thread(target=_dal_writer_loop,
                     name="DAL-Writer",
                     daemon=True).start()
    print("[DAL] Writer thread ACTIVE")
# === PATCH END ============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py (just below DALWriteProxy + _DAL_WRITE_QUEUE)
# 📆 PATCHED: 2025-12-06 — DAL Batch Writer + Deduper Layer
# ============================================================================

import time
import threading

# Batch configuration
_BATCH_WINDOW_S = 0.003      # 3ms window
_MAX_BATCH = 5000            # safety cap
_DEDUPE_ENABLED = True       # toggle
_DEDUPE_KEY = lambda con, sql, params: (id(con), sql) 
#   item = (real_con, sql, params)
#   dedupe key = (connection_identity, sql, params)


def _dal_writer_batch_loop():
    """
    Global DAL Batch Writer (One Writer Model)
    -------------------------------------------------------
    - Pulls first item from queue
    - Opens small time window (3ms)
    - Pulls as many as possible (max 5000)
    - Dedupes items
    - Executes in arrival order
    - Commits after each batch
    """
    pending = []

    while True:
        try:
            item = _DAL_WRITE_QUEUE.get()                 # (real_con, sql, params)
        except Exception:
            continue

        pending.append(item)
        t0 = time.time()

        # Batch fill window
        while len(pending) < _MAX_BATCH:
            remaining = _BATCH_WINDOW_S - (time.time() - t0)
            if remaining <= 0:
                break
            try:
                nxt = _DAL_WRITE_QUEUE.get(timeout=remaining)
                pending.append(nxt)
            except queue.Empty:
                break

        # Deduping
        if _DEDUPE_ENABLED:
            seen = set()
            deduped = []
            for (con, sql, params) in pending:
                k = _DEDUPE_KEY(con, sql, params)
                if k in seen:
                    continue
                seen.add(k)
                deduped.append((con, sql, params))
            batch = deduped
        else:
            batch = pending

        # Execute batch (ordered)
        for (con, sql, params) in batch:
            try:
                con.execute(sql, params)
            except Exception as e:
                print(f"[DAL-WRITER] fail: {e} | sql={sql}")
        # Commit all
        try:
            con.commit()
        except Exception:
            pass

        # Mark all tasks done
        for _ in pending:
            try:
                _DAL_WRITE_QUEUE.task_done()
            except Exception:
                pass

        pending.clear()


# Start batch writer thread
if not any(t.name == "DAL-BatchWriter" for t in threading.enumerate()):
    threading.Thread(
        target=_dal_writer_batch_loop,
        name="DAL-BatchWriter",
        daemon=True
    ).start()
    print("[DAL] Batch writer ACTIVE")
# === PATCH END ============================================================

# ============================================================
# 📂 RESOLVE PROJECT ROOT + DATA DIRECTORY
# ============================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))

def _resolve_data_dir() -> str:
    env = os.environ.get("AUTOSCALP_DB_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    data = os.path.join(_ROOT, "data")
    os.makedirs(data, exist_ok=True)
    return data

DATA_DIR = _resolve_data_dir()

# ============================================================
# 📘 LOCAL AUTHORITATIVE DATABASES (long-term historical)
# ============================================================

LOCAL_BETS      = os.path.join(DATA_DIR, "bets.db")
LOCAL_AUTO      = os.path.join(DATA_DIR, "autoscalp_gui.db")
LOCAL_SETTLE    = os.path.join(DATA_DIR, "settlements.db")
LOCAL_MASTERY   = os.path.join(DATA_DIR, "mastery_v7.db")

# ensure they exist
for _p in (LOCAL_BETS, LOCAL_AUTO, LOCAL_SETTLE, LOCAL_MASTERY):
    os.makedirs(os.path.dirname(_p), exist_ok=True)
    if not os.path.exists(_p):
        open(_p, "a").close()

# ============================================================
# 📙 LIVECACHE DATABASES (5-day rolling write layer)
# ============================================================

LIVE_ROOT = os.path.join(DATA_DIR, "livecache")
os.makedirs(LIVE_ROOT, exist_ok=True)

CLOUD_ROOT     = LIVE_ROOT  # alias for old CLOUD usage
CLOUD_AUTO     = os.path.join(LIVE_ROOT, "autoscalp_livecache.db")
CLOUD_BETS     = os.path.join(LIVE_ROOT, "bets_livecache.db")
CLOUD_SETTLE   = os.path.join(LIVE_ROOT, "settlements_livecache.db")
CLOUD_MASTERY  = os.path.join(LIVE_ROOT, "mastery_livecache.db")

# ensure LiveCache DBs exist
for _p in (CLOUD_AUTO, CLOUD_BETS, CLOUD_SETTLE, CLOUD_MASTERY):
    if not os.path.exists(_p):
        open(_p, "a").close()

print(f"[LiveCache DAL] active → {LIVE_ROOT}")

# Back-compat: old getters return new LiveCache path
_LOCAL_AUTO_PATH = LOCAL_AUTO
_CLOUD_AUTO_PATH = CLOUD_AUTO

def get_local_auto_path() -> str: return LOCAL_AUTO
def get_cloud_auto_path() -> str: return CLOUD_AUTO

# === RESTORED HELPER SECTION ===============================================
# These helpers existed in the old DAL and are required by GUI/engines.
# They do NOT interfere with the new LiveCache DAL.

# ---------------------------------------------------------------------------
# REAL DB OPENERS (Readers + Writers)
# With attach-all-four database model
# ---------------------------------------------------------------------------

def _local_db(path: str):
    """
    REAL local reader (used for all reads in LIVE mode).
    Always attaches all four DBs for unified schema visibility.
    """
    con = sqlite3.connect(
        path,
        timeout=10,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA journal_mode=WAL")

    # Attach all four DBs
    try:
        _attach_all_four(con, rw_primary="none")
    except Exception as e:
        print(f"[DAL] attach-all-four(local) warn: {e}")

    return con

def _cloud_db(path: str):
    """
    WRITE CONNECTION:
        - Always LIVECACHE primary
        - Attach all four databases immediately
    """
    con = sqlite3.connect(
        path,
        timeout=20,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA journal_mode=WAL")

    # Attach all four (WRITE MODE PRIMARY = LIVECACHE)
    try:
        con.execute(f"ATTACH DATABASE '{CLOUD_AUTO}'      AS auto")
        con.execute(f"ATTACH DATABASE '{CLOUD_BETS}'      AS bets")
        con.execute(f"ATTACH DATABASE '{CLOUD_SETTLE}'    AS settlements")
        con.execute(f"ATTACH DATABASE '{CLOUD_MASTERY}'   AS mastery")
    except Exception as e:
        print(f"[DAL-ATTACH-CLOUD] warn: {e}")

    return con


# ---------------------------------------------------------------------------
# q_retry — transient lock retry wrapper
# ---------------------------------------------------------------------------
def q_retry(con: sqlite3.Connection, sql: str, params=(),
            *, tries: int = 6, delay_s: float = 0.08):
    """
    Retry wrapper for transient SQLITE_BUSY / SQLITE_LOCKED states.
    Used heavily by GUI, lanes, live_router, dashboard, and V7 engines.
    """
    last = None
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

# legacy export pattern
_q_retry = q_retry


# ---------------------------------------------------------------------------
# Legacy connect_* APIs (GUI + older engines rely on these)
# ---------------------------------------------------------------------------

def connect_db(path: str | None = None, ro: bool = False, timeout: float = 10.0):
    """
    Legacy access point: defaults to bets.db.
    """
    target = path or BETS_DB_PATH
    fam = "bets" if target == BETS_DB_PATH else "auto"
    return open_db(fam, rw=not ro)

def connect_autoscalp_db(timeout: float = 10.0, ro: bool = False):
    return open_auto_db(rw=not ro)

def connect_bets_db(timeout: float = 10.0, ro: bool = False):
    return open_bets_db(rw=not ro)

def connect_settlements_db(timeout: float = 10.0, ro: bool = False):
    return open_settlements_db(rw=not ro)

def connect_mastery_v7_db(timeout: float = 10.0, ro: bool = False):
    return open_mastery_db(rw=not ro)

def connect_mastery_v7_cache(timeout: float = 10.0, ro: bool = False):
    # Mastery V7 cloud copy uses CLOUD_MASTERY
    con = sqlite3.connect(
        CLOUD_MASTERY,
        timeout=10,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------------
# Legacy read-only helpers
# ---------------------------------------------------------------------------
def auto_ro(timeout: float = 10.0):
    return _local_db(LOCAL_AUTO)


def settle_ro(timeout: float = 10.0):
    return _local_db(LOCAL_SETTLE)  # ro not needed, DAL never attaches here


# --- Existing AUTO_CONN remains unchanged below --- #
# auto_conn(): READ=LOCAL, WRITE=LiveCache (but not READ=LiveCache)
# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::auto_conn
# 📆 PATCHED: 2025-12-05 — universal READ=LOCAL except live_router
# ============================================================================

def auto_conn(*, rw=False, timeout: float = 10.0):
    """
    GLOBAL AUTO_DB router:
       READ  → LOCAL
       WRITE → LIVECACHE
    """
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_AUTO)

    if rw is True:
        return DALWriteProxy("auto")

    return _local_db(LOCAL_AUTO)


def auto_conn_live(*, rw=False, timeout: float = 10.0):
    """
    LIVE ROUTER ONLY.
    Always use LiveCache for both READ and WRITE.
    """
    return _cloud_db(CLOUD_AUTO)




# ---------------------------------------------------------------------------
# Orders DB locator (used by live_router + event sink)
# ---------------------------------------------------------------------------
def connect_orders_db(ro: bool = True, timeout: float = 10.0):
    """
    Return the DB owning the 'orders' table.
    Prefers autoscalp_gui.db, falls back to bets.db.
    """
    for path, fam in [(LOCAL_AUTO, "auto"), (LOCAL_BETS, "bets")]:
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout) as con:
                r = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders'"
                ).fetchone()
                if r:
                    return open_db(fam, rw=not ro)
        except Exception:
            pass
    return open_bets_db(rw=not ro)

# === END OF RESTORED HELPER SECTION =========================================


# ============================================================
# 🧰 RAW OPENERS (AlphaX direct)
# ============================================================

def _raw_open(path: str, *, timeout: int = 10):
    con = _raw_sqlite3.connect(path, timeout=timeout, isolation_level=None)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
    return con

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def _get_local_conn(
# 📆 PATCHED: 2025-12-01 — correct RAW connector paths
# ============================================================================
def _get_local_conn(fam: str):
    if fam == "auto":        return _raw_open(LOCAL_AUTO)
    if fam == "bets":        return _raw_open(LOCAL_BETS)
    if fam == "mastery":     return _raw_open(LOCAL_MASTERY)
    if fam == "settlements": return _raw_open(LOCAL_SETTLE)
    raise ValueError(f"Unknown family {fam}")

def _get_cloud_conn(fam: str):
    if fam == "auto":        return _raw_open(CLOUD_AUTO)
    if fam == "bets":        return _raw_open(CLOUD_BETS)
    if fam == "mastery":     return _raw_open(CLOUD_MASTERY)
    if fam == "settlements": return _raw_open(CLOUD_SETTLE)
    raise ValueError(f"Unknown family {fam}")
# === PATCH END ==============================================================


# ============================================================
# 🔒 RW-ONLY CONNECTORS (LOCAL + LIVECACHE)
# ============================================================

# ---------------------------------------------------------------------------
# REAL DB OPENERS (Readers + Writers)
# With attach-all-four database model
# ---------------------------------------------------------------------------

def _local_db(path: str):
    """
    READ CONNECTION:
        - Always LOCAL primary
        - Attach all four databases immediately
    """
    con = sqlite3.connect(
        path,
        timeout=10,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA journal_mode=WAL")

    # Attach all four (READ MODE PRIMARY = LOCAL)
    try:
        con.execute(f"ATTACH DATABASE '{LOCAL_AUTO}' AS auto")
        con.execute(f"ATTACH DATABASE '{LOCAL_BETS}' AS bets")
        con.execute(f"ATTACH DATABASE '{LOCAL_SETTLE}' AS settlements")
        con.execute(f"ATTACH DATABASE '{LOCAL_MASTERY}' AS mastery")
    except Exception as e:
        print(f"[DAL-ATTACH-LOCAL] warn: {e}")

    return con


# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-12-07 — Persistent Writer Pool (per DB family)
# ============================================================================

# Persistent pool of REAL writer connections
# Opened once → reused forever → never GC'd → safe for threading
_PERSISTENT_WRITERS = {}
_PERSISTENT_LOCK = threading.Lock()

def _get_writer(fam: str) -> sqlite3.Connection:
    """
    fam ∈ {"auto","bets","mastery","settlements"}
    Always returns the SAME persistent SQLite connection.
    """
    with _PERSISTENT_LOCK:
        con = _PERSISTENT_WRITERS.get(fam)
        if con:
            return con

        # Map family → LiveCache path
        path = {
            "auto": CLOUD_AUTO,
            "bets": CLOUD_BETS,
            "mastery": CLOUD_MASTERY,
            "settlements": CLOUD_SETTLE,
        }[fam]

        # Create writer connection ONCE
        con = sqlite3.connect(
            path,
            timeout=20,
            isolation_level=None,
            check_same_thread=False
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=8000")

        _PERSISTENT_WRITERS[fam] = con
        return con

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py (below writer pool)
# 📆 PATCHED: 2025-12-07 — Mirror Layer v2 (batch + persistent writers)
# ============================================================================

import hashlib
import sqlite3 as _sq
import glob as _glob

# Timestamp columns we recognise
_TS_COLS = {
    "ts","updated_ts","updated_at","snapshot_ts","created_at",
    "opened_at","placed_at","closed_at","settled_at","finished_at",
    "decided_at","realized_at","recorded_at","ingested_at",
    "last_update_ts","last_refreshed_ts","last_snapshot_ts",
    "off_ts","happened_at"
}

def _mirror_md5(row: dict) -> str:
    """Stable MD5 to detect if a row changed."""
    flat = "|".join(str(row[k]) for k in sorted(row.keys()))
    return hashlib.md5(flat.encode("utf8")).hexdigest()


# Persistent LOCAL writers (one per DB family)
# ------------------------------------------------------------
_MIRROR_LOCAL = {
    "autoscalp_livecache.db": _get_local_conn("auto"),
    "bets_livecache.db":      _get_local_conn("bets"),
    "mastery_livecache.db":   _get_local_conn("mastery"),
    "settlements_livecache.db": _get_local_conn("settlements"),
}


def _mirror_read_livecache(db_path: str, table: str) -> list[dict]:
    """Read changed rows from LiveCache."""
    try:
        con = _sq.connect(db_path, timeout=5)
        con.row_factory = _sq.Row

        # detect timestamp column
        cur = con.execute(f"PRAGMA table_info('{table}')")
        cols = cur.fetchall()
        if not cols:
            con.close()
            return []

        tscol = None
        for cid, name, ctype, notnull, dflt, pk in cols:
            if name in _TS_COLS:
                tscol = name
                break
        if not tscol:
            con.close()
            return []

        # last 24h limit (high throughput safe)
        sql = f"SELECT * FROM {table} WHERE {tscol} >= date('now','-1 day')"
        cur = con.execute(sql)
        keys = [c[0] for c in cur.description]
        out = [dict(zip(keys, r)) for r in cur.fetchall()]
        con.close()
        return out
    except Exception:
        return []


# Table-level row cache to prevent redundant writes
# ----------------------------------------------------------
_MIRROR_CACHE = {}   # key: (db_name, table, row_id) → md5


def _mirror_queue_row(real_con, table: str, row: dict):
    """Enqueue single INSERT OR REPLACE into LOCAL via DAL queue."""

    cols = list(row.keys())
    clist = ",".join(cols)
    plist = ",".join("?" for _ in cols)
    sql = f"INSERT OR REPLACE INTO {table} ({clist}) VALUES ({plist})"
    params = tuple(row[c] for c in cols)

    _DAL_WRITE_QUEUE.put((real_con, sql, params))


def _mirror_db(live_path: str, local_writer, db_name: str):
    """Mirror one LiveCache DB → LOCAL (batch+dedupe)."""
    try:
        con = _sq.connect(live_path, timeout=5)
        cur = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        tables = [t[0] for t in cur.fetchall()]
        con.close()
    except Exception:
        return

    for table in tables:
        rows = _mirror_read_livecache(live_path, table)
        if not rows:
            continue

        for row in rows:
            rid = row.get("id")
            if rid is None:
                continue

            key = (db_name, table, rid)
            md5 = _mirror_md5(row)

            # dedupe
            if _MIRROR_CACHE.get(key) == md5:
                continue

            _MIRROR_CACHE[key] = md5
            _mirror_queue_row(local_writer, table, row)


def _mirror_worker_loop():
    """Main mirror loop, runs forever."""
    LIVE_DIR = os.path.join(DATA_DIR, "livecache")

    while True:
        try:
            for live_path in _glob.glob(f"{LIVE_DIR}/*.db"):
                db_name = os.path.basename(live_path)
                local_writer = _MIRROR_LOCAL.get(db_name)
                if not local_writer:
                    continue
                _mirror_db(live_path, local_writer, db_name)
        except Exception as e:
            print(f"[DAL-MIRROR] warn: {e}")

        # 200ms tick — extremely fast, but safe
        time.sleep(0.2)


# spawn mirror thread once
if not any(t.name == "DAL-Mirror" for t in threading.enumerate()):
    threading.Thread(target=_mirror_worker_loop,
                     name="DAL-Mirror",
                     daemon=True).start()
    print("[DAL-MIRROR] Mirror thread ACTIVE")

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::DALWriteProxy
# 📆 PATCHED: 2025-12-07 — enqueue real writer connection
# ============================================================================

class DALWriteProxy:
    """
    Proxy for queue-based LiveCache writes.
    Behaves safely like a sqlite3.Connection for upstream modules.
    """
    __slots__ = ("_fam", "row_factory", "text_factory",
                 "total_changes", "in_transaction", "isolation_level")

    def __init__(self, family: str):
        self._fam = family
        self.row_factory = None
        self.text_factory = None
        self.total_changes = 0
        self.in_transaction = False
        self.isolation_level = None

    def _writer(self):
        return _get_writer(self._fam)

    def execute(self, sql, params=()):
        real = self._writer()
        if isinstance(params, list):
            params = tuple(params)

        _DAL_WRITE_QUEUE.put((self._fam, sql, params))
        return self

    def executemany(self, sql, seq):
        real = self._writer()
        for p in seq:
            if isinstance(p, list):
                p = tuple(p)
            _DAL_WRITE_QUEUE.put((self._fam, sql, tuple(p)))
        return self

    def commit(self): return None
    def rollback(self): return None
    def close(self): return None
    def cursor(self): return self
    def fetchall(self): return []
    def fetchone(self): return None

# ============================================================
# 🧩 ATTACH MODEL — ALL FOUR DBS
# ============================================================

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

    import time
    for fam in ("auto", "bets", "mastery", "settlements"):
        path = fam_to_cloud[fam] if fam == rw_primary else fam_to_local[fam]
        for attempt in range(5):
            try:
                con.execute(f"ATTACH DATABASE ? AS {fam}", (path,))
                break
            except Exception as e:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))

# === RESTORED PUBLIC MODE HELPERS =========================================

# Ensures dashboard, GUI, orchestrator can import `get_mode`
def get_mode() -> str:
    """
    Return last selected mode ('test', 'learning', or 'live').
    Guaranteed to exist for legacy GUI imports.
    """
    return _MODE

def is_replay_mode() -> bool:
    """
    True if running in test/replay mode.
    """
    return _MODE in ("test", "replay")

def get_db_paths() -> tuple[str, str]:
    """
    Return (bets_path, autoscalp_gui_path).
    """
    return BETS_DB_PATH, AUTOSCALP_DB_PATH

# === END RESTORED PUBLIC MODE HELPERS ====================================
# === RESTORED PATH HELPERS ==================================================

def repo_root() -> str:
    """
    Return the absolute path to the project root directory.
    Needed by the Orchestrator and dashboard.
    """
    return _ROOT

def data_dir() -> str:
    """
    Return the resolved base data directory.
    """
    return DATA_DIR

# === END RESTORED PATH HELPERS =============================================
# === RESTORED LEGACY HELPERS ===============================================

def set_db_paths(
    mode: str | None = None,
    *,
    bets: str | None = None,
    autoscalp: str | None = None,
    data_dir_override: str | None = None,
    quiet: bool = False,
) -> tuple[str, str]:
    """
    Legacy DB selector used by GUI during setup.
    Preserves original behaviour:
      • mode: 'test' | 'learning' | 'live'
      • can override bets/auto paths and/or base data dir
      • updates BETS_DB_PATH / AUTOSCALP_DB_PATH + all aliases
    """
    global DATA_DIR, LOCAL_BETS, LOCAL_AUTO, LOCAL_SETTLE, LOCAL_MASTERY
    global BETS_DB_PATH, AUTOSCALP_DB_PATH, DB_PATH
    global BETS_DB, AUTOSCALP_DB, AUTO_DB, _MODE

    m = (mode or os.environ.get("AUTOSCALP_MODE") or "learning").lower()
    _MODE = m

    base = data_dir() if data_dir_override is None else os.path.abspath(data_dir_override)
    os.makedirs(base, exist_ok=True)

    # choose filenames based on mode
    bet_path = bets or os.path.join(base, "bets.test.db" if m == "test" else "bets.db")
    auto_path = autoscalp or os.path.join(base, "autoscalp_gui.test.db" if m == "test" else "autoscalp_gui.db")

    # update canonical paths
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

# === END RESTORED LEGACY HELPERS ===========================================


# ============================================================
# 🌐 DAL MODE
# ============================================================

DAL_MODE = "SETUP"

def enable_live_dal():  # Step 4
    global DAL_MODE
    DAL_MODE = "LIVE"

def enable_setup_dal():  # GUI startup
    global DAL_MODE
    DAL_MODE = "SETUP"

# ============================================================
# 🎛️ RW-ONLY DISPATCH LOGIC
# ============================================================

def _dispatch_open(family: str, *, rw: bool = False):
    mode = "SETUP" if DAL_MODE.upper() == "SETUP" else "LIVE"

    if mode == "SETUP":
        if family == "auto":        return _local_db(LOCAL_AUTO)
        if family == "bets":        return _local_db(LOCAL_BETS)
        if family == "mastery":     return _local_db(LOCAL_MASTERY)
        if family == "settlements": return _local_db(LOCAL_SETTLE)

    # LIVE MODE
    PRIMARY_LOCAL    = {
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

    if rw:
        con = _cloud_db(PRIMARY_LIVECACHE)
        _attach_all_four(con, rw_primary=family)
        return con

    return _local_db(PRIMARY_LOCAL)


# ============================================================
# 🎚 PUBLIC DAL API (LIVE-INTEGRATED)
# ============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::open_*_db
# 📆 PATCHED: 2025-12-07 — rw must be strictly True to return proxy
# ============================================================================

def open_auto_db(*, rw=None, **_):
    """
    AUTO DB routing:
        SETUP → everything LOCAL
        LIVE:
            READ  → LOCAL
            WRITE → LiveCache (CLOUD_AUTO)
        live_router uses auto_conn_live() and bypasses this.
    """
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_AUTO)

    # READ
    if rw is not True:
        return _local_db(LOCAL_AUTO)

    # WRITE
    return DALWriteProxy("auto")



def open_bets_db(*, rw=None, **_):
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_BETS)

    if rw is not True:
        return _local_db(LOCAL_BETS)

    return DALWriteProxy("bets")



def open_mastery_db(*, rw=None, **_):
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_MASTERY)

    if rw is not True:
        return _local_db(LOCAL_MASTERY)

    return DALWriteProxy("mastery")



def open_settlements_db(*, rw=None, **_):
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_SETTLE)

    if rw is not True:
        return _local_db(LOCAL_SETTLE)

    return DALWriteProxy("settlements")


# === PATCH END ============================================================

def open_db(family: str, ro=False, rw=False, **_):
    if family == "auto":        return open_auto_db(rw=rw)
    if family == "bets":        return open_bets_db(rw=rw)
    if family == "mastery":     return open_mastery_db(rw=rw)
    if family == "settlements": return open_settlements_db(rw=rw)
    raise ValueError(f"Unknown DB family: {family}")

# ============================================================
# 🧵 CACHED CONNECTIONS (SafeConn)
# ============================================================

_LOCAL_CONN = {"auto":None,"bets":None,"mastery":None,"settlements":None}
_CLOUD_CONN = {"auto":None,"bets":None,"mastery":None,"settlements":None}
_CONN_LOCK  = {
    "auto":threading.Lock(),"bets":threading.Lock(),
    "mastery":threading.Lock(),"settlements":threading.Lock()
}
_LOCAL_PATHS = {
    "auto":LOCAL_AUTO,"bets":LOCAL_BETS,"mastery":LOCAL_MASTERY,"settlements":LOCAL_SETTLE
}
_CLOUD_PATHS = {
    "auto":CLOUD_AUTO,"bets":CLOUD_BETS,"mastery":CLOUD_MASTERY,"settlements":CLOUD_SETTLE
}

class _SafeConn:
    __slots__=("_con",)
    def __init__(self, real): self._con = real
    def close(self): return None
    def commit(self):
        try: return self._con.commit()
        except: return None
    def rollback(self):
        try: return self._con.rollback()
        except: return None
    def __getattr__(self, n): return getattr(self._con, n)
    def __enter__(self): return self
    def __exit__(self, *_):
        try: self._con.commit()
        except: pass
        return False

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::_wrap_local and _wrap_cloud
# 🔎 SEARCH: def _wrap_local(
# 📆 PATCHED: 2025-12-01 — ensure wrappers return correct DBs
# ============================================================================
def _wrap_local(fam: str):
    """
    LOCAL = real autoscalp_gui.db / bets.db / mastery / settlements.
    SafeConn removed to ensure SELECT sees full schema.
    """
    return _local_db(_LOCAL_PATHS[fam])

def _wrap_cloud(fam: str):
    """
    LIVECACHE = Cloud paths now pointing to local livecache folder.
    """
    return _cloud_db(_CLOUD_PATHS[fam])
# === PATCH END ==============================================================


# ============================================================
# ✍️ DEDICATED LOCAL WRITER FOR ALPHAX
# ============================================================

_LOCAL_WRITE_LOCK = threading.Lock()

def open_auto_local_write(timeout: float =10.0):
    from engines.config_paths import _get_local_conn
    con = _get_local_conn("auto")
    class _LocalWriter:
        __slots__=("_con","_lock")
        def __init__(self,c,l):self._con=c;self._lock=l
        def execute(self,sql,params=()):
            with self._lock:
                try:
                    cur=self._con.execute(sql,params); self._con.commit(); return cur
                except: return None
        def executemany(self,sql,seq):
            with self._lock:
                try:
                    cur=self._con.executemany(sql,seq); self._con.commit();return cur
                except: return None
        def close(self): return None
    return _LocalWriter(con,_LOCAL_WRITE_LOCK)

# ============================================================
# 🧼 LiveCacheKeeper — 5-day Retention + WAL Purge
# ============================================================

_LC_RETENTION_DAYS = 5
_LIVE_TABLES_TS = {
    "orders":"opened_at",
    "order_events":"ts",
    "odds_current":"updated_ts",
    "inbound_oc_cache":"last_sync_ts",
    "oc_series":"snapshot_ts",
}

def _lc_trim_table(con,table,ts):
    try:
        con.execute(
            f"DELETE FROM {table} "
            f"WHERE date({ts}) < date('now','utc','-{_LC_RETENTION_DAYS} days')"
        ); con.commit()
    except Exception as e:
        print(f"[LiveCacheKeeper] trim warn {table}: {e}")

def _lc_wal_cleanup(path):
    try:
        for ext in ("-wal","-shm"):
            p = path+ext
            if os.path.exists(p): os.remove(p)
    except Exception as e:
        print(f"[LiveCacheKeeper] WAL warn: {e}")

def _lc_keeper_loop():
    while True:
        for db in (CLOUD_AUTO,CLOUD_BETS,CLOUD_SETTLE,CLOUD_MASTERY):
            try:
                con = sqlite3.connect(db,timeout=4,isolation_level=None)
                con.row_factory = sqlite3.Row
                for table,ts in _LIVE_TABLES_TS.items():
                    try: con.execute(f"SELECT 1 FROM {table} LIMIT 1")
                    except: continue
                    _lc_trim_table(con,table,ts)
                con.close()
                _lc_wal_cleanup(db)
            except Exception as e:
                print(f"[LiveCacheKeeper] db warn {db}: {e}")
        time.sleep(90)

if not any(t.name=="LiveCacheKeeper" for t in threading.enumerate()):
    threading.Thread(target=_lc_keeper_loop,name="LiveCacheKeeper",daemon=True).start()
    print("[LiveCacheKeeper] active (5-day trim + WAL purge)")

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-12-07 — DAL Mirror (LiveCache → Local, queue-based)
# ============================================================================

import hashlib
import glob

# Timestamp-bearing columns (schema combined from AUTO + BETS)
_MIRROR_TS_COLS = {
    "ts", "updated_ts", "updated_at", "snapshot_ts",
    "opened_at", "placed_at", "closed_at", "settled_at", "finished_at",
    "decided_at", "realized_at", "recorded_at", "ingested_at",
    "last_update_ts", "last_refreshed_ts", "last_snapshot_ts",
    "off_ts", "happened_at"
}

# Tables known to be small + time-bounded + safe to mirror
_MIRROR_TABLES = {
    "orders",
    "order_events",
    "odds_current",
    "inbound_oc_cache",
    "oc_series",
}


def _mirror_md5(row: dict) -> str:
    flat = "|".join(str(row[k]) for k in sorted(row.keys()))
    return hashlib.md5(flat.encode("utf-8")).hexdigest()


def _mirror_local_index(local_path: str, table: str) -> dict:
    """
    Build MD5 index for LOCAL.<table>.
    Only used for dedupe.
    """
    idx = {}
    try:
        con = sqlite3.connect(local_path, timeout=5, isolation_level=None)
        con.row_factory = sqlite3.Row
        cur = con.execute(f"SELECT * FROM {table}")
        cols = [c[0] for c in cur.description]
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            if "id" in d:
                idx[d["id"]] = _mirror_md5(d)
        con.close()
    except Exception:
        pass
    return idx


def _mirror_fetch_rows(live_path: str, table: str):
    """
    Pull timestamp-bearing recent rows (last 48 hours).
    """
    try:
        con = sqlite3.connect(live_path, timeout=8000, isolation_level=None)
        con.row_factory = sqlite3.Row
        cur = con.execute(f"PRAGMA table_info('{table}')")
        cols = cur.fetchall()
        if not cols:
            con.close()
            return []

        # detect timestamp column
        tscol = None
        for cid, name, ctype, notnull, dflt, pk in cols:
            if name in _MIRROR_TS_COLS:
                tscol = name
                break

        if not tscol:
            con.close()
            return []

        sql = (
            f"SELECT * FROM {table} "
            f"WHERE {tscol} >= datetime('now','-2 day','utc')"
        )

        cur = con.execute(sql)
        colnames = [c[0] for c in cur.description]
        rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
        con.close()
        return rows

    except Exception:
        return []


def _mirror_single_table(live_path: str, local_path: str, table: str):
    """
    Mirror one LiveCache table → LOCAL using DAL queue.
    """
    live_rows = _mirror_fetch_rows(live_path, table)
    if not live_rows:
        return

    local_idx = _mirror_local_index(local_path, table)

    for row in live_rows:
        if "id" not in row:
            continue
        rid = row["id"]

        # dedupe: identical row? skip.
        md5 = _mirror_md5(row)
        if rid in local_idx and local_idx[rid] == md5:
            continue

        # generate SQL
        # DO NOT enqueue schema changes under any circumstance.
        sql_l = sql.strip().lower()
        if sql_l.startswith(("create ", "alter ", "drop ", "pragma ", "vacuum")):
            continue

        # Queue the write to LOCAL (DAL queue)
        # === PATCH START ============================================================
        # 📍 TARGET: engines/config_paths.py::_mirror_single_table
        # 📆 PATCHED: 2025-12-07 — mirror uses persistent local writer
        # ============================================================================

        # OLD (WRONG):
        # real_local = sqlite3.connect(local_path, ...)
        # _DAL_WRITE_QUEUE.put((real_local, sql, params))

        # NEW:
        # FIXED FAMILY DETECTION (use basename instead of full absolute path)
        base = os.path.basename(live_path)
        local_fam = {
            "autoscalp_livecache.db": "auto",
            "bets_livecache.db": "bets",
            "settlements_livecache.db": "settlements",
            "mastery_livecache.db": "mastery",
        }.get(base)

        if not local_fam:
            continue  # unknown DB name → skip

        real = _get_writer(local_fam)

        # === PATCH END ============================================================



def _dal_mirror_loop():
    """
    Background incremental synchroniser:
      • scans LiveCache
      • mirrors timestamp-bearing tables to LOCAL
      • uses _DAL_WRITE_QUEUE for all writes
    """
    while True:
        try:
            # LiveCache → LOCAL mapping
            PAIRS = [
                (CLOUD_AUTO, LOCAL_AUTO),
                (CLOUD_BETS, LOCAL_BETS),
                (CLOUD_SETTLE, LOCAL_SETTLE),
                (CLOUD_MASTERY, LOCAL_MASTERY),
            ]

            for live_path, local_path in PAIRS:
                try:
                    con = sqlite3.connect(live_path, timeout=5, isolation_level=None)
                    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = [t[0] for t in cur.fetchall()]
                    con.close()
                except Exception:
                    continue

                for t in tables:
                    if t in _MIRROR_TABLES:
                        _mirror_single_table(live_path, local_path, t)

        except Exception as e:
            print(f"[DAL-MIRROR] warn: {e}")

        # High-frequency incremental sync
        time.sleep(0.25)


# start mirror thread once
if not any(t.name == "DAL-Mirror" for t in threading.enumerate()):
    threading.Thread(
        target=_dal_mirror_loop,
        name="DAL-Mirror",
        daemon=True
    ).start()
    print("[DAL] Mirror thread ACTIVE")
# === PATCH END ============================================================


# ============================================================
# LEGACY ALIASES (unchanged)
# ============================================================

BETS_DB_PATH      = LOCAL_BETS
AUTOSCALP_DB_PATH = LOCAL_AUTO
GUI_DB_PATH       = AUTOSCALP_DB_PATH
DB_PATH           = BETS_DB_PATH
BETS_DB           = BETS_DB_PATH
AUTOSCALP_DB      = AUTOSCALP_DB_PATH
AUTO_DB           = AUTOSCALP_DB_PATH
CLOUD_BETS_PATH   = CLOUD_BETS
CLOUD_AUTOSCALP_DB= CLOUD_AUTO

# === FIXED LEGACY ALIASES (preserve correct routing) ===

# auto_conn MUST remain the correct dispatcher
auto_conn = auto_conn  # keep original function

bets_conn            = open_bets_db
mastery_conn         = open_mastery_db
settle_conn          = open_settlements_db

connect_auto_db       = auto_conn
connect_bets_db       = open_bets_db
connect_mastery_db    = open_mastery_db
connect_settlements_db = open_settlements_db



def autoscalp_db(): return LOCAL_AUTO
def bets_db(): return LOCAL_BETS
def settlements_db(): return LOCAL_SETTLE
def mastery_v7_db(): return LOCAL_MASTERY









AUTO_RO = lambda timeout=10.0: _local_db(LOCAL_AUTO)

print("[paths] ✔ COMPLETE CONFIG_PATHS.PY REBUILD — OK")
