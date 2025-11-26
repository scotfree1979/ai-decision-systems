#!/usr/bin/env python3

"""
GUI phase-runner: build in micro-steps with hard gates.
Steps implemented:
  1) Set credentials (token via UI; app key from daily_config.json or env)
  2) Ensure DB + start writer + seed bets (runners only) for today/tomorrow
  3) Seed anchor odds (OC0) into bets and oc_series
  4) Start OC1 polling loop (records into oc_series)

After each step: prints "READY: next step" to terminal.
"""



import os, sys, sqlite3
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
if _root not in sys.path:
    sys.path.insert(0, _root)

# --- STEP TAGGING FOR AUDIT ------------------------------------
try:
    import import_audit
    set_step = import_audit.set_step
except Exception:
    def set_step(*args, **kwargs):
        pass
# ---------------------------------------------------------------


# === TRACE: FULL sqlite3.connect CALLER AUDIT (Pre-DAL Writer) ==================
import sqlite3, inspect, time, os

try:
    if not hasattr(sqlite3, "_pre_dal_trace_active"):

        ORIGINAL_CONNECT = sqlite3.connect
        TRACE_PATH = "/tmp/sql_connect_trace.csv"

        # prepare CSV
        if not os.path.exists(TRACE_PATH):
            with open(TRACE_PATH, "w") as f:
                f.write("timestamp,db_path,caller_file,caller_line\n")

        def traced_connect(db_path, *args, **kwargs):
            # caller info
            frm = inspect.stack()[1]
            caller_file = frm.filename
            caller_line = frm.lineno
            ts = time.time()

            # append log
            try:
                with open(TRACE_PATH, "a") as f:
                    f.write(f"{ts},{db_path},{caller_file},{caller_line}\n")
            except Exception:
                pass

            # execute real connect
            return ORIGINAL_CONNECT(db_path, *args, **kwargs)

        sqlite3.connect = traced_connect
        sqlite3._pre_dal_trace_active = True

        print(f"[TRACE] sqlite3.connect tracer ACTIVE → {TRACE_PATH}")
except Exception as e:
    print(f"[TRACE] activation failed: {e}")
# =======================================================================

import engines.config_paths as CP
print("[DEBUG-GUI] auto_conn pointer =", CP.auto_conn)
print("[DEBUG-GUI] open_auto_db pointer =", CP.open_auto_db)
print("[DEBUG-GUI] DAL_MODE =", CP.DAL_MODE)


import engines.config_paths as cp
# near top of file
from gui.dashboard import DashboardView, open_dashboard_window

# === PATCH START ===
# 📍 TARGET: gui/GUI.py (top-level imports)
# 📆 PATCHED: 2025-10-27Z — ensure subprocess is globally imported
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, sys, time, sqlite3, argparse, json, subprocess
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: gui/GUI.py (top-level form engine bootstrap)
# 📆 PATCHED: 2025-11-22 — correct full form pipeline (official → inferred → book → canonical)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# --- FORM ENGINE: run the full daily unified form pipeline ---
try:
    # 1) Official winners (this is the module that asks for the session token)
    from engines.form.form_reconcile_betfair import fetch_official_winners
    # 2) Infer missing winners (profit/BSP logic)
    from engines.form.form_infer_fallback import infer_missing_winners
    # 3) runner_form builder
    from engines.form.form_book_builder import build_runner_form
    # 4) canonical runner_form builder
    from engines.form.form_canonical_builder import build_canonical_runner_form

    print("[form] Updating unified form book …")

    # Step 1 → Official winners (TOKEN PROMPT HAPPENS HERE)
    fetch_official_winners(7)

    # Step 2 → Inferred winners for missing markets
    infer_missing_winners(90)

    # Step 3 → Build runner_form
    build_runner_form()

    # Step 4 → Build canonical runner_form_canonical
    build_canonical_runner_form()

except Exception as e:
    print(f"[form] warn: {e}")

# === PATCH END ===

def _load_markets_from_bets_today() -> list[dict]:
    """
    Fallback loader: read today's WIN markets + runners from BETS_DB.bets
    and return [{'marketId': str, 'marketStartTime': isoZ, 'runners': [...]}, ...]
    """
    import sqlite3
    from engines.config_paths import connect_db
    out = []
    bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    try:
        # Only take rows that actually have a start time; don't fabricate local dates.
        mkts = bdb.execute("""
            SELECT marketId, marketStartTime
            FROM bets
            WHERE marketStartTime IS NOT NULL
              AND datetime(marketStartTime) BETWEEN datetime('now','utc','-4 hours')
                                               AND datetime('now','utc','+28 hours')
              AND (UPPER(COALESCE(market_name,''))='WIN' OR market_name IS NULL)
            GROUP BY marketId
            ORDER BY datetime(marketStartTime) ASC
        """).fetchall()

        # local normaliser to keep this function self-contained
        def _norm_iso_utc(s):
            try:
                from datetime import datetime, timezone
                t = str(s).strip()
                if "T" not in t:
                    t = t.replace(" ", "T", 1)
                dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                else:
                    dt = dt.astimezone(timezone.utc)
                return dt.replace(tzinfo=timezone.utc).isoformat(timespec="seconds").replace("+00:00","Z")
            except Exception:
                return s

        for m in mkts or []:
            mid = str(m["marketId"])
            start_iso = _norm_iso_utc(str(m["marketStartTime"]))
            runners = bdb.execute("""
                SELECT selectionId, COALESCE(horse_name, selectionId) AS nm
                FROM bets
                WHERE marketId=?
                GROUP BY selectionId
                ORDER BY selectionId ASC
            """, (mid,)).fetchall()
            out.append({
                "marketId": mid,
                "marketStartTime": start_iso,
                "runners": [{"selectionId": int(r["selectionId"]), "runnerName": str(r["nm"])} for r in runners]
            })
    finally:
        try: bdb.close()
        except Exception: pass
    return out

# === BLUEPRINTS DAILY BOOTSTRAP (run once/day, before Step 4) ===
try:
    import sqlite3
    from datetime import datetime, timezone
    from engines.config_paths import autoscalp_db
    from engines.blueprint_build import main as build_blueprints

    today = datetime.now(timezone.utc).date().isoformat()
    key = f"blueprints_ran_{today}"

    con = sqlite3.connect(autoscalp_db(), timeout=8)
    con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS app_kv(
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)

    row = con.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
    already_ran = bool(row)

    if not already_ran:
        print("[blueprints] running early builder (pre-Step4)…")
        try:
            build_blueprints(force=False)
        except Exception as e:
            print(f"[blueprints] bootstrap error: {e}")
        else:
            con.execute(
                "INSERT OR REPLACE INTO app_kv(key,value,updated_at) "
                "VALUES (?, '1', datetime('now','utc'))",
                (key,)
            )
            con.commit()
            print("[blueprints] bootstrap complete")
    else:
        print("[blueprints] already ran today (bootstrap skipped)")

    con.close()

except Exception as e:
    print(f"[blueprints] bootstrap warn: {e}")


import logging
# gui/GUI.py
try:
    from engines.decision_engine.orchestrator import _log_once_per_minute  # canonical
except Exception:
    # minimal fallback — returns True once per minute per tag
    def _log_once_per_minute(tag: str, now_fn=None, _state: dict | None = None):
        if _state is None: _state = {}
        import time
        now = int((now_fn or time.time)() // 60) if callable(now_fn) else int(time.time() // 60)
        last = _state.get(tag)
        if last != now:
            _state[tag] = now
            return True
        return False



import time as _time
from engines.config_paths import auto_conn as _auto_conn, q_retry as _q_retry
# ── util: log at most once per minute per key ────────────────────────────────
import time, logging
from engines.decision_engine.orchestrator import _upsert_schedule_from_plan



import sqlite3, engines.database_hijack_monitor as dbh
print("connect is hijacked:", getattr(sqlite3, "_autosc_hijacked", False))
rows = dbh.enqueue_read("SELECT 1")
print("read ok:", rows is not None)

from engines.mastery.event_sink import install_mastery_shim
install_mastery_shim()



# bridge: ensure engines.utils.api_tools has a _log_once_per_minute symbol
try:
    from engines.decision_engine.orchestrator import _log_once_per_minute as _orch_log_once
except Exception:
    def _orch_log_once(mid, sid, level, msg):  # silent no-op fallback
        pass

try:
    import engines.utils.api_tools as _api_tools
    if not hasattr(_api_tools, "_log_once_per_minute"):
        _api_tools._log_once_per_minute = lambda mid, sid, level, msg: _log_once_per_minute(
            level, f"{mid}/{sid}", msg
        )
except Exception:
    # keep going even if api_tools import fails
    pass
# === PATCH END ===


import os, json, logging, requests
from datetime import datetime, timezone

def keep_alive_once(app_key: str | None = None,
                    session: str | None = None,
                    timeout_s: int = 8) -> bool:
    """
    Direct Betfair keepAlive call. Returns True if the session is valid.
    If app_key/session not provided, falls back to env: BETFAIR_APP_KEY / BETFAIR_SESSION.
    """
    ak = (app_key or os.environ.get("BETFAIR_APP_KEY") or "").strip()
    ss = (session or os.environ.get("BETFAIR_SESSION") or "").strip()
    if not ak or not ss:
        logging.warning("[Session] keep-alive skipped (missing app key or session)")
        return False

    try:
        r = requests.post(
            "https://identitysso.betfair.com/api/keepAlive",
            headers={
                "X-Application": ak,
                "X-Authentication": ss,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )
    except Exception as e:
        logging.error(f"[Session] keep-alive network error: {e}")
        return False

    if r.status_code != 200:
        logging.warning(f"[Session] keep-alive HTTP {r.status_code}: {r.text[:160]}")
        return False

    try:
        js = r.json()
    except Exception:
        js = {}
    status = (js.get("status") if isinstance(js, dict) else
              (js[0].get("status") if isinstance(js, list) and js else "")) or ""
    ok = str(status).upper() == "SUCCESS"
    if ok:
        # keep the success line lightweight; move to DEBUG if it’s too chatty
        logging.info(f"✅ [Session] keep-alive OK @ {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    else:
        logging.warning(f"⚠️ [Session] keep-alive failed payload: {json.dumps(js)[:160]}")

    return ok

import threading, time, logging

# ── Runtime Header Status ────────────────────────────────────────────────
from datetime import datetime, timezone

def runtime_header():
    """Dynamic status line for GUI footer or console heartbeat."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    version = "v5.3.1"
    mode = "LIVE"
    loop_status = "stable"
    settlements = "auto-sync (5 min)"
    mastery = "active"
    bank = "OK"
    return (
        f"AutoScalp {version} — {mode} loop {loop_status} | "
        f"Settlements: {settlements} | Mastery: {mastery} | BankState: {bank} | {now}"
    )

# Example printout:
print(runtime_header())


def start_keepalive_thread(app_key_getter=None,
                           session_getter=None,
                           period_s: int = 60) -> threading.Thread:
    """
    Start a daemon thread that runs keep_alive_once() every period_s seconds.
    app_key_getter/session_getter: callables returning current creds (so UI updates are picked up).
    If omitted, keep_alive_once() will read env each tick.
    """
    stop = threading.Event()

    def _ak(): return app_key_getter() if callable(app_key_getter) else None
    def _ss(): return session_getter() if callable(session_getter) else None

# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*def loop\(
# 🩹 PURPOSE: fix “database is locked” retries + UnboundLocalError: last
# 📆 PATCHED: 2025-10-14
# ============================================================
    def loop(self):
        """
        OC_Timeline_Loop — patched for safe SQLite writes and retry logic.

        Fixes:
          - [SQLITE_EXEC] OperationalError: database is locked
          - UnboundLocalError: 'last' before assignment
        """
        import time, sqlite3

        last = time.time()  # ensure defined early
        retry_wait = 0.05

        while True:
            try:
                # main OC timeline update call (existing logic)
                _autoscalp_update_cache_oc(mid, sid, label, lay_val, band_json=[lay_val])

            except sqlite3.OperationalError as e:
                # graceful retry for transient DB locks
                if "database is locked" in str(e).lower():
                    print("[WRITERS] busy: sqlite locked, retrying shortly…")
                    time.sleep(retry_wait)
                    continue
                else:
                    raise

            except Exception as e:
                # catch-all to avoid thread death
                print(f"[OC_Timeline_Loop] {type(e).__name__}: {e}")
                time.sleep(1.0)
                continue

            # periodic housekeeping every 60s
            now = time.time()
            last = 0.0  # ✅ ensure initialized before use
            while True:
                now = time.time()
                if now - last >= 60.0:
  

                    last = now
                    try:
                        # existing heartbeat or cleanup task goes here
                        pass
                    except Exception as e:
                        print(f"[OC_Timeline_Loop-Heartbeat] {type(e).__name__}: {e}")
                        continue
# ============================================================



def _oc_timeline_log(mid: str, sid: str, msg: str, lvl: int = logging.WARNING) -> None:
    # map logging levels (ints) to the strings orchestrator._log_event expects
    if   lvl <= logging.DEBUG:   s = "DEBUG"
    elif lvl <= logging.INFO:    s = "INFO"
    elif lvl <= logging.WARNING: s = "WARN"
    elif lvl <= logging.ERROR:   s = "ERROR"
    else:                        s = "ERROR"
    try:
        _oc_orch_log(str(mid), str(sid), s, msg)
    except Exception:
        # last-resort: still print something
        logging.log(lvl, f"[OC_TIMELINE] {mid}/{sid} {msg}")




import os, sys, json, threading, time, logging
from datetime import datetime, timedelta
from typing import Optional, Iterable, Set, Tuple
from datetime import datetime, timezone, timedelta
import threading
import sqlite3
import json
import time
from decimal import Decimal, ROUND_HALF_UP
from gui.dashboard import DashboardView

try:
    from engines.decision_engine.engine import DecisionEngine
except Exception:
    DecisionEngine = None


from engines.db_triggers import ensure_order_events_triggers

from engines.utils.time_utils import minutes_to_off, now_utc
import tkinter as tk
from tkinter import ttk, messagebox

# Scalper View launcher (safe import)
try:
    from gui.scalper_view import open_scalper_window
except Exception:
    open_scalper_window = None


# --- make repo root importable ------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))            # .../analytics_beta/gui
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))       # .../analytics_beta
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# --- DB router shim: synchronous SQLite writes, no hijack ---------------------
import sqlite3, types, re

# Resolve both DB paths (prefer repo /data/, fall back to /engines/data/)
_BETS_DB = os.path.join(_ROOT, "data", "bets.db")
if not os.path.exists(_BETS_DB):
    _BETS_DB = os.path.join(_ROOT, "engines", "data", "bets.db")

_AUTOSCALP_DB = os.path.join(_ROOT, "data", "autoscalp_gui.db")
if not os.path.exists(_AUTOSCALP_DB):
    _AUTOSCALP_DB = os.path.join(_ROOT, "engines", "data", "autoscalp_gui.db")

# --- DB router shim: synchronous SQLite writes, no hijack ---------------------
# --- DB router shim: asks config_paths for current TEST/LEARNING/LIVE paths ---
import types, re
import engines.config_paths as cp

try:
    from adapters import ensure_betfair_ready, keep_alive
except ImportError:
    try:
        from engines.decision_engine.adapters import ensure_betfair_ready, keep_alive
    except ImportError:
        from engines.adapters import ensure_betfair_ready, keep_alive  # final fallback if you keep it under engines/

import logging
log = logging.getLogger("gui")

def _auto_boot_feeder_if_ready(self=None, mode: str = "LIVE"):
    """
    Auto-start feeder is disabled. Use Step 1 (Validate) to launch the feeder.
    This prevents background spawns that were ballooning memory.
    """
    try:
        print("[gui] auto-start disabled — launch feeder via Validate only")
    except Exception:
        pass
    return False


from engines.config_paths import auto_conn as _adb, q_retry as _q
import datetime as _dt

def _seed_guard_ok(tag: str) -> bool:
    con = _adb()
    try:
        con.execute("""
          CREATE TABLE IF NOT EXISTS app_kv(
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now','utc'))
          )""")
        today = _dt.datetime.now(timezone.utc).date().isoformat()
        key = f"seed_done_{tag}_{today}"
        row = con.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
        if row: 
            con.close()
            return False
        con.execute("INSERT OR REPLACE INTO app_kv(key,value,updated_at) VALUES(?,?,datetime('now','utc'))", (key, "1"))
        con.commit()
        con.close()
        return True
    except Exception:
        try: con.close()
        except Exception: pass
        return True  # fail-open once

# ──────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py  (place near _ensure_inbound_cache_upsertable)
def _ensure_odds_current_columns():
    """
    Backfill odds_current to the full shape used by writers:
      fav_rank_now, mto_minutes, slope_ppm, tick_vel_1s_up, tick_vel_3s_up
    """
    import sqlite3
    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db(), timeout=10, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("""
            CREATE TABLE IF NOT EXISTS odds_current(
              day TEXT NOT NULL,
              marketId TEXT NOT NULL,
              selectionId TEXT NOT NULL,
              updated_ts TEXT NOT NULL,
              ltp REAL, back1 REAL, lay1 REAL,
              fav_rank_now INTEGER, mto_minutes REAL,
              slope_ppm REAL, tick_vel_1s_up INTEGER, tick_vel_3s_up INTEGER,
              PRIMARY KEY(day, marketId, selectionId)
            )
        """)
        cols = {r[1] for r in con.execute("PRAGMA table_info(odds_current)")}
        def _add(col, ddl):
            if col not in cols:
                con.execute(f"ALTER TABLE odds_current ADD COLUMN {col} {ddl}")
                cols.add(col)
        _add("fav_rank_now",   "INTEGER")
        _add("mto_minutes",    "REAL")
        _add("slope_ppm",      "REAL")
        _add("tick_vel_1s_up", "INTEGER")
        _add("tick_vel_3s_up", "INTEGER")
    finally:
        try: con.close()
        except Exception: pass


def restart_feeder_with_creds(app_key: str, session: str) -> None:
    """
    Start/restart the dashboard feeder as a background subprocess.
    Works regardless of which widget (tkapp, frame, view) calls it.
    """
    try:
        import subprocess, sys, os, json
        from engines.config_paths import autoscalp_db

        # Persist creds so feeder (and future sessions) can read them
        base = os.path.dirname(autoscalp_db())
        os.makedirs(base, exist_ok=True)
        creds_file = os.path.join(base, "betfair_creds.json")
        with open(creds_file, "w", encoding="utf-8") as f:
            json.dump({"app_key": app_key or "", "session": session or ""}, f)

        # Spawn feeder in LIVE/MIRROR mode; it will resolve creds from DB/JSON
        cmd = [
            sys.executable, "-u", "-m", "engines.dashboard_feeder",
            "--mode", "LIVE", "--source", "MIRROR", "--tick", "1.0",
            "--app-key", app_key or "",
            "--session", session or "",
        ]
        # inherit stdout/stderr so you see feeder prints live
        subprocess.Popen(cmd)  # inherits stdout/stderr

        print("[FEEDER] launched via module-level restart_feeder_with_creds")
    except Exception as e:
        print(f"[FEEDER] module-level launch error: {e}")


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^def _sqlite_exec\(path, sql, params=None, fetch=True\):
# 📆 PATCHED: 2025-10-14T23:59Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.config_paths import connect_db as _bdb, auto_conn as _adb, connect_orders_db as _odb
from engines.database_hijack_monitor import enqueue_write, enqueue_read, launch_db_writer

# Start queue writer once per process
try:
    launch_db_writer()
    print("[DB] async writer thread started (ABD)")
except Exception as e:
    print(f"[DB] writer launch warn: {e}")

from engines.database_hijack_monitor import enqueue_write as _dbq_write, enqueue_read as _dbq_read

def _sqlite_exec(sql: str, params=None, fetch=True):
    """
    Unified DB exec using the hijacker:
      - reads → direct, with busy_timeout+retries
      - writes → queued through the per‑DB writer (bets or autoscalp chosen by SQL)
    """
    if fetch:
        return _dbq_read(sql, params or ())
    _dbq_write(sql, params or ())
    return None

# back-compat alias (older code referenced __sqlite_exec)
__sqlite_exec = _sqlite_exec



_AUTOSCALP_PAT = re.compile(
    r"\b(inbound_|oc_series\b|stories\b|chapters\b|orders\b|decisions\b|ladders_obs\b|events\b|pnl_)",
    re.IGNORECASE
)


def _route(sql: str) -> str:
    # decide target *at call time* so TEST routing works
    return cp.autoscalp_db() if _AUTOSCALP_PAT.search(sql.lower()) else cp.bets_db()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: def _enqueue_write
# 📆 PATCHED: 2025-10-14T23:59Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _enqueue_write(sql: str, params=None, priority: int = 5):
    """Route writes automatically and call ABD _sqlite_exec safely."""
    _sqlite_exec(sql, params, fetch=False)

def _enqueue_read(sql: str, params=None):
    """Route reads automatically and call ABD _sqlite_exec safely."""
    return _sqlite_exec(sql, params, fetch=True)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


#class _NoopQueue:
#    def join(self): pass

#_hm = types.ModuleType("engines.database_hijack_monitor")
#_hm.launch_db_writer = lambda: None
#_hm.enqueue_write = _enqueue_write
#_hm.enqueue_read  = _enqueue_read
#_hm.priority_queue = _NoopQueue()
#sys.modules["engines.database_hijack_monitor"] = _hm

# --- DB writer queue import (back-compatible) --------------------------
try:
    from engines.database_hijack_monitor import (
        launch_db_writer, enqueue_write, enqueue_read
    )
except ImportError:
    # Fallback for newer builds without enqueue_* helpers
    def launch_db_writer(): pass
    def enqueue_write(*_a, **_k): pass
    def enqueue_read(*_a, **_k): pass


# --- imports from repo --------------------------------------------------------
from engines.config_paths import DB_PATH, connect_db
from engines.path_guard import ensure_parent
from engines.db_migrations import ensure_tables

from engines.utils.api_tools import fetch_live_odds

# get_markets function we’ll use to seed runners
from engines.get_markets import get_markets_and_insert

# === OC schedule (minutes before off-time) ===
OC_SCHEDULE_MINUTES = {
    0: 90,    # OC0 (anchor)
    1: 80,
    2: 60,
    3: 40,
    4: 20,
    5: 10,
    6: 5,
    7: 0,
    8: -1,
    9: -2,
    10: -3,
    11: -4,
    12: -5,
    13: -6,
    14: -7,   # 30s
    15: -8,  # 20s
    16: -9,  # 10s
    17: -10,  # 5s
    18: -12,  # 30s AFTER off-time
    19: -13   # 60s AFTER off-time
}

# --- simple persistent app state (mode) --------------------------------------
_STATE_FILE = os.path.join(_ROOT, "data", "app_state.json")

def _load_state() -> dict:
    try:
        with open(_STATE_FILE, "r") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _save_state(d: dict) -> None:
    try:
        os.makedirs(os.path.dirname(_STATE_FILE), exist_ok=True)
        with open(_STATE_FILE, "w") as f:
            json.dump(d, f)
    except Exception:
        pass


# --- scheduling constants ---
OC0_MINUTES = 90  # gate anchor at T-90

def _parse_iso_utc(s: str):
    # tolerates ...Z and offsets
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)

# ── OC_TIMELINE throttle helper (once-per-runner/period) ─────────────────────
import time as _oc_time, logging as _oc_log

def _oc_timeline_log(mid: str, sid: str, msg: str,
                     *, benign: tuple[str, ...] = (
                         "unable to open database file", "database is locked", "busy",
                         "NameResolutionError", "Max retries exceeded", "No odds"),
                     period_s: float = 30.0) -> None:
    """
    Log at WARNING once per (mid/sid)/period; demote known-benign to DEBUG.
    """
    if not hasattr(_oc_timeline_log, "_last"):
        _oc_timeline_log._last = {}
    key = f"{mid}/{sid}"
    now = _oc_time.time()
    last = _oc_timeline_log._last.get(key, 0.0)
    if now - last < period_s:
        return
    _oc_timeline_log._last[key] = now
    level = _oc_log.DEBUG if any(b in msg for b in benign) else _oc_log.WARNING
    _oc_log.log(level, f"[OC_TIMELINE] {mid}/{sid} {msg}")

# ──────────────────────────────────────────────────────────────────────
# DB preflight + tracer (module-level helpers) — paste once in GUI.py
# ──────────────────────────────────────────────────────────────────────
import os, sqlite3, time, traceback, inspect, csv

def _db_preflight_one(db_path: str, label: str, *, wal: bool = True, do_probe: bool = True) -> tuple[bool, str]:
    """
    Ensure parent dir exists, file exists, openable, PRAGMA WAL (fallback DELETE), and a tiny R/W probe works.
    Returns (ok, reason).
    """
    try:
        base = os.path.dirname(db_path) or "."
        os.makedirs(base, exist_ok=True)
        if not os.path.exists(db_path):
            # touch file
            open(db_path, "ab").close()
        # permissive but not world-writable
        try:
            os.chmod(base, 0o775)
            os.chmod(db_path, 0o664)
        except Exception:
            pass

        con = sqlite3.connect(db_path, timeout=8)
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA synchronous=NORMAL")
        try:
            if wal:
                con.execute("PRAGMA journal_mode=WAL")
            else:
                con.execute("PRAGMA journal_mode=DELETE")
        except Exception:
            # fallback if filesystem disallows WAL
            con.execute("PRAGMA journal_mode=DELETE")

        if do_probe:
            cur = con.cursor()
            cur.execute("CREATE TABLE IF NOT EXISTS __gui_probe(k TEXT PRIMARY KEY, v TEXT)")
            cur.execute("INSERT OR REPLACE INTO __gui_probe(k,v) VALUES('ts',?)", (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),))
            cur.execute("DELETE FROM __gui_probe WHERE k NOT IN ('ts')")  # keep one row
            con.commit()
        con.close()
        return True, f"{label}: OK ({db_path})"
    except Exception as e:
        return False, f"{label}: FAIL {type(e).__name__}: {e} @ {db_path}"

def _db_preflight_all(*, wal=True, do_probe=True) -> tuple[bool, list[str]]:
    """
    Resolve canonical DB paths via config_paths and run preflight on both.
    """
    try:
        import engines.config_paths as cp
        b = cp.bets_db()
        a = cp.autoscalp_db()
    except Exception as e:
        return False, [f"resolve_paths: FAIL {type(e).__name__}: {e}"]

    oks = []
    results = []
    for path, label in ((b, "bets.db"), (a, "autoscalp_gui.db")):
        ok, why = _db_preflight_one(path, label, wal=wal, do_probe=do_probe)
        oks.append(ok); results.append(why)
    return all(oks), results

_SQLITE3_CONNECT_ORIG = None

def _install_sqlite_tracer(log_path: str) -> None:
    """
    Monkey-patch sqlite3.connect to log every DB open (path + topmost non-stdlib caller)
    into a CSV (log_path). Safe to call multiple times.
    """
    global _SQLITE3_CONNECT_ORIG
    if hasattr(sqlite3, "_connect_traced"):
        return
    _SQLITE3_CONNECT_ORIG = sqlite3.connect

    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    # write header once if file empty
    if not os.path.exists(log_path) or os.path.getsize(log_path) == 0:
        with open(log_path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(["ts","db_path","caller_file","caller_func","caller_line"])

    def _traced_connect(db, *args, **kwargs):
        # find first frame outside sqlite3 / stdlib
        caller_file, caller_func, caller_line = "?", "?", 0
        for frame in inspect.stack():
            fn = frame.filename
            if "/python" in fn or "sqlite3" in fn:
                continue
            caller_file, caller_func, caller_line = fn, frame.function, frame.lineno
            break
        try:
            with open(log_path, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow([time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                                        str(db), caller_file, caller_func, caller_line])
        except Exception:
            pass
        return _SQLITE3_CONNECT_ORIG(db, *args, **kwargs)

    sqlite3.connect = _traced_connect  # type: ignore[attr-defined]
    setattr(sqlite3, "_connect_traced", True)



# ------------------------------------------------------------------------------
# helpers
# ------------------------------------------------------------------------------
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: adapters configured app=
def _configure_adapters_and_persist() -> None:
    import logging
    from engines.upgrade_import_patch import get_app_key, get_session_token, persist_app_key_to_db
    app = get_app_key()
    tok = get_session_token()
    persist_app_key_to_db(app)  # ensure DB reflects the canonical key
    logging.info(f"[GUI] adapters configured app={app[:6]}…{app[-3:]} sess={tok[:6]}…")

# (Call this where you currently log “adapters configured …” before spawning Feeder.)


from engines.session_secrets import set_secret, APP_KEY_KEY, SESSION_KEY
from engines.decision_engine.adapters import configure_betfair, keep_alive

def on_validate_credentials(app_key: str, session_token: str):
    """
    Called by the Validate button in the GUI after user enters creds.
    Persists to DB, configures adapters, and (optionally) boots feeder.
    """
    ak = (app_key or "").strip()
    st = (session_token or "").strip()
    if not ak or not st:
        return False

    # persist to DB
    try:
        set_secret(APP_KEY_KEY, ak)
        set_secret(SESSION_KEY, st)
    except Exception:
        pass

    # configure current process
    try:
        configure_betfair(ak, st)
    except Exception:
        pass

    # sanity ping
    ok = bool(keep_alive())

    # optional: start feeder immediately if OK
    if ok:
        try:
            _auto_boot_feeder_if_ready()
        except Exception:
            pass

    return ok


def _load_app_key_from_daily_config() -> Optional[str]:
    """
    Order:
      1) BETFAIR_APP_KEY env
      2) analytics_beta/daily_config.json  -> {"app_key": "..."}
      3) engines/daily_config.json         -> {"app_key": "..."}
      4) engines/upgrade_import_patch.get_app_key() if available
    """
    k = os.environ.get("BETFAIR_APP_KEY") or os.environ.get("APP_KEY")
    if k:
        return k.strip()

    candidates = [
        os.path.join(_ROOT, "daily_config.json"),
        os.path.join(_ROOT, "engines", "daily_config.json"),
    ]
    for path in candidates:
        try:
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                v = (data.get("app_key") or "").strip()
                if v:
                    return v
        except Exception:
            pass

    try:
        from engines.upgrade_import_patch import get_app_key  # type: ignore
        v = (get_app_key() or "").strip()
        return v or None
    except Exception:
        return None


def _install_upgrade_patch_shim(token: str, app_key: Optional[str], mode: Optional[str] = None):
    """
    Ensure engines.upgrade_import_patch exposes:
      - set/get_session_token
      - set/get_app_key
      - set/get_mode
    Works whether the module already exists or not.
    """
    import types, sys
    try:
        import engines.upgrade_import_patch as uip  # type: ignore
    except Exception:
        uip = types.ModuleType("engines.upgrade_import_patch")
        sys.modules["engines.upgrade_import_patch"] = uip

    # Session token
    if not hasattr(uip, "set_session_token"):
        def set_session_token(t: str): setattr(uip, "_tok", t or "")
        uip.set_session_token = set_session_token
    if not hasattr(uip, "get_session_token"):
        uip.get_session_token = lambda: getattr(uip, "_tok", "")

    # App key
    if not hasattr(uip, "set_app_key"):
        def set_app_key(k: str): setattr(uip, "_app", k or "")
        uip.set_app_key = set_app_key
    if not hasattr(uip, "get_app_key"):
        uip.get_app_key = lambda: getattr(uip, "_app", "")

    # Mode
    if not hasattr(uip, "set_mode"):
        def set_mode(m: str): setattr(uip, "_mode", (m or "learning").lower())
        uip.set_mode = set_mode
    if not hasattr(uip, "get_mode"):
        uip.get_mode = lambda: getattr(uip, "_mode", "learning")

    # Set current values
    uip.set_session_token(token or "")
    if app_key is not None:
        uip.set_app_key(app_key)
    if mode is not None:
        uip.set_mode(mode)

    return uip



def _iso_date_from_start(start: Optional[str]) -> Optional[str]:
    if not start:
        return None
    try:
        s = start.replace("Z", "+00:00") if "Z" in start else start
        return datetime.fromisoformat(s).date().isoformat()
    except Exception:
        return None


def _filter_markets_by_dates(markets: Iterable[dict], target_dates: Set[str]) -> list:
    out = []
    for m in markets or []:
        d = _iso_date_from_start(m.get("marketStartTime"))
        if d and d in target_dates:
            out.append(m)
    return out


def _band_from_value(v: Optional[float], pct: float = 0.02):
    if v is None:
        return None, None, None
    low, high = v * (1 - pct), v * (1 + pct)
    return low, high, json.dumps([low, v, high])



def _ensure_autoscalp_tables():
    # Only create if missing; your DB already has these, so this is safe.
    sql1 = """CREATE TABLE IF NOT EXISTS inbound_bets_min (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        placed_at TEXT,
        anchor_odd REAL,
        odds_check_1 REAL, odds_check_2 REAL, odds_check_3 REAL,
        odds_check_4 REAL, odds_check_5 REAL, odds_check_6 REAL,
        horse_name TEXT,
        meta_json TEXT
    );"""
    sql2 = """CREATE TABLE IF NOT EXISTS inbound_oc_cache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        anchor_odd REAL,
        oc1 REAL,  oc2 REAL,  oc3 REAL,  oc4 REAL,  oc5 REAL,
        oc6 REAL,  oc7 REAL,  oc8 REAL,  oc9 REAL,  oc10 REAL,
        oc11 REAL, oc12 REAL, oc13 REAL, oc14 REAL, oc15 REAL,
        oc16 REAL, oc17 REAL, oc18 REAL, oc19 REAL, oc20 REAL,
        oc1_band_json  TEXT, oc2_band_json  TEXT, oc3_band_json  TEXT, oc4_band_json  TEXT, oc5_band_json  TEXT,
        oc6_band_json  TEXT, oc7_band_json  TEXT, oc8_band_json  TEXT, oc9_band_json  TEXT, oc10_band_json TEXT,
        oc11_band_json TEXT, oc12_band_json TEXT, oc13_band_json TEXT, oc14_band_json TEXT, oc15_band_json TEXT,
        oc16_band_json TEXT, oc17_band_json TEXT, oc18_band_json TEXT, oc19_band_json TEXT, oc20_band_json TEXT,
        last_sync_ts TEXT
    );"""
    enqueue_write(sql1)
    enqueue_write(sql2)

from engines.config_paths import auto_conn as _adb


# ──────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^def _reset_day_flags_if_needed\(\):\n(?:[ \t].*\n)+?^
# ⛏️ ACTION: replace whole _reset_day_flags_if_needed with direct autoscalp_db writer
# 📆 PATCHED: 2025-11-19T11:05Z
# ──────────────────────────────────────────────────────────────────────
def _reset_day_flags_if_needed():
    """
    Reset daily seed/blueprint flags in AUTOSCALP_DB.app_kv.

    Uses a direct sqlite3 connection to the autoscalp_gui.db path resolved
    by config_paths.autoscalp_db(). This keeps Step 1 independent of the
    hijack/AlphaX layer and avoids readonly DAL connections.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).date().isoformat()
    con = None
    try:
        con = sqlite3.connect(autoscalp_db(), timeout=8)
        con.execute("""
            CREATE TABLE IF NOT EXISTS app_kv(
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        con.execute(
            "DELETE FROM app_kv "
            "WHERE key LIKE 'seed_done_bets_%' AND substr(key, -10) <> ?",
            (today,),
        )
        con.execute(
            "DELETE FROM app_kv "
            "WHERE key LIKE 'blueprints_ran_%' AND substr(key, -10) <> ?",
            (today,),
        )
        con.commit()
    except Exception as e:
        # Non-fatal: these flags are housekeeping; log and continue.
        try:
            print(f"[app_kv] reset_day_flags warn: {e}")
        except Exception:
            pass
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass
# === PATCH END ===



def _ensure_inbound_cache_unique_index():
    import sqlite3
    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db(), timeout=8, isolation_level=None)
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS inbound_oc_cache(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                marketId TEXT NOT NULL,
                selectionId TEXT NOT NULL,
                anchor_odd REAL,
                oc1 REAL, oc2 REAL, oc3 REAL, oc4 REAL, oc5 REAL,
                oc6 REAL, oc7 REAL, oc8 REAL, oc9 REAL, oc10 REAL,
                oc11 REAL, oc12 REAL, oc13 REAL, oc14 REAL, oc15 REAL,
                oc16 REAL, oc17 REAL, oc18 REAL, oc19 REAL, oc20 REAL,
                oc1_band_json  TEXT, oc2_band_json  TEXT, oc3_band_json  TEXT, oc4_band_json  TEXT, oc5_band_json  TEXT,
                oc6_band_json  TEXT, oc7_band_json  TEXT, oc8_band_json  TEXT, oc9_band_json  TEXT, oc10_band_json TEXT,
                oc11_band_json TEXT, oc12_band_json TEXT, oc13_band_json TEXT, oc14_band_json TEXT, oc15_band_json TEXT,
                oc16_band_json TEXT, oc17_band_json TEXT, oc18_band_json TEXT, oc19_band_json TEXT, oc20_band_json TEXT,
                last_sync_ts TEXT
            );
        """)
        # ✅ This gives ON CONFLICT(marketId,selectionId) a legal target
        con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_inbound_oc_cache_mid_sid ON inbound_oc_cache(marketId, selectionId)")
    finally:
        try: con.close()
        except Exception: pass


def _bets_lookup_horse_name(market_id: str, selection_id: int) -> str | None:
    rows = _sqlite_exec(_BETS_DB,
        "SELECT horse_name FROM bets WHERE marketId=? AND selectionId=? LIMIT 1",
        [market_id, selection_id], fetch=True)
    return (rows[0][0] if rows and rows[0] and rows[0][0] else None)

def _autoscalp_upsert_inbound_bets_min(market_id: str, selection_id: int, placed_at: str, anchor_odd: float | None, horse_name: str | None, meta: dict | None):
    meta_json = json.dumps(meta or {})
    # create if missing
    enqueue_write(
        "INSERT INTO inbound_bets_min (marketId, selectionId, placed_at, anchor_odd, horse_name, meta_json) "
        "SELECT ?,?,?,?,?,? WHERE NOT EXISTS (SELECT 1 FROM inbound_bets_min WHERE marketId=? AND selectionId=?)",
        [market_id, selection_id, placed_at, anchor_odd, horse_name, meta_json, market_id, selection_id]
    )
    # then update latest fields
    enqueue_write(
        "UPDATE inbound_bets_min SET placed_at=?, anchor_odd=?, horse_name=COALESCE(?, horse_name), meta_json=? "
        "WHERE marketId=? AND selectionId=?",
        [placed_at, anchor_odd, horse_name, meta_json, market_id, selection_id]
    )

def _autoscalp_upsert_cache_anchor(market_id: str, selection_id: int, anchor_odd: float | None):
    from datetime import datetime, timezone
    enqueue_write(
        "INSERT INTO inbound_oc_cache (marketId, selectionId, anchor_odd, last_sync_ts) "
        "SELECT ?,?, ?, datetime('now','utc') "
        "WHERE NOT EXISTS (SELECT 1 FROM inbound_oc_cache WHERE marketId=? AND selectionId=?)",
        [market_id, selection_id, anchor_odd, market_id, selection_id]
    )
    enqueue_write(
        "UPDATE inbound_oc_cache SET anchor_odd=?, last_sync_ts=datetime('now','utc') "
        "WHERE marketId=? AND selectionId=?",
        [anchor_odd, market_id, selection_id]
    )


# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: def _autoscalp_update_cache_oc(
# 📆 PATCHED: 2025-10-14T23:59Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _autoscalp_update_cache_oc(
    market_id: str,
    selection_id: int,
    oc_label: str,
    odd: float | None,
    band_json: "list[float] | str | None" = None,
):
    """
    Upsert ocN value and ocN_band_json for inbound_oc_cache (thread-safe).
    Retries automatically on transient SQLITE_BUSY / locked errors.
    """
    import re, json, time, sqlite3

    m = re.fullmatch(r"OC(\d{1,2})", oc_label.upper())
    if not m:
        return
    n = int(m.group(1))
    if not (1 <= n <= 20):
        return

    # ── ensure row exists ────────────────────────────────────────────────
    insert_sql = (
        "INSERT INTO inbound_oc_cache (marketId, selectionId, last_sync_ts) "
        "SELECT ?, ?, datetime('now','utc') "
        "WHERE NOT EXISTS (SELECT 1 FROM inbound_oc_cache "
        "WHERE marketId=? AND selectionId=?)"
    )
    for attempt in range(3):
        try:
            enqueue_write(insert_sql, [market_id, selection_id, market_id, selection_id])
            break
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 2:
                print(f"[OC_CACHE] insert locked → retry {attempt+1}/3")
                time.sleep(0.25 * (attempt + 1))
                continue
            raise

    # ── build band JSON safely ───────────────────────────────────────────
    if band_json is None:
        _, _, bj = _band_from_value(odd)
    elif isinstance(band_json, str):
        bj = band_json
    else:
        try:
            bj = json.dumps(band_json)
        except Exception:
            _, _, bj = _band_from_value(odd)

    # ── update OCn value + band + last_sync_ts ───────────────────────────
    update_sql = (
        f"UPDATE inbound_oc_cache "
        f"SET oc{n}=?, oc{n}_band_json=?, last_sync_ts=datetime('now','utc') "
        f"WHERE marketId=? AND selectionId=?"
    )
    for attempt in range(3):
        try:
            enqueue_write(update_sql, [odd, bj, market_id, selection_id])
            break
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower() and attempt < 2:
                print(f"[OC_CACHE] update locked → retry {attempt+1}/3")
                time.sleep(0.25 * (attempt + 1))
                continue
            raise
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━




def _autoscalp_cache_has_oc(market_id: str, selection_id: int, n: int) -> bool:
    try:
        row = _sqlite_exec(
            f"SELECT oc{n} FROM inbound_oc_cache WHERE marketId=? AND selectionId=? LIMIT 1",
            [market_id, selection_id], fetch=True)
        return bool(row and row[0] and row[0][0] is not None)
    except Exception:
        return False


def _record_oc_series(market_id: str, selection_id: int, stage: str,
                      odd: Optional[float], source: str = "SIM", meta: Optional[dict] = None):
    """
    Insert a snapshot into oc_series. Idempotent table guard (no-op if exists).
    Uses SQLite datetime('now','utc') so time arithmetic works in validators.
    """
    low, high, band_json = _band_from_value(odd)
    sql_ddl = """
      CREATE TABLE IF NOT EXISTS oc_series(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        stage TEXT NOT NULL,
        snapshot_ts TEXT NOT NULL,
        odd REAL,
        band_low REAL,
        band_high REAL,
        band_json TEXT,
        meta_json TEXT,
        source TEXT
      )"""
    sql_idx1 = "CREATE INDEX IF NOT EXISTS idx_oc_series_mkt_time ON oc_series(marketId, snapshot_ts)"
    sql_idx2 = "CREATE INDEX IF NOT EXISTS idx_oc_series_sel_time ON oc_series(selectionId, snapshot_ts)"
    sql_ins = ("INSERT INTO oc_series (marketId, selectionId, stage, snapshot_ts, odd, "
               "band_low, band_high, band_json, meta_json, source) "
               "VALUES (?, ?, ?, datetime('now','utc'), ?, ?, ?, ?, ?, ?)")

    try:
        # write directly to BETS_DB (router will choose the right file)
        enqueue_write(sql_ddl)
        enqueue_write(sql_idx1)
        enqueue_write(sql_idx2)
        params = [
            market_id, selection_id, stage,
            odd, low, high, band_json, json.dumps(meta or {}), source
        ]
        enqueue_write(sql_ins, params, priority=3)
    except Exception:
        # swallow — this is telemetry only, core flow is inbound_oc_cache
        pass

_SEEN_NONRUNNERS: dict[str, set[str]] = {}

def _today_key() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _update_bets_anchor(mid: str, sid: int | str, lay: float, dbg=lambda *_a, **_k: None) -> tuple[bool, str]:
    """
    Write anchor to BETS_DB.bets. Returns (ok, reason).
    dbg: optional debug logger like dbg("message")
    """
    import sqlite3
    from datetime import datetime, timezone
    from engines.config_paths import connect_db

    mid_s = str(mid); sid_s = str(sid)
    low = high = None
    try:
        # if you have a band helper:
        low, high = (lay * 0.98, lay * 1.02)
        band_json = f"[{low},{lay},{high}]"
    except Exception:
        band_json = None

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

    # Existence check
    con_ro = connect_db(ro=True)
    try:
        row = con_ro.execute("SELECT 1 FROM bets WHERE marketId=? AND selectionId=? LIMIT 1",(mid_s, sid_s)).fetchone()
    finally:
        try: con_ro.close()
        except Exception: pass
# === PATCH START ===
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: if not row:
# 📆 PATCHED: 2025-10-08T23:15Z — one-time daily non-runner print cache (🐎)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


    # inside _update_bets_anchor(...)
    if not row:
        try:
            key = _today_key()
            if key not in _SEEN_NONRUNNERS:
                _SEEN_NONRUNNERS.clear()
                _SEEN_NONRUNNERS[key] = set()
            seen = _SEEN_NONRUNNERS[key]
            uid = f"{mid}-{sid}"

            if uid not in seen:
                # try get horse name and event name for clarity
                try:
                    from engines.config_paths import connect_db as _conn
                    con = _conn(ro=True); con.row_factory = __import__("sqlite3").Row
                    r = con.execute(
                        "SELECT horse_name, event_name FROM bets WHERE marketId=? AND selectionId=? LIMIT 1",
                        (mid, sid)
                    ).fetchone()
                    con.close()
                    name = r["horse_name"] if r and r["horse_name"] else sid
                    ev   = r["event_name"] if r and r["event_name"] else "?"
                except Exception:
                    name, ev = sid, "?"
                print(f"🐎 Non-Runner detected — {name} ({ev}) mid={mid} sid={sid}")
                seen.add(uid)
        except Exception:
            pass
        return (False, "row_missing")
# === PATCH END ===


    # Upsert (idempotent COALESCE)
    sql = ("UPDATE bets SET "
           "anchor_odd = COALESCE(anchor_odd, ?), "
           "OC0        = COALESCE(OC0, ?), "
           "OC0_band   = COALESCE(OC0_band, ?), "
           "placed_at  = COALESCE(placed_at, ?), "
           "timestamp  = COALESCE(timestamp, ?) "
           "WHERE marketId=? AND selectionId=?")

    try:
        con = connect_db(ro=False)
        try:
            p = con.execute("PRAGMA database_list").fetchone()[2]
            dbg(f"[bets.db] writing to: {p}")
        except Exception:
            pass

        cur = con.execute(sql, (lay, lay, band_json, now_iso, now_iso, mid_s, sid_s))
        con.commit()
        return ((cur.rowcount or 0) > 0, "ok" if (cur.rowcount or 0) > 0 else "updated_0")
    except Exception as e:
        dbg(f"[anchors] bets.db write error mid={mid_s} sid={sid_s}: {e}")
        return (False, "db_error")
    finally:
        try: con.close()
        except Exception: pass

# ──────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^def _ensure_inbound_cache_upsertable\(\):\n(?:[ \t].*\n)+?^\s*finally:\n\s*try: con\.close\(\)\n\s*except Exception: pass\n
# ⛏️ ACTION: replace whole _ensure_inbound_cache_upsertable with schema-extension logic
def _ensure_inbound_cache_upsertable():
    """
    Make inbound_oc_cache upsert-safe:
      1) Create the table if missing with all oc1..oc20 + band columns
      2) Remove duplicates by (marketId, selectionId)
      3) Create the exact unique index used by ON CONFLICT(marketId,selectionId)
    """
    import sqlite3
    from engines.config_paths import autoscalp_db

    con = sqlite3.connect(autoscalp_db(), timeout=15, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL")
        # Create table with all columns if missing
        con.execute("""
          CREATE TABLE IF NOT EXISTS inbound_oc_cache(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            anchor_odd REAL,
            oc1 REAL, oc2 REAL, oc3 REAL, oc4 REAL, oc5 REAL,
            oc6 REAL, oc7 REAL, oc8 REAL, oc9 REAL, oc10 REAL,
            oc11 REAL, oc12 REAL, oc13 REAL, oc14 REAL, oc15 REAL,
            oc16 REAL, oc17 REAL, oc18 REAL, oc19 REAL, oc20 REAL,
            oc1_band_json  TEXT, oc2_band_json  TEXT, oc3_band_json  TEXT, oc4_band_json  TEXT, oc5_band_json  TEXT,
            oc6_band_json  TEXT, oc7_band_json  TEXT, oc8_band_json  TEXT, oc9_band_json  TEXT, oc10_band_json TEXT,
            oc11_band_json TEXT, oc12_band_json TEXT, oc13_band_json TEXT, oc14_band_json TEXT, oc15_band_json TEXT,
            oc16_band_json TEXT, oc17_band_json TEXT, oc18_band_json TEXT, oc19_band_json TEXT, oc20_band_json TEXT,
            last_sync_ts TEXT
          )
        """)
        # Backfill any missing ocN/ocN_band_json columns
        existing = {r[1] for r in con.execute("PRAGMA table_info(inbound_oc_cache)")}
        for n in range(1, 21):
            if f"oc{n}" not in existing:
                con.execute(f"ALTER TABLE inbound_oc_cache ADD COLUMN oc{n} REAL")
            if f"oc{n}_band_json" not in existing:
                con.execute(f"ALTER TABLE inbound_oc_cache ADD COLUMN oc{n}_band_json TEXT")

        # Deduplicate
        con.execute("""
            DELETE FROM inbound_oc_cache
            WHERE rowid NOT IN (
              SELECT MIN(rowid) FROM inbound_oc_cache GROUP BY marketId, selectionId
            )
        """)
        # Ensure unique index
        con.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS ux_inbound_oc_cache_mid_sid
            ON inbound_oc_cache(marketId, selectionId)
        """)
    finally:
        try:
            con.close()
        except Exception:
            pass
# === PATCH END ===


def _start_keepalive_thread(get_token, get_app_key, interval=60):
    try:
        import requests
    except Exception:
        logging.warning("[keepalive] 'requests' module missing; keep-alive disabled")
        return None

    def tick():
        tok = (get_token() or "").strip()
        if not tok:
            logging.warning("[session] no token yet; skipping keep-alive tick")
            return
        try:
            r = requests.post(
                "https://identitysso.betfair.com/api/keepAlive",
                headers={"X-Authentication": tok, "X-Application": (get_app_key() or "")},
                timeout=8,
            )
            if r.status_code == 200:
                logging.info("✅ [session] keep-alive OK")
            else:
                logging.warning(f"⚠️ [session] keep-alive HTTP {r.status_code}: {r.text[:120]}")
        except Exception as e:
            logging.error(f"❌ [session] keep-alive error: {e}")

    def loop():
        while True:
            tick(); time.sleep(interval)

    t = threading.Thread(target=loop, name="KeepAliveThread", daemon=True)
    t.start()
    return t

# --- API credential resolver (session rotates; app key from daily_config) ---
def _resolve_api_creds() -> tuple[str | None, str | None]:
    """
    Returns (app_key, session). Also exports them to ENV for helpers that read ENV.
    Precedence:
      session: upgrade_import_patch.get_session_token() -> GUI var -> ENV
      app key: daily_config (BETFAIR_APP_KEY or APP_KEY) -> upgrade_import_patch.get_app_key() -> ENV
    """
    import os
    ak = None
    ss = None

    # app key (static; daily_config first)
    try:
        from engines import daily_config as _dc
        ak = getattr(_dc, "BETFAIR_APP_KEY", None) or getattr(_dc, "APP_KEY", None)
        if ak: ak = str(ak).strip() or None
    except Exception:
        ak = None
    if not ak:
        try:
            from engines.upgrade_import_patch import get_app_key  # type: ignore
            ak = (get_app_key() or "").strip() or None
        except Exception:
            ak = None
    if not ak:
        ak = (os.environ.get("BETFAIR_APP_KEY") or "").strip() or None

    # session (dynamic; Step-1 sets it)
    try:
        from engines.upgrade_import_patch import get_session_token  # type: ignore
        ss = (get_session_token() or "").strip() or None
    except Exception:
        ss = None
    if not ss:
        # Fallback: GUI field (if present)
        try:
            # guard in case we call from outside the class
            ss = (getattr(self, "_token").get() if getattr(self, "_token", None) else "").strip() or None  # type: ignore[name-defined]
        except Exception:
            ss = None
    if not ss:
        ss = (os.environ.get("BETFAIR_SESSION") or "").strip() or None

    # export to ENV so lower layers can find them
    if ak: os.environ["BETFAIR_APP_KEY"] = ak
    if ss: os.environ["BETFAIR_SESSION"] = ss

    return ak, ss

def _db_call_report(log_path: str, limit: int = 50) -> None:
    """
    Quick console summary: unique (db_path, caller_file)->count ordered by count desc.
    Run this at end of day if you want a map.
    """
    try:
        from collections import Counter
        rows = []
        with open(log_path, "r", encoding="utf-8") as f:
            next(f, None)  # skip header
            for line in f:
                parts = [p.strip() for p in line.rstrip("\n").split(",")]
                if len(parts) >= 5:
                    _, dbp, caller_file, caller_func, caller_line = parts[:5]
                    rows.append((dbp, caller_file))
        counts = Counter(rows).most_common(limit)
        print("=== DB CALL REPORT (top) ===")
        for (dbp, cfile), n in counts:
            print(f"{n:5d} → {dbp}  <--  {cfile}")
    except Exception as e:
        print(f"[db_call_report] warn: {e}")

def _fetch_live_odds_smart(mid: str, sid: str) -> dict:
    """
    Unified wrapper for engines.utils.api_tools.fetch_live_odds.
    Always uses the new signature (session_token, marketId, selectionId).
    """
    from engines.utils.api_tools import fetch_live_odds as _flo
    ak, ss = _resolve_api_creds()

    if not ss:
        print(f"[fetch_live_odds_smart] no session token; skipping {mid}/{sid}")
        return {}

    try:
        return _flo(session_token=ss, marketId=str(mid), selectionId=str(sid))
    except Exception as e:
        print(f"[fetch_live_odds_smart] warn {mid}/{sid}: {e}")
        return {}


# --- anchors writer helper (module-level) ---
try:
    from engines.decision_engine.decide_once.helpers import (
        update_bets_anchor_safe as _update_bets_anchor_safe
    )
except Exception:
    # Fallback shim if the canonical helper isn’t available
    # Uses a direct writable connection to bets.db (no DAL / ro flags).
    def _update_bets_anchor_safe(mid: str, sid: str, lay: float, dbg=None) -> tuple[bool, str]:
        """
        Safe anchor upsert into bets.bets for Step 3 / OC0.

        [SCHEMA VERIFIED] bets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId        TEXT    NOT NULL,
            selectionId     INTEGER NOT NULL,
            ...
            anchor_odd      REAL,
            placed_at       TEXT,
            OC0             REAL,
            OC0_band        TEXT,
            ...
        )
        """
        import sqlite3
        import engines.config_paths as cp

        bdb = None
        try:
            # Resolve the canonical bets.db path and open it directly for R/W.
            path = cp.bets_db()
            bdb = sqlite3.connect(path, timeout=8, isolation_level=None)
            bdb.row_factory = sqlite3.Row
            bdb.execute("PRAGMA busy_timeout=8000")
            bdb.execute("PRAGMA journal_mode=WAL")
            bdb.execute("PRAGMA synchronous=NORMAL")

            # ensure column exists (idempotent)
            cols = {r[1] for r in bdb.execute("PRAGMA table_info(bets)")}
            if "anchor_odd" not in cols:
                try:
                    bdb.execute("ALTER TABLE bets ADD COLUMN anchor_odd REAL")
                except Exception:
                    # If ALTER fails (older schema), continue; UPDATE/INSERT will still work if column exists.
                    pass

            mid_s = str(mid)
            sid_s = str(sid)
            lay_f = float(lay)

            # upsert: try UPDATE first…
            cur = bdb.execute(
                "UPDATE bets SET anchor_odd=? WHERE marketId=? AND selectionId=?",
                (lay_f, mid_s, sid_s),
            )
            if cur.rowcount == 0:
                # …if no row, insert minimal row with anchor_odd
                bdb.execute(
                    "INSERT INTO bets(marketId, selectionId, anchor_odd) VALUES(?,?,?)",
                    (mid_s, sid_s, lay_f),
                )

            bdb.commit()
            return True, "ok"

        except Exception as e:
            if dbg:
                dbg(f"bets upsert warn {mid}/{sid}: {e}")
            try:
                if bdb is not None:
                    bdb.rollback()
            except Exception:
                pass
            return False, "db_error"

        finally:
            try:
                if bdb is not None:
                    bdb.close()
            except Exception:
                pass



# ------------------------------------------------------------------------------
# GUI
# ------------------------------------------------------------------------------

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*class PhaseGUI\(tk\.Tk\):\n(?:[ \t].*\n)+?self\.btn4\.grid\(row=4, column=0, columnspan=3, sticky="we", pady=\(6,0\)\)\n\n\n\n\n        # --- Modes \(TEST / LEARNING / LIVE\) .*\n(?:[ \t].*\n)+?self\.status = ttk\.Label\(frm, text="• Ready to run Step 1", foreground="#2c7"\)
# ─────────────────────────────────────────────────────────────────────────────
class PhaseGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AutoScalp – Step Runner")
        self.geometry("720x270")
        self.resizable(True, True)

        # state
        self._token = tk.StringVar()
        self._app_key = _load_app_key_from_daily_config() or ""
        self._markets: list[dict] = []
        self._oc1_thread = None
        self._learn_thread = None
        self._feeder_started = False


        # default mode: last saved, else env, else learning
        _state = _load_state()
        initial_mode = (_state.get("mode")
                        or os.environ.get("AUTOSCALP_MODE")
                        or "learning").lower()
        self._mode = tk.StringVar(value=initial_mode)

        # route DBs right away so everything looks at the same files on launch
        try:
            import engines.config_paths as cp
            cp.set_db_paths(mode=initial_mode, quiet=True)
        except Exception:
            pass


        # layout
        frm = ttk.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)

        # Token
        ttk.Label(frm, text="Session Token:").grid(row=0, column=0, sticky="w")
        ent = ttk.Entry(frm, textvariable=self._token, width=58, show="•")
        ent.grid(row=0, column=1, columnspan=2, sticky="we", padx=(8,0))

        # App key (readonly)
        ttk.Label(frm, text="App Key (daily_config):").grid(row=1, column=0, sticky="w", pady=(8,0))
        self.appkey_lbl = ttk.Label(frm, text=self._mask(self._app_key))
        self.appkey_lbl.grid(row=1, column=1, columnspan=2, sticky="w", padx=(8,0), pady=(8,0))

        # Buttons (step runner)
        self.btn1 = ttk.Button(frm, text="Step 1: Set Credentials", command=self._step1)
        self.btn2 = ttk.Button(frm, text="Step 2: Seed Bets (today+tomorrow)", command=self._step2, state="disabled")
        self.btn3 = ttk.Button(frm, text="Step 3: Seed Anchor Odds (OC0)", command=self._step3, state="disabled")
        self.btn4 = ttk.Button(frm, text="Step 4: Start OC1 Loop", command=self._step4, state="disabled")



        self.btn1.grid(row=3, column=0, sticky="we", pady=(16,6))
        self.btn2.grid(row=3, column=1, sticky="we", padx=8, pady=(16,6))
        self.btn3.grid(row=3, column=2, sticky="we", pady=(16,6))
        self.btn4.grid(row=4, column=0, columnspan=3, sticky="we", pady=(6,0))

        # --- Launch TEST ---
        # Dedicated button (no radio). Pressing this runs TEST fast-path (no Steps).
      
        ttk.Button(frm, text="Launch TEST (Simulator)", command=self._launch_test).grid(
            row=2, column=0, sticky="we", pady=(8,0)
        )

        # --- Modes (LEARNING / LIVE) ---
        ttk.Label(frm, text="Mode:").grid(row=2, column=1, sticky="w", pady=(8,0))
        modes = ttk.Frame(frm)
        modes.grid(row=2, column=2, sticky="w", padx=(8,0), pady=(8,0))

        ttk.Radiobutton(modes, text="Learning", value="learning", variable=self._mode).pack(side="left", padx=(0,12))
        ttk.Radiobutton(modes, text="Live",     value="live",     variable=self._mode).pack(side="left", padx=(0,0))

        # Status
        self.status = ttk.Label(frm, text="• Ready to run Step 1", foreground="#2c7")
        self.status.grid(row=5, column=0, columnspan=3, sticky="w", pady=(16,0))

        # logging to console
        logging.basicConfig(level=logging.INFO, format="%(message)s")


    @staticmethod
    def _mask(s: str) -> str:
        if not s: return "(not found)"
        if len(s) <= 6: return "*" * let(s)
        return s[:3] + "…" + s[-3:]


    def _open_dashboard_window(self):
        from dashboard import DashboardView  # lazy import here
        # Re-focus if already open
        if getattr(self, "_dash_win", None) and self._dash_win.winfo_exists():
            self._dash_win.deiconify()
            self._dash_win.lift()
            self._dash_win.focus_force()
            return

        win = tk.Toplevel(self)
        win.title("Auto Scalping — Live Dashboard")
        win.geometry("1100x730")
        win.minsize(1000, 680)

        container = ttk.Frame(win)
        container.pack(fill="both", expand=True)

        view = DashboardView(parent=container, app=self)
        view.pack(fill="both", expand=True)
        # Kick learning loop in case user launched dashboard first
        self._start_learning_loop_background()
        self._dash_win = win
        self._dash_view = view
        def _on_close():
            self._dash_win = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)
        return win, view

    def _upsert_schedule_from_plan(plan_markets: list[dict]) -> None:
        """Legacy no-op (schedule handled by inbound_oc_cache now)."""
        return


    def _check_loop_alive(self, name: str, run_id: str) -> None:
        """One-shot liveness check a moment after starting the loop thread."""
        import threading
        alive = any(t.name == name and t.is_alive() for t in threading.enumerate())
        print(f"[LOOP] {name} {'ALIVE' if alive else 'NOT-ALIVE'} run_id={run_id}")

    def _log_decision_prereq_probe(self, mode: str = "LIVE") -> None:
        """
        Print a compact snapshot of the essentials the decision loop needs:
          - today schedule rows (future or OFF≤15m)  [AUTO_DB]
          - inbound_oc_cache.oc1 presence            [AUTO_DB]
          - oc_series rows (optional)                [AUTO_DB]
          - strategy registry ORDER contents
        """
        try:
            import sqlite3
            from engines.config_paths import autoscalp_db

            adb_path = autoscalp_db()
            con = sqlite3.connect(adb_path, timeout=8)
            con.row_factory = sqlite3.Row

            # schedule (today, future or in-play window)
            # ✅ Real live count from inbound_oc_cache instead of markets_schedule
            srow = con.execute("""
                SELECT COUNT(DISTINCT marketId) AS n
                  FROM inbound_oc_cache
                 WHERE date(last_sync_ts)=date('now')
            """).fetchone()
            sched_n = int(srow["n"] if srow else 0)



            # oc1 rows (primary health)
            r1 = con.execute("SELECT COUNT(*) AS n FROM inbound_oc_cache WHERE oc1 IS NOT NULL").fetchone()
            auto_oc1 = int(r1["n"] if r1 else 0)

            # oc_series rows (optional)
            if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='oc_series'").fetchone():
                r2 = con.execute("SELECT COUNT(*) AS n FROM oc_series WHERE date(snapshot_ts)=date('now','utc')").fetchone()
                auto_series = int(r2["n"] if r2 else 0)
            else:
                auto_series = 0

            con.close()

            # registry ORDER
            try:
                from engines.decision_engine.strategies import registry as REG
                try:
                    _ = REG.ORDER
                except Exception:
                    pass
                if not getattr(REG, "ORDER", []):
                    try:
                        REG.active_strategies()
                    except Exception:
                        pass
                order_names = [n for (n, _fn) in getattr(REG, "ORDER", [])]
            except Exception:
                order_names = []

            # --- blueprints status ---
            bp_ran = False
            bp_cnt = 0
            bp_path = None
            try:
                import os, json, sqlite3
                from engines.config_paths import autoscalp_db
                adb = sqlite3.connect(autoscalp_db(), timeout=6); adb.row_factory = sqlite3.Row
                # app_kv flag set by builder
                key = f"blueprints_ran_{datetime.now(timezone.utc).date().isoformat()}"
                row = adb.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
                bp_ran = bool(row)
                adb.close()
                # count patterns from the exported file (if present)
                outdir = os.path.join(os.path.dirname(autoscalp_db()), "blueprints")
                today_fn = os.path.join(outdir, f"blueprint_signals_{datetime.now(timezone.utc).date().isoformat()}.json")
                if os.path.exists(today_fn):
                    bp_path = today_fn
                    try:
                        with open(today_fn, "r") as f:
                            data = json.load(f)
                        bp_cnt = len(data or {})
                    except Exception:
                        bp_cnt = 0
            except Exception:
                pass

            # quick odds probe: try first scheduled market
            try:
                import sqlite3
                from engines.config_paths import autoscalp_db
                a = sqlite3.connect(autoscalp_db(), timeout=6); a.row_factory = sqlite3.Row
                r = a.execute("SELECT marketId FROM inbound_oc_cache WHERE date(last_sync_ts)=date('now') LIMIT 1").fetchone()

                a.close()
                have_odds = False
                if r and r["marketId"]:
                    from engines.decision_engine.decide_once.helpers import latest_prices_for_market
                    have_odds = bool(latest_prices_for_market(str(r["marketId"])))
                print(f"[HEALTH] odds:write => {'OK' if have_odds else 'FAIL — empty'}")
            except Exception:
                pass


            print(
                f"[PREREQ] mode={mode} schedule_today={sched_n} "
                f"auto.oc1_rows={auto_oc1} auto.oc_series_rows={auto_series} "
                f"ORDER={order_names or '[]'}"
            )

        except Exception as e:
            print(f"[PREREQ] probe warn: {e}")

            # registry ORDER
            try:
                from engines.decision_engine.strategies import registry as REG
                # ensure ORDER is populated
                try:
                    _ = REG.ORDER
                except Exception:
                    pass
                if not getattr(REG, "ORDER", []):
                    try:
                        REG.active_strategies()
                    except Exception:
                        pass
                order_names = [n for (n, _fn) in getattr(REG, "ORDER", [])]
            except Exception:
                order_names = []

            # print summary — stop reporting BETS oc_series
            print(f"[PREREQ] mode={mode} schedule_today={sched_cnt} auto.oc1_rows={auto_oc1} auto.oc_series_rows={auto_series} "
                  f"ORDER={order_names or '[]'}")

        except Exception as e:
            print(f"[PREREQ] probe warn: {e}")

    def _run_blueprints_if_needed(self, force: bool = False) -> None:
        """
        Run the blueprint builder exactly once per UTC day (or when force=True).
        Safe: never raises; logs concise status lines.
        """
        import sqlite3
        from datetime import datetime, timezone
        from importlib import import_module
        try:
            from engines.config_paths import autoscalp_db
        except Exception:
            print("[blueprints] warn: config paths unavailable")
            return

        # --- once-per-day gate stored in AUTOSCALP_DB.app_kv ---
        today = datetime.now(timezone.utc).date().isoformat()
        key = f"blueprints_ran_{today}"
        ran_already = False
        con = None
        try:
            con = sqlite3.connect(autoscalp_db(), timeout=8, isolation_level=None)
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=8000")
            con.execute("""
                CREATE TABLE IF NOT EXISTS app_kv(
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now','utc'))
                )
            """)
            if not force:
                row = con.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
                ran_already = bool(row)
            if ran_already:
                print("↩️ Blueprints already ran today — skipping.")
                # force refresh of blueprint cache after skip
                try:
                    from engines.blueprint_cache import reload_cache
                    reload_cache()   # make sure bp_cache['bp'] is populated
                    print("[blueprints] cache reloaded from latest JSON")
                except Exception as e:
                    print(f"[blueprints] reload warn: {e}")

                try:
                    con.close()
                except Exception:
                    pass
                return
        except Exception as e:
            # If we can't read the gate, continue but log it.
            print(f"[blueprints] gate warn: {e}")

        # --- import the real builder and run it ---
        builder = None
        last_err = None
        for modname in ("engines.blueprint_build", "blueprint_build"):
            try:
                mod = import_module(modname)
                builder = getattr(mod, "main", None)
                if callable(builder):
                    break
            except Exception as e:
                last_err = e
        if not callable(builder):
            print(f"[blueprints] builder unavailable: {last_err or 'main() not found'}")
            # do not set the ran flag
            try:
                if con:
                    con.close()
            except Exception:
                pass
            return

        # Execute builder (guard SystemExit from older scripts)
        try:
            builder(force=bool(force))
        except SystemExit:
            print("[blueprints] builder attempted to exit() — ignored")
        except Exception as e:
            print(f"[blueprints] builder error: {e}")
            try:
                if con:
                    con.close()
            except Exception:
                pass
            return

        # Mark as ran for today
        try:
            if con is None:
                con = sqlite3.connect(autoscalp_db(), timeout=8, isolation_level=None)
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA busy_timeout=8000")
            con.execute(
                "INSERT OR REPLACE INTO app_kv(key, value, updated_at) VALUES(?, '1', datetime('now','utc'))",
                (key,)
            )
            con.commit()
            print("✅ Running Enhanced Blueprint Builder")
        except Exception as e:
            print(f"[blueprints] flag set warn: {e}")
        finally:
            try:
                if con:
                    con.close()
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*def _open_dashboard_window\(self\):\n(?:[ \t].*\n)+?win\.protocol\("WM_DELETE_WINDOW", _on_close\)\n\n
# ─────────────────────────────────────────────────────────────────────────────
    def _build_mode_menu(self):
        self.mode_var.set(self.mode_var.get() or "LIVE")
        self.btn_live.configure(command=lambda: self._launch_mode("LIVE"))
        self.btn_replay.configure(command=lambda: self._launch_mode("REPLAY"))

    def _launch_mode(self, mode: str):
        """
        Mode switcher. TEST behaves as REPLAY with a compressed clock.
        """
        import datetime as dt
        from engines.config_paths import set_db_paths, DATA_DIR
        set_db_paths(
            mode="test",
            bets=os.path.join(DATA_DIR, "bets.db"),
            autoscalp=os.path.join(DATA_DIR, "autoscalp_gui.db"),
        )

        # Clear any optional banner safely (works whether you use a StringVar or a Label)
        try:
            if hasattr(self, "banner_var") and hasattr(self.banner_var, "set"):
                self.banner_var.set("")
            elif hasattr(self, "banner") and hasattr(self.banner, "configure"):
                self.banner.configure(text="")
        except Exception as _e:
            print("[replay] banner clear skipped:", _e)
  
        m = (mode or "LIVE").upper()
        if m in ("TEST", "REPLAY"):
            # Configure compressed clock (timezone-aware; no utcnow() deprecation)
            try:
                from engines.replay_clock import configure_replay
                yday = (dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)).isoformat()
                # Optional speed control
                speed_x = 60.0
                if hasattr(self, "replay_speed_var"):
                    try:
                        speed_x = float(self.replay_speed_var.get() or 60.0)
                    except Exception:
                        pass
                configure_replay(
                    replay_day_iso=yday,
                    start_at_iso=f"{yday}T10:30:00Z",
                    speed_x=speed_x,
                )
                # Set banner if available
                try:
                    if hasattr(self, "banner_var") and hasattr(self.banner_var, "set"):
                        self.banner_var.set(f"REPLAY x{speed_x:g}")
                    elif hasattr(self, "banner") and hasattr(self.banner, "configure"):
                        self.banner.configure(text=f"REPLAY x{speed_x:g}")
                except Exception as _e2:
                    print("[replay] banner set skipped:", _e2)
            except Exception as e:
                print("[replay] configure_replay failed:", e)

            # ✅ Always open the dashboard (restores old behaviour of Launch TEST)
            try:
                if hasattr(self, "_open_dashboard_window"):
                    self._open_dashboard_window()
                else:
                    # Fallback if method name changes in future
                    from dashboard import DashboardView
                    win = tk.Toplevel(self)
                    win.title("Auto Scalping — Live Dashboard")
                    container = ttk.Frame(win); container.pack(fill="both", expand=True)
                    view = DashboardView(parent=container, app=self); view.pack(fill="both", expand=True)
            except Exception as e:
                print("[GUI] Failed to open dashboard:", e)

        # Kick your existing loops
        if hasattr(self, "_start_loops"):
            self._start_loops()

    def _ensure_creds_and_launch_feeder(self) -> bool:
        """
        Use Step-1 UI token + app key (then DB/ENV/JSON as fallbacks),
        verify via keep-alive, persist betfair_creds.json, and launch feeder once.
        Log any failure to feeder_launch.log. Returns True on successful attempt.
        """
        # log path next to autoscalp_gui.db
        try:
            from engines.config_paths import autoscalp_db
            base = os.path.dirname(autoscalp_db())
        except Exception:
            base = os.path.join(os.path.dirname(__file__), os.pardir, "data")
        os.makedirs(base, exist_ok=True)
        log_path = os.path.join(base, "feeder_launch.log")

        def _log(line: str):
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S%z")
                    f.write(f"[{ts}] {line}\n")
            except Exception:
                pass

        if getattr(self, "_feeder_started", False):
            return True

        # prefer the creds you typed in Step 1
        ak = (self._app_key or "").strip()
        ss = (self._token.get() or "").strip()

        # DB fallback
        if not (ak and ss):
            try:
                from engines.session_secrets import load_betfair_creds
                ak2, ss2 = load_betfair_creds()
                if ak2 and ss2:
                    if not ak:
                        ak = ak2.strip()
                    if not ss:
                        ss = ss2.strip()
            except Exception:
                pass

        # ENV fallback
        ak = ak or (os.environ.get("BETFAIR_APP_KEY") or "").strip()
        ss = ss or (os.environ.get("BETFAIR_SESSION") or "").strip()

        # JSON fallback
        if not (ak and ss):
            try:
                jp = os.path.join(base, "betfair_creds.json")
                if os.path.exists(jp):
                    import json
                    obj = json.load(open(jp, "r", encoding="utf-8"))
                    ak = ak or (obj.get("app_key") or obj.get("application_key") or "").strip()
                    ss = ss or (obj.get("session") or obj.get("session_token") or obj.get("ssoid") or "").strip()
            except Exception:
                pass

        if not (ak and ss):
            _log("ERROR no_creds: missing after UI/DB/ENV/JSON")
            return False

        # Configure adapters now (for this process)
        try:
            from engines.decision_engine import adapters as AD
            AD.lock_cred_source("DB")
            AD.configure_betfair(ak, ss)
            AD.force_reload_creds()
        except Exception as e:
            _log(f"WARN adapters_configure: {e!s}")

        # persist JSON for feeder
        try:
            with open(os.path.join(base, "betfair_creds.json"), "w", encoding="utf-8") as f:
                import json
                json.dump({"app_key": ak, "session": ss}, f)
        except Exception as e:
            _log(f"WARN write_creds_json: {e!s}")

    def _start_memory_guard(self, soft_mb: int = 1024, hard_mb: int = 1536, period_s: int = 15):
        """
        Every period_s seconds:
          - if RSS > soft_mb: run gc.collect()
          - if RSS > hard_mb: ask settlement loop to skip one cycle by touching a flag file
        Never kills the GUI; best-effort cleanup only.
        """
        import threading, time, os
        try:
            import psutil
            proc = psutil.Process()
        except Exception:
            psutil = None
            proc = None

        # flag file next to autoscalp_gui.db to signal “skip once”
        try:
            from engines.config_paths import autoscalp_db
            base = os.path.dirname(autoscalp_db())
        except Exception:
            base = os.path.join(os.path.dirname(__file__), os.pardir, "data")
        os.makedirs(base, exist_ok=True)
        skip_flag = os.path.join(base, ".settlement_skip_once")

        def rss_mb():
            if proc is None:
                return 0.0
            try:
                return proc.memory_info().rss / (1024.0 * 1024.0)
            except Exception:
                return 0.0

        def loop():
            import gc
            while True:
                try:
                    m = rss_mb()
                    if m > float(soft_mb):
                        try: gc.collect()
                        except Exception: pass
                    if m > float(hard_mb):
                        # signal the settlement loop to skip exactly one cycle
                        try:
                            with open(skip_flag, "w") as f:
                                f.write("1\n")
                        except Exception:
                            pass
                    time.sleep(max(5, int(period_s)))
                except Exception:
                    time.sleep(max(5, int(period_s)))

        if getattr(self, "_mem_guard_thread", None) and self._mem_guard_thread.is_alive():
            return
        self._mem_guard_thread = threading.Thread(target=loop, name="MemoryGuard", daemon=True)
        self._mem_guard_thread.start()


        # quick keep-alive probe
        try:
            from engines.decision_engine.adapters import keep_alive
            ok = bool(keep_alive()) or (time.sleep(1.0) or bool(keep_alive()))
            if not ok:
                _log("ERROR keepalive_failed: GUI preflight")
                return False
        except Exception as e:
            _log(f"WARN keepalive_gui: {e!s}")
            # continue — feeder will also verify

        try:
            if hasattr(self, "_restart_feeder_with_creds"):
                self._restart_feeder_with_creds(ak, ss)
            else:
                restart_feeder_with_creds(ak, ss)
            self._feeder_started = True
            _log("INFO feeder_launch_ok")
            return True
        except Exception as e:
            _log(f"ERROR feeder_launch_failed: {e!s}")
            return False

    def _launch_test(self):
        """Back-compat: TEST behaves as REPLAY (compressed clock)."""
        return self._launch_mode("TEST")

    def _preview_tick(self):
        from engines.config_paths import is_replay_mode
        if is_replay_mode():
            from engines.replay_clock import replay_now_utc
            now_utc = replay_now_utc()
        else:
            now_utc = dt.datetime.now(timezone.utc)

        # existing logic that computes next-race header, countdown, bar positions
        # must use 'now_utc' from above
        self._refresh_dashboard(now_utc=now_utc)
        self.after(1000, self._preview_tick)

    # --- log helper used by Step 4 / dashboard ---
    def _append(self, msg: str):
        try:
            print(msg)
            if hasattr(self, "txt_alerts") and self.txt_alerts.winfo_exists():
                self.txt_alerts.insert("end", msg + "\n")
                self.txt_alerts.see("end")
        except Exception:
            pass

    def _seed_test_markets(self, n_markets: int = 10, runners_range=(8, 12)) -> None:
        """Create synthetic markets + runners for TEST (no network)."""
        from random import randint
        from datetime import timedelta

        markets = []
        base = now_utc().replace(microsecond=0)

        # Single writer to BETS_DB for the whole seeding step
        bdb = connect_db(ro=False)
        try:
            # Autocommit + sane pragmas so we don't hold long transactions
            bdb.isolation_level = None              # autocommit per statement
            bdb.execute("PRAGMA journal_mode=WAL")
            bdb.execute("PRAGMA synchronous=NORMAL")
            bdb.execute("PRAGMA busy_timeout=8000")

            # Ensure the minimal table we also seed (join target for TEST builder)
            bdb.execute("""
                CREATE TABLE IF NOT EXISTS inbound_bets_min (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    marketId TEXT NOT NULL,
                    selectionId TEXT NOT NULL,
                    active INTEGER,
                    placed_at TEXT,
                    horse_name TEXT,
                    meta_json TEXT
                )
            """)

            # Backfill columns on older DBs (idempotent)
            cols = [r[1] for r in bdb.execute("PRAGMA table_info(inbound_bets_min)")]
            if "active" not in cols:
                bdb.execute("ALTER TABLE inbound_bets_min ADD COLUMN active INTEGER")
            if "placed_at" not in cols:
                bdb.execute("ALTER TABLE inbound_bets_min ADD COLUMN placed_at TEXT")
            if "horse_name" not in cols:
                bdb.execute("ALTER TABLE inbound_bets_min ADD COLUMN horse_name TEXT")
            if "meta_json" not in cols:
                bdb.execute("ALTER TABLE inbound_bets_min ADD COLUMN meta_json TEXT")

            # Seed markets + runners
            for i in range(n_markets):
                mid = f"1.TEST.MKT.{i+1:03}"
                start = (base + timedelta(minutes=5 + i*2)).isoformat()

                rcount = randint(runners_range[0], runners_range[1])
                runners = []

                for j in range(rcount):
                    sid = 100000 + i*1000 + j
                    name = f"Runner {j+1}"
                    runners.append({"selectionId": sid, "runnerName": name})

                    # (1) Insert minimal 'bets' row using the SAME connection
                    bdb.execute(
                        "INSERT OR IGNORE INTO bets (marketId, selectionId, horse_name, event_name, market_name, race_name, marketStartTime, date, timestamp, meta_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        [
                            mid, sid, name,
                            "TEST", "WIN", f"TEST RACE {i+1}",
                            start, start.split('T')[0], now_utc().isoformat(),
                            json.dumps({"src": "test-seed"})
                        ]
                    )

                    # (2) Ensure TEST builder join table has an ACTIVE row
                    bdb.execute(
                        "INSERT OR IGNORE INTO inbound_bets_min (marketId, selectionId) VALUES (?, ?)",
                        (mid, str(sid))
                    )
                    bdb.execute(
                        "UPDATE inbound_bets_min "
                        "SET active=1, placed_at=COALESCE(placed_at, datetime('now','utc')), "
                        "    horse_name=COALESCE(?, horse_name), meta_json=? "
                        "WHERE marketId=? AND selectionId=?",
                        (name, json.dumps({"src": "test-seed"}), mid, str(sid))
                    )

                markets.append({"marketId": mid, "marketStartTime": start, "runners": runners})

            # autocommit is on; this is effectively a no-op, but harmless
            bdb.commit()

        finally:
            try:
                bdb.close()
            except Exception:
                pass

        self._markets = markets
        print(f"✅ Step 2 OK — TEST seeded {sum(len(m['runners']) for m in markets)} runners across {len(markets)} markets.")
        print("READY: next step")
        self.status.config(text="• Step 2 complete → Run Step 3", foreground="#2c7")
        self.btn3.config(state="normal")


    # ---------- Steps ----------
# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*def _step1\(self\):\n(?:[ \t].*\n)+?
# ─────────────────────────────────────────────────────────────────────────────
    def _step1(self):
        set_step("STEP1")
        from tkinter import messagebox
        """Persist creds + mode to the shim and (if not TEST) start keep-alive."""
        import os, sys
        mode_now = (self._mode.get() or "learning").lower()
        token = (self._token.get() or "").strip()

        # ✅ Always take canonical app key from daily_config
        try:
            from engines.daily_config import APP_KEY as DK_APP
            app_key = (DK_APP or "").strip()
        except Exception:
            app_key = ""

        import engines.config_paths as cp
        cp.set_db_paths(mode=mode_now, quiet=False)
        # Ensure DBs are initialised (after preflight has passed)
        try:
            from engines.config_paths import ensure_db_ready
            ensure_db_ready()
        except Exception as e:
            print(f"[db-preflight] ensure_db_ready warn: {e}")

        try:
            from engines.dashboard_schema import ensure_dashboard_schema
            ensure_dashboard_schema(verbose=True)
        except Exception as e:
            print(f"[schema] GUI pre-flight warn: {e}")


        # --- DB readiness + tracer -----------------------------------------
        ok, msgs = _db_preflight_all(wal=True, do_probe=True)
        for m in msgs:
            print(f"[db-preflight] {m}")
        if not ok:
            # Block Step 1 and show an actionable alert you can paste back to me
            try:
                from tkinter import messagebox
                messagebox.showerror("Database preflight failed",
                                     "One or more databases are not writable/openable.\n"
                                     "See terminal [db-preflight] lines and paste them here.")
            except Exception:
                pass
            print("[db-preflight] ABORTING Step 1 due to failing DB check.")
            self.status.config(text="• Step 1 failed: DB preflight", foreground="#c22")
            return

        # install per-process DB call tracer (writes to data/db_call_log.csv)
        try:
            from engines.config_paths import autoscalp_db
            _install_sqlite_tracer(os.path.join(os.path.dirname(autoscalp_db()), "db_call_log.csv"))
            print("[db-tracer] installed (logging sqlite3.connect calls).")
        except Exception as e:
            print(f"[db-tracer] warn: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: _reset_day_flags_if_needed\(\)\n\s*_save_state\(\{"mode": mode_now\}\)
# ⛏️ ACTION: insert the block **immediately after** those two lines
        _reset_day_flags_if_needed()
        _save_state({"mode": mode_now})

        # Ensure dashboard + cache schemas are ready for all writers
        try:
            # Prefer the central schema module if present
            from engines.dashboard_schema import ensure_dashboard_schema as _ensure_dash
            _ensure_dash()
        except Exception:
            pass
        try:
            _ensure_inbound_cache_upsertable()
            _ensure_odds_current_columns()
            print("[db-preflight] inbound_oc_cache + odds_current ensured.")
        except Exception as e:
            print(f"[db-preflight] schema ensure warn: {e}")

        if not token:
            messagebox.showwarning("Credentials", "Please paste your Betfair session token.")
            return

        # 1) Install the shim (keeps app_key/token in one place for this process)
        _install_upgrade_patch_shim(token, app_key, mode=mode_now)

        # --- Sync mode globally so threads and API tools see it ---
        try:
            import os
            os.environ["APP_MODE"] = mode_now.upper()
            from engines.upgrade_import_patch import set_mode  # type: ignore
            set_mode(mode_now)
            print(f"[MODE] Global mode synced: {mode_now.upper()}")
        except Exception as e:
            print(f"[MODE] sync warn: {e}")


        # 2) OS env (current process + children) — canonical names
        os.environ["BETFAIR_APP_KEY"] = app_key
        os.environ["BETFAIR_SESSION"] = token  # normalize key name

        # inside _step1(), after setting os.environ["BETFAIR_SESSION_TOKEN"]
        os.environ["BETFAIR_SESSION_TOKEN"] = token   # ensure both aliases are populated

        # 3) Persist to secrets DB (single source all processes can read)
        try:
            from engines.session_secrets import save_betfair_creds
            save_betfair_creds(app_key, token)   # our patched version ignores app_key
        except Exception as e:
            print(f"[step1] DB persist warn: {e}")

        # 4) Persist to session store file
        try:
            from engines.session_token import set_session_token
            set_session_token(token)
        except Exception as e:
            print(f"[step1] session store warn: {e}")

        # 5) Make adapters use fresh creds NOW
        try:
            from engines.decision_engine import adapters as AD
            AD.lock_cred_source("DB")
            AD.configure_betfair(app_key, token)
            AD.force_reload_creds()
        except Exception as e:
            print(f"[step1] adapters reload warn: {e}")

        # 6–8: leave your existing code (strategies alias, keepalive, self-check) untouched


        # 6) Strategy import alias (your original block)
        try:
            from engines.upgrade_import_patch import install_strategy_import_alias
            install_strategy_import_alias()
        except Exception as e:
            print(f"[strategies] alias install warn: {e}")

        # 7) Keep-alive for live/learning (use the shim getters if available)
        try:
            from engines.upgrade_import_patch import get_session_token, get_app_key  # type: ignore
        except Exception:
            module = sys.modules.get("engines.upgrade_import_patch")
            get_session_token = (lambda: getattr(module, "_tok", token))
            get_app_key = (lambda: getattr(module, "_app", app_key))
        _start_keepalive_thread(get_session_token, get_app_key, interval=60)

        # 8) Small self-check: what the router/status would resolve right now
        try:
            from engines.betfair_status import _keys as _bf_keys
            ak, tk = _bf_keys()
            print(f"[creds] resolved app={ak[:6]}… token={tk[:6]}… (betfair_status)")
        except Exception as e:
            print(f"[creds] resolve warn: {e}")

        print(f"🧭 Mode selected: {mode_now.upper()}")
        global _BETS_DB, _AUTOSCALP_DB
        print(f"[paths] DATA_DIR={os.path.join(_ROOT,'data')} BETS_DB={_BETS_DB} AUTO_DB={_AUTOSCALP_DB}")

        # Probe strategies (unchanged)
        try:
            from engines.decision_engine.strategies.registry import discover_strategies
            new_act, leg_act, disc, reasons = discover_strategies()
            print(f"[strategies] discovered={len(disc)}")
            print(f"[strategies] active(NEW)={new_act}")
            print(f"[strategies] active(LEGACY)={leg_act}")
            for r in reasons: print(f"[strategies] {r}")
        except Exception as e:
            print(f"[strategies] probe failed: {e}")

        print("✅ Step 1 OK — token/app key set.")
# === PATCH START ===
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: print("✅ Step 1 OK — token/app key set.")
# ⛏️ ACTION: insert bank_state init just before the print

        # --- Initialise internal bank (persisted in autoscalp_gui.db) ---
        try:
            from engines.live import bank_state
            bank_state.reset_for_live()          # seeds + ensures internal_bank
            bal = bank_state.get_balance()
            if bal == 0.0:
                # Fallback: refresh from in-memory live balance
                bal = bank_state.get_live_balance()
            print(f"[GUI] LiveBalance display: {bal:.2f}")
        except Exception as e:
            print(f"[bank_state] init warn: {e}")

        print("✅ Step 1 OK — token/app key set.")


        print("READY: next step")
        self.status.config(text="• Step 1 complete → Run Step 2", foreground="#2c7")
# === PATCH START ===
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: self\.btn2\.config\(state="normal"\)
# 📆 PATCHED: 2025-11-19T11:20Z — make global hijack optional via env flag
# ──────────────────────────────────────────────────────────────────────
        self.btn2.config(state="normal")

        # Optional: only activate global Hijack/AlphaX when explicitly requested.
        # Default behaviour (no env set) = DAL-only, no sqlite3.connect hijack.
        try:
            import os
            use_hijack = os.getenv("AUTOSCALP_USE_HIJACK", "0") == "1"
            if use_hijack:
                from engines import database_hijack_monitor as dbh
                dbh.activate_hijack()
            else:
                print("[HIJACK] skipped — AUTOSCALP_USE_HIJACK!=1 (DAL-only mode).")
        except Exception as e:
            print(f"[HIJACK] activate warn: {e}")
# === PATCH END ===






        # Do NOT auto-launch here — Step 4 will own feeder launch
        # --- LiveRouter runtime initialisation (after Step-1 only) ---
        try:
            from engines.live.live_router import init_live_router
            init_live_router()     # runs orphan-run repair safely
            print("[live_router] startup repair run.")
        except Exception as e:
            print(f"[live_router] init warn: {e}")

        print("[feeder] will launch on Step 4")

        # Also persist JSON alongside DB so any external feeder can read creds
        try:
            from engines.config_paths import autoscalp_db
            base = os.path.dirname(autoscalp_db())
            os.makedirs(base, exist_ok=True)
            with open(os.path.join(base, "betfair_creds.json"), "w", encoding="utf-8") as f:
                json.dump({"app_key": self._app_key or "", "session": token}, f)
        except Exception:
            pass






# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*def _step2\(self\):\n(?:[ \t].*\n)+?
# ─────────────────────────────────────────────────────────────────────────────
    def _step2(self):
        set_step("STEP2")
        from tkinter import messagebox
        """Ensure markets are in memory (self._markets). Prefer API; fallback to bets.db."""
        from engines.config_paths import connect_db, autoscalp_db
        import sqlite3, datetime as _dt
        from datetime import timezone


        # fast short-circuit: if we already seeded enough runners today, skip
        try:
            bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
            today = _dt.datetime.now(timezone.utc).date().isoformat()
            row = bdb.execute("""
                SELECT COUNT(DISTINCT marketId || '/' || selectionId) AS c
                FROM bets
                WHERE date(COALESCE(marketStartTime, timestamp)) IN (date('now'), date('now','utc'))
            """).fetchone()
            already = int(row["c"] or 0)
            bdb.close()
        except Exception:
            already = 0

        # check a small day-flag and a runner threshold (tune 400 to your taste)
        skip_seed = (already >= 400)
        try:
            adb = sqlite3.connect(autoscalp_db(), timeout=8)
            adb.row_factory = sqlite3.Row
            if skip_seed:
                # Load into memory even when we skip DB seeding
                fb = _load_markets_from_bets_today()
                if fb:
                    self._markets = fb
                    total_runners = sum(len(m.get("runners") or []) for m in self._markets)
                    print(f"✅ Step 2 OK — using existing runner seed for today "
                          f"(markets={len(self._markets)} runners={total_runners}).")
                    print("READY: next step")
                    self.status.config(text="• Step 2 complete → Run Step 3", foreground="#2c7")
                    self.btn3.config(state="normal")
                    return
                # Fallback: don’t skip if nothing to load
                skip_seed = False


   
        finally:
            try: adb.close()
            except Exception: pass

        self.btn2.config(state="disabled")

        def run():
            try:
                # Make sure tables exist
                from engines.db_migrations import ensure_tables
                ensure_tables()

                mode = (self._mode.get() or "learning").lower()
                seeded = False

                if mode == "test":
                    # Your existing synthetic seeding path (unchanged)
                    self._seed_test_markets(n_markets=10, runners_range=(8, 12))
                    return

                # LIVE/LEARNING: try normal fetch first
                try:
                    from engines.get_markets import get_markets_and_insert
                    mkts = get_markets_and_insert() or []
                except Exception as e:
                    print(f"[Step2] API fetch warn: {e}")
                    mkts = []

                # Filter to today/tomorrow if you want; otherwise accept all provided by helper
                if mkts and any(m.get("runners") for m in mkts):
                    self._markets = mkts
                    seeded = True
                else:
                    # Fallback: load from bets.db that’s already seeded earlier today
                    fb = _load_markets_from_bets_today()
                    if fb:
                        self._markets = fb
                        seeded = True

                # --- NORMALIZE: ensure marketStartTime is AWARE-UTC ISO (…Z) for all markets ---
                def _norm_iso_utc(ts):
                    try:
                        from datetime import datetime, timezone
                        t = str(ts).strip()
                        if "T" not in t:
                            t = t.replace(" ", "T", 1)  # "YYYY-MM-DD HH:MM:SS" -> "YYYY-MM-DDTHH:MM:SS"
                        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
                        if dt.tzinfo is None:
                            dt = dt.replace(tzinfo=timezone.utc)
                        else:
                            dt = dt.astimezone(timezone.utc)
                        return dt.isoformat(timespec="seconds").replace("+00:00", "Z")
                    except Exception:
                        return ts

                if seeded:
                    for m in self._markets:
                        ts = m.get("marketStartTime")
                        if ts:
                            m["marketStartTime"] = _norm_iso_utc(ts)

                if not seeded:
                    from tkinter import messagebox
                    messagebox.showwarning(
                        "Seed Bets",
                        "No markets with runners were found from API or bets.db.\n"
                        "Check your session token (Step 1) or run again later."
                    )
                    print("⚠️ Step 2 could not load markets/runners (API & fallback empty).")
                    self.btn2.config(state="normal")
                    return

                total_runners = sum(len(m.get("runners") or []) for m in self._markets)
                # mark seed-done for today
                try:
                    adb = sqlite3.connect(autoscalp_db(), timeout=8)
                    adb.execute("CREATE TABLE IF NOT EXISTS app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
                    adb.execute("INSERT OR REPLACE INTO app_kv(key,value,updated_at) VALUES(?, '1', datetime('now','utc'))",
                                (f"seed_done_bets_{_dt.datetime.now(timezone.utc).date().isoformat()}",))
                    adb.commit(); adb.close()
                except Exception:
                    pass

                print(f"✅ Step 2 OK — markets={len(self._markets)} runners={total_runners}.")
                print("READY: next step")
                self.status.config(text="• Step 2 complete → Run Step 3", foreground="#2c7")
                self.btn3.config(state="normal")

            except Exception as e:
                print(f"❌ Step 2 failed: {e}")
                from tkinter import messagebox
                messagebox.showerror("Seed Bets", str(e))
                self.btn2.config(state="normal")

        import threading
        threading.Thread(target=run, daemon=True).start()



# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*def _step3\(self\):\n(?:[ \t].*\n)+?
# ─────────────────────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*def _step3\(self\):\n\s*"""[\s\S]*?^        threading\.Thread\(target=run, daemon=True\)\.start\(\)\n
# ⛏️ ACTION: replace the entire _step3 function block with backfill-first logic

    def _step3(self):
        set_step("STEP3")
        """
        Step 3 (LIVE/LEARNING): write anchors (OC0) robustly.
        Backfill-first:
          - If BETS already has anchors today but AUTO inbound cache is empty, copy anchors
            to AUTO.inbound_oc_cache immediately (no network).
          - Else, fetch live odds and seed anchors where missing.
        Always idempotent; never blocks the tool.
        """
        import os, json, threading, logging, sqlite3
        from datetime import datetime, timezone
        from tkinter import messagebox
        from engines.config_paths import autoscalp_db, connect_db

        # ensure unique keys so ON CONFLICT works
        def _ensure_unique_indices_once():
            import sqlite3
            from engines.config_paths import autoscalp_db, connect_db

            # inbound_oc_cache (autoscalp_gui.db)
            try:
                con = sqlite3.connect(autoscalp_db(), timeout=8, isolation_level=None)
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA busy_timeout=8000")
                con.execute("""
                    CREATE TABLE IF NOT EXISTS inbound_oc_cache(
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      marketId TEXT NOT NULL,
                      selectionId TEXT NOT NULL,
                      anchor_odd REAL,
                      last_sync_ts TEXT
                      -- OCn columns can already exist; we don't redeclare them here
                    )
                """)
                con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_inbound_oc_cache_mid_sid ON inbound_oc_cache(marketId, selectionId)")
                con.close()
            except Exception:
                pass

            # dashboard_runners (autoscalp_gui.db) – top6 upserts use (day, marketId, selectionId)
            try:
                con = sqlite3.connect(autoscalp_db(), timeout=8, isolation_level=None)
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA busy_timeout=8000")
                con.execute("""
                    CREATE TABLE IF NOT EXISTS dashboard_runners(
                      day TEXT,
                      marketId TEXT,
                      selectionId TEXT,
                      -- other columns…
                      PRIMARY KEY(day, marketId, selectionId)
                    )
                """)
                con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_dash_run_day_mid_sid ON dashboard_runners(day, marketId, selectionId)")
                con.close()
            except Exception:
                pass

        # small logger to both console and a Step-3 file
        log_path = os.path.join(os.path.dirname(autoscalp_db()), "step3_anchor_debug.log")
        def dbg(msg: str):
            try:
                print(f"[anchors] {msg}")
                with open(log_path, "a", encoding="utf-8") as f:
                    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"[{ts}] {msg}\n")
            except Exception:
                pass

        # make sure indices/tables exist (we forgot to call this before)
        _ensure_unique_indices_once()

        # also ensure app_kv + inbound unique idx from helpers (safe if missing)
        try:
            from engines.decision_engine.decide_once.helpers import ensure_inbound_schema
            ensure_inbound_schema()
        except Exception:
            pass

        if not getattr(self, "_markets", None):
            try:
                mkts = _load_markets_from_bets_today()
                if mkts:
                    self._markets = mkts
                    print(f"[SCOPE] auto-restored {len(mkts)} markets from bets.db for Step 4.")
                    try:
                        from engines.decision_engine.orchestrator import _upsert_schedule_from_plan
                        _upsert_schedule_from_plan(self._markets)
                        print(f"[SCOPE] upserted {len(self._markets)} markets into markets_schedule.")
                    except Exception as e:
                        print(f"[SCOPE] schedule upsert warn: {e}")
            except Exception as e:
                print(f"[SCOPE] auto-restore failed: {e}")


        # Choose mode + token (network path only)
        mode_now = (self._mode.get() or "learning").lower()
        os.environ["AUTOSCALP_MODE"] = mode_now
        try:
            from engines.upgrade_import_patch import get_session_token  # type: ignore
            token = (get_session_token() or "").strip()
        except Exception:
            token = (self._token.get() or "").strip()

        # helper: DIRECT odds fetch (already fixed)
        from engines.utils.api_tools import fetch_live_odds

        # helper: upsert inbound cache anchor in autoscalp_gui.db (best-effort)
        def _upsert_auto_cache_anchor(mid: str, sid: str, lay: float):
            try:
                con = sqlite3.connect(autoscalp_db(), timeout=8, isolation_level=None)
                con.execute("PRAGMA journal_mode=WAL")
                con.execute("PRAGMA busy_timeout=8000")
                con.execute(
                    "INSERT INTO inbound_oc_cache(marketId,selectionId,anchor_odd,last_sync_ts) "
                    "VALUES(?,?,?, datetime('now','utc')) "
                    "ON CONFLICT(marketId,selectionId) DO UPDATE SET "
                    "anchor_odd=COALESCE(inbound_oc_cache.anchor_odd, excluded.anchor_odd), "
                    "last_sync_ts=datetime('now','utc')",
                    (str(mid), str(sid), float(lay))
                )
                con.commit()
                con.close()
            except Exception as e:
                dbg(f"auto_cache upsert warn {mid}/{sid}: {e}")

        # --- NEW: backfill-first branch (no network needed) -----------------------
        try:
            bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
            bets_anchors = int(bdb.execute(
                "SELECT COUNT(*) FROM bets WHERE date(marketStartTime)=date('now','utc') AND anchor_odd IS NOT NULL"
            ).fetchone()[0] or 0)
            bdb.close()
        except Exception:
            bets_anchors = 0

        try:
            adb = sqlite3.connect(autoscalp_db(), timeout=6)
            auto_anchors = int(adb.execute(
                "SELECT COUNT(*) FROM inbound_oc_cache WHERE date(last_sync_ts)=date('now','utc') AND anchor_odd IS NOT NULL"
            ).fetchone()[0] or 0)
            adb.close()
        except Exception:
            auto_anchors = 0

        if bets_anchors > 0 and auto_anchors < bets_anchors:

            # copy anchors from BETS → AUTO to seed inbound cache for decision path
            try:
                from engines.decision_engine.decide_once.helpers import upsert_anchor_batch_from_bets
                upsert_anchor_batch_from_bets()  # today UTC
            except Exception as e:
                dbg(f"backfill warn: {e}")

            # recount AUTO after backfill
            try:
                adb = sqlite3.connect(autoscalp_db(), timeout=6)
                auto_anchors = int(adb.execute(
                    "SELECT COUNT(*) FROM inbound_oc_cache WHERE date(last_sync_ts)=date('now','utc') AND anchor_odd IS NOT NULL"
                ).fetchone()[0] or 0)
                adb.close()
            except Exception:
                pass

            dbg(f"summary attempts={bets_anchors} wrote={auto_anchors} reasons={{\"backfill\": 1}} "
                f"bets_today={bets_anchors} auto_anchor_mkts={auto_anchors}")

            print(f"✅ Step 3 verification — anchors today: bets={bets_anchors} auto={auto_anchors} "
                  f"(wrote {auto_anchors}, attempted {bets_anchors})")

            # enable next step immediately; OC timeline will progress and candidates can read inbound now
            self.status.config(text="• Step 3 complete (backfilled) → Run Step 4",
                               foreground="#2c7")
            self.btn4.config(state="normal")
            return
        # --------------------------------------------------------------------------

        # run writer in background so UI stays snappy (network path for missing anchors)
        def run():
            wrote = 0
            attempts = 0
            reasons: dict[str, int] = {"row_missing": 0, "updated_0": 0, "db_error": 0, "no_price": 0}

            # only write anchors where missing (speeds up relaunch)
            missing_map = {}  # mid -> set(selectionId)
            try:
                bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
                rows = bdb.execute("""
                    SELECT marketId AS mid, selectionId AS sid
                    FROM bets
                    WHERE anchor_odd IS NULL
                      AND date(COALESCE(marketStartTime, date('now'))) IN (date('now'), date('now','utc'))
                """).fetchall()
                for r in rows:
                    missing_map.setdefault(str(r["mid"]), set()).add(str(r["sid"]))
                bdb.close()
            except Exception:
                missing_map = {}  # fail-open: will process everything

            # iterate today’s markets/runners
            for m in (self._markets or []):
                mid = str(m.get("marketId") or "")
                for r in (m.get("runners") or []):
                    sid = str(r.get("selectionId"))
                    if not mid or sid is None:
                        continue
                    attempts += 1
                    # skip if this pair already has an anchor today
                    if missing_map and sid not in (missing_map.get(mid) or set()):
                        continue

                    # 1) get a live price (DIRECT)
                    odds = {}
                    try:
                        odds = _fetch_live_odds_smart(mid, sid) or {}
                    except Exception as e:
                        dbg(f"fetch err {mid}/{sid}: {e}")
                        odds = {}

                    lay = odds.get("lay") or odds.get("back")
                    if not lay:
                        reasons["no_price"] += 1
                        continue

                    # 2) write to BETS_DB (anchor, OC0, band, timestamps)
                    ok, why = _update_bets_anchor_safe(mid, sid, float(lay), dbg=dbg)

                    # Reflect anchor into AUTO.inbound_oc_cache so inbound readers see it
                    try:
                        _upsert_auto_cache_anchor(mid, str(sid), float(lay))
                    except Exception as e:
                        dbg(f"auto_cache upsert warn {mid}/{sid}: {e}")

                    if ok:
                        wrote += 1
                    else:
                        reasons[why] = reasons.get(why, 0) + 1
                        dbg(f"write miss {mid}/{sid} reason={why}")

            # verify counts after writes
            bets_anchors = 0
            auto_markets = 0
            try:
                bdb = connect_db(ro=True)
                try:
                    bets_anchors = int(bdb.execute(
                        "SELECT COUNT(*) FROM bets WHERE date(marketStartTime)=date('now','utc') "
                        "AND anchor_odd IS NOT NULL"
                    ).fetchone()[0] or 0)
                    try:
                        p = bdb.execute("PRAGMA database_list").fetchone()[2]
                        dbg(f"[bets.db] verify path: {p}")
                    except Exception:
                        pass
                finally:
                    bdb.close()
            except Exception as e:
                dbg(f"verify bets.db warn: {e}")

            try:
                adb = sqlite3.connect(autoscalp_db(), timeout=6)
                auto_markets = int(adb.execute(
                    "SELECT COUNT(DISTINCT marketId) FROM inbound_oc_cache "
                    "WHERE date(last_sync_ts)=date('now','utc') AND anchor_odd IS NOT NULL"
                ).fetchone()[0] or 0)
                adb.close()
            except Exception as e:
                dbg(f"verify auto warn: {e}")

            dbg(f"summary attempts={attempts} wrote={wrote} reasons={json.dumps(reasons)} "
                f"bets_today={bets_anchors} auto_anchor_mkts={auto_markets}")

            print(f"✅ Step 3 verification — anchors today: bets={bets_anchors} auto={auto_markets} "
                  f"(wrote {wrote}, attempted {attempts})")

            self.status.config(text="• Step 3 complete → Run Step 4 (OC timeline will treat first price as anchor if missing)",
                               foreground="#2c7")
            self.btn4.config(state="normal")

        threading.Thread(target=run, daemon=True).start()

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*def _step4\(self\):\n(?:[ \t].*\n)+?
# ─────────────────────────────────────────────────────────────────────────────
    def _step4(self):
        set_step("STEP4")
        from tkinter import messagebox
        """Start OC timeline loop (OC1…OC20 on schedule, with band accumulation)."""
        import os, threading, time, logging
        from engines.decision_engine.orchestrator import _upsert_schedule_from_plan

        from tkinter import messagebox
        _ensure_inbound_cache_unique_index()
        _ensure_autoscalp_tables()
        mode_now = (self._mode.get() or "learning").lower()
        os.environ["AUTOSCALP_MODE"] = mode_now


        # === PATCH START ===
        # 📍 TARGET: gui/GUI.py:_step4
        # 📆 PATCHED: 2025-11-22 — Move LiveLoop start BEFORE any writer threads

        # --- Start Live/Learning loops (must occur BEFORE writers start) ---
        if not any(t.name in ("LiveLoop", "LearningLoop") and t.is_alive()
                   for t in threading.enumerate()):
            from datetime import datetime, timezone
            run_id_prefix = "LIVE" if mode_now == "live" else "LEARN"
            run_id = f"{run_id_prefix}-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}"

            if mode_now == "live":
                def _live_target():
                    import traceback
                    print(f"[LOOP] LiveLoop thread booting run_id={run_id}")

                    try:
                        self._log_decision_prereq_probe(mode="LIVE")
                    except Exception as e:
                        print(f"[LOOP] prereq probe warn (LIVE): {e}")

                    try:
                        from engines.upgrade_import_patch import set_session_token, set_app_key
                        set_session_token(os.environ.get("BETFAIR_SESSION") or "")
                        set_app_key(os.environ.get("BETFAIR_APP_KEY") or "")

                        # LIVE LOOPS EARLY — DAL/Hijack-safe startup
                        from engines.decision_engine.orchestrator import start_live_loop
                        start_live_loop(run_id=run_id, hz=2, logger=lambda m: print(m))

                    except BaseException as e:
                        print(f"[LOOP] LiveLoop fatal: {e}")
                        traceback.print_exc()
                    finally:
                        print(f"[LOOP] LiveLoop thread exited run_id={run_id}")

                t = threading.Thread(target=_live_target,
                                     name="LiveLoop", daemon=True)
                t.start()
                print(f"[LOOP] LiveLoop start requested (run_id={run_id})")

                try:
                    self.after(1500, lambda: self._check_loop_alive("LiveLoop", run_id))
                except Exception:
                    pass

            else:
                def _learn_target():
                    import traceback
                    print(f"[LOOP] LearningLoop thread booting run_id={run_id}")

                    try:
                        self._log_decision_prereq_probe(mode="LEARNING")
                    except Exception as e:
                        print(f"[LOOP] prereq probe warn (LEARNING): {e}")

                    try:
                        from engines.decision_engine.orchestrator import start_learning_loop
                        start_learning_loop(run_id=run_id, hz=2, logger=lambda m: print(m))

                    except BaseException as e:
                        print(f"[LOOP] LearningLoop fatal: {e}")
                        traceback.print_exc()
                    finally:
                        print(f"[LOOP] LearningLoop thread exited run_id={run_id}")

                t = threading.Thread(target=_learn_target,
                                     name="LearningLoop", daemon=True)
                t.start()
                print(f"[LOOP] LearningLoop start requested (run_id={run_id})")

                try:
                    self.after(1500, lambda: self._check_loop_alive("LearningLoop", run_id))
                except Exception:
                    pass

        # === PATCH END ===


        from engines.live import settlements
        settlements.start_settlement_daemon(interval_s=60)  # every minute



# === PATCH START ===
# 📍 TARGET: gui/GUI.py:_step4
# 📆 PATCHED: 2025-10-16Z — wire live matched-odds updater into Step 4 loop
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # ── live matched-odds refresh (Betfair fills → GUI) ─────────────
        from engines.config_paths import enable_live_dal
        enable_live_dal()


# === PATCH START ===
# 📍 TARGET: gui/GUI.py:_step4()
# 📆 PATCHED: 2025-10-09Z — self-healing scope restore + clean tick prep
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Auto-restore markets if GUI restarted or scope empty
        if not getattr(self, "_markets", None):
            try:
               
                mkts = _load_markets_from_bets_today()
                if mkts:
                    self._markets = mkts
                    print(f"[SCOPE] auto-restored {len(mkts)} markets from bets.db for Step 4.")
                    try:
                        from engines.decision_engine.orchestrator import _upsert_schedule_from_plan
                        _upsert_schedule_from_plan(self._markets)
                        print(f"[SCOPE] upserted {len(self._markets)} markets into markets_schedule.")
                    except Exception as e:
                        print(f"[SCOPE] schedule upsert warn: {e}")
            except Exception as e:
                print(f"[SCOPE] auto-restore failed: {e}")


        # --- ensure live scope pre-populated before LiveLoop start ---
        try:
            from engines.decision_engine.decide_once import scope
            from engines.market_monitor import monitor
            import time

            # call once to build initial list
            scope.build_and_maintain_scope(show_dashboard=False)
            mids = scope._SCOPE_STATE.get("markets") or []

            # actively refresh monitor until we have odds for those mids
            for _ in range(5):  # up to ~5 seconds total
                monitor.refresh(mids)
                time.sleep(0.1)
                monitor.refresh(mids)      # 2nd pass (px → band)
                filled = sum(bool(monitor.get_market_state(m).get("runners")) for m in mids)
                if filled:
                    break
                time.sleep(1.0)

            scope.build_and_maintain_scope(show_dashboard=False)
            n_mkts = len(scope._SCOPE_STATE.get("markets", []))
            print(f"[SCOPE] preloaded {n_mkts} markets before LiveLoop start")

        except Exception as e:
            print(f"[SCOPE] preload warn: {e}")


        # --- HYBRID: auto-validate creds (same effect as pressing Validate) ---
        try:
            # Prefer UI entries, then env, then any stored secrets.
            ak = getattr(self, "_cred_ak", None)
            ss = getattr(self, "_cred_ss", None)

            # Fallback to DB secrets if UI fields aren’t set yet
            if not (ak and ss):
                try:
                    from engines.session_secrets import load_betfair_creds
                    ak2, ss2 = load_betfair_creds()
                    if ak2 and ss2:
                        ak, ss = ak2, ss2
                        self._cred_ak, self._cred_ss = ak, ss
                except Exception:
                    pass
            try:
                # optional secret store
                from engines.session_secrets import get_secret, set_secret, APP_KEY_KEY, SESSION_KEY  # type: ignore
                if not ak:
                    ak = (get_secret(APP_KEY_KEY) or "").strip()
                if not ss:
                    ss = (get_secret(SESSION_KEY) or "").strip()
                # refresh stored values so adapters/routers can read them later
                if ak: set_secret(APP_KEY_KEY, ak)
                if ss: set_secret(SESSION_KEY, ss)
            except Exception:
                pass

            # Configure adapters (same side-effects as Validate button).
            try:
                from engines.decision_engine.adapters import ensure_betfair_ready  # type: ignore
                # Many builds read creds from env/secret store; setting env helps all codepaths.
                if ak: os.environ["BETFAIR_APP_KEY"] = ak
                if ss: os.environ["BETFAIR_SESSION"] = ss
                try:
                    ensure_betfair_ready()  # signatureless in our builds; returns bool
                except TypeError:
                    # some versions accept explicit args
                    ensure_betfair_ready(app_key=ak, session=ss)  # type: ignore
            except Exception:
                pass

            # Launch feeder the same way your manual path does, if creds present.
            if ak and ss and hasattr(self, "_restart_feeder_with_creds"):
                try:
                    self._restart_feeder_with_creds(ak, ss)
                    print("[FEEDER] launched via Step-4 auto-validate")
                except Exception as e:
                    print(f"[FEEDER] auto-launch error: {e}")
        except Exception:
            # Never block Step 4 if auto-validate fails; OC loop will still run.
            pass

        # --- after feeder launched, backfill schedule into bets.db
        def _retry_upsert_schedule():
            import time
            tries = 0
            while tries < 5:   # 5 retries max
                try:
                    if getattr(self, "_markets", None):
                        _upsert_schedule_from_plan(self._markets)
                        print(f"[SCHEDULE] upserted {len(self._markets)} markets into markets_schedule")
                        return
                except Exception as e:
                    print(f"[SCHEDULE][WARN] upsert attempt {tries+1} failed: {e}")
                tries += 1
                time.sleep(3)   # wait a few seconds before retry
            print("[SCHEDULE][FAIL] could not upsert schedule after retries")

        import threading
        threading.Thread(target=_retry_upsert_schedule, daemon=True).start()


        # --- TEST mode: keep existing behavior ---
        if mode_now == "test":
            messagebox.showinfo(
                "OC Loop (TEST)",
                "TEST mode bypasses the live OC loop. Use Dashboard → Tests to run synthetic OC timelines."
            )
            return

        # --- guards you already had ---
        if not self._markets:
            messagebox.showwarning("OC Loop", "No markets in memory. Run Step 2 first.")
            return
        if getattr(self, "_oc1_thread", None) and self._oc1_thread.is_alive():
            messagebox.showinfo("OC Loop", "OC loop already running.")
            return

        # Run Blueprints once/day as part of the load sequence
        try:
            
            self._run_blueprints_if_needed(force=False)
        except Exception as _e:
            print(f"[blueprints] helper warn: {_e}")


        # --- your existing OC loop (unchanged) ---
        def loop():
            """Minimal resilient live OC loop (keeps UI responsive if live odds fail)."""
            try:
                # Get token once per loop iteration to allow re-auth without restart.
                from engines.upgrade_import_patch import get_session_token  # type: ignore
            except Exception:
                get_session_token = lambda: (self._token.get() or "").strip()

            def _snap(v: float) -> float:
                try:
                    from engines.price_math import snap_to_tick
                    return snap_to_tick(float(v))
                except Exception:
                    return round(float(v), 2)

            # cadence in seconds (keep modest to avoid UI churn)
            CADENCE = 2.0
            while True:
                token = (get_session_token() or "").strip()
                for m in self._markets:
                    mid = m.get("marketId")
                    start_iso = m.get("marketStartTime")
                    if not mid or not start_iso:
                        continue

                    # gui/GUI.py Step-4 loop(), before minutes_to_off(...)
                    from engines.utils.datetime_norm import to_iso_utc
                    start_iso = to_iso_utc(m.get("marketStartTime"))
                    mto = minutes_to_off(start_iso)


                    # Which OC number is due right now?
                    mto = minutes_to_off(start_iso)
                    if mto is None:
                        continue
                    ordered = sorted(OC_SCHEDULE_MINUTES.items(), key=lambda kv: kv[1])
                    due = [n for (n, thr) in ordered if n >= 1 and mto <= thr]
                    current_n = max(due) if due else 0

                    for r in (m.get("runners") or []):
                        sid = r.get("selectionId")
                        if sid is None:
                            continue
                        try:
                            odds = fetch_live_odds(token, mid, sid) or {}
                            lay = odds.get("lay")
                            if lay is None:
                                continue
                            lay_val = _snap(lay)

                            # Backfill anchor if missing
                            rows = _sqlite_exec(
                                "SELECT anchor_odd FROM inbound_oc_cache WHERE marketId=? AND selectionId=? LIMIT 1",
                                [mid, sid], fetch=True)
                            has_anchor = bool(rows and rows[0] and rows[0][0] is not None)
                            if not has_anchor:
                                _update_bets_anchor(mid, sid, lay_val)
                                _autoscalp_upsert_cache_anchor(mid, sid, lay_val)
                                _record_oc_series(mid, sid, "OC0", lay_val, source="SIM", meta={"odds": odds})

                            # Pre-OC1: keep recording OC0 as band samples
                            if current_n == 0:
                                _record_oc_series(mid, sid, "OC0", lay_val, source="SIM", meta={"odds": odds})
                                continue

                            # OC1..OC20: write current value and grow band
                            if not _autoscalp_cache_has_oc(mid, sid, current_n):
                                label = f"OC{current_n}"
                                _record_oc_series(mid, sid, label, lay_val, source="SIM", meta={"odds": odds})
                                _autoscalp_update_cache_oc(mid, sid, label, lay_val, band_json=[lay_val])
                            else:
                                # append to band json (cheap update: set last value; impl already handles append)
                                label = f"OC{current_n}"
                                _autoscalp_update_cache_oc(mid, sid, label, lay_val, band_json=None)

                        except Exception as e:
                            # quiet reserve/no-odds + transient issues
                            import time, logging

                            # simple rate-limiter state
                            if not hasattr(logging, "_oc_last"):
                                logging._oc_last = {}

                            key = f"{mid}/{sid}"
                            now = time.time()

                            # once per minute per runner: lightweight "no price" info
                            last_min = logging._oc_last.get(("min", key), 0.0)
                            if now - last_min >= 60.0:
                                logging.info("timeline: transient network/no price")
                                logging._oc_last[("min", key)] = now

                            # once per 30s per runner: full exception detail to OC timeline log
                            last_30 = logging._oc_last.get(("30s", key), 0.0)
                            if now - last_30 >= 30.0:
                                msg = str(e)
                                benign = (
                                    "unable to open database file",
                                    "database is locked",
                                    "busy",
                                    "NameResolutionError",
                                    "Max retries exceeded",
                                    "No odds",
                                )
                                lvl = logging.DEBUG if any(b in msg for b in benign) else logging.WARNING
                                _oc_timeline_log(mid, sid, f"fetch error: {e}", lvl)
                                logging._oc_last[("30s", key)] = now






                try:
                    DBQ.join()
                except Exception:
                    pass
                _time.sleep(CADENCE)

        self._oc1_thread = threading.Thread(target=loop, name="OC_Timeline_Loop", daemon=True)
        self._oc1_thread.start()
        # after starting OC_Timeline_Loop and LiveLoop/LearningLoop …
        try:
            self._start_runtime_watch()
        except Exception:
            pass

        try:
            self._start_runtime_watch()
            self._start_memory_guard()
        except Exception:
            pass
        

        print("✅ Step 4 OK — OC timeline loop started (OC1…OC20).")






# === PATCH START ===
# 📍 TARGET: gui/GUI.py:_step4 warm-up block
# 🔎 SEARCH: "# --- Force MarketMonitor to warm up once before LiveLoop starts ---"
# 📆 PATCHED: 2025-11-20 — guaranteed warm-up using Step-3 markets, no timestamp filters
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # --- Force MarketMonitor to warm up once before LiveLoop starts ---
        try:
            from engines.market_monitor import monitor

            # 1) Collect mids from Step-3 (these were validated + anchored)
            mids = [m.get("marketId") for m in (self._markets or []) if m.get("marketId")]

            if not mids:
                print("[MARKET_MONITOR] warm-up skipped (no markets in Step-3 list)")
            else:
                print(f"[MARKET_MONITOR] warming up with {len(mids)} markets (Step-3)…")

                # First pass: populate runners + favourite snapshot
                monitor.refresh(mids)
                time.sleep(0.25)

                # Second pass: ensure partial state resolves correctly
                monitor.refresh(mids)

                print(
                    f"[MARKET_MONITOR] warm-up complete: "
                    f"{len(monitor._STATE.get('runner_band', {}))} markets in state"
                )

        except Exception as e:
            print(f"[MARKET_MONITOR] warm-up fatal: {e}")
# === PATCH END ===


        # --- Background schedule keeper (keeps markets_schedule alive) ---
        try:
            import threading, time, sqlite3
            from engines.config_paths import autoscalp_db
            from engines.decision_engine.orchestrator import _upsert_schedule_from_plan

            # --- clean schedule keeper loop ---
            def _schedule_keeper_loop(period_s: int = 30):
                """Continuously repopulate markets_schedule from bets.db (failsafe)."""
                while True:
                    try:
                        con = sqlite3.connect(autoscalp_db(), timeout=6)
                        con.row_factory = sqlite3.Row
                        row = con.execute(
                            "SELECT COUNT(*) AS n FROM markets_schedule "
                            "WHERE date(off_at_utc)=date('now','utc')"
                        ).fetchone()
                        cnt = int(row["n"] if row else 0)
                        con.close()

                        # If the schedule is too small → rebuild it
                        if cnt < 10:
                            mkts = _load_markets_from_bets_today()   # ← global, already defined
                            if mkts:
                                _upsert_schedule_from_plan(mkts)
                                print(f"[SCHEDULE] refreshed {len(mkts)} markets into markets_schedule")
                    except Exception as e:
                        print(f"[SCHEDULE] keeper warn: {e}")
                    time.sleep(period_s)

            # start background schedule keeper
            threading.Thread(
                target=_schedule_keeper_loop,
                name="ScheduleKeeper",
                daemon=True
            ).start()

            print("[SCHEDULE] background keeper started (30 s cadence)")

        except Exception as e:
            print(f"[SCHEDULE] keeper launch failed: {e}")

        print("READY: next step")
        self.status.config(
            text="• Step 4 running (OC timeline). Bands accumulate per window.",
            foreground="#2c7"
        )


        # === PATCH START ===
        # 📍 TARGET: gui/GUI.py:_step4()
        # 📆 PATCHED: 2025-10-27Z — safe LIVE dashboard launch (fixed self scope)
        # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        def launch_dashboard_separate():
            """Launch dashboard in a new isolated process (keeps Tk mainloop free)."""
            import subprocess, os, sys
            dashboard_path = os.path.join(os.path.dirname(__file__), "dashboard.py")
            subprocess.Popen(
                [sys.executable, dashboard_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("[Step4] 🟢 Dashboard launched in separate process (thread-safe).")

        # invoke launcher inside Step 4 method
        launch_dashboard_separate()

        # --- delay-first LIVE KPI refresh (dashboard warm-up) ---
        def _force_refresh_later():
            """Force dashboard to read LIVE KPIs after writer threads start."""
            try:
                from gui.dashboard_data import kpi_tiles
                _ = kpi_tiles(source="LIVE")
                print("[dashboard] forced first LIVE refresh (data now visible)")
            except Exception as e:
                print(f"[dashboard] warm-up refresh warn: {e}")

        self.after(5000, _force_refresh_later)
        # === PATCH END ===

        try:
            from gui.dashboard_data import update_live_matched_odds
            update_live_matched_odds()
        except Exception as e:
            print(f"[Step4] live matched-odds warn: {e}")
# === PATCH END ===
        from engines.risk import budget_manager
        budget_manager.start_watcher()

        # ── Stage 3: start Mastery feedback scheduler (runs every 60 s) ──
        try:
            from engines.mastery.feedback_scheduler import start as _start_feedback_sched
            _start_feedback_sched(60)
            print("[feedback_scheduler] started (interval=60s)")
        except Exception as _e:
            print(f"[feedback_scheduler] start warn: {_e}")
# === PATCH END ===

        # --- Launch OddsService background thread (writes odds_current) ---
        try:
            import threading, time
            from engines.odds import odds_service

            def _odds_writer_loop():
                """Continuously refresh autoscalp_gui.db.odds_current"""
                while True:
                    try:
                        odds_service.tick_update_for_scope(inplay_window_min=15)
                    except Exception as e:
                        print(f"[ODDS_SERVICE] warn: {e}")
                    time.sleep(15)   # run every ~15 s

            t = threading.Thread(target=_odds_writer_loop,
                                 name="OddsServiceWriter", daemon=True)
            t.start()
            print("[ODDS_SERVICE] background writer started (15-sec cadence)")
        except Exception as e:
            print(f"[ODDS_SERVICE] launch failed: {e}")

        # --- Launch Market Data background thread (fills autoscalp_gui.db.market_data) ---
        try:
            import threading, time
            from engines.indicators import market_data
            from engines.upgrade_import_patch import get_session_token  # type: ignore

            def _market_data_loop():
                while True:
                    try:
                        tok = (get_session_token() or "").strip()
                        if tok:
                            mkt = market_data.fetch_market_catalogue(
                                tok, projection=["RUNNER_DESCRIPTION"]
                            )
                            if mkt:
                                market_data.insert_market_data(mkt)
                                print(f"[MARKET_DATA] updated {mkt.get('marketId')} "
                                      f"with {len(mkt.get('runners') or [])} runners")
                    except Exception as e:
                        print(f"[MARKET_DATA] error: {e}")
                    time.sleep(300)  # every 5 minutes

            t = threading.Thread(target=_market_data_loop,
                                 name="MarketDataThread", daemon=True)
            t.start()
            print("[MARKET_DATA] background thread started (5-min cadence)")
        except Exception as e:
            print(f"[MARKET_DATA] launch failed: {e}")

        # --- Launch Overwatcher ---
        try:
            from engines.live.overwatcher import start_overwatcher
            start_overwatcher(hz=2, stop_ticks_default=4)
            print("[OVERWATCHER] started (hz=2, stop_ticks=4)")
        except Exception as e:
            print(f"[OVERWATCHER] failed to launch: {e}")






    def _ready_for_learning(self) -> bool:
        """True when OC1 is flowing so the policy has live ctx to work with."""
        try:
            import sqlite3, engines.config_paths as cp
            db = _auto_conn()
            row = db.execute("SELECT COUNT(1) FROM inbound_oc_cache WHERE oc1 IS NOT NULL").fetchone()
            return bool(row and int(row[0] or 0) > 0)
        except Exception:
            return False
        finally:
            try: db.close()
            except Exception: pass

    def _start_runtime_watch(self, period_s: int = 10):
        """
        RuntimeWatch: print only on spikes/faults; always append to data/runtime_watch.log.
        Spikes = RSS jump ≥ AUTOSCALP_WATCH_SPIKE_MB (default 250 MB)
        Fault  = RSS ≥ AUTOSCALP_WATCH_CEILING_MB (default 1500 MB)
        Set AUTOSCALP_WATCH_DEBUG=1 to print every tick.
        """
        import threading, time, os, gc, resource, random

        # ---- one-time init ----------------------------------------------------
        if getattr(self, "_watch_thread", None) and self._watch_thread.is_alive():
            return

        # log next to your DBs (data/)
        try:
            from engines.config_paths import autoscalp_db
            base = os.path.dirname(autoscalp_db())
        except Exception:
            base = os.path.join(os.path.dirname(__file__), os.pardir, "data")
        os.makedirs(base, exist_ok=True)
        log_path = os.path.join(base, "runtime_watch.log")

        # tracemalloc (optional richness; only printed on spikes/faults or DEBUG)
        try:
            import tracemalloc
            if not tracemalloc.is_tracing():
                tracemalloc.start(25)
        except Exception:
            tracemalloc = None  # graceful degrade

        def _rss_mb() -> float:
            # macOS ru_maxrss is bytes; Linux is KB — normalise to MB
            ru = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return (ru / (1024.0)) if ru > (1 << 34) else (ru / 1024.0 / 1024.0)

        def _thread_summary():
            import threading
            counts = {}
            for t in threading.enumerate():
                counts[t.name] = counts.get(t.name, 0) + 1
            top = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
            return len(threading.enumerate()), top[:12]

        def _snapshot_line():
            n_thr, top_thr = _thread_summary()
            rss = _rss_mb()
            parts = [f"threads={n_thr}", f"rss={rss:.1f} MB"]

            # tracemalloc top offenders (only compute when we’ll print)
            if tracemalloc:
                try:
                    snap = tracemalloc.take_snapshot()
                    lines = snap.statistics("lineno")[:5]
                    hot = [f"{str(s.traceback[0])}: size={s.size / (1024*1024):.1f} MB, count={s.count}"
                           for s in lines]
                    parts.append("top5=" + repr(hot))
                except Exception:
                    pass

            parts.append("top_thr=" + repr(top_thr))
            return " | ".join(parts)

        # quiet policy knobs
        CEIL_MB  = float(os.getenv("AUTOSCALP_WATCH_CEILING_MB", "1500"))
        SPIKE_MB = float(os.getenv("AUTOSCALP_WATCH_SPIKE_MB", "250"))
        DEBUG    = (os.getenv("AUTOSCALP_WATCH_DEBUG", "0") == "1")

        state = {"last_rss": 0.0}

        def _should_print(rss_now: float) -> bool:
            if DEBUG:
                return True
            # print only when crossing the soft ceiling or spiking between ticks
            over  = (rss_now >= CEIL_MB)
            spike = (rss_now - state["last_rss"]) >= SPIKE_MB
            return over or spike

        def _tick():
            try:
                line = _snapshot_line()
                # Always append to file (for post-mortems)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {line}\n")

                # Only print on spikes or anomalies
                # thresholds (env tunable)
                rss_thresh = float(os.environ.get("AUTOSCALP_WATCH_RSS_DELTA_MB", "200"))
                thr_thresh = int(os.environ.get("AUTOSCALP_WATCH_THREAD_DELTA", "10"))

                # stateful deltas
                last = getattr(_tick, "_last", {"rss": None, "threads": None})
                # parse the current snapshot
                try:
                    parts = dict(
                        p.split("=") for p in line.replace(" ", "").split("|")[0].split(",")
                    )
                except Exception:
                    parts = {}
                try:
                    rss_now = float(parts.get("rss", "0").split("MB")[0])
                except Exception:
                    rss_now = 0.0
                try:
                    thr_now = int(parts.get("threads", "0"))
                except Exception:
                    thr_now = 0

                spike = False
                if last["rss"] is not None and (rss_now - last["rss"]) >= rss_thresh:
                    spike = True
                if last["threads"] is not None and (thr_now - last["threads"]) >= thr_thresh:
                    spike = True

                # Print only if spike
                if spike:
                    print(f"[WATCH] {line}", flush=True)

                _tick._last = {"rss": rss_now, "threads": thr_now}

            except Exception:
                try:
                    with open(log_path, "a", encoding="utf-8") as f:
                        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [WATCH] <error writing snapshot>\n")
                except Exception:
                    pass
            finally:
                try: gc.collect(0)
                except Exception: pass

        def _loop():
            while True:
                _tick()
                # tiny jitter to avoid sync with other workers; removed when DEBUG
                time.sleep(max(2, int(period_s)) if DEBUG else max(2, int(period_s)) + random.uniform(0, 2))

        self._watch_thread = threading.Thread(target=_loop, name="RuntimeWatch", daemon=True)
        self._watch_thread.start()

    def _start_learning_loop_background(self):
        """Start orchestrator's learning loop once OC1 exists; retry until ready."""
        import threading
        mode = (self._mode.get() or "learning").lower()
        if mode != "learning":
            return
        # don't double-start
        if getattr(self, "_learn_thread", None) and self._learn_thread.is_alive():
            return
        # wait for OC1 to appear (validator already checks this)
        if not self._ready_for_learning():
            # retry in 3 seconds
            try: self.after(3000, self._start_learning_loop_background)
            except Exception: pass
            return

        from engines.decision_engine.orchestrator import start_live_loop, start_learning_loop
        mode = (os.environ.get("AUTOSCALP_MODE") or "learning").lower()
        prefix = "LIVE" if mode == "live" else "LEARN"
        run_id = f"{prefix}-{now_utc().strftime('%Y%m%d-%H%M%S')}"

        if mode == "live":
            start_live_loop(run_id=run_id, hz=2, logger=print)
        else:
            start_learning_loop(run_id=run_id, hz=2, logger=print)

        def _logger(msg: str):
            try: self._append(msg)
            except Exception: print(msg)

        self._learn_thread = threading.Thread(
            target=lambda: start_learning_loop(run_id=run_id, hz=2, logger=_logger),
            name="LearningLoop",
            daemon=True,
        )
        self._learn_thread.start()



# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^\s*class PhaseGUI\(tk\.Tk\):\n
# ─────────────────────────────────────────────────────────────────────────────
    def _ensure_test_run_and_order(self):
        """Create a TEST run + one sample open order if none exist, so ScalperView shows content."""
        try:
            import engines.config_paths as cp
            autoscalp_path = cp.autoscalp_db()
        except Exception:
            # fallback to local router if needed
            import engines.config_paths as cp
            autoscalp_path = cp.autoscalp_db()


        with _auto_conn() as con:
            con.row_factory = sqlite3.Row
            # 1) Ensure a TEST run
            row = con.execute(
                "SELECT id FROM runs WHERE finished_at IS NULL AND mode='TEST' ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row:
                run_id = row["id"]
            else:
                con.execute(
                    "INSERT INTO runs (started_at, mode, blueprint_file, notes) VALUES (?,?,?,?)",
                    (now_utc().isoformat(), "TEST", None, "Simulator"),
                )
                run_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]

            # 2) If no open orders in TEST, create one using first seeded market/runner
            open_cnt = con.execute(
                "SELECT COUNT(1) AS c FROM orders "
                "WHERE (closed_at IS NULL OR closed_at='') AND mode='TEST'"
            ).fetchone()["c"]

            if open_cnt == 0:
                # pick a runner from self._markets
                mid, sid = None, None
                for m in (self._markets or []):
                    if m.get("runners"):
                        mid = m.get("marketId")
                        sid = m["runners"][0].get("selectionId")
                        if mid and sid is not None:
                            break
                if not mid or sid is None:
                    return  # nothing to seed yet

                # Seed a queued lay scalp
                con.execute(
                    "INSERT INTO orders (run_id, decision_id, customerOrderRef, marketId, selectionId, mode, "
                    "side, entry_odds, entry_stake, entry_status, opened_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        run_id, None, f"TEST-{mid}-{sid}-{int(time.time())}", mid, str(sid), "TEST",
                        "LAY", 6.0, 2.0, "queued", now_utc().isoformat()
                    ),
                )


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: gui/GUI.py
# 🔎 SEARCH: ^def main\(\):\n(?:[ \t].*\n)+?
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print(f"[GUI] DB_PATH = {DB_PATH}")
    print("[GUI] Start app → choose: Launch TEST or run Step 1.")
    try:
        ensure_order_events_triggers()
    except Exception:
        pass

    app = PhaseGUI()
    app.mainloop()


if __name__ == "__main__":
    # LIVE by default
    try:
        from config_paths import set_db_paths   # root-level
    except Exception:
        from engines.config_paths import set_db_paths  # engines/ fallback

    set_db_paths("LIVE")
  
    main()

