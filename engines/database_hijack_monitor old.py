#!/usr/bin/env python3
# database_hijack_monitor.py — safe, synchronous DB writer (no background queue)
from __future__ import annotations

import os
import sys
import sqlite3
import logging
import time
import tempfile
from datetime import datetime
from typing import Iterable, Any, Optional

import os, sqlite3, inspect, threading
DISABLE_HIJACK = os.getenv("AUTOSCALP_DISABLE_HIJACK", "").lower() in ("1","true","yes")
_ORIG_CONNECT = sqlite3.connect


# ───────────────────────── repo + DB path ─────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))      # .../engines
_ROOT = os.path.dirname(_HERE)                          # repo root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)



def _ensure_parent(path: str) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass




import os, time, queue, threading, logging, tempfile, sqlite3
from typing import Iterable, Optional, Dict, Tuple
try:
    from engines.sqlite_shim import open_db
except Exception:
    from engines.config_paths import open_db


# ── paths (lazy import inside helpers to avoid import loops) ───────────
def _bets_db() -> str:
    try:
        from engines import config_paths as cp
        return cp.bets_db()
    except Exception:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        return os.path.join(root, "data", "bets.db")

def _auto_db() -> str:
    try:
        from engines import config_paths as cp
        return cp.autoscalp_db()
    except Exception:
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
        return os.path.join(root, "data", "autoscalp_gui.db")


def _wrapped_connect(*args, **kwargs):
    # Optional: bypass for DAL calls even if hijack is enabled
    try:
        mods = [fr.frame.f_globals.get("__name__", "") for fr in inspect.stack()]
        if any(m.startswith(("engines.config_paths", "engines.sqlite_shim")) for m in mods):
            return _ORIG_CONNECT(*args, **kwargs)
    except Exception:
        pass
    # … your existing queue/trace logic can run here …
    return _ORIG_CONNECT(*args, **kwargs)

# start writers on import (idempotent; respects env)
# -- init (once, after all defs) --
try:
    if not DISABLE_HIJACK:
        install_global_sqlite_hijack()
        launch_db_writer()
    else:
        print("[hijack] disabled via AUTOSCALP_DISABLE_HIJACK=1 — DAL controls sqlite3.connect()")
except Exception as _e:
    try:
        DLOG.error(f"hijack init warn: {_e}")
    except Exception:
        print(f"[hijack] init warn: {_e}")

# ── logging ────────────────────────────────────────────────────────────
def _choose_log_dir() -> str:
    root = os.path.abspath(os.environ.get("ANALYTICS_BETA_ROOT", os.getcwd()))
    for cand in (
        os.path.join(root, "logs"),
        os.path.join(os.path.expanduser("~"), "Library", "Logs", "analytics_beta"),
        os.path.join(os.path.expanduser("~"), ".analytics_beta", "logs"),
        os.path.join(tempfile.gettempdir(), "analytics_beta_logs"),
    ):
        try:
            os.makedirs(cand, exist_ok=True)
            return cand
        except Exception:
            continue
    return tempfile.gettempdir()

LOG_DIR = _choose_log_dir()
def _mk_logger(name: str, file: str, level=logging.INFO, fmt="%(asctime)s - %(levelname)s - %(message)s") -> logging.Logger:
    log = logging.getLogger(name); log.setLevel(level)
    try:
        h = logging.FileHandler(os.path.join(LOG_DIR, file))
    except Exception:
        h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(fmt))
    log.handlers.clear(); log.addHandler(h); log.propagate = False
    return log

ALOG = _mk_logger("DBHijack", "hijack_activity.log", logging.INFO, "%(asctime)s - %(message)s")
DLOG = _mk_logger("DBHijackDebug", "hijack_debug.log", logging.INFO)

# ── per‑DB writer pools ────────────────────────────────────────────────
_DB_QUEUES: Dict[str, "queue.PriorityQueue[Tuple[int,float,str,Tuple]]"] = {}
_DB_THREADS: Dict[str, threading.Thread] = {}
_DB_STOPS: Dict[str, threading.Event] = {}

def _ensure_writer(db_path: str) -> None:
    if db_path in _DB_THREADS and _DB_THREADS[db_path].is_alive():
        return
    _DB_QUEUES.setdefault(db_path, queue.PriorityQueue(maxsize=5000))
    _DB_STOPS.setdefault(db_path, threading.Event())
    stop = _DB_STOPS[db_path]
    q = _DB_QUEUES[db_path]

    def loop():
        con = open_db(db_path, ro=False, wal=True)
        ALOG.info(f"[DBQ] writer started → {db_path}")
        while not stop.is_set():
            try:
                priority, ts, sql, params = q.get(timeout=0.5)
            except queue.Empty:
                continue
            for attempt in range(4):
                try:
                    con.execute("PRAGMA busy_timeout=12000")
                    con.execute(sql, params)
                    con.commit()
                    break
                except sqlite3.OperationalError as e:
                    msg = str(e).lower()
                    if ("locked" in msg or "busy" in msg) and attempt < 3:
                        time.sleep(0.05 * (attempt + 1))
                        continue
                    DLOG.error(f"[DBQ:{os.path.basename(db_path)}] write error: {e} | {sql[:120]}")
                    break
                except Exception as e:
                    DLOG.error(f"[DBQ:{os.path.basename(db_path)}] unexpected: {e} | {sql[:120]}")
                    break
            q.task_done()
        try: con.close()
        except Exception: pass
        ALOG.info(f"[DBQ] writer stopped ← {db_path}")

    th = threading.Thread(target=loop, name=f"DBQ:{os.path.basename(db_path)}", daemon=True)
    th.start()
    _DB_THREADS[db_path] = th

def launch_db_writer() -> None:
    if DISABLE_HIJACK:
        try:
            ALOG.info("[DBQ] hijack disabled; writer not started")
        except Exception:
            pass
        return
    _ensure_writer(_bets_db())
    _ensure_writer(_auto_db())

def _q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


# ── routing helpers ────────────────────────────────────────────────────
_AUTOSCALP_HINTS = (
    "inbound_", "oc_series", "odds_current", "markets_schedule",
    "dashboard_", "runners", "market_data", "orders", "runs", "events"
)

def _target_db_for_sql(sql: str, explicit: Optional[str] = None) -> str:
    if explicit:
        return explicit
    s = (sql or "").lower()
    return _auto_db() if any(h in s for h in _AUTOSCALP_HINTS) else _bets_db()

def _priority_for_sql(sql: str) -> int:
    s = (sql or "").lower()
    if any(k in s for k in ("orders", "bets", "hedge", "ladder", "decisions")):
        return 1           # highest: bet/order related
    if any(k in s for k in ("odds_current", "inbound_", "oc_series", "markets_schedule")):
        return 3           # mid: odds/schedule cache
    return 5               # low: housekeeping / everything else

# ── public API used by the rest of the app ─────────────────────────────
def enqueue_write(sql: str, params: Optional[Iterable] = None, priority: int = 5, *, db: Optional[str] = None) -> None:
    db_path = _target_db_for_sql(sql, db)
    if DISABLE_HIJACK:
        # Direct, synchronous write through the DAL (no queue/threads)
        con = open_db(db_path, ro=False, wal=True)
        try:
            con.execute("PRAGMA busy_timeout=12000")
            con.execute(str(sql), tuple(params or ()))
            con.commit()
        finally:
            try: con.close()
            except Exception: pass
        return

    _ensure_writer(db_path)
    item = (min(max(int(priority), 1), 9), time.time(), str(sql), tuple(params or ()))
    _DB_QUEUES[db_path].put_nowait(item)



def enqueue_read(sql: str, params: Optional[Iterable] = None, *, db: Optional[str] = None):
    """Direct read with sane pragmas + retries. Never uses the writer thread."""
    db_path = _target_db_for_sql(sql, db)
    con = open_db(db_path, ro=True, wal=True)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        for attempt in range(3):
            try:
                cur = con.execute(sql, tuple(params or ()))
                return cur.fetchall()
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                # 🔄 retry on locked OR unable to open database file
                if (("locked" in msg) or ("unable to open database file" in msg)) and attempt < 2:
                    time.sleep(0.06 * (attempt + 1))
                    continue
                print(f"[dbhijack] read skip: {e}")
                return []  # <-- safely skip this cycle
    finally:
        try:
            con.close()
        except Exception:
            pass
    return []


# ── optional: global sqlite3.connect hijack (sets pragmas everywhere) ─

# === PATCH 5 START ============================================
# 📆 PATCHED: 2025-11-18
# Hijack now ONLY intercepts SQL and forwards to AlphaX.
# It does NOT open a connection. It does NOT choose a DB.
# It sends metadata to AlphaX → WAL does everything else.

class _HijackedConnection(sqlite3.Connection):
    """
    Hijack layer:
        1) capture SQL + params
        2) forward to AlphaX scheduler
        3) AlphaX returns: { family, priority, rw }
        4) Then forward to WAL (correct open_*_db call)
    """

    def execute(self, sql, params=()):
        from engines.alphax_gateway import alphax_schedule
        meta = alphax_schedule(sql, params)

        fam     = meta["family"]
        prio    = meta["priority"]
        rw      = meta["rw"]

        # WAL does routing
        from engines.config_paths import (
            open_auto_db,
            open_bets_db,
            open_mastery_db,
            open_settlements_db,
        )

        WAL = {
            "auto":        open_auto_db,
            "bets":        open_bets_db,
            "mastery":     open_mastery_db,
            "settlements": open_settlements_db,
        }[fam]

        # high-priority → immediate
        if prio == 1:
            con = WAL(rw=rw, ro=(not rw))
            return con.execute(sql, params)

        # otherwise enqueue through hijack queue
        enqueue_write(sql, params, priority=prio)
        return super().cursor()
# === PATCH 5 END ==============================================

# === PATCH 6 START ============================================
from engines.alphax_gateway import alphax_entrypoint

def install_global_sqlite_hijack():
    """
    Global intercept:
        sqlite3.connect() → AlphaX entrypoint
    """
    if getattr(sqlite3, "_autosc_hijacked", False):
        return

    def _connect(db_path, *args, **kwargs):
        sql = kwargs.pop("sql", None)
        return alphax_entrypoint(db_path, sql)

    sqlite3.connect = _connect
    sqlite3._autosc_hijacked = True
# === PATCH 6 END ==============================================

# === PATCH END ======================================================


# convenience alias used by some legacy code
def db_write(sql: str, params: Iterable = (), *, priority: int = 5) -> None:
    enqueue_write(sql, params, priority=priority)

# start writers on import (idempotent)
try:
    install_global_sqlite_hijack()
    launch_db_writer()
except Exception as _e:
    DLOG.error(f"hijack init warn: {_e}")


# backward-compat alias some code calls
def hijack_db_write(sql: str, params: Iterable[Any] = (), priority: int = 5) -> None:
    db_write(sql, params, priority=priority)
