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
