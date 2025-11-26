#!/usr/bin/env python3
# engines/mastery/event_sink.py — CIS backbone + Live queue bridge

from __future__ import annotations
import sqlite3, json, queue, threading, time as _time
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db
from engines.decision_engine.decide_once.helpers import open_auto_db, exec_rows, q_retry as _q_retry

# ───────────────────────────────────────────────────────────────────────
# 1️⃣  SAFE UTILITIES (KV helpers, retry wrappers)
# ───────────────────────────────────────────────────────────────────────
KV_KEY = "mastery_last_event_id"

def _open_db_rw(retries: int = 6, delay_s: float = 0.08):
    last = None
    for i in range(max(1, retries)):
        try:
            conn = open_auto_db(rw=True)
            _q_retry(conn, "PRAGMA busy_timeout=8000")
            _q_retry(conn, "PRAGMA journal_mode=WAL")
            _q_retry(conn, "PRAGMA synchronous=NORMAL")
            return conn
        except sqlite3.OperationalError as e:
            last = e
            if i < retries - 1 and any(x in str(e).lower() for x in ("locked", "unable")):
                _time.sleep(delay_s * (i + 1))
                continue
            raise
    raise last

def install_mastery_shim():
    """
    Create backward-compatible read-only views inside autoscalp_gui.db
    that proxy to mastery_v7.db for legacy readers.
    """
    from engines.config_paths import autoscalp_db, mastery_v7_db
    import sqlite3, os

    gui = autoscalp_db()
    v7 = mastery_v7_db()
    if not (os.path.exists(gui) and os.path.exists(v7)):
        print("[shim] skipped (databases not ready)")
        return

    con = sqlite3.connect(gui)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    try:
        cur.execute(f"ATTACH DATABASE '{v7}' AS v7;")

        # Legacy readers of events
        cur.executescript("""
            DROP VIEW IF EXISTS events;
            CREATE VIEW events AS
            SELECT * FROM v7.events;
        """)

        # Legacy readers of mastery_cache
        cur.executescript("""
            DROP VIEW IF EXISTS mastery_cache;
            CREATE VIEW mastery_cache AS
            SELECT * FROM v7.mastery_cache;
        """)

        # Legacy readers of mastery_state
        cur.executescript("""
            DROP VIEW IF EXISTS mastery_state;
            CREATE VIEW mastery_state AS
            SELECT * FROM v7.mastery_state;
        """)

        con.commit()
        print("[shim] ✅ legacy views installed → GUI can read v7 events/cache/state")

    except Exception as e:
        print(f"[shim] warn: {e}")
    finally:
        cur.execute("DETACH DATABASE v7;")
        con.close()

# ───────────────────────────────────────────────────────────────────────
# 2️⃣  CIS UNIFIED EMITTER  (Core Event Log + MasteryCast Mirror)
# ───────────────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: engines/mastery/event_sink.py:emit()
# 📆 PATCHED: 2025-11-07Z — Redirect writes to mastery_v7.db (isolated WAL)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, json, queue, threading
from datetime import datetime, timezone
from engines.config_paths import mastery_v7_db  # 🔄 changed from autoscalp_db

_event_queue = queue.Queue(maxsize=1000)
_listeners: list = []
_lock = threading.Lock()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/mastery/event_sink.py
# 🔎 SEARCH: ^def _open_mastery_conn
# 📆 PATCHED: 2025-11-24 — Mastery queue-writer proxy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from engines.database_hijack_monitor import enqueue_write as _ax_write

class _MasteryConnProxy:
    """
    Proxy connection object routed through AlphaX LP queue.

    All execute() calls become queued writes.
    fetch() methods return empty because mastery_v7 writes are fire-and-forget.
    """
    def execute(self, sql, params=()):
        _ax_write(sql, params, priority=3)
        return self

    def executemany(self, sql, seq):
        for p in seq:
            _ax_write(sql, p, priority=3)
        return self

    def commit(self):  return None
    def close(self):   return None
    def cursor(self):  return self
    def fetchall(self): return []
    def fetchone(self): return None


def _open_mastery_conn() -> _MasteryConnProxy:
    """
    Mastery DB connector (RW-only, queue-safe).

    IMPORTANT:
      • No real sqlite3.connect() is called.
      • All writes go through AlphaX → single-writer → no locks.
      • DAL is used under the hood to actually open mastery_v7.db.
    """
    return _MasteryConnProxy()



def emit(event_type: str, payload: dict):
    """
    Unified hybrid emitter (v7-safe):
      • Logs events → mastery_v7.db
      • Mirrors key live events into mastery_cache
      • Broadcasts to memory subscribers (GUI/Overwatcher)
      • Never writes into autoscalp_gui.db
    """
    global _event_queue, _listeners, _lock
    ts = payload.get("ts") or datetime.now(timezone.utc).isoformat()
    msg = f"MASTERY {json.dumps({'type': event_type, **payload}, separators=(',', ':'), ensure_ascii=False)}"

    # ───────────────────────────────
    # 1️⃣ Core persistent event log
    # ───────────────────────────────
    try:
        con = _open_mastery_conn()
        con.execute("""
            CREATE TABLE IF NOT EXISTS events(
                ts TEXT, level TEXT, source TEXT, message TEXT
            )
        """)
        con.execute(
            "INSERT INTO events(ts, level, source, message) VALUES (?, 'INFO', 'Mastery', ?)",
            (ts, msg),
        )
        con.commit(); con.close()
    except Exception as e:
        print(f"[event_sink] emit warn {event_type}: {e}")

    # ───────────────────────────────
    # 2️⃣ Mastery cache mirror
    # ───────────────────────────────
    mirror_types = {
        "cashout_tick", "feedback_tick", "liability_update",
        "feedback_summary", "feedback_assimilated_summary",
        "feedback_policy_update", "feedback_policy_update_summary",
        "goal_alignment_tick",
    }
    if event_type in mirror_types:
        try:
            con = _open_mastery_conn()
            con.execute("""
                CREATE TABLE IF NOT EXISTS mastery_cache(
                    ts TEXT,
                    event_type TEXT,
                    marketId TEXT,
                    json_payload TEXT
                )
            """)
            con.execute("""
                INSERT INTO mastery_cache(ts, event_type, marketId, json_payload)
                VALUES (?, ?, ?, ?)
            """, (ts, event_type, payload.get("marketId"), json.dumps(payload, ensure_ascii=False)))
            con.commit(); con.close()
        except Exception as e:
            print(f"[event_sink] cache warn {event_type}: {e}")

    # ───────────────────────────────
    # 3️⃣ Goal alignment persistence
    # ───────────────────────────────
    # ────────────────────────────────────────────────────────────────
    # GOAL ALIGNMENT TICK — full persistence, no missing variables
    # ────────────────────────────────────────────────────────────────
    if event_type == "goal_alignment_tick":
        """
        Persist goal-alignment snapshots into mastery_state using the same
        canonical schema used by train_mastery_v7:

            columns:
              • ts (auto)
              • version (incrementing)
              • thresholds_json (full payload)
              • source ('SCHEDULER')

        This replaces all prior partial patches and removes the old
        column-ensure / thresholds_json placeholders.
        """
        try:
            # full payload → JSON
            payload_json = json.dumps(payload, ensure_ascii=False)

            # open mastery_v7.db (WAL writer)
            con = _open_mastery_conn()
            cur = con.cursor()

            # ensure canonical table exists
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mastery_state(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT DEFAULT (datetime('now','utc')),
                    version INTEGER DEFAULT 0,
                    thresholds_json TEXT,
                    source TEXT
                )
            """)

            # compute next version number
            row = cur.execute(
                "SELECT COALESCE(MAX(version), 0) FROM mastery_state"
            ).fetchone()
            next_version = (row[0] if row else 0) + 1

            # write new snapshot
            cur.execute("""
                INSERT INTO mastery_state(thresholds_json, version, source)
                VALUES(?, ?, 'SCHEDULER')
            """, (payload_json, next_version))

            con.commit()
            con.close()

            # safe print (ga may or may not exist in payload)
            ga = payload.get("goal_alignment")
            if isinstance(ga, (int, float)):
                print(f"[event_sink] mastery_state updated → goal_alignment={ga:.3f}")
            else:
                print("[event_sink] mastery_state updated (goal_alignment missing)")

        except Exception as e:
            print(f"[event_sink] goal_alignment persist warn: {e}")


    # ───────────────────────────────
    # 4️⃣ Live queue broadcast
    # ───────────────────────────────
    evt = dict(payload); evt["type"] = event_type
    try:
        try:
            _event_queue.put_nowait(evt)
        except Exception:
            try:
                _event_queue.get_nowait()
                _event_queue.put_nowait(evt)
            except Exception:
                pass

        with _lock:
            for fn in list(_listeners):
                try:
                    fn(evt)
                except Exception as sub_e:
                    print(f"[event_sink] listener warn {event_type}: {sub_e}")
    except Exception as e:
        print(f"[event_sink] queue warn {event_type}: {repr(e)}")
# === PATCH END ===



# ───────────────────────────────────────────────────────────────────────
# 3️⃣  HIGH-LEVEL WRAPPERS (decision + feedback)
# ───────────────────────────────────────────────────────────────────────
def on_decision(payload: dict):
    """Unified decision logger (redirected to mastery_v7.db)."""
    try:
        msg = "MASTERY " + json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    except Exception:
        msg = "MASTERY {\"error\":\"serialize\"}"

    try:
        con = _open_mastery_conn()
        con.execute("""
            CREATE TABLE IF NOT EXISTS events(
                ts TEXT, level TEXT, source TEXT, message TEXT
            )
        """)
        con.execute(
            "INSERT INTO events(ts, level, source, message) VALUES (datetime('now','utc'),'INFO','Mastery',?)",
            (msg,)
        )
        con.commit(); con.close()
    except Exception as e:
        print(f"[event_sink] warn (on_decision): {e}")

    # ──────────────────────────────────────────────────────────────
    # 🔁 NEW: broadcast payload to all live listeners immediately
    # ──────────────────────────────────────────────────────────────
    evt = dict(payload)
    evt["type"] = payload.get("type", "decision")

    try:
        with _lock:
            for fn in list(_listeners):
                try:
                    fn(evt)
                except Exception as sub_e:
                    print(f"[event_sink] listener warn (on_decision): {sub_e}")
    except Exception as e:
        print(f"[event_sink] broadcast warn (on_decision): {e}")



def emit_feedback(marketId: str, bias_delta: float, exposure_gap: float,
                  hedge_eff: float, pnl_now: float, status: str = "ACTIVE"):
    """Emit a structured feedback_tick event (redirected to mastery_v7.db)."""
    payload = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "marketId": marketId,
        "bias_delta": round(bias_delta, 6),
        "exposure_gap": round(exposure_gap, 6),
        "hedge_eff": round(hedge_eff, 6),
        "pnl_now": round(pnl_now, 6),
        "status": status,
        "source": "LIVE",
    }
    emit("feedback_tick", payload)




def get(timeout:float=0.5):
    """Retrieve next queued event (non-blocking with timeout)."""
    try:
        return _event_queue.get(timeout=timeout)
    except queue.Empty:
        return None

def subscribe(fn):
    """Register a callback function to receive live events."""
    with _lock:
        _listeners.append(fn)

# === AUTOLOAD UNIFIED BRIDGE ======================================
try:
    import importlib
    bridge = importlib.import_module("engines.mastery_v7.live_router_bridge")
    subscribe(bridge.handle_mastery_event)
    print("[event_sink] 🔗 unified v7 bridge subscribed successfully")
except Exception as e:
    print(f"[event_sink] bridge auto-subscribe warn: {e}")
# ================================================================

# ───────────────────────────────────────────────────────────────────────
# ✅ END OF FILE

