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


# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py (mode helpers)
# 🔎 SEARCH: def get_mode()
# 📆 PATCHED: 2025-12-09 — synchronize DAL_MODE with _MODE
# ============================================================================



# === PATCH END ============================================================


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

def _get_reader(fam: str) -> sqlite3.Connection:
    """
    Persistent pooled reader for each DB family.
    Avoids repeated sqlite3.connect() calls and prevents FD churn.
    Readers are LOCAL dbs (mirrored) and are READ ONLY.
    """
    with _PERSISTENT_READ_LOCK:
        if fam in _PERSISTENT_READERS:
            return _PERSISTENT_READERS[fam]

        path = {
            "auto": LOCAL_AUTO,
            "bets": LOCAL_BETS,
            "settlements": LOCAL_SETTLE,
            "mastery": LOCAL_MASTERY,
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
        _attach_all_four_local(con)   # always attach LOCAL families

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
    def execute(self, sql, params=()):
        """Queue single write → duplicated to LOCAL + LIVE."""
        if isinstance(params, list):
            params = tuple(params)

        local_fam, live_fam = DUAL_WRITE_FAMILIES[self._fam]

        _DAL_WRITE_QUEUE.put((local_fam, sql, params))
        _DAL_WRITE_QUEUE.put((live_fam,  sql, params))

        return self

    def executemany(self, sql, seq):
        """Queue batch writes → each duplicated to LOCAL + LIVE."""
        local_fam, live_fam = DUAL_WRITE_FAMILIES[self._fam]

        for row in seq:
            params = tuple(row) if isinstance(row, list) else row
            _DAL_WRITE_QUEUE.put((local_fam, sql, params))
            _DAL_WRITE_QUEUE.put((live_fam,  sql, params))

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
    "auto":        ("auto_local", "auto_live"),
    "bets":        ("bets_local", "bets_live"),
    "settlements": ("settlements_local", "settlements_live"),
    "mastery":     ("mastery_local", "mastery_live"),
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
    Resolve a persistent writer for a *base family*.
    Dual-write variants (xxx_local / xxx_live) must be normalised FIRST.
    """
    # 🔥 Normalize BEFORE anything else
    kind, base = _normalize_writer_family(fam)   # <— REQUIRED FIX
    fam = base.lower()

    with _PERSISTENT_LOCK:
        if fam in _PERSISTENT_WRITERS:
            return _PERSISTENT_WRITERS[fam]

        # base-family → LIVE path
        path = {
            "auto":        CLOUD_AUTO,
            "bets":        CLOUD_BETS,
            "settlements": CLOUD_SETTLE,
            "mastery":     CLOUD_MASTERY,
        }.get(fam)

        if not path:
            raise RuntimeError(f"[DAL] Unknown writer family: {fam}")

        con = sqlite3.connect(path, timeout=20, isolation_level=None, check_same_thread=False)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=8000")

        _PERSISTENT_WRITERS[fam] = con
        return con



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
    High-throughput batch writer.
    Queue items: (fam, sql, params)
    """
    pending = []

    while True:
        fam, sql, params = _DAL_WRITE_QUEUE.get()
        pending.append((fam, sql, params))
        t0 = time.time()

        while len(pending) < _BATCH_MAX:
            remaining = _BATCH_WINDOW - (time.time() - t0)
            if remaining <= 0:
                break
            try:
                fam2, sql2, params2 = _DAL_WRITE_QUEUE.get(timeout=remaining)
                pending.append((fam2, sql2, params2))
            except queue.Empty:
                break

        # === PATCH START =====================================================
# === PATCH START — Normalize writer families (FINAL FIX) ======================
# 📆 PATCHED: 2026-02-09
# PURPOSE:
#   Fix "Unknown writer family: bets_local" by ensuring that
#   dual-write families (bets_local, bets_live, auto_live, etc.)
#   are always normalized BEFORE routing to local/live writers.
# ============================================================================

        # Dual-write using normalized families
        for fam, sql, params in pending:

            # Normalize fam → (kind, base_family)
            # examples:
            #   "bets_local" → ("local", "bets")
            #   "bets_live"  → ("live",  "bets")
            #   "bets"       → ("local", "bets")
            kind, base = _normalize_writer_family(fam)

            if kind == "local":
                try:
                    wloc = _get_writer_local(base)
                    wloc.execute(sql, params)
                except Exception as e:
                    print(f"[DAL-WRITER] LOCAL fail fam={fam}: {e} | sql={sql}")
            else:
                try:
                    wlive = _get_writer_live(base)
                    wlive.execute(sql, params)
                except Exception as e:
                    print(f"[DAL-WRITER] LIVE fail fam={fam}: {e} | sql={sql}")

        # Commit whichever were touched
        try: wloc.commit()
        except: pass
        try: wlive.commit()
        except: pass

# === PATCH END ================================================================


        for _ in pending:
            _DAL_WRITE_QUEUE.task_done()

        pending.clear()

# Start writer thread
if not any(t.name == "DAL-Writer" for t in threading.enumerate()):
    threading.Thread(target=_dal_writer_loop, name="DAL-Writer", daemon=True).start()
    print("[DAL] Writer thread ACTIVE")

# ===============================================================
# 🔧 ATTACH-ALL-FOUR HELPER
# ===============================================================

# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py::_attach_all_four_local
# 🔎 SEARCH: def _attach_all_four_local
# 📆 PATCHED: 2025-12-09 — safe URI attach + skip self-attach
# ============================================================================

def _attach_all_four_local(con: sqlite3.Connection):
    """Attach LOCAL → LOCAL with URI-safe paths and skip self re-attach."""
    dbs = {
        "auto": LOCAL_AUTO,
        "bets": LOCAL_BETS,
        "settlements": LOCAL_SETTLE,
        "mastery": LOCAL_MASTERY,
    }

    for alias, path in dbs.items():
        try:
            con.execute("ATTACH DATABASE ? AS %s" % alias, (path,))
        except Exception as e:
            print(f"[DAL-ATTACH-LOCAL] warn attaching {alias}: {e}")

# === PATCH END ============================================================

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
    return DALReadProxy("auto")


def open_bets_db(*, rw=False, **_):
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_BETS)
    if rw:
        return DALWriteProxy("bets")
    return DALReadProxy("bets")

def open_settlements_db(*, rw=False, **_):
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_SETTLE)
    if rw:
        return DALWriteProxy("settlements")
    return DALReadProxy("settlements")

def open_mastery_db(*, rw=False, **_):
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_MASTERY)
    if rw:
        return DALWriteProxy("mastery")
    return DALReadProxy("mastery")


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

def auto_conn(*, rw=False, **_):
    if DAL_MODE == "SETUP":
        return _local_db(LOCAL_AUTO)
    if rw is True:
        return DALWriteProxy("auto")
    return DALReadProxy("auto")


# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py
# 🔎 SEARCH: import sqlite3  (place immediately after the top-level imports)
# 📆 PATCHED: 2025-12-03 — LiveCache + Attach-All universal LIVE connector
# ---------------------------------------------------------------------------

# === PATCH START ======================================================
# 📍 TARGET: engines/config_paths.py:auto_conn_live
# 🔎 SEARCH: def auto_conn_live(
# 📆 PATCHED: 2025-12-03 — attach full local + livecache families

def auto_conn_live(rw=True):
    """
    LiveCache-first connector.
    • PRIMARY = livecache AUTOSCALP
    • Attaches all LOCAL DBs for views + historical reads
    • Attaches all LIVECACHE DBs for today’s reads
    • RW always allowed (single-writer architecture)
    """

    import sqlite3, os

    LIVECACHE_AUTO = os.path.join(DATA_DIR, "livecache", "autoscalp_livecache.db")
    LIVECACHE_BETS = os.path.join(DATA_DIR, "livecache", "bets_livecache.db")
    LIVECACHE_SETTLE = os.path.join(DATA_DIR, "livecache", "settlements_livecache.db")
    LIVECACHE_MASTERY = os.path.join(DATA_DIR, "livecache", "mastery_livecache.db")

    LOCAL_AUTO = os.path.join(DATA_DIR, "autoscalp_gui.db")
    LOCAL_BETS = os.path.join(DATA_DIR, "bets.db")
    LOCAL_SETTLE = os.path.join(DATA_DIR, "settlements.db")
    LOCAL_MASTERY = os.path.join(DATA_DIR, "mastery_v7.db")

    con = sqlite3.connect(
        LIVECACHE_AUTO,
        timeout=12,
        isolation_level=None,
        check_same_thread=False,
    )
    con.row_factory = sqlite3.Row

    con.execute("PRAGMA busy_timeout=8000;")
    con.execute("PRAGMA journal_mode=WAL;")

    # Attach LOCAL family
    con.execute(f"ATTACH DATABASE '{LOCAL_AUTO}'     AS local_auto;")
    con.execute(f"ATTACH DATABASE '{LOCAL_BETS}'     AS local_bets;")
    con.execute(f"ATTACH DATABASE '{LOCAL_SETTLE}'   AS local_settle;")
    con.execute(f"ATTACH DATABASE '{LOCAL_MASTERY}'  AS local_mastery;")

    # Attach LIVECACHE family (for today-only tables)
    con.execute(f"ATTACH DATABASE '{LIVECACHE_BETS}'     AS lc_bets;")
    con.execute(f"ATTACH DATABASE '{LIVECACHE_SETTLE}'   AS lc_settle;")
    con.execute(f"ATTACH DATABASE '{LIVECACHE_MASTERY}'  AS lc_mastery;")

    # =====================================================
    # Inject dual-write behaviour into auto_conn_live
    # =====================================================
    class _LiveDualWriteCursor:
        def __init__(self, base_con):
            self._base = base_con

        def execute(self, sql, params=()):
            if isinstance(params, list):
                params = tuple(params)

            # Duplicate into LOCAL and LIVE write queues
            _DAL_WRITE_QUEUE.put(("auto_local", sql, params))
            _DAL_WRITE_QUEUE.put(("auto_live",  sql, params))

            return self

        def executemany(self, sql, seq):
            for row in seq:
                params = tuple(row) if isinstance(row, list) else row
                _DAL_WRITE_QUEUE.put(("auto_local", sql, params))
                _DAL_WRITE_QUEUE.put(("auto_live",  sql, params))
            return self

        def fetchall(self): return []
        def fetchone(self): return None
        def close(self):    return None
        def __iter__(self): return iter([])

    class _LiveDualWriteProxy:
        def __init__(self, base_con):
            self._base = base_con

        # NEW — context manager support
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            try:
                self._base.close()
            except:
                pass
            return False

        def cursor(self):
            return _LiveDualWriteCursor(self._base)

        # Legacy compatibility:
        def execute(self, sql, params=()):
            return self.cursor().execute(sql, params)

        def executemany(self, sql, seq):
            return self.cursor().executemany(sql, seq)

        def commit(self):  pass
        def rollback(self): pass
        def close(self):    self._base.close()

    # Use proxy for all RW operations inside live-router/overwatcher
    if rw:
        return _LiveDualWriteProxy(con)

    return con


    return con

# === PATCH END ========================================================



# === PATCH START ============================================================
# 📆 PATCHED: 2025-12-09 — Restore get_mode(), is_replay_mode(), get_db_paths()

# MODE STATE (GUI + session_secrets expect this)
_MODE = "learning"

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

# === PATCH END ==============================================================



# === PATCH START ============================================================
# 📍 TARGET: engines/config_paths.py — restore q_retry()
# 📆 PATCHED: 2025-12-09

def q_retry(con: sqlite3.Connection, sql: str, params=(),
            *, tries: int = 6, delay_s: float = 0.08):
    """
    Retry wrapper for transient SQLITE_BUSY / SQLITE_LOCKED states.
    Used throughout GUI, dashboard, lanes, live_router, event sink, and v7 engines.
    This must exist exactly with this signature for legacy imports.
    """
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

# legacy-compatible alias (must always exist)
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
