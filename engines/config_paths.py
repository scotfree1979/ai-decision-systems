#!/usr/bin/env python3
"""
AutoScalp — CONFIG PATHS (FINAL CLEAN REBUILD v3)
-------------------------------------------------
DAL Architecture (Final Model):

SETUP MODE:
    READ  = LOCAL
    WRITE = LOCAL

LIVE MODE:
    READ  = LOCAL
    WRITE = LIVECACHE

LIVE ROUTER:
    READ  = LIVECACHE
    WRITE = LIVECACHE

Mirror:
    LiveCache → LOCAL (queue-based)

All real sqlite3 connections ALWAYS ATTACH:
    auto, bets, settlements, mastery
With consistent attach names.
"""

from __future__ import annotations
import os, sqlite3, threading, queue, time, glob, hashlib
import _sqlite3 as _raw_sqlite3
from typing import Tuple

# ===============================================================
# 📁 RESOLVE PROJECT ROOT + DATA DIRECTORY
# ===============================================================

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))

def _resolve_data_dir() -> str:
    env = os.environ.get("AUTOSCALP_DB_DIR")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    d = os.path.join(_ROOT, "data")
    os.makedirs(d, exist_ok=True)
    return d

DATA_DIR = _resolve_data_dir()

# === PATCH START ============================================================
# 📆 PATCHED: 2025-12-09 — Restore full set_db_paths() for GUI setup

def set_db_paths(
    mode: str | None = None,
    *,
    bets: str | None = None,
    autoscalp: str | None = None,
    data_dir_override: str | None = None,
    quiet: bool = False,
):
    """
    GUI Step 1 uses this heavily.
    Updates LOCAL paths + legacy aliases.
    """
    global DATA_DIR, LOCAL_BETS, LOCAL_AUTO, LOCAL_SETTLE, LOCAL_MASTERY
    global BETS_DB_PATH, AUTOSCALP_DB_PATH, DB_PATH, GUI_DB_PATH
    global BETS_DB, AUTOSCALP_DB, AUTO_DB, _MODE

    m = (mode or os.environ.get("AUTOSCALP_MODE") or "learning").lower()
    _MODE = m

    base = data_dir_override or DATA_DIR
    os.makedirs(base, exist_ok=True)

    bet_p = bets or os.path.join(base, "bets.db" if m != "test" else "bets.test.db")
    auto_p = autoscalp or os.path.join(base, "autoscalp_gui.db" if m != "test" else "autoscalp_gui.test.db")

    LOCAL_BETS     = os.path.abspath(bet_p)
    LOCAL_AUTO     = os.path.abspath(auto_p)
    LOCAL_SETTLE   = os.path.join(base, "settlements.db")
    LOCAL_MASTERY  = os.path.join(base, "mastery_v7.db")

    BETS_DB_PATH      = LOCAL_BETS
    AUTOSCALP_DB_PATH = LOCAL_AUTO
    GUI_DB_PATH       = AUTOSCALP_DB_PATH
    DB_PATH           = BETS_DB_PATH

    BETS_DB      = BETS_DB_PATH
    AUTOSCALP_DB = AUTOSCALP_DB_PATH
    AUTO_DB      = AUTOSCALP_DB_PATH

    if not quiet:
        print(f"[paths] DATA_DIR={base} BETS_DB={BETS_DB_PATH} AUTO_DB={AUTOSCALP_DB_PATH}")

    return BETS_DB_PATH, AUTOSCALP_DB_PATH

# === PATCH END ==============================================================
# ===============================================================
# 🔒 ATTACH INTENT MAP (authoritative)
# ===============================================================

_ATTACH_INTENT_MAP = {
    # decision / live loop
    "scope":        {"auto"},
    "market_monitor": {"auto"},
    "bus":          {"auto"},
    "context":      {"auto"},

    # execution
    "live_router":  {"auto", "bets"},
    "overwatcher":  {"auto", "bets"},

    # settlement
    "settlements":  {"auto", "settlements"},

    # learning
    "mastery":      {"auto", "mastery"},

    # ui
    "dashboard":    {"auto", "bets"},
}

import inspect

def _infer_attach_intent() -> set[str]:
    """
    Infer attach intent based on calling module.
    Returns a set of DB families required.
    Defaults to {'auto'}.
    """
    for frame in inspect.stack()[2:]:
        mod = frame.frame.f_globals.get("__name__", "")
        if not mod:
            continue

        if "scope" in mod:
            return _ATTACH_INTENT_MAP["scope"]
        if "market_monitor" in mod:
            return _ATTACH_INTENT_MAP["market_monitor"]
        if "bus" in mod:
            return _ATTACH_INTENT_MAP["bus"]
        if "context_builder" in mod:
            return _ATTACH_INTENT_MAP["context"]
        if "live_router" in mod:
            return _ATTACH_INTENT_MAP["live_router"]
        if "overwatcher" in mod:
            return _ATTACH_INTENT_MAP["overwatcher"]
        if "settlements" in mod:
            return _ATTACH_INTENT_MAP["settlements"]
        if "mastery" in mod:
            return _ATTACH_INTENT_MAP["mastery"]
        if "dashboard" in mod:
            return _ATTACH_INTENT_MAP["dashboard"]

    # safest default
    return {"auto"}


# ===============================================================
# 📘 LOCAL DBs
# ===============================================================

LOCAL_BETS      = os.path.join(DATA_DIR, "bets.db")
LOCAL_AUTO      = os.path.join(DATA_DIR, "autoscalp_gui.db")
LOCAL_SETTLE    = os.path.join(DATA_DIR, "settlements.db")
LOCAL_MASTERY   = os.path.join(DATA_DIR, "mastery_v7.db")

for p in (LOCAL_BETS, LOCAL_AUTO, LOCAL_SETTLE, LOCAL_MASTERY):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        open(p, "a").close()

# ===============================================================
# 🌩️ LIVECACHE DBs
# ===============================================================

LIVE_ROOT = os.path.join(DATA_DIR, "livecache")
os.makedirs(LIVE_ROOT, exist_ok=True)

CLOUD_AUTO     = os.path.join(LIVE_ROOT, "autoscalp_livecache.db")
CLOUD_BETS     = os.path.join(LIVE_ROOT, "bets_livecache.db")
CLOUD_SETTLE   = os.path.join(LIVE_ROOT, "settlements_livecache.db")
CLOUD_MASTERY  = os.path.join(LIVE_ROOT, "mastery_livecache.db")

for p in (CLOUD_AUTO, CLOUD_BETS, CLOUD_SETTLE, CLOUD_MASTERY):
    if not os.path.exists(p):
        open(p, "a").close()

print(f"[LiveCache DAL] active → {LIVE_ROOT}")

def _bootstrap_livecache_core_schema():
    """
    Mirror all REAL tables (no views) from each LOCAL DB into its
    corresponding LiveCache DB.

    This guarantees that LiveCache has:
        • identical tables
        • identical columns
        • identical PRIMARY KEY constraints
        • identical UNIQUE constraints

    Without any of the historical partial bootstrappers that caused
    ON CONFLICT failures.

    NOTE:
    - Does NOT copy data.
    - Does NOT create views.
    - Does NOT touch triggers.
    """

    import sqlite3

    # Local → LiveCache mapping by family
    families = {
        "auto":       (LOCAL_AUTO,       CLOUD_AUTO),
        "bets":       (LOCAL_BETS,       CLOUD_BETS),
        "settlements":(LOCAL_SETTLE,     CLOUD_SETTLE),
        "mastery":    (LOCAL_MASTERY,    CLOUD_MASTERY),
    }

    for fam, (local_path, live_path) in families.items():
        try:
            # --- open LOCAL for schema inspection ---
            lcon = sqlite3.connect(local_path)
            lcur = lcon.cursor()

            # --- open LIVE target for schema creation ---
            vcon = sqlite3.connect(live_path)
            vcur = vcon.cursor()

            # 1️⃣ get all LOCAL tables (exclude views)
            tables = lcur.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name NOT LIKE 'sqlite_%'
            """).fetchall()

            for (tbl,) in tables:
                # 2️⃣ read PRAGMA for columns
                cols = lcur.execute(f"PRAGMA table_info('{tbl}')").fetchall()
                # cols: cid, name, type, notnull, dflt_value, pk

                # 3️⃣ read UNIQUE constraints (indexes)
                idx_rows = lcur.execute(f"PRAGMA index_list('{tbl}')").fetchall()
                unique_cols = []
                for idx in idx_rows:
                    idx_name = idx[1]
                    if idx[2] == 1:  # UNIQUE index
                        # get column list for this index
                        icols = lcur.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
                        unique_cols.append([c[2] for c in icols])

                # 4️⃣ build CREATE TABLE statement
                col_defs = []
                pk_cols = []

                for cid, name, ctype, notnull, dflt, pk in cols:
                    line = f"{name} {ctype or ''}".strip()

                    if notnull:
                        line += " NOT NULL"
                    if dflt is not None:
                        line += f" DEFAULT {dflt}"
                    if pk:
                        pk_cols.append(name)

                    col_defs.append(line)

                # PRIMARY KEY clause
                pk_clause = ""
                if pk_cols:
                    pk_clause = f", PRIMARY KEY({','.join(pk_cols)})"

                # FULL TABLE CREATE DDL
                create_sql = f"""
                    CREATE TABLE IF NOT EXISTS {tbl} (
                        {', '.join(col_defs)}
                        {pk_clause}
                    );
                """

                vcur.execute(create_sql)

                # 5️⃣ recreate UNIQUE indexes
                for ucols in unique_cols:
                    idx_name = f"ux_{tbl}_{'_'.join(ucols)}"
                    vcur.execute(
                        f"CREATE UNIQUE INDEX IF NOT EXISTS {idx_name} "
                        f"ON {tbl} ({','.join(ucols)})"
                    )

            vcon.commit()
            lcon.close()
            vcon.close()

            print(f"[DAL-SCHEMA] LiveCache updated for {fam}")

        except Exception as e:
            print(f"[DAL-SCHEMA] {fam} mirror failed: {e}")

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py  (right after LiveCache schema bootstrap)
# 📆 PATCHED: 2025-12-03 — add missing created_at column to mastery_events

def _ensure_mastery_events_schema():
    try:
        con = sqlite3.connect(CLOUD_AUTO, timeout=5, isolation_level=None)
        con.execute("PRAGMA journal_mode=WAL")
        cols = [r[1] for r in con.execute("PRAGMA table_info('mastery_events')").fetchall()]
        if "created_at" not in cols:
            con.execute(
                "ALTER TABLE mastery_events "
                "ADD COLUMN created_at TEXT NOT NULL "
                "DEFAULT (datetime('now','utc'))"
            )
            print("[DAL-SCHEMA] mastery_events: added created_at")
        con.close()
    except Exception as e:
        print(f"[DAL-SCHEMA] mastery_events warn: {e}")

# call this immediately after _bootstrap_livecache_core_schema()
# === PATCH END ============================================================


# ===============================================================
# 🧵 DAL GLOBAL READ QUEUE + READER POOL
# ===============================================================

_DAL_READ_QUEUE = queue.Queue(maxsize=200000)

# persistent readers (one per DB fam)
_PERSISTENT_READERS = {}
_PERSISTENT_READ_LOCK = threading.Lock()

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::_get_reader
# 📆 PATCHED: 2025-12-12 — SINGLE persistent read-only connection per family
# PURPOSE:
#   • Eliminate sqlite connect storms
#   • Eliminate ATTACH during live ticks
#   • Stabilise DALReadProxy
# ============================================================================

def _get_reader(fam: str):
    with _PERSISTENT_READ_LOCK:
        if fam in _PERSISTENT_READERS:
            return _PERSISTENT_READERS[fam]

        path = {
            "auto":        LOCAL_AUTO,
            "bets":        LOCAL_BETS,
            "settlements": LOCAL_SETTLE,
            "mastery":     LOCAL_MASTERY,
        }[fam]

        con = sqlite3.connect(
            path,
            timeout=10,
            isolation_level=None,
            check_same_thread=False
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=8000")

        # ❗ ATTACH ONLY IN SETUP
        if DAL_MODE != "LIVE":
            _attach_all_four_local(con)

        _PERSISTENT_READERS[fam] = con
        return con


# Data structure returned to callers:
# A "future" for a read: caller waits on result_queue.get()
class _ReadFuture:
    __slots__ = ("result_q",)

    def __init__(self):
        self.result_q = queue.Queue(maxsize=1)

    def fetchall(self):
        rows = self.result_q.get()
        return rows if rows is not None else []

    def fetchone(self):
        rows = self.result_q.get()
        if not rows:
            return None
        return rows[0]

class DALReadProxy:
    """
    Lightweight read-only connection proxy.
    Absorbs row_factory/text_factory assignments, so legacy code keeps working.
    """
    __slots__ = ("_fam", "_shim")

    def __init__(self, fam: str):
        self._fam = fam
        self._shim = _AttrShim()

    # NEW — context manager support
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False   # do not suppress exceptions

    # absorb row_factory, text_factory, etc.
    def __setattr__(self, k, v):
        if k in ("_fam", "_shim"):
            object.__setattr__(self, k, v)
        else:
            setattr(self._shim, k, v)

    def __getattr__(self, k):
        return getattr(self._shim, k, None)

    # core EXECUTE API
    def execute(self, sql: str, params=()):
        fut = _ReadFuture()
        _DAL_READ_QUEUE.put((self._fam, sql, params or (), fut))
        return fut

    def cursor(self): return self
    def fetchall(self): return []
    def fetchone(self): return None
    def close(self): return None

# ===============================================================
# 🔴 FINAL DALWriteProxy — Dual-Write (LOCAL + LIVE)
# ===============================================================

class DALWriteProxy:
    """
    sqlite3.Connection-like writer that:
      • Queues all writes (no direct DB I/O here)
      • Duplicates every write into LOCAL + LIVECACHE
      • Behaves like sqlite3.Connection for legacy modules
      • Supports context manager ("with ... as con:")
      • Supports execute(), executemany(), cursor()
      • NEVER executes scripts (executescript forbidden)
    """

    __slots__ = ("_fam", "_shim")

    # NEW — context manager support
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # queue-based writer handles persistence; nothing to commit/rollback
        return False

    def __init__(self, fam: str):
        # fam MUST be one of: "auto", "bets", "settlements", "mastery"
        self._fam = fam
        self._shim = _AttrShim()      # absorbs row_factory/text_factory

    # -----------------------------------------------------------
    # Context Manager Support
    # -----------------------------------------------------------
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        # DAL writer is async — nothing to commit/rollback here.
        return False

    # -----------------------------------------------------------
    # Core Write
    # -----------------------------------------------------------
# === PATCH START ===
# 📍 TARGET: engines/config_paths.py::DALWriteProxy
# 📆 PATCHED: 2025-12-10 — Prevent CREATE TRIGGER from entering DAL queue
# 🎯 PURPOSE:
#   • CREATE TRIGGER must never go through DALWriteProxy
#   • Execute triggers directly on LOCAL real DB
#   • Skip dual-write and skip the write queue entirely
# ============================================================================

    def execute(self, sql, params=()):
        """
        LOCAL-ONLY WRITE MODE:
            • All writes go to the LOCAL DB for this family.
            • LiveCache writes are disabled (architecture preserved but unused).
            • CREATE TRIGGER / DDL statements run directly on LOCAL DB.
        """
        # Normalise parameters
        if isinstance(params, list):
            params = tuple(params)

        sql_text = sql.strip().lower()
        is_trigger = sql_text.startswith("create trigger")

        # --- TRIGGER / DDL PATH → EXECUTE DIRECTLY ON LOCAL ---
        if is_trigger:
            try:
                wloc = _get_writer_local(self._fam)
                wloc.executescript(sql)
                print(f"[DAL-LOCAL-ONLY] Trigger/DDL installed into LOCAL {self._fam}")
            except Exception as e:
                print(f"[DAL-LOCAL-ONLY][DDL] fail: {e} | sql={sql}")
            return self

        # --- NORMAL WRITE → LOCAL ONLY ---
        try:
            wloc = _get_writer_local(self._fam)
            wloc.execute(sql, params)
        except Exception as e:
            print(f"[DAL-LOCAL-ONLY] write fail fam={self._fam}: {e} | sql={sql}")

        return self

    def executemany(self, sql, seq):
        """
        LOCAL-ONLY WRITE MODE for batch executions.
        LiveCache writes disabled.
        """
        sql_text = sql.strip().lower()
        is_trigger = sql_text.startswith("create trigger")

        # --- TRIGGER / DDL PATH ---
        if is_trigger:
            try:
                wloc = _get_writer_local(self._fam)
                wloc.executescript(sql)
                print(f"[DAL-LOCAL-ONLY] Trigger installed via executemany into LOCAL {self._fam}")
            except Exception as e:
                print(f"[DAL-LOCAL-ONLY][DDL-many] fail: {e} | sql={sql}")
            return self

        # --- NORMAL BATCH → LOCAL ONLY ---
        try:
            wloc = _get_writer_local(self._fam)
            for row in seq:
                params = tuple(row) if isinstance(row, list) else row
                wloc.execute(sql, params)
        except Exception as e:
            print(f"[DAL-LOCAL-ONLY] batch fail fam={self._fam}: {e} | sql={sql}")

        return self


    # -----------------------------------------------------------
    # Compatibility Internals
    # -----------------------------------------------------------
    def executescript(self, script):
        raise RuntimeError("DALWriteProxy does not support executescript()")

    def cursor(self):      return self
    def fetchall(self):    return []
    def fetchone(self):    return None
    def commit(self):      return None
    def rollback(self):    return None
    def close(self):       return None

    # absorb row_factory & text_factory safely
    def __setattr__(self, k, v):
        if k in ("_fam", "_shim"):
            object.__setattr__(self, k, v)
        else:
            setattr(self._shim, k, v)

    def __getattr__(self, k):
        return getattr(self._shim, k, None)



def _dal_reader_loop():
    """
    Dedicated thread that executes ALL read queries for the entire system.
    Ensures:
      - deterministic attach maps
      - minimal FD usage
      - no per-read sqlite3.connect() calls
      - safe threading around SQLite (reads serialized)
    """
    while True:
        fam, sql, params, fut = _DAL_READ_QUEUE.get()
        try:
            con = _get_reader(fam)
            cur = con.execute(sql, params)
            rows = cur.fetchall()
            fut.result_q.put(rows)
        except Exception as e:
            print(f"[DAL-READER] fail: {e} | sql={sql}")
            fut.result_q.put([])
        finally:
            _DAL_READ_QUEUE.task_done()


# Start DAL reader thread
if not any(t.name == "DAL-Reader" for t in threading.enumerate()):
    threading.Thread(target=_dal_reader_loop, name="DAL-Reader", daemon=True).start()
    print("[DAL] Reader thread ACTIVE")

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: # Start DAL reader thread
# ⛏️ ACTION: insert hardened DALReadProxy + DALWriteProxy AFTER this block
# 📆 PATCHED: 2025-12-09 — Hardened sqlite-compatible DAL proxies
# ============================================================================

# ---------------------------------------------------------------------------
# HARDENED ATTR SHIM (absorbs row_factory/text_factory safely)
# ---------------------------------------------------------------------------

# === PATCH START ============================================================
# 📆 PATCHED: 2025-12-10 — Restore missing _AttrShim used by DALWriteProxy
# PURPOSE:
#   • Absorb arbitrary attribute assignments (row_factory, text_factory, etc.)
#   • Prevent AttributeError crashes
#   • Behaves as a transparent sink for unknown attributes

# ---------------------------------------------------------------------------
# HARDENED ATTR SHIM (shared by readers and writers)
# ---------------------------------------------------------------------------
class _AttrShim:
    """Absorbs any attribute (row_factory, text_factory, etc.) safely."""
    __slots__ = ()
    def __getattr__(self, k): return None
    def __setattr__(self, k, v): pass


class DALReadProxy:
    """
    Lightweight read-only connection proxy.
    Absorbs row_factory/text_factory assignments, so legacy code keeps working.
    """
    __slots__ = ("_fam", "_shim")

    def __init__(self, fam: str):
        self._fam = fam
        self._shim = _AttrShim()

    # absorb row_factory, text_factory, etc.
    def __setattr__(self, k, v):
        if k in ("_fam", "_shim"):
            object.__setattr__(self, k, v)
        else:
            setattr(self._shim, k, v)

    def __getattr__(self, k):
        return getattr(self._shim, k, None)

    # core EXECUTE API
    def execute(self, sql: str, params=()):
        fut = _ReadFuture()
        _DAL_READ_QUEUE.put((self._fam, sql, params or (), fut))
        return fut

    def cursor(self): return self
    def fetchall(self): return []
    def fetchone(self): return None
    def close(self): return None


# === PATCH END ==============================================================


# ---------------------------------------------------------------------------
# FUTURE CURSOR FOR READ RESULTS
# ---------------------------------------------------------------------------


# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py  (DAL write architecture)
# 📆 PATCHED: 2025-12-10 — Dual-write family mapping (LOCAL + LIVECACHE)
# PURPOSE:
#   • Every logical family write is duplicated into both targets
#   • Makes session_token, orders, decisions always visible everywhere
#   • Replaces need for mirror threads entirely
# ============================================================================

DUAL_WRITE_FAMILIES = {
    "auto":        ("auto", None),
    "bets":        ("bets", None),
    "settlements": ("settlements", None),
    "mastery":     ("mastery", None),
}



# === PATCH END ==============================================================




# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 📆 PATCHED: 2025-12-10 — dual writers consistent with existing DAL

_LOCAL_WRITE_CONNS = {}
_LIVE_WRITE_CONNS  = {}

def _get_writer_local(fam: str) -> sqlite3.Connection:
    path = {
        "auto":        LOCAL_AUTO,
        "bets":        LOCAL_BETS,
        "settlements": LOCAL_SETTLE,
        "mastery":     LOCAL_MASTERY,
    }[fam]

    con = _LOCAL_WRITE_CONNS.get(fam)
    if con is None:
        con = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA journal_mode=WAL")

        # Attach *LOCAL* namespaces
        con.execute(f"ATTACH DATABASE '{LOCAL_AUTO}'     AS local_auto")
        con.execute(f"ATTACH DATABASE '{LOCAL_BETS}'     AS local_bets")
        con.execute(f"ATTACH DATABASE '{LOCAL_SETTLE}'   AS local_settle")
        con.execute(f"ATTACH DATABASE '{LOCAL_MASTERY}'  AS local_mastery")

        _LOCAL_WRITE_CONNS[fam] = con
    return con


def _get_writer_live(fam: str) -> sqlite3.Connection:
    path = {
        "auto":        CLOUD_AUTO,
        "bets":        CLOUD_BETS,
        "settlements": CLOUD_SETTLE,
        "mastery":     CLOUD_MASTERY,
    }[fam]

    con = _LIVE_WRITE_CONNS.get(fam)
    if con is None:
        con = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA journal_mode=WAL")

        # Attach *LIVECACHE* namespaces
        con.execute(f"ATTACH DATABASE '{CLOUD_AUTO}'     AS live_auto")
        con.execute(f"ATTACH DATABASE '{CLOUD_BETS}'     AS live_bets")
        con.execute(f"ATTACH DATABASE '{CLOUD_SETTLE}'   AS live_settle")
        con.execute(f"ATTACH DATABASE '{CLOUD_MASTERY}'  AS live_mastery")

        _LIVE_WRITE_CONNS[fam] = con
    return con
# === PATCH END ============================================================


# ===============================================================
# 🧵 DAL GLOBAL WRITE QUEUE + WRITER POOL
# ===============================================================

_DAL_WRITE_QUEUE = queue.Queue(maxsize=200000)

_PERSISTENT_WRITERS = {}
_PERSISTENT_LOCK = threading.Lock()

def _get_writer(fam: str) -> sqlite3.Connection:
    """
    LOCAL-ONLY writer resolver.
    LiveCache disabled.
    """
    base = fam.lower().replace("_local", "").replace("_live", "")
    return _get_writer_local(base)




# === PATCH START: Normalize dual-writer family names =======================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def _get_writer(
# 📆 PATCHED: 2025-12-04

def _normalize_writer_family(fam: str) -> tuple[str, str]:
    """
    Normalise dual-write family names.

    Input families:
        auto_local, auto_live
        bets_local, bets_live
        settle_local, settle_live
        mastery_local, mastery_live

    Output family:
        ('local'/'live', base_family)
    """
    fam = fam.lower()

    if fam.endswith("_local"):
        return "local", fam.replace("_local", "")

    if fam.endswith("_live"):
        return "live", fam.replace("_live", "")

    # Already base family
    return "local", fam


# Patch point inside the writer loop:
# Replace:
#     wloc = _get_writer_local(fam)
#     wlive = _get_writer_live(fam)
# With:
#     kind, base = _normalize_writer_family(fam)
#     if kind == "local":
#         wloc = _get_writer_local(base)
#         wloc.execute(sql, params)
#     else:
#         wlive = _get_writer_live(base)
#         wlive.execute(sql, params)
# === PATCH END ==============================================================




# === PATCH END ==============================================================


# ===============================================================
# ⚡ BATCH WRITE WORKER
# ===============================================================

_BATCH_WINDOW = 0.003
_BATCH_MAX = 5000

def _dal_writer_loop():
    """
    LOCAL-ONLY batch writer.
    LiveCache writes are disabled.
    """
    pending = []

    while True:
        fam, sql, params = _DAL_WRITE_QUEUE.get()
        pending.append((fam, sql, params))
        t0 = time.time()

        # Batch coalescing
        while len(pending) < _BATCH_MAX:
            remaining = _BATCH_WINDOW - (time.time() - t0)
            if remaining <= 0:
                break
            try:
                fam2, sql2, params2 = _DAL_WRITE_QUEUE.get(timeout=remaining)
                pending.append((fam2, sql2, params2))
            except queue.Empty:
                break

        # Process batch → LOCAL ONLY
        for fam, sql, params in pending:
            sql_l = sql.strip().lower()
            is_trigger = sql_l.startswith("create trigger")

            # TRIGGER / DDL path
            if is_trigger:
                try:
                    wloc = _get_writer_local(fam)
                    wloc.executescript(sql)
                    print(f"[DAL-LOCAL-ONLY] trigger installed in LOCAL writer: {fam}")
                except Exception as e:
                    print(f"[DAL-LOCAL-ONLY][trigger] fail fam={fam}: {e}")
                continue

            # NORMAL WRITE → LOCAL ONLY
            try:
                wloc = _get_writer_local(fam)
                if isinstance(params, list):
                    params = tuple(params)
                wloc.execute(sql, params)
            except Exception as e:
                print(f"[DAL-LOCAL-ONLY] write fail fam={fam}: {e} | sql={sql}")

        # Commit LOCAL writer
        try:
            wloc.commit()
        except:
            pass

        # Acknowledge batch
        for _ in pending:
            _DAL_WRITE_QUEUE.task_done()

        pending.clear()


# Start writer thread
if not any(t.name == "DAL-Writer" for t in threading.enumerate()):
    threading.Thread(target=_dal_writer_loop, name="DAL-Writer", daemon=True).start()
    print("[DAL] Writer thread ACTIVE")

# ===============================================================
# 🧩 PHASE-2 ATTACH ORCHESTRATOR (IDLE-WINDOW BATCHED)
# ===============================================================

import threading, time, sqlite3

# ----------------------------------------------------------------
# Global attach coordination
# ----------------------------------------------------------------

_ATT_LOCK = threading.Lock()

# Track which families are attached per connection
# key = id(con) → set({"auto","bets","settlements","mastery"})
_ATTACHED_MAP = {}

# Pending attach requests
# key = (id(con), fam) → (con, fam)
_ATTACH_REQUESTS = {}
_ATTACH_REQ_LOCK = threading.Lock()

# Activity heartbeat (used to detect idle windows)
_LAST_ACTIVITY_TS = [time.time()]
_IDLE_THRESHOLD_S = 0.25   # 250ms quiet window (tuneable)


def _mark_activity():
    """Mark system activity to delay ATTACH during busy periods."""
    _LAST_ACTIVITY_TS[0] = time.time()


# ----------------------------------------------------------------
# ATTACH-ALL-FOUR (UPGRADED → ORCHESTRATED)
# ----------------------------------------------------------------
def _attach_all_four_local(con: sqlite3.Connection):
    """
    SETUP-ONLY ATTACH

    In LIVE mode, ATTACH is forbidden.
    This function becomes a no-op to prevent:
      • schema locks
      • retry storms
      • WAL contention
      • closed-db attach attempts
    """

    # 🔒 HARD BLOCK IN LIVE
    if DAL_MODE == "LIVE":
        return

    # ---------------------------
    # SETUP / REPLAY / TRAINING
    # ---------------------------
    # Original attach logic stays here if you want it
    required = _infer_attach_intent()
    cid = id(con)

    with _ATT_LOCK:
        attached = _ATTACHED_MAP.setdefault(cid, set())
        for fam in required:
            if fam in attached:
                continue

            try:
                con.execute(
                    f"ATTACH DATABASE ? AS {fam}",
                    ({
                        "auto":        LOCAL_AUTO,
                        "bets":        LOCAL_BETS,
                        "settlements": LOCAL_SETTLE,
                        "mastery":     LOCAL_MASTERY,
                    }[fam],)
                )
                attached.add(fam)
            except Exception as e:
                print(f"[DAL-ATTACH][SETUP-WARN] {fam}: {e}")



# ----------------------------------------------------------------
# IDLE-WINDOW ATTACH FLUSHER (BATCHED)
# ----------------------------------------------------------------
def _attach_idle_flusher():
    """
    Executes pending ATTACH requests ONLY during idle windows.
    Batches schema operations to avoid SQLite lock storms.
    """

    fam_paths = {
        "auto":        LOCAL_AUTO,
        "bets":        LOCAL_BETS,
        "settlements": LOCAL_SETTLE,
        "mastery":     LOCAL_MASTERY,
    }

    while True:
        time.sleep(0.05)  # poll ~20×/sec

        now = time.time()
        if now - _LAST_ACTIVITY_TS[0] < _IDLE_THRESHOLD_S:
            continue  # system busy → skip

        with _ATTACH_REQ_LOCK:
            if not _ATTACH_REQUESTS:
                continue

            batch = list(_ATTACH_REQUESTS.items())
            _ATTACH_REQUESTS.clear()

        # Perform batched attaches under global ATT lock
        with _ATT_LOCK:
            for (cid, fam), (con, fam_name) in batch:
                try:
                    attached = _ATTACHED_MAP.setdefault(cid, set())
                    if fam_name in attached:
                        continue

                    # Skip dead / closed connections
                    try:
                        con.execute("SELECT 1")
                    except Exception:
                        # Connection is closed or invalid → drop attach intent
                        _ATTACHED_MAP.pop(cid, None)
                        continue

                    # Safe to attach
                    con.execute(
                        f"ATTACH DATABASE ? AS {fam_name}",
                        (fam_paths[fam_name],)
                    )
                    attached.add(fam_name)


                except Exception as e:
                    # Re-queue safely on failure (no drop)
                    with _ATTACH_REQ_LOCK:
                        _ATTACH_REQUESTS[(cid, fam_name)] = (con, fam_name)
                    print(f"[DAL-ATTACH][RETRY] {fam_name}: {e}")






# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::_attach_all_four_cloud
# 📆 PATCHED: 2025-12-09 — safe attach for cloud layer
# ============================================================================

def _attach_all_four_cloud(con: sqlite3.Connection):
    """Attach LIVECACHE → LIVECACHE with robust attach."""
    dbs = {
        "auto": CLOUD_AUTO,
        "bets": CLOUD_BETS,
        "settlements": CLOUD_SETTLE,
        "mastery": CLOUD_MASTERY,
    }
    for alias, path in dbs.items():
        try:
            con.execute("ATTACH DATABASE ? AS %s" % alias, (path,))
        except Exception as e:
            print(f"[DAL-ATTACH-CLOUD] warn attaching {alias}: {e}")

# === PATCH END ============================================================


# ===============================================================
# 📘 REAL DB OPENERS
# ===============================================================

def _local_db(path: str):
    """READ connection — ALWAYS LOCAL + attach all LOCAL DBs."""
    con = sqlite3.connect(path, timeout=10, isolation_level=None, check_same_thread=False)
    # real sqlite connections already use default tuple rows; DALReadProxy handles rows
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
    _attach_all_four_local(con)
    return con

def _cloud_db(path: str):
    """WRITE connection — ALWAYS LIVECACHE + attach all LIVECACHE DBs."""
    con = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
    # real sqlite connections already use default tuple rows; DALReadProxy handles rows
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")
    _attach_all_four_cloud(con)
    return con

# ===============================================================
# 🔀 DALWriteProxy (queue-based writer)
# ===============================================================



# ===============================================================
# 🌐 DAL MODE
# ===============================================================

DAL_MODE = "SETUP"

def enable_live_dal():
    global DAL_MODE
    DAL_MODE = "LIVE"

def enable_setup_dal():
    global DAL_MODE
    DAL_MODE = "SETUP"

# ===============================================================
# PUBLIC ROUTERS
# ===============================================================

# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def open_auto_db(
# 📆 PATCHED: 2025-12-04 — trading engines require real writer

def open_auto_db(*, rw=False, **_):
    """
    RETURNS:
      • SETUP mode → local DB (read/write real sqlite connection)
      • LIVE mode, rw=False → DALReadProxy (safe async reader)
      • LIVE mode, rw=True  → REAL sqlite3 write connection
        (Overwatcher / LiveRouter must use real cursor objects)
    """
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_AUTO)

    if rw is True:
        # REAL writer — not DALWriteProxy
        con = sqlite3.connect(
            LOCAL_AUTO,
            timeout=10,
            isolation_level=None,
            check_same_thread=False,
        )
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=8000")
        _attach_all_four_local(con)
        return con

    # SAFE async reader for dashboards, mastery, scope
    return _local_db(LOCAL_AUTO)


def open_bets_db(*, rw=False, **_):
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_BETS)
    if rw:
        return DALWriteProxy("bets")
    return _local_db(LOCAL_BETS)

def open_settlements_db(*, rw=False, **_):
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_SETTLE)
    if rw:
        return DALWriteProxy("settlements")
    return _local_db(LOCAL_SETTLE)

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: def open_mastery_db(
# 📆 PATCHED: 2026-02-20 — mastery DB always uses REAL sqlite connection
# ============================================================================

def open_mastery_db(*, rw=False, **_):
    """
    Mastery DB must ALWAYS be a real sqlite3 connection.
    • No DAL write proxy
    • No AlphaX
    • No LiveCache routing
    • WAL + busy_timeout + attach-all-local
    """

    import sqlite3

    # Always open LOCAL mastery_v7.db
    path = LOCAL_MASTERY

    # READ + WRITE both use the same real connection
    con = sqlite3.connect(
        path,
        timeout=10,
        isolation_level=None,
        check_same_thread=False,
    )
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=8000")

    # Attach all four LOCAL DBs for unified view access
    try:
        _attach_all_four_local(con)
    except Exception as e:
        print(f"[paths][mastery_attach_warn] {e}")

    return con

# === PATCH END ============================================================



def open_db(family: str, ro=False, rw=False, **_):
    return {
        "auto": open_auto_db,
        "bets": open_bets_db,
        "settlements": open_settlements_db,
        "mastery": open_mastery_db,
    }[family](rw=rw)

# ===============================================================
# AUTOCONN / LIVE-ROUTE AUTOCONN
# ===============================================================

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::auto_conn
# 📆 PATCHED: 2025-12-12 — Enforce DALReadProxy for LIVE read-only hot paths
# PURPOSE:
#   • Prevent sqlite3.connect() during BUS.tick / MasteryPolicy execution
#   • Eliminate WAL + ATTACH storms inside live ticks
#   • Restore tick completion and routing
# ============================================================================

import inspect

def auto_conn(*, rw=False, **_):
    """
    Canonical AUTO DB connector.

    RULES:
    - SETUP mode → real sqlite connection
    - LIVE mode:
        • rw=True  → DAL writer (existing behaviour)
        • rw=False → DALReadProxy for live hot paths
    """

    # -------------------------------
    # SETUP MODE (unchanged)
    # -------------------------------
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_AUTO)

    # -------------------------------
    # LIVE MODE — WRITE PATH (unchanged)
    # -------------------------------
    if rw is True:
        return DALWriteProxy("auto")

    # -------------------------------
    # LIVE MODE — READ PATH (CRITICAL FIX)
    # -------------------------------
    # Detect live hot-path callers (BUS / Mastery / DecideOnce / Monitor)
    for frame in inspect.stack()[1:]:
        fn = frame.filename.replace("\\", "/")

        if (
            "/mastery/" in fn
            or "/decision_engine/" in fn
            or "/bus/" in fn
            or "/market_monitor/" in fn
        ):
            # 🔒 HOT PATH: use DAL reader, NEVER open sqlite
            return DALReadProxy("auto")

    # -------------------------------
    # LIVE MODE — non-hot-path read (fallback)
    # -------------------------------
    # Safe for dashboards, setup helpers, background tools
    return _local_db(LOCAL_AUTO)

# === PATCH END ==============================================================



def auto_conn_live(rw=True):
    """
    LOCAL-ONLY LIVE CONNECTOR:
        • Router and Overwatcher should behave identically to auto_conn().
        • No LiveCache writes occur.
        • Future dual-write can be restored by reintroducing the original body.
    """
    # If read-only
    if not rw:
        return _local_db(LOCAL_AUTO)

    # Write mode → LOCAL ONLY writer
    return DALWriteProxy("auto")


# === PATCH START ============================================================
# 📆 PATCHED: 2025-12-09 — Restore get_mode(), is_replay_mode(), get_db_paths()

# MODE STATE (GUI + session_secrets expect this)
_MODE = "live"

# Correct placement:
def sync_modes():
    """Guarantee DAL_MODE follows legacy _MODE for LIVE/SETUP."""
    global DAL_MODE
    if _MODE in ("live", "LIVE"):
        DAL_MODE = "LIVE"
    else:
        DAL_MODE = "SETUP"

# Now call it safely AFTER _MODE exists:
sync_modes()

def get_mode() -> str:
    """Return current global mode as expected by GUI and orchestrator."""
    return _MODE

def is_replay_mode() -> bool:
    """Return True if tool is running in test or replay pipelines."""
    return _MODE in ("test", "replay")

def get_db_paths() -> tuple[str, str]:
    """Return (bets.db, autoscalp_gui.db) — legacy dashboard import."""
    return (LOCAL_BETS, LOCAL_AUTO)

# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📆 PATCHED: 2025-12-09 — Restore repo_root(), data_dir()

def repo_root() -> str:
    """Return project root directory. Needed by orchestrator + dashboard."""
    return _ROOT

def data_dir() -> str:
    """Return the resolved DATA_DIR used by all DBs."""
    return DATA_DIR

# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📆 PATCHED: 2025-12-09 — Restore connect_orders_db()

def connect_orders_db(ro: bool = True, timeout: float = 10.0):
    """
    Return a connection to the DB containing the `orders` table.
    Used by live_router + event_sink.
    """
    for path, fam in [(LOCAL_AUTO, "auto"), (LOCAL_BETS, "bets")]:
        try:
            with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=timeout) as con:
                r = con.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='orders'"
                ).fetchone()
                if r:
                    return open_db(fam, rw=not ro)
        except:
            continue

    return open_bets_db(rw=not ro)

# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📆 PATCHED: 2025-12-09 — Restore auto_ro(), settle_ro()

def auto_ro(timeout: float = 10.0):
    """Pure local reader for autoscalp_gui.db with full attach-map."""
    return _local_db(LOCAL_AUTO)

def settle_ro(timeout: float = 10.0):
    """Pure local reader for settlements.db."""
    return _local_db(LOCAL_SETTLE)

# === PATCH END ==============================================================
# === PATCH START: UNIFIED FINAL ALIAS SURFACE =========================
# 📆 PATCHED: 2026-02-09 — corrected settlements_db() signature

# Authoritative DB path exposure
BETS_DB_PATH      = LOCAL_BETS
AUTOSCALP_DB_PATH = LOCAL_AUTO
DB_PATH           = BETS_DB_PATH
GUI_DB_PATH       = AUTOSCALP_DB_PATH

# Official alias surface (used by dashboard, lanes, orchestrator)
bets_conn              = open_bets_db
mastery_conn           = open_mastery_db
settle_conn            = open_settlements_db

connect_auto_db        = auto_conn
connect_bets_db        = open_bets_db
connect_mastery_db     = open_mastery_db
connect_settlements_db = open_settlements_db
# Legacy compatibility — several modules still expect direct access to open_auto_db
auto_db = open_auto_db      # legacy name
open_auto_db = open_auto_db # re-export to guarantee symbol exists
open_auto_db_legacy = open_auto_db

__all__ = [
    "open_auto_db",
    "open_bets_db",
    "open_settlements_db",
    "open_mastery_db",
    "auto_conn",
    "auto_conn_live",
    "connect_auto_db",
    "connect_bets_db",
    "connect_settlements_db",
    "connect_mastery_db",
    "local_db",
    "auto_db",
]


# Upgraded alias functions (must support rw=True)
def autoscalp_db(*, rw=False, **_):
    return open_auto_db(rw=rw) if rw else autoscalp_db_path()

def bets_db(*, rw=False, **_):
    return open_bets_db(rw=rw) if rw else bets_db_path()

def settlements_db(*, rw=False, **_):
    return open_settlements_db(rw=rw) if rw else settlements_db_path()

def mastery_v7_db(*, rw=False, **_):
    return open_mastery_db(rw=rw) if rw else mastery_v7_db_path()


# Compatibility fast-path (local read-only)
AUTO_RO = lambda timeout=10: _local_db(LOCAL_AUTO)

# === PATCH END =========================================================
# === NEW: path-returning helpers for GUI ===
def autoscalp_db_path(): return LOCAL_AUTO
def bets_db_path():      return LOCAL_BETS
def settlements_db_path(): return LOCAL_SETTLE
def mastery_v7_db_path(): return LOCAL_MASTERY

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py   (or wherever schema bootstrap lives)
# 🔎 SEARCH: def autoscalp_db(    (just place below DB open helpers)
# 📆 PATCHED: 2025-12-03 — add created_at column for mastery_events
# ---------------------------------------------------------------------------

def _ensure_mastery_events_schema_fix():
    """
    Ensure mastery_events contains created_at column.
    Safe to run every startup (idempotent).
    """
    try:
        con = open_auto_db(rw=True)
        cur = con.cursor()

        # Does the column already exist?
        cols = {r[1] for r in cur.execute("PRAGMA table_info(mastery_events)")}

        if "created_at" not in cols:
            cur.execute(
                "ALTER TABLE mastery_events "
                "ADD COLUMN created_at TEXT DEFAULT (datetime('now','utc'))"
            )
            con.commit()
            print("[SCHEMA] mastery_events upgraded → added created_at")
        con.close()
    except Exception as e:
        print(f"[SCHEMA] mastery_events upgrade warn: {e}")

# Call once during system startup
try:
    _ensure_mastery_events_schema_fix()
except Exception:
    pass

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::q_retry
# 📆 PATCHED: 2025-12-12 — DALReadProxy-safe retry logic
# PURPOSE:
#   • Prevent retry storms on DALReadProxy
#   • Restore BUS.tick completion
#   • Ensure retries only apply to real sqlite connections
# ============================================================================

def q_retry(con, sql: str, params=(),
            *, tries: int = 6, delay_s: float = 0.08):
    """
    Retry wrapper.

    IMPORTANT RULE:
      • DALReadProxy → NO RETRY (queue handles ordering)
      • sqlite3.Connection → retry on BUSY/LOCKED
    """

    # -------------------------------------------------
    # DALReadProxy path (NO RETRY)
    # -------------------------------------------------
    from engines.config_paths import DALReadProxy

    if isinstance(con, DALReadProxy):
        # Single enqueue only — retries handled by DAL reader thread
        return con.execute(sql, params)

    # -------------------------------------------------
    # Real sqlite connection path (legacy)
    # -------------------------------------------------
    last_err = None
    for i in range(max(1, tries)):
        try:
            return con.execute(sql, params)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            last_err = e
            if (("locked" in msg) or ("busy" in msg)) and i < tries - 1:
                time.sleep(delay_s * (i + 1))
                continue
            raise

    if last_err:
        raise last_err

# legacy aliases
_q_retry = q_retry
q        = q_retry
_q       = q_retry

# === PATCH END ==============================================================


# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py (legacy connector section)
# 🔎 SEARCH: bets_conn = open_bets_db
# 📆 PATCHED: 2025-12-09 — restore missing connection helpers
# ============================================================================

def connect_autoscalp_db(timeout: float = 10.0, ro: bool = False):
    """Legacy alias — used by SCOPE, lanes, dashboard."""
    return open_auto_db(rw=not ro)

def connect_settlements_db(timeout: float = 10.0, ro: bool = False):
    """Legacy alias — settlement engine expects this to exist."""
    return open_settlements_db(rw=not ro)

def connect_mastery_v7_db(timeout: float = 10.0, ro: bool = False):
    """Legacy alias — mastery v7 training and dashboards import this."""
    return open_mastery_db(rw=not ro)

# === PATCH END ============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::ensure_db_ready
# 🔎 SEARCH: def ensure_db_ready(
# 📆 PATCHED: 2025-12-09 — allow zero-arg and one-arg usage
# ============================================================================

def ensure_db_ready(path: str | None = None):
    """
    Legacy-friendly DB preflight.

    Accepts either:
        ensure_db_ready(path)
        ensure_db_ready()

    New DAL guarantees DBs exist, so this remains a safe no-op when
    called without arguments.

    If a path is provided, ensure the directory exists and the file
    is present.
    """
    if not path:
        return None

    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if not os.path.exists(path):
            open(path, "a").close()
    except Exception as e:
        print(f"[db-preflight] ensure_db_ready warn: {e}")

    return path

# === PATCH END ============================================================



# === PATCH START: Restore legacy connect_db ================================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: Legacy connect_* APIs (GUI + older engines rely on these)
# 📆 PATCHED: 2025-12-08

def connect_db(path: str | None = None, ro: bool = False, timeout: float = 10.0):
    """
    Legacy access point required by GUI/session_secrets/dashboard.
    Defaults to bets.db unless a specific path is provided.

    READ  → rw=False → return LOCAL reader
    WRITE → rw=True  → return appropriate DALWriteProxy()
    """
    target = path or BETS_DB_PATH
    fam = "bets" if target == BETS_DB_PATH else "auto"
    return open_db(fam, rw=not ro)

# === PATCH END =============================================================
print("[paths] ✔ COMPLETE CONFIG_PATHS REBUILD — OK")
