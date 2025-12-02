#!/usr/bin/env python3
# ============================================================
#  AlphaX Gateway — PURE Scheduler (FINAL, LiveCache edition)
# ============================================================
# Responsibilities:
#   • Receive SQL from Hijack
#   • Classify: family / priority / rw
#   • Queue: HP (immediate) / LP (batched)
#   • Never open DBs for normal SQL execution
#   • Never attach; never inspect paths
#   • DAL (config_paths) performs all routing
#   • Mirror thread syncs LiveCache → Local
# ============================================================

import sqlite3, threading, queue, time, os
from typing import Optional

# ------------------------------------------------------------
# Stable SQL family classifier
# ------------------------------------------------------------
FAMILIES = {
    "auto": ["inbound_", "oc_", "odds_", "dashboard_", "runs", "events"],
    "bets": ["bets", "anchor", "marketstarttime", "placed_at"],
    "mastery": ["mastery_", "training_", "posterior"],
    "settlements": ["settlement", "cleared"],
}

# ------------------------------------------------------------
# Queues + control flags
# ------------------------------------------------------------
_HP = queue.Queue()            # High-priority queue
_LP = queue.Queue(maxsize=5000)
_STOP = threading.Event()

# ============================================================
#  PUBLIC API: enqueue_write()
# ============================================================
def enqueue_write(sql: str, params=None, priority: int = 5):
    """
    Public enqueue function (Hijack-compatible).
    Pushes (priority, timestamp, sql, params) into LP queue.
    """
    try:
        _LP.put((priority, time.time(), sql, params or ()))
    except Exception as e:
        print(f"[AlphaX enqueue_write] warn: {e} | sql={sql[:80]}")


# ============================================================
#  FAMILY DETECTOR / PRIORITY
# ============================================================
def _detect_family(sql_l: str) -> str:
    for fam, keys in FAMILIES.items():
        if any(k in sql_l for k in keys):
            return fam
    return "auto"

def _classify(sql_l: str):
    is_write = sql_l.startswith((
        "insert", "update", "delete", "replace",
        "alter", "create", "drop"
    ))

    if any(k in sql_l for k in ("orders", "bets", "hedge", "ladder", "decisions")):
        return 1, True   # HP write

    if any(k in sql_l for k in ("odds_current", "inbound_", "oc_series", "markets_schedule")):
        return 3, True

    return 5, False      # read


def alphax_route(sql: str, params=None):
    sql_l = (sql or "").lower().strip()
    fam = _detect_family(sql_l)
    prio, rw = _classify(sql_l)

    if (os.environ.get("AUTOSCALP_MODE") or "learning").upper() == "SETUP":
        prio, rw = 1, True

    return {"family": fam, "priority": prio, "rw": rw}


# ============================================================
#  LP DISPATCHER (main worker)
# ============================================================
def _lp_loop():
    """
    Low-priority batched write dispatcher.
    """

    from engines.config_paths import (
        open_auto_db,
        open_bets_db,
        open_mastery_db,
        open_settlements_db,
    )

    WAL = {
        "auto": open_auto_db,
        "bets": open_bets_db,
        "mastery": open_mastery_db,
        "settlements": open_settlements_db,
    }

    def _normalise(item):
        if not isinstance(item, tuple):
            return None
        if len(item) == 3:
            prio, sql, fam = item
            params = ()
        elif len(item) == 4:
            prio, _ts, sql, params = item
            fam = alphax_route(sql, params)["family"]
        else:
            return None
        sql_s = (sql or "").strip()
        if not sql_s:
            return None
        return prio, sql_s, fam, tuple(params or ())

    BATCH_WINDOW = 0.003

    while not _STOP.is_set():
        try:
            raw = _LP.get(timeout=0.001)
        except queue.Empty:
            continue

        first = _normalise(raw)
        if not first:
            _LP.task_done()
            continue

        batch = [first]
        t0 = time.time()

        while (time.time() - t0) < BATCH_WINDOW:
            try:
                raw2 = _LP.get_nowait()
            except queue.Empty:
                break
            n2 = _normalise(raw2)
            if not n2:
                _LP.task_done()
                continue
            batch.append(n2)

        for (_prio, sql_s, fam, params) in batch:
            opener = WAL.get(fam, WAL["auto"])
            try:
                con = opener(rw=True)
                con.execute(sql_s, params)
                con.commit()
            except Exception:
                pass
            finally:
                try: con.close()
                except Exception: pass

        for _ in batch:
            _LP.task_done()


# ============================================================
#  Start LP thread (idempotent)
# ============================================================
if not any(t.name == "AlphaX-LP" for t in threading.enumerate()):
    threading.Thread(
        target=_lp_loop, name="AlphaX-LP", daemon=True
    ).start()


# ============================================================
#  LiveCache → Local Mirror (orders + odds only)
# ============================================================
import sqlite3 as _ax_sqlite
import hashlib as _ax_hash
import time as _ax_time

def _ax_row_md5(row: dict) -> str:
    flat = "|".join(str(row[k]) for k in sorted(row.keys()))
    return _ax_hash.md5(flat.encode("utf-8")).hexdigest()


def _ax_fetch_cloud_rows(con, table: str, today_only: bool):
    try:
        if today_only:
            sql = f"SELECT * FROM {table} WHERE date(opened_at)=date('now','utc')"
        else:
            sql = f"SELECT * FROM {table}"
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []

# 📍 TARGET: engines/alphax_gateway.py
# 🔎 SEARCH: def _ax_mirror_table(
# 📆 PATCHED: 2025-12-02 — LiveCache → Local: incremental, DAL-safe mirror

# ======================================================================
# NEW MIRROR SUBSYSTEM (Replaces old cloud mirror logic)
# ======================================================================

import glob as _ax_glob
import sqlite3 as _ax_sql
import time as _ax_time
import hashlib as _ax_hash
from engines.config_paths import (
    open_auto_db,
    open_bets_db,
    open_mastery_db,
    open_settlements_db,
)

# Timestamp-bearing columns (from full schema dump)
_TS_COLS = {
    "ts", "updated_ts", "updated_at", "snapshot_ts", "created_at",
    "opened_at", "placed_at", "closed_at", "settled_at", "finished_at",
    "decided_at", "realized_at", "recorded_at", "ingested_at",
    "last_update_ts", "last_refreshed_ts", "last_snapshot_ts",
    "off_ts", "happened_at"
}

# LiveCache directory
_LIVE_ROOT = "data/livecache"

# Map LiveCache DB names → Local DAL opener
_DB_MAP = {
    "autoscalp_livecache.db": open_auto_db,
    "bets_livecache.db": open_bets_db,
    "mastery_livecache.db": open_mastery_db,
    "settlements_livecache.db": open_settlements_db,
}


def _ax_md5_row(row: dict) -> str:
    flat = "|".join(str(row[k]) for k in sorted(row.keys()))
    return _ax_hash.md5(flat.encode("utf-8")).hexdigest()


def _ax_livecache_get_rows(db_path: str, table: str) -> list[dict]:
    """
    Read only timestamp-bearing rows from LiveCache table.
    Uses WAL + busy_timeout for safety.
    """
    try:
        con = _ax_sql.connect(db_path, timeout=8000)
        con.execute("PRAGMA journal_mode=WAL")
        con.row_factory = _ax_sql.Row

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

        sql = (
            f"SELECT * FROM {table} "
            f"WHERE {tscol} >= date('now','-1 day')"
        )

        cur = con.execute(sql)
        colnames = [c[0] for c in cur.description]
        rows = [dict(zip(colnames, r)) for r in cur.fetchall()]
        con.close()
        return rows

    except Exception:
        return []


def _ax_local_index(opener, table: str) -> dict:
    """
    Build MD5 index of Local table rows so we can skip unchanged rows.
    """
    index = {}
    try:
        con = opener(rw=True)
        con.execute("PRAGMA journal_mode=WAL")
        con.row_factory = _ax_sql.Row
        cur = con.execute(f"SELECT * FROM {table}")
        cols = [c[0] for c in cur.description]
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            rid = d.get("id")
            if rid is not None:
                index[rid] = _ax_md5_row(d)
        try: con.close()
        except: pass
    except Exception:
        pass
    return index


def _ax_mirror_table_livecache(live_db: str, table: str, opener):
    """
    Incremental LiveCache → Local mirror for one table.
    Mirrors only timestamp-bearing tables.
    """
    rows = _ax_livecache_get_rows(live_db, table)
    if not rows:
        return

    idx = _ax_local_index(opener, table)
    writer = opener(rw=True)

    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA busy_timeout=8000")
        writer.row_factory = _ax_sql.Row
    except Exception:
        pass

    for row in rows:
        rid = row.get("id")
        if rid is None:
            continue

        md5 = _ax_md5_row(row)
        if rid in idx and idx[rid] == md5:
            continue  # unchanged

        cols = list(row.keys())
        plist = ",".join("?" for _ in cols)
        clist = ",".join(cols)
        sql = f"INSERT OR REPLACE INTO {table} ({clist}) VALUES ({plist})"
        params = tuple(row[k] for k in cols)

        try:
            writer.execute(sql, params)
        except Exception:
            pass

    try:
        writer.commit()
    except Exception:
        pass

    try:
        writer.close()
    except Exception:
        pass


def _ax_cloud_mirror_loop():
    """
    FINAL MIRROR LOOP:
       • Scans all LiveCache DBs
       • Mirrors ONLY timestamp-bearing tables
       • MD5 diff skip
       • WAL + busy_timeout
       • Single-thread incremental sync
    """
    while not _STOP.is_set():
        try:
            dbs = _ax_glob.glob(f"{_LIVE_ROOT}/*.db")
        except Exception:
            _ax_time.sleep(0.5)
            continue

        for live_db_path in dbs:
            live_name = os.path.basename(live_db_path)
            opener = _DB_MAP.get(live_name)
            if opener is None:
                continue

            try:
                con = _ax_sql.connect(live_db_path)
                cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = [t[0] for t in cur.fetchall()]
                con.close()
            except Exception:
                continue

            for table in tables:
                try:
                    _ax_mirror_table_livecache(live_db_path, table, opener)
                except Exception:
                    pass

        _ax_time.sleep(0.35)

# === PATCH END ==========================================================



# Start mirror thread (idempotent)
if not any(t.name == "AlphaX-Mirror" for t in threading.enumerate()):
    threading.Thread(
        target=_ax_cloud_mirror_loop,
        name="AlphaX-Mirror",
        daemon=True
    ).start()


# ============================================================
#  PUBLIC: explicit thread bootstrap for GUI Step 4
# ============================================================
def start_alphax_threads():
    if not any(t.name == "AlphaX-LP" for t in threading.enumerate()):
        threading.Thread(target=_lp_loop, name="AlphaX-LP", daemon=True).start()
        print("[AlphaX] LP dispatcher started")

    if not any(t.name == "AlphaX-Mirror" for t in threading.enumerate()):
        threading.Thread(target=_ax_cloud_mirror_loop,
                         name="AlphaX-Mirror",
                         daemon=True).start()
        print("[AlphaX] Cloud→Local mirror started")


# ============================================================
#  Hijack entrypoint
# ============================================================
def alphax_entrypoint(db_path: str, sql=None):
    """
    Hijack-level sqlite3.connect() → dummy non-DB object.
    AlphaX never opens DBs directly except mirror.
    """
    class _Dummy:
        def execute(self, *a, **k): return self
        def cursor(self): return self
        def fetchall(self): return []
        def fetchone(self): return None
    return _Dummy()
