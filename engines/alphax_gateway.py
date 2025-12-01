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


def _ax_mirror_table(table: str, *, today_only: bool):
    """
    RAW → RAW mirror:
      CLOUD_AUTO → LOCAL_AUTO
    """
    from engines.config_paths import _raw_open, CLOUD_AUTO, LOCAL_AUTO
    from engines.config_paths import open_auto_local_write

    cloud = _raw_open(CLOUD_AUTO)
    try:
        cloud.execute("PRAGMA busy_timeout=8000")
        cloud.execute("PRAGMA journal_mode=WAL")
        cloud.row_factory = _ax_sqlite.Row
    except Exception:
        pass

    local = _raw_open(LOCAL_AUTO)
    try:
        local.execute("PRAGMA busy_timeout=8000")
        local.execute("PRAGMA journal_mode=WAL")
        local.row_factory = _ax_sqlite.Row
    except Exception:
        pass

    rows = _ax_fetch_cloud_rows(cloud, table, today_only)
    try: cloud.close()
    except Exception: pass

    index = {}
    try:
        cur = local.execute(f"SELECT * FROM {table}")
        cols = [c[0] for c in cur.description]
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            index[d.get("id")] = _ax_row_md5(d)
    except Exception:
        pass
    finally:
        try: local.close()
        except Exception:
            pass

    writer = open_auto_local_write()

    for row in rows:
        rid = row.get("id")
        md5 = _ax_row_md5(row)
        if rid not in index or md5 != index[rid]:
            cols = list(row.keys())
            plist = ",".join("?" for _ in cols)
            clist = ",".join(cols)
            sql = f"INSERT OR REPLACE INTO {table} ({clist}) VALUES ({plist})"
            params = tuple(row[k] for k in cols)
            writer.execute(sql, params)


def _ax_cloud_mirror_loop():
    PRIORITY_TABLES = [
        ("orders", True),
        ("order_events", True),
        ("odds_current", True),
        ("inbound_oc_cache", False),
        ("oc_series", True),
    ]
    while not _STOP.is_set():
        for table, today_only in PRIORITY_TABLES:
            try:
                _ax_mirror_table(table, today_only=today_only)
            except Exception as e:
                print(f"[AlphaX Mirror] warn {table}: {e}")
        _ax_time.sleep(0.35)


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
