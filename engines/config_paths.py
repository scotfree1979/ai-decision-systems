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
    return _open_auto_local(timeout=timeout, ro=True)

def settle_ro(timeout: float = 10.0):
    return _local_db(LOCAL_SETTLE)  # ro not needed, DAL never attaches here

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py  (final auto_conn definition)
# 🔎 SEARCH: def auto_conn(
# 📆 PATCHED: 2025-12-01 — enforce READ=LOCAL / WRITE=LiveCache
# ============================================================================
def auto_conn(*, rw=False, timeout: float = 10.0):
    """
    Correct DAL router:
       SETUP: always LOCAL
       LIVE:  READ → LOCAL
              WRITE → LiveCache
    """
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_AUTO)

    if rw:
        # WRITE → LiveCache writer DB
        return _cloud_db(CLOUD_AUTO)

    # READ → Local autoscalp_gui.db only
    return _local_db(LOCAL_AUTO)
# === PATCH END ==============================================================


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

def _local_db(path: str):
    con = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA journal_mode=WAL")
    return con

def _cloud_db(path: str):
    con = sqlite3.connect(path, timeout=12, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=12000")
    con.execute("PRAGMA journal_mode=WAL")
    return con

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
# 📍 TARGET: engines/config_paths.py  (last open_auto_db definition)
# 🔎 SEARCH: def open_auto_db(
# 📆 PATCHED: 2025-12-01 — unify selector to match auto_conn
# ============================================================================
def open_auto_db(*, rw=None, **_):
    """
    Mirrors auto_conn routing exactly.
    Prevents legacy overrides.
    """
    if DAL_MODE.upper() == "SETUP":
        return _local_db(LOCAL_AUTO)

    if rw:
        return _cloud_db(CLOUD_AUTO)

    return _local_db(LOCAL_AUTO)
# === PATCH END ==============================================================


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

def connect_db(path: str|None=None, ro:bool=False, timeout:float=10.0):
    # if a specific path was supplied → determine correct family
    if path:
        if os.path.abspath(path) == os.path.abspath(LOCAL_BETS):
            return open_bets_db(rw=not ro)
        if os.path.abspath(path) == os.path.abspath(LOCAL_AUTO):
            return auto_conn(rw=not ro)
    # fallback = AUTO_DB
    return auto_conn(rw=not ro)



def autoscalp_db(): return LOCAL_AUTO
def bets_db(): return LOCAL_BETS
def settlements_db(): return LOCAL_SETTLE
def mastery_v7_db(): return LOCAL_MASTERY

def connect_db(path: str|None=None, ro:bool=False, timeout:float=10.0):
    return open_auto_db(rw=not ro)

def connect_bets_db(timeout:float=10.0,ro:bool=False):
    return open_bets_db(rw=not ro)

def connect_settlements_db(timeout:float=10.0,ro:bool=False):
    return open_settlements_db(rw=not ro)

def connect_mastery_v7_db(ro:bool=False,timeout:float=10.0):
    return open_mastery_db(rw=not ro)

AUTO_RO = lambda timeout=10.0: _local_db(LOCAL_AUTO)

print("[paths] ✔ COMPLETE CONFIG_PATHS.PY REBUILD — OK")
