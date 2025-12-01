#!/usr/bin/env python3
# ============================================================
#  AlphaX Gateway — PURE Scheduler (FINAL)
# ============================================================
# Responsibilities:
#   • Receive SQL from Hijack
#   • Classify: family / priority / rw
#   • Queue: HP (immediate) / LP (batched)
#   • Return dummy cursor for writes
#   • Never open DBs. Never attach. Never choose connections.
#
# DAL (config_paths) performs ALL routing + DB opening.
# ============================================================

import sqlite3, threading, queue, time, os

# ------------------------------------------------------------
# Family classification dictionary (stable)
# ------------------------------------------------------------
FAMILIES = {
    "auto": ["inbound_", "oc_", "odds_", "dashboard_", "runs", "events"],
    "bets": ["bets", "anchor", "marketstarttime", "placed_at"],
    "mastery": ["mastery_", "training_", "posterior"],
    "settlements": ["settlement", "cleared"],
}

# Global scheduler queues
_HP = queue.Queue()                  # High priority → immediate
_LP = queue.Queue(maxsize=5000)      # Low priority → microbatch
_STOP = threading.Event()

# === PATCH START ===
# 📍 TARGET: engines/alphax_gateway.py
# 📆 PATCHED: 2025-11-25Z — Add unified enqueue_write() API for Hijack
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import time

def enqueue_write(sql: str, params=None, priority: int = 5):
    """
    Public AlphaX enqueue API.
    Accepts SQL + params and pushes into the LP queue.
    AlphaX will classify (family + priority) and DAL will open a RW cloud writer.
    Fully backwards-compatible with Hijack.
    """
    try:
        _LP.put((priority, time.time(), sql, params or ()))
    except Exception as e:
        # Minimal risk logging; avoids circular import
        print(f"[AlphaX enqueue_write] warn: {e} | sql={sql[:80]}")
# === PATCH END ===

# ------------------------------------------------------------
#  FAMILY DETECTOR — SQL-only (no path-based routing)
# ------------------------------------------------------------
def _detect_family(sql_l: str) -> str:
    for fam, keys in FAMILIES.items():
        if any(k in sql_l for k in keys):
            return fam
    return "auto"

# === PATCH START ============================================================
# 📍 TARGET: engines/alphax_gateway.py (module level)
# 📆 PATCHED: 2025-12-03 — Cloud Retention + WAL Manager
# PURPOSE:
#   • Keep CLOUD DB trimmed to 5 days
#   • Prevent runaway WAL/SHM
#   • Prevent iCloud from growing DB beyond safe mirrorable size
# ============================================================================

import sqlite3 as _cx_sql
import time as _cx_time
import os as _cx_os

def _cloud_retention_loop():
    """
    Runs forever, every 15 minutes:
      • delete rows > 5 days old
      • VACUUM cloud DB safely
      • purge runaway WAL/SHM files
    """
    from engines.config_paths import CLOUD_AUTO

    while not _STOP.is_set():
        try:
            # Open CLOUD DB raw
            con = _cx_sql.connect(
                CLOUD_AUTO,
                timeout=10,
                isolation_level=None
            )
            con.row_factory = _cx_sql.Row
            cur = con.cursor()

            # Delete old rows (> 5 days)
            cur.execute("""
                DELETE FROM orders
                WHERE date(opened_at) < date('now','utc','-5 days')
            """)
            cur.execute("""
                DELETE FROM order_events
                WHERE date(ts) < date('now','utc','-5 days')
            """)
            cur.execute("""
                DELETE FROM inbound_oc_cache
                WHERE date(last_sync_ts) < date('now','utc','-5 days')
            """)
            cur.execute("""
                DELETE FROM oc_series
                WHERE date(snapshot_ts) < date('now','utc','-5 days')
            """)
            cur.execute("""
                DELETE FROM odds_current
                WHERE date(updated_ts) < date('now','utc','-5 days')
            """)

            con.commit()
            con.close()

            # VACUUM — RECLAIM SPACE
            try:
                con2 = _cx_sql.connect(CLOUD_AUTO, timeout=20, isolation_level=None)
                con2.execute("VACUUM")
                con2.close()
            except Exception as ve:
                print(f"[CloudKeeper] vacuum warn: {ve}")

            # Purge WAL/SHM (they will be recreated safely)
            for ext in ("-wal", "-shm"):
                path = f"{CLOUD_AUTO}{ext}"
                if _cx_os.path.exists(path):
                    try:
                        _cx_os.remove(path)
                        print(f"[CloudKeeper] removed {ext}")
                    except Exception as re:
                        print(f"[CloudKeeper] purge warn: {re}")

        except Exception as e:
            print(f"[CloudKeeper] warn: {e}")

        _cx_time.sleep(900)  # 15 minutes

# start the retention loop if not already running
if not any(t.name == "CloudKeeper" for t in threading.enumerate()):
    threading.Thread(
        target=_cloud_retention_loop,
        name="CloudKeeper",
        daemon=True
    ).start()

# === PATCH END ==============================================================



# ------------------------------------------------------------
#  PRIORITY CLASSIFIER
# ------------------------------------------------------------
def _classify(sql_l: str):
    # Write?
    is_write = sql_l.startswith((
        "insert", "update", "delete", "replace",
        "alter", "create", "drop"
    ))

    # Betting logic → priority 1
    if any(k in sql_l for k in ("orders", "bets", "hedge", "ladder", "decisions")):
        return 1, True

    # Mid-priority writes
    if any(k in sql_l for k in ("odds_current", "inbound_", "oc_series", "markets_schedule")):
        return 3, True

    # Default read
    return 5, False


# ------------------------------------------------------------
# AlphaX Route API (Hijack consumes this)
# ------------------------------------------------------------
def alphax_route(sql: str, params=None):
    sql_l = (sql or "").lower().strip()

    fam = _detect_family(sql_l)
    prio, rw = _classify(sql_l)

    # Setup mode forces everything RW + immediate
    if (os.environ.get("AUTOSCALP_MODE") or "learning").upper() == "SETUP":
        prio, rw = 1, True

    return {
        "family": fam,
        "priority": prio,
        "rw": rw
    }


# ------------------------------------------------------------
# Write dispatchers (LP batcher + HP immediate)
# ------------------------------------------------------------
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/alphax_gateway.py
# 🔎 SEARCH: ^def _lp_loop\(
# 📆 PATCHED: 2025-11-19T10:40Z — adapt LP queue to 4-tuple items (prio, ts, sql, params)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _lp_loop():
    """
    Low-priority write dispatcher.

    Accepts both legacy 3-tuples (prio, sql, fam) and new 4-tuples
    (prio, ts, sql, params). When family is not present, we re-run
    alphax_route(sql, params) to determine the DB family.
    """
    BATCH_WINDOW = 0.003  # 3–4 ms batching window

    # DAL handles actual execution — via open_* helpers
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
        """
        Normalise queue items to a unified shape:
            (prio, sql_s, fam, params)

        Supports:
          • (prio, sql, fam)
          • (prio, ts, sql, params)
        Returns None for unusable items.
        """
        if not isinstance(item, tuple):
            return None

        # Legacy: (prio, sql, fam)
        if len(item) == 3:
            prio, sql, fam = item
            params = ()
        # New: (prio, ts, sql, params)
        elif len(item) == 4:
            prio, _ts, sql, params = item
            meta = alphax_route(sql, params)
            fam = meta["family"]
        else:
            return None

        sql_s = (sql or "").strip()
        if not sql_s:
            return None

        return prio, sql_s, fam, tuple(params or ())

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

        # Small batching window to amortise open/commit cost
        while (time.time() - t0) < BATCH_WINDOW:
            try:
                raw_more = _LP.get_nowait()
            except queue.Empty:
                break

            norm = _normalise(raw_more)
            if not norm:
                _LP.task_done()
                continue
            batch.append(norm)

        # Execute batch
        for (_prio, sql_s, fam, params) in batch:
            opener = WAL.get(fam)
            if opener is None:
                # Defensive: default to AUTO if classification ever returns unknown
                opener = WAL["auto"]

            con = opener(rw=True, ro=False)
            try:
                con.execute(sql_s, params)
                con.commit()
            except Exception:
                # Best-effort; individual failures are non-fatal to the loop
                pass
            finally:
                try:
                    con.close()
                except Exception:
                    pass

        # Mark all processed items as done
        for _ in batch:
            _LP.task_done()



# Start LP dispatcher thread (idempotent)
if not any(t.name == "AlphaX-LP" for t in threading.enumerate()):
    threading.Thread(target=_lp_loop, name="AlphaX-LP", daemon=True).start()

# === PATCH START ===============================================
# 📍 TARGET: engines/alphax_gateway.py
# 📆 PATCHED: 2025-12-03 — Explicit AlphaX thread bootstrap
# PURPOSE:
#   • Provide a public function to start LP + Mirror threads
#   • Safe to call multiple times (idempotent)
#   • GUI must call this during Step 4
# ==============================================================

import threading as _ax_boot

def start_alphax_threads():
    """
    Explicitly start AlphaX LP dispatcher + Cloud→Local mirror.
    Safe to call multiple times (idempotent).
    """
    # LP dispatcher
    if not any(t.name == "AlphaX-LP" for t in _ax_boot.enumerate()):
        _ax_boot.Thread(
            target=_lp_loop,
            name="AlphaX-LP",
            daemon=True
        ).start()
        print("[AlphaX] LP dispatcher started")

    # Cloud→Local mirror
    if not any(t.name == "AlphaX-Mirror" for t in _ax_boot.enumerate()):
        _ax_boot.Thread(
            target=_ax_cloud_mirror_loop,
            name="AlphaX-Mirror",
            daemon=True
        ).start()
        print("[AlphaX] Cloud→Local mirror started")

# === PATCH END =================================================


# ===============================================================
# 📍 TARGET: engines/alphax_gateway.py
# 🔎 SEARCH: Start LP dispatcher thread (idempotent)
# 🛠 ACTION: Insert Cloud→Local mirror loop just below LP start
# 📆 PATCHED: 2025-12-01T23:55Z
# ===============================================================

# === PATCH START: Cloud→Local Mirror (orders + odds) =========================
import sqlite3 as _ax_sqlite
import hashlib as _ax_hash
import time as _ax_time
import threading as _ax_thread

def _ax_row_md5(row: dict) -> str:
    """Stable MD5 of a row for change detection."""
    flat = "|".join(str(row[k]) for k in sorted(row.keys()))
    return _ax_hash.md5(flat.encode("utf-8")).hexdigest()

def _ax_fetch_cloud_rows(con, table: str, day_filter: bool) -> list[dict]:
    """Fetch rows from CLOUD for the given table; optionally filter by today's date."""
    try:
        if day_filter:
            sql = f"SELECT * FROM {table} WHERE date(opened_at)=date('now','utc')"
        else:
            sql = f"SELECT * FROM {table}"
        cur = con.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception:
        return []

def _ax_mirror_table(table: str, *, filter_today: bool):
    """
    Mirror a single table from CLOUD → LOCAL:
      • Reads CLOUD from raw connection (never SafeConn, never DAL)
      • Builds LOCAL index from raw LOCAL connection
      • Writes into LOCAL via dedicated local writer
      • No deletes
    """
    # === FIXED RAW CONNECTION PATCH ============================================
    from engines.config_paths import _raw_open, CLOUD_AUTO, LOCAL_AUTO

    # fresh RAW CLOUD reader
    cloud_con = _raw_open(CLOUD_AUTO, timeout=10)
    try:
        cloud_con.execute("PRAGMA busy_timeout=8000")
        cloud_con.execute("PRAGMA journal_mode=WAL")
        cloud_con.row_factory = _ax_sqlite.Row
    except Exception as e:
        print(f"[Mirror] cloud pragma warn: {e}")

    # fresh RAW LOCAL reader
    local_con = _raw_open(LOCAL_AUTO, timeout=10)
    try:
        local_con.execute("PRAGMA busy_timeout=8000")
        local_con.execute("PRAGMA journal_mode=WAL")
        local_con.row_factory = _ax_sqlite.Row
    except Exception as e:
        print(f"[Mirror] local pragma warn: {e}")
    # ===========================================================================

    # fetch cloud rows
    cloud_rows = _ax_fetch_cloud_rows(cloud_con, table, filter_today)
    try:
        cloud_con.close()
    except Exception:
        pass

    # build local index
    local_index = {}
    try:
        cur = local_con.execute(f"SELECT * FROM {table}")
        cols = [c[0] for c in cur.description]
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            local_index[d.get("id")] = _ax_row_md5(d)
    except Exception:
        pass
    finally:
        try:
            local_con.close()
        except Exception:
            pass

    # === LOCAL WRITER (RAW) ===================================================
    from engines.config_paths import open_auto_local_write as _local_writer
    writer = _local_writer()

    # insert into LOCAL if missing or changed
    for row in cloud_rows:
        rid = row.get("id")
        md5 = _ax_row_md5(row)

        if rid not in local_index or local_index[rid] != md5:
            cols = list(row.keys())
            placeholders = ",".join("?" for _ in cols)
            col_list = ",".join(cols)
            sql = f"""
                INSERT OR REPLACE INTO {table} ({col_list})
                VALUES ({placeholders})
            """
            params = tuple(row[k] for k in cols)
            writer.execute(sql.strip(), params)



def _ax_cloud_mirror_loop():
    """
    Runs forever:
      - sync priority tables CLOUD → LOCAL
      - orders (today)
      - odds_current (today)
      - inbound_oc_cache (all)
      - oc_series (today)
    Low priority so it never blocks betting.
    """
    PRIORITY_TABLES = [
        ("orders", True),
        ("order_events", True),
        ("odds_current", True),
        ("inbound_oc_cache", False),
        ("oc_series", True),
    ]

    while not _STOP.is_set():
        for table, today in PRIORITY_TABLES:
            try:
                _ax_mirror_table(table, filter_today=today)
            except Exception as e:
                print(f"[AlphaX Mirror] warn {table}: {e}")
        _ax_time.sleep(0.35)  # ~3 cycles/sec; safe + fast enough

# start mirror thread (idempotent)
if not any(t.name == "AlphaX-Mirror" for t in threading.enumerate()):
    _ax_thread.Thread(target=_ax_cloud_mirror_loop,
                      name="AlphaX-Mirror",
                      daemon=True).start()
# === PATCH END ==============================================================

# ------------------------------------------------------------
# AlphaX entrypoint for Hijack sqlite3.connect()
# ------------------------------------------------------------
def alphax_entrypoint(db_path: str, sql=None):
    """
    Hijack-level call:
        sqlite3.connect() → dummy object
    No real DB is opened at this stage.
    """
    class _Dummy:
        def execute(self, *a, **k): return self
        def cursor(self): return self
        def fetchall(self): return []
        def fetchone(self): return None

    return _Dummy()
