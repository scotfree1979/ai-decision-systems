#!/usr/bin/env python3
"""Centralized path config — single source of truth for DB paths (Phase 1)"""

from __future__ import annotations
import os, sqlite3, time

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py (top of file)
# 📆 PATCHED: 2025-11-15Z — temporarily disable global sqlite hijack
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3 as _sq

try:
    # We still start the background DB writer if available,
    # but we DO NOT override sqlite3.connect anywhere.
    from engines.database_hijack_monitor import launch_db_writer
    launch_db_writer()
    print("[paths] hijack temporarily disabled — using native sqlite3.connect()")
except Exception as e:
    print(f"[paths] warn: database writer not applied ({e})")
# === PATCH END ===



def open_auto_db(*, timeout: float = 10.0, retries: int = 8, delay_s: float = 0.08, ro: bool = False):
    """
    Open autoscalp_gui.db with WAL + busy timeout + backoff.
    ro=True => read-only URI open.
    """
    path = autoscalp_db()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    last = None
    for i in range(max(1, retries)):
        try:
            uri = f"file:{path}?mode=ro" if ro else path
            con = sqlite3.connect(uri, uri=ro, timeout=timeout, isolation_level=None, check_same_thread=False)
            con.row_factory = sqlite3.Row
            try:
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                con.execute("PRAGMA busy_timeout=12000")
                con.execute("PRAGMA read_uncommitted=1")
            except Exception:
                pass
            return con
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            last = e
            if (("unable to open database file" in msg) or ("locked" in msg)) and i < retries-1:
                time.sleep(delay_s*(i+1)); continue
            raise
    raise last

# 1) top
from engines.sqlite_shim import open_db

# 2) universal connector (swallows legacy kwargs)
def connect_db(ro: bool = False, **kwargs):
    """Unified DB connector for BETS_DB (primary)."""
    global DB_PATH
    return open_db(DB_PATH, ro=ro, wal=True)

# 3) per-DB helpers must NOT forward unsupported kwargs
def connect_bets_db(timeout: float = 5.0, ro: bool = False):
    return open_db(BETS_DB_PATH, ro=ro, wal=True)

def connect_autoscalp_db(timeout: float = 5.0, ro: bool = False):
    return open_db(AUTOSCALP_DB_PATH, ro=ro, wal=True)

# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def autoscalp_db(
# (Add the following new block **below** your autoscalp_db() and DB_PATH definitions.)
def settlements_db() -> str:
    return os.path.join(_DATA_DIR, "settlements.db")

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-11-07Z — add mastery_v7.db path + connector
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def mastery_v7_db() -> str:
    """
    Canonical path for the Mastery v7 intelligence database.
    This is where v7 views, ML features, and micro_signals are stored.
    """
    return os.path.join(DATA_DIR, "mastery_v7.db")


def connect_mastery_v7_db(ro: bool = False, timeout: float = 10.0):
    """
    Open mastery_v7.db with WAL and busy_timeout; creates the file if missing.
    """
    path = mastery_v7_db()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    import sqlite3
    con = sqlite3.connect(path, timeout=timeout, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA foreign_keys=ON")
    except Exception:
        pass
    return con
# === PATCH END ===




def mastery_v7_cloud_db() -> str:
    """
    Return path to the iCloud-hosted Mastery v7 cache (mastery_cache.db).
    All large narrative, probability, and enrichment builds read/write here.
    """
    return os.path.join(cloud_data_dir(), "mastery_cache.db")


def connect_mastery_v7_cloud(ro: bool = False, timeout: float = 10.0):
    """
    Open a connection to the iCloud-hosted Mastery v7 cache with WAL mode enabled.
    Safe for use by story/enrich/probability builders.
    """
    path = mastery_v7_cloud_db()
    import sqlite3
    con = sqlite3.connect(path, timeout=timeout, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA temp_store=MEMORY;")
    except Exception:
        pass
    return con
# === PATCH END ===


# ──────────────────────────────────────────────────────────────────────────────
# Canonical location for ORDERS table
# ──────────────────────────────────────────────────────────────────────────────
_ORDERS_DB_CACHE: str | None = None

def orders_db() -> str:
    """
    Decide which DB file the `orders` table belongs to.
    Priority:
      1) app_kv key 'orders_db' = 'GUI' or 'BETS'
      2) If GUI has table 'orders', prefer GUI
      3) Else if BETS has table 'orders', use BETS
      4) Fallback to GUI
    Result is cached for this process.
    """
    import sqlite3
    global _ORDERS_DB_CACHE
    if _ORDERS_DB_CACHE:
        return _ORDERS_DB_CACHE

    gui = autoscalp_db()
    bets = DB_PATH

    # 1) config hint from app_kv (optional)
    try:
        from engines.session_secrets import get_secret
        pref = (get_secret("orders_db") or "").strip().upper()
        if pref == "GUI":
            _ORDERS_DB_CACHE = gui
            return _ORDERS_DB_CACHE
        if pref == "BETS":
            _ORDERS_DB_CACHE = bets
            return _ORDERS_DB_CACHE
    except Exception:
        pass  # no app_kv yet → detect

    def has_table(db_path: str, name: str) -> bool:
        try:
            con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
            with con:
                return con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (name,),
                ).fetchone() is not None
        except Exception:
            return False

    # 2/3) detect
    if has_table(gui, "orders"):
        _ORDERS_DB_CACHE = gui
    elif has_table(bets, "orders"):
        _ORDERS_DB_CACHE = bets
    else:
        # 4) default to GUI
        _ORDERS_DB_CACHE = gui

    return _ORDERS_DB_CACHE

def connect_orders_db(ro: bool = True, timeout: float = 15.0):
    """
    Open a connection to the DB that owns `orders`, using the project’s unified
    SQLite opener if available.
    """
    try:
        # Prefer your unified opener if present
        from engines.db import open_db  # adjust if your helper lives elsewhere
        return open_db(orders_db(), ro=ro, wal=not ro)
    except Exception:
        import sqlite3
        mode = "ro" if ro else "rwc"
        return sqlite3.connect(f"file:{orders_db()}?mode={mode}", uri=True, timeout=timeout, isolation_level=None)


# --- Mode tracking ------------------------------------------------------------
_MODE: str | None = None   # last mode set via set_db_paths()

def get_mode() -> str:
    """
    Return current mode (TEST | LEARNING | LIVE).
    Falls back to AUTOSCALP_MODE env var (default LEARNING) if set_db_paths() hasn't run yet.
    """
    import os
    m = (_MODE or os.environ.get("AUTOSCALP_MODE") or "learning").upper()
    return m if m in ("TEST", "LEARNING", "LIVE") else "LEARNING"


# add this helper near the top (after imports)
def current_mode() -> str:
    try:
        from engines.upgrade_import_patch import get_mode  # canonical
        m = get_mode() or ""
        return str(m).lower() if m else (os.environ.get("AUTOSCALP_MODE") or "learning").lower()
    except Exception:
        return (os.environ.get("AUTOSCALP_MODE") or "learning").lower()

# ── repo & data dirs ───────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))          # .../engines
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))     # repo root
DATA_DIR = os.path.join(_ROOT, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# ── default DB paths (prod/learning/live) ─────────────────────────────────────
BETS_DB_PATH = os.path.join(DATA_DIR, "bets.db")
AUTOSCALP_DB_PATH = os.path.join(DATA_DIR, "autoscalp_gui.db")

# ── TEST mode override: use isolated /data/test and *.test.db ─────────────────
def _get_mode_env() -> str:
    try:
        from engines.upgrade_import_patch import get_mode  # type: ignore
        m = get_mode() or ""
        return str(m).lower()
    except Exception:
        return (os.environ.get("AUTOSCALP_MODE") or "").lower()

if _get_mode_env() == "test":
    DATA_DIR = os.path.join(DATA_DIR, "test")
    os.makedirs(DATA_DIR, exist_ok=True)
    BETS_DB_PATH = os.path.join(DATA_DIR, "bets.test.db")
    AUTOSCALP_DB_PATH = os.path.join(DATA_DIR, "autoscalp_gui.test.db")

# ── Back-compat aliases (some legacy imports use these names) ─────────────────
DB_PATH = BETS_DB_PATH
AUTOSCALP_DB = AUTOSCALP_DB_PATH

# ── small helpers ─────────────────────────────────────────────────────────────
def repo_root() -> str: return _ROOT
def data_dir() -> str: return DATA_DIR
def bets_db() -> str:      return BETS_DB_PATH
def autoscalp_db() -> str: return AUTOSCALP_DB_PATH

# ── connection helpers ────────────────────────────────────────────────────────


# ADD THIS to engines/config_paths.py (place near the bottom, after the existing helpers)
# REPLACE the existing set_db_paths(..) with this in engines/config_paths.py

def set_db_paths(

    mode: str | None = None,
    *,
    bets: str | None = None,
    autoscalp: str | None = None,
    data_dir_override: str | None = None,
    quiet: bool = False,
) -> tuple[str, str]:
    global BETS_DB_PATH, AUTOSCALP_DB_PATH, DB_PATH, AUTOSCALP_DB, DATA_DIR, _MODE

    """
    Switch active DB files.

    Two usage patterns:

      A) By mode:
         set_db_paths(mode="test")        → <DATA_DIR>/test/bets.test.db, autoscalp_gui.test.db
         set_db_paths(mode="learning")    → <DATA_DIR>/bets.db,       autoscalp_gui.db
         set_db_paths(mode="live")        → <DATA_DIR>/bets.db,       autoscalp_gui.db

      B) By explicit paths (back-compat with GUI):
         set_db_paths(bets=..., autoscalp=..., data_dir_override=...)
    """

    m = (mode or os.environ.get("AUTOSCALP_MODE") or "learning").lower()
    _MODE = m  # <-- persist last selected mode for get_mode()

    # Decide base directory
    base = data_dir_override or DATA_DIR

    # If explicit paths provided, use them; else derive from mode
    if bets or autoscalp:
        if bets is None:
            bets = os.path.join(base, "bets.test.db" if m == "test" else "bets.db")
        if autoscalp is None:
            autoscalp = os.path.join(base, "autoscalp_gui.test.db" if m == "test" else "autoscalp_gui.db")
    else:
        if m == "test":
            base = os.path.join(DATA_DIR, "test")
            os.makedirs(base, exist_ok=True)
            bets = os.path.join(base, "bets.test.db")
            autoscalp = os.path.join(base, "autoscalp_gui.test.db")
        else:
            bets = os.path.join(DATA_DIR, "bets.db")
            autoscalp = os.path.join(DATA_DIR, "autoscalp_gui.db")

    # Ensure parent folders exist
    os.makedirs(os.path.dirname(bets), exist_ok=True)
    os.makedirs(os.path.dirname(autoscalp), exist_ok=True)

    # Update globals (and legacy aliases)
    BETS_DB_PATH = os.path.abspath(bets)
    AUTOSCALP_DB_PATH = os.path.abspath(autoscalp)
    DB_PATH = BETS_DB_PATH
    AUTOSCALP_DB = AUTOSCALP_DB_PATH

    # If caller overrode data dir, persist it so future calls align
    if data_dir_override:
        DATA_DIR = os.path.abspath(base)

    if not quiet:
        print(f"[paths] DATA_DIR={DATA_DIR} BETS_DB={BETS_DB_PATH} AUTO_DB={AUTOSCALP_DB_PATH}")
    return BETS_DB_PATH, AUTOSCALP_DB_PATH

# ─────────────────────────────────────────────────────────────────────────────
# 🧩 PATCH: engines/config_paths.py — add get_mode/is_replay helpers
# 📍 TARGET: append near bottom (no replacement of set_db_paths)
# 🔎 SEARCH: def get_db_paths() -> tuple[str, str]:
# ─────────────────────────────────────────────────────────────────────────────
def get_mode() -> str:
    """Return last selected mode as a lowercase token ('live','learning','test',...)."""
    try:
        return _MODE  # set in set_db_paths(...)
    except NameError:
        return (os.environ.get("AUTOSCALP_MODE") or "learning").lower()

def is_replay_mode() -> bool:
    m = (get_mode() or "").lower()
    return m in ("test","replay")

def get_db_paths() -> tuple[str, str]:
    """Return the current (bets_db_path, autoscalp_db_path)."""
    return BETS_DB_PATH, AUTOSCALP_DB_PATH

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/config_paths.py  (place near connect_db helpers)
# ─────────────────────────────────────────────────────────────────────────────
def ensure_db_ready() -> None:
    """Create/prime both DB files so ro readers never fail on first tick."""
    try:
        # Prime bets DB
        con = open_db(BETS_DB_PATH, ro=False, wal=True)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=8000")
            con.execute("CREATE TABLE IF NOT EXISTS app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            con.commit()
        finally:
            con.close()
    except Exception:
        pass
    try:
        # Prime autoscalp GUI DB (if you use it separately)
        con = open_db(AUTOSCALP_DB_PATH, ro=False, wal=True)
        try:
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("PRAGMA busy_timeout=8000")
            con.execute("CREATE TABLE IF NOT EXISTS app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/config_paths.py
# --- PATCH START: canonical lowercase data/ ----------------------------
import os

def _resolve_data_dir() -> str:
    """
    Canonical data directory resolver (lowercase 'data').
    Priority:
      1) Env override AUTOSCALP_DB_DIR
      2) <repo>/data        (lowercase)
      3) <repo>/Data        (capital D) -> create/point a symlink to 'data'
    If both exist, always use 'data' and attempt to symlink Data -> data.
    """
    env_dir = os.environ.get("AUTOSCALP_DB_DIR")
    if env_dir:
        os.makedirs(env_dir, exist_ok=True)
        return env_dir

    data_low = os.path.join(_ROOT, "data")
    data_cap = os.path.join(_ROOT, "Data")

    # create lowercase if missing
    os.makedirs(data_low, exist_ok=True)

    # if a capital Data exists, try to make it a symlink to data_low (best-effort)
    if os.path.isdir(data_cap) and not os.path.islink(data_cap):
        try:
            # do nothing destructive; we only ensure both point to the same place
            pass
        except Exception:
            pass
    elif not os.path.exists(data_cap):
        try:
            os.symlink(data_low, data_cap)
        except Exception:
            pass

    return data_low

_DATA_DIR = _resolve_data_dir()

def autoscalp_db() -> str:
    """GUI/Orders DB (current path set by set_db_paths)."""
    return AUTOSCALP_DB_PATH

def bets_db() -> str:
    """BETS DB (current path set by set_db_paths)."""
    return BETS_DB_PATH

def cloud_data_dir() -> str:
    return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCloud/data")

# --- PATCH END ----------------------------------------------------------



def replay_filter_sql(col: str, day_iso: str) -> str:
    """
    Build a date filter compatible with SQLite 'YYYY-MM-DD'.
    Example: replay_filter_sql('snapshot_ts','2025-08-24')
    """
    return f"date({col}) = date('{day_iso}')"

# ─────────────────────────────────────────────────────────────────
# Shared resilient SQLite openers for AUTOSCALP GUI db
# ─────────────────────────────────────────────────────────────────
import sqlite3 as _sqlite, time as _time, os as _os

def _ensure_parent_dir(_p: str) -> None:
    try: _os.makedirs(_os.path.dirname(_p), exist_ok=True)
    except Exception: pass

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py:auto_conn
# 📆 PATCHED: 2025-11-06Z — revert to single-handle WAL connection (stable)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def auto_conn(*, rw: bool = True, timeout: float = 10.0,
              retries: int = 6, delay_s: float = 0.08) -> _sqlite.Connection:
    """
    Open autoscalp_gui.db (RW by default) with WAL, busy_timeout and retries.
    Uses a single handle per thread — no self-ATTACH, no double opens.
    This is the stable pattern that avoids 'unable to open database file' cascades.
    """
    db_path = autoscalp_db()
    _ensure_parent_dir(db_path)
    last = None

    for i in range(max(1, retries)):
        try:
            con = _sqlite.connect(
                db_path,
                timeout=timeout,
                isolation_level=None,
                check_same_thread=False,
            )
            con.row_factory = _sqlite.Row
            try:
                con.execute("PRAGMA foreign_keys=ON")
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA synchronous=NORMAL")
                con.execute("PRAGMA busy_timeout=8000")
                con.execute("PRAGMA read_uncommitted=1")
            except Exception:
                pass
            return con

        except _sqlite.OperationalError as e:
            last = e
            m = str(e).lower()
            if (("unable to open database file" in m) or ("locked" in m)) and i < retries - 1:
                _time.sleep(delay_s * (i + 1))
                continue
            raise
    raise last
# === PATCH END ===


# make read-only open a no-op (RW) until we properly split readers/writers
def auto_ro(*args, **kwargs):
    return auto_conn(*args, **kwargs)


def q_retry(con: _sqlite.Connection, sql: str, params=(), *, tries: int = 6, delay_s: float = 0.08):
    """Retry a single statement on transient SQLite errors."""
    last = None
    for i in range(max(1, tries)):
        try:
            return con.execute(sql, params)
        except _sqlite.OperationalError as e:
            last = e
            m = str(e).lower()
            if (("locked" in m) or ("unable to open database file" in m)) and i < tries - 1:
                _time.sleep(delay_s * (i + 1))
                continue
            raise
    raise last

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-11-08Z — mastery v7 cloud cache shim
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, sqlite3

def cloud_data_dir() -> str:
    return os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCloud/data")
    
    os.makedirs(path, exist_ok=True)
    return path

def mastery_v7_cache_db() -> str:
    """
    Returns the path to the iCloud-hosted mastery cache database.
    All large story, enrichment, and probability scripts read/write here.
    """
    return os.path.join(cloud_data_dir(), "mastery_cache.db")

def connect_mastery_v7_cache(ro: bool = False, timeout: float = 10.0):
    """
    Open a connection to the mastery cache (iCloud) database.
    Drops in as a direct replacement for mastery_v7_db().
    """
    path = mastery_v7_cache_db()
    con = sqlite3.connect(path, timeout=timeout, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA temp_store=MEMORY;")
    except Exception:
        pass
    return con
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-11-08Z — universal autoscalp→icloud cache shim
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os

def autoscalp_cache_db() -> str:
    """
    Cloud-mirrored replacement for autoscalp_gui.db.
    Used by cache builders and story pipelines that
    reference autoscalp_db() implicitly.
    """
    path = os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache/autoscalp_gui_cache.db"
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path

def autoscalp_db() -> str:
    """
    Shim redirect — if a cached AutoScalp DB exists in iCloud,
    use it instead of the local data/autoscalp_gui.db.
    Falls back automatically to local if cache missing.
    """
    cloud_path = autoscalp_cache_db()
    local_path = os.path.join(os.path.dirname(__file__), "../../data/autoscalp_gui.db")
    if os.path.exists(cloud_path):
        return cloud_path
    return os.path.abspath(local_path)
# === PATCH END ===
# === PATCH START ===
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-11-10Z — Local+Cloud Mirror Synchronizer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, threading, shutil, time

# canonical local and cloud paths
_LOCAL_AUTO_PATH = os.path.join(DATA_DIR, "autoscalp_gui.db")
_CLOUD_AUTO_PATH = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache/autoscalp_gui_cache.db"
)

# ensure both exist
os.makedirs(os.path.dirname(_LOCAL_AUTO_PATH), exist_ok=True)
os.makedirs(os.path.dirname(_CLOUD_AUTO_PATH), exist_ok=True)
if not os.path.exists(_LOCAL_AUTO_PATH):
    open(_LOCAL_AUTO_PATH, "a").close()
if not os.path.exists(_CLOUD_AUTO_PATH):
    open(_CLOUD_AUTO_PATH, "a").close()

def _open_sync(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA busy_timeout=8000")
    except Exception:
        pass
    return con


def dual_conn() -> tuple[sqlite3.Connection, sqlite3.Connection]:
    """
    Return (local_con, cloud_con) both open and ready.
    Used internally by read/write shims.
    """
    return _open_sync(_LOCAL_AUTO_PATH), _open_sync(_CLOUD_AUTO_PATH)

#def _continuous_sync(interval_s: float = 15.0):
#    """
#    Background thread: periodically mirror local→cloud if size differs.
#    Lightweight safety net against iCloud delays.
#    """
#    def loop():
#        while True:
#            try:
#                if os.path.getsize(_LOCAL_AUTO_PATH) > 0:
#                    shutil.copy2(_LOCAL_AUTO_PATH, _CLOUD_AUTO_PATH)
#            except Exception:
#                pass
#            time.sleep(interval_s)
#    threading.Thread(target=loop, name="AutoDBSync", daemon=True).start()

#_continuous_sync()



def unified_execute(sql: str, params: tuple = (), fetch: bool = True):
    """
    Execute SQL on local DB first; if fetch returns empty, retry on cloud.
    On write statements, execute on both.
    """
    # open both lazily
    local, cloud = dual_conn()
    try:
        is_select = sql.strip().lower().startswith("select")
        if is_select:
            cur = local.execute(sql, params)
            rows = cur.fetchall()
            if not rows:
                cur = cloud.execute(sql, params)
                rows = cur.fetchall()
            return rows
        else:
            # non-select → write to both
            local.execute(sql, params)
            cloud.execute(sql, params)
            return None
    finally:
        try:
            local.close()
            cloud.close()
        except Exception:
            pass


# patch auto_conn() to use unified reads/writes transparently

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/config_paths.py:auto_conn
# 📆 PATCHED: 2025-11-13T22:00Z — silent success mode
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def auto_conn(*, rw: bool = True, timeout: float = 10.0,
              retries: int = 6, delay_s: float = 0.08):
    """
    AutoScalp hybrid connection shim — silent success mode
      • Opens the verified iCloud cache DB directly.
      • Only prints if a genuine error occurs.
      • Keeps all schema PRAGMAs and behavior identical.
    """
    import sqlite3, os, threading

    CLOUD_PATH = "/Users/malachikelly/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache/autoscalp_gui_cache.db"
    os.makedirs(os.path.dirname(CLOUD_PATH), exist_ok=True)
    if not os.path.exists(CLOUD_PATH):
        open(CLOUD_PATH, "a").close()

    _lock = threading.Lock()
    _shared_con = None
    _schema_init_done = threading.Event()

    def _get_shared_con():
        nonlocal _shared_con
        with _lock:
            if _shared_con is None:
                _shared_con = sqlite3.connect(
                    CLOUD_PATH,
                    timeout=timeout,
                    isolation_level=None,
                    check_same_thread=False,
                )
                _shared_con.row_factory = sqlite3.Row
            return _shared_con

    def _init_schema_once(con):
        if _schema_init_done.is_set():
            return
        try:
            con.execute("PRAGMA foreign_keys=ON;")
            con.execute("PRAGMA journal_mode=WAL;")
            con.execute("PRAGMA busy_timeout=8000;")
            con.execute("PRAGMA synchronous=NORMAL;")
            con.execute("PRAGMA read_uncommitted=1;")
            _schema_init_done.set()
        except Exception as e:
            print(f"[auto_conn] init warn: {e}")

    class DualConnection:
        """sqlite3-compatible wrapper with persistent iCloud DB connection."""
        def __init__(self):
            self.con = _get_shared_con()
            _init_schema_once(self.con)

        def execute(self, sql, params=()):
            try:
                cur = self.con.execute(sql, params)
                if sql.strip().lower().startswith("select"):
                    return cur
                self.con.commit()
                return cur
            except Exception as e:
                msg = str(e)
                if "duplicate column name" in msg or "no such table" in msg:
                    return []
                print(f"[auto_conn] hybrid warn: {msg}")
                return []

        def cursor(self): return self.con.cursor()
        def commit(self):
            try: self.con.commit()
            except Exception: pass
        def close(self): pass
        def __enter__(self): return self
        def __exit__(self, exc_type, exc, tb): return False

    return DualConnection()
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-11-10Z — async cloud replication (low-priority)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import queue, threading, sqlite3, time, os, shutil

_LOCAL_AUTO_PATH = os.path.join(DATA_DIR, "autoscalp_gui.db")
_CLOUD_AUTO_PATH = os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache/autoscalp_gui_cache.db"
)
os.makedirs(os.path.dirname(_CLOUD_AUTO_PATH), exist_ok=True)
os.makedirs(os.path.dirname(_LOCAL_AUTO_PATH), exist_ok=True)

_cloud_q: "queue.Queue[tuple[str, tuple]]" = queue.Queue(maxsize=10000)

def _cloud_writer():
    """Background thread that drains queued SQL into the cloud copy."""
    delay = 0.05
    while True:
        try:
            sql, params = _cloud_q.get()
            with sqlite3.connect(_CLOUD_AUTO_PATH, isolation_level=None, timeout=10) as con:
                con.execute("PRAGMA busy_timeout=8000")
                con.execute(sql, params)
            delay = 0.05  # fast path when active
        except Exception:
            delay = min(delay * 2, 2.0)  # exponential backoff
        finally:
            _cloud_q.task_done()
            time.sleep(delay)


# start once
if not any(t.name == "CloudReplicator" for t in threading.enumerate()):
    t = threading.Thread(target=_cloud_writer, name="CloudReplicator", daemon=True)
    t.start()


def unified_execute(sql: str, params: tuple = (), fetch: bool = True):
    """Local-first read/write with async cloud replication."""
    import sqlite3
    is_select = sql.strip().lower().startswith("select")
    with sqlite3.connect(_LOCAL_AUTO_PATH, isolation_level=None, timeout=10) as con:
        con.row_factory = sqlite3.Row
        if is_select:
            rows = con.execute(sql, params).fetchall()
            if not rows and os.path.exists(_CLOUD_AUTO_PATH):
                with sqlite3.connect(_CLOUD_AUTO_PATH) as cc:
                    cc.row_factory = sqlite3.Row
                    rows = cc.execute(sql, params).fetchall()
            return rows
        else:
            con.execute(sql, params)
            # enqueue for cloud replication (non-blocking)
            try:
                _cloud_q.put_nowait((sql, params))
            except queue.Full:
                pass
            return None
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py (very bottom of file)
# 📆 PATCHED: 2025-11-15Z — Safe Local+Cloud fresh-read shim (race-day stable)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3 as __sq
import os as __os
import time as __time

# Resolve local + cloud AUTOSCALP DB paths using your current config
__LOCAL_AUTO = AUTOSCALP_DB_PATH
__CLOUD_AUTO = __os.path.expanduser(
    "~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache/autoscalp_gui_cache.db"
)

# Ensure both paths exist
__os.makedirs(__os.path.dirname(__LOCAL_AUTO), exist_ok=True)
__os.makedirs(__os.path.dirname(__CLOUD_AUTO), exist_ok=True)
if not __os.path.exists(__LOCAL_AUTO):
    open(__LOCAL_AUTO, "a").close()
if not __os.path.exists(__CLOUD_AUTO):
    open(__CLOUD_AUTO, "a").close()

def unified_fresh_read(sql: str, params: tuple = ()):
    """
    SAFE READ:
      • Read LOCAL first.
      • If LOCAL has rows → return them immediately.
      • If LOCAL is empty → read CLOUD.
      • Never trusts cloud schema for writes.
      • No fallbacks, no overrides, no hijack.
    """
    # Read LOCAL
    try:
        with __sq.connect(__LOCAL_AUTO, timeout=5) as con:
            con.row_factory = __sq.Row
            rows = con.execute(sql, params).fetchall()
            if rows:
                return rows
    except Exception:
        pass

    # Read CLOUD only if LOCAL empty
    try:
        with __sq.connect(__CLOUD_AUTO, timeout=5) as con:
            con.row_factory = __sq.Row
            rows = con.execute(sql, params).fetchall()
            return rows
    except Exception:
        return []

def unified_safe_execute(sql: str, params: tuple = ()):
    """
    SAFE WRITE:
      • Always write to CLOUD (no locks).
      • Writes NEVER touch LOCAL during execution.
      • Background sync (your existing code) will backfill local.
    """
    try:
        with __sq.connect(__CLOUD_AUTO, timeout=10) as con:
            con.execute("PRAGMA busy_timeout=8000;")
            con.execute(sql, params)
            con.commit()
    except Exception as e:
        print(f"[unified_safe_execute] warn: {e}")

# Expose these helpers to the rest of the tool
fresh_read = unified_fresh_read
safe_write = unified_safe_execute

print("[paths] ✔ Race-day safe Local+Cloud shim active (fresh-read + safe-write)")
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/config_paths.py — force consistent DB paths across all imports
# 📆 PATCHED: 2025-11-15Z — Race-day forced DB path freeze
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Freeze AUTOSCALP_DB_PATH and BETS_DB_PATH for the entire process.
# Any later override or hijack will be ignored by all modules.
import builtins
builtins.AUTOSCALP_DB_PATH = AUTOSCALP_DB_PATH
builtins.BETS_DB_PATH = BETS_DB_PATH
print(f"[paths] 🔒 DB PATH FREEZE ACTIVE → AUTO_DB={AUTOSCALP_DB_PATH}")
# === PATCH END ===



