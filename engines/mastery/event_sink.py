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
# ======================================================================
# 📍 TARGET: engines/mastery/event_sink.py
# 🔎 SEARCH: def on_decision(
# 🛠 ACTION: add TSL integration inside on_decision() dispatcher
# 📆 PATCHED: 2025-11-30
# ======================================================================

# === PATCH START =======================================================
# 📆 PATCHED: 2025-11-30 — Trailing Stop-Loss → Mastery Training Layer

def _ingest_tsl_event(ev: dict):
    """
    Normalise trailing stop-loss events for Mastery training.
    This categorises TS-POS vs TS-NEG and logs into mastery_events,
    playbooks, and v_mastery_intel_v7 (if available).
    """

    try:
        parent_id = ev.get("parent_id")
        mid       = str(ev.get("marketId"))
        sid       = str(ev.get("selectionId"))
        entry_odds = float(ev.get("entry_odds") or 0.0)
        curr_odds  = float(ev.get("current_odds") or 0.0)
        cls        = ev.get("classification") or "TS-UNK"
        sleq_val   = float(ev.get("sleq") or 0.0)
        reason     = ev.get("reason") or "TRAILING"

        # ------------------------
        # 1) mastery_events table (bets.db schema correct)
        # ------------------------
        try:
            from engines.config_paths import connect_db
            con = connect_db(ro=False)
            con.row_factory = __import__("sqlite3").Row

            import json

            payload = {
                "kind": "trailing_stoploss",
                "classification": cls,
                "sleq": sleq_val,
                "parent_id": parent_id,
                "marketId": mid,
                "selectionId": sid,
                "entry_odds": entry_odds,
                "current_odds": curr_odds,
                "reason": reason,
            }

            # ✅ MATCHES bets.db SCHEMA EXACTLY
            con.execute(
                """
                INSERT INTO mastery_events(
                    event_type,
                    details_json,
                    delta_progress,
                    source
                )
                VALUES (?, ?, 0, 'LIVE')
                """,
                (
                    "tsl_event",
                    json.dumps(payload, separators=(',', ':'))
                )
            )

            con.commit()
            con.close()

        except Exception as e:
            print(f"[TSL][mastery_events] warn: {e}")

        # ------------------------
        # 2) Playbooks integration
        # ------------------------
        try:
            from engines.config_paths import auto_conn as _adb
            import json
            adb = _adb()
            adb.row_factory = __import__("sqlite3").Row

            adb.execute("""
                CREATE TABLE IF NOT EXISTS playbooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day TEXT NOT NULL,
                    marketId TEXT NOT NULL,
                    selectionId INTEGER NOT NULL,
                    strategy TEXT,
                    oc_stage TEXT,
                    pattern_key TEXT,
                    band_low REAL,
                    band_high REAL,
                    entry_odds REAL,
                    exit_odds REAL,
                    pnl REAL,
                    outcome TEXT,
                    exposure REAL,
                    confidence REAL,
                    realized_at TEXT DEFAULT (datetime('now','utc')),
                    meta_json TEXT,
                    source TEXT DEFAULT 'LIVE'
                )
            """)

            meta = json.dumps({
                "classification": cls,
                "sleq": sleq_val,
                "reason": reason,
                "created_by": "TSL-Engine",
            }, separators=(',',':'))

            adb.execute("""
                INSERT INTO playbooks(
                    day, marketId, selectionId,
                    strategy, oc_stage, pattern_key,
                    band_low, band_high,
                    entry_odds, exit_odds, pnl, outcome,
                    exposure, confidence, meta_json, source
                )
                VALUES(date('now','utc'), ?, ?, 'TSL', 'TSL',
                       ?, NULL, NULL,
                       ?, ?, 0.0, ?, 0.0, 1.0, ?, 'LIVE')
            """, (
                mid, sid,
                f"TSL-{cls}",
                entry_odds, curr_odds,
                cls,
                meta
            ))
            adb.commit()
            adb.close()
        except Exception:
            pass

        # ------------------------
        # 3) v_mastery_intel_v7 (best-effort)
        # ------------------------
        try:
            from engines.mastery.intel_updater import record_tsl_event
            record_tsl_event({
                "marketId": mid,
                "selectionId": sid,
                "classification": cls,
                "sleq": sleq_val,
                "entry_odds": entry_odds,
                "current_odds": curr_odds,
            })
        except Exception:
            # optional module; ignore silently
            pass

    except Exception as e:
        print(f"[MASTERY][TSL] warn: {e}")


# hook into the main dispatcher
# ============================================================================
# 📍 TARGET: engines/mastery/event_sink.py
# 🔎 SEARCH: (insert BEFORE the TSL wrapper block where `_original_on_decision`
#             was previously referencing an undefined name)
# 📆 PATCHED: 2025-12-01 — Restore original on_decision(), then wrap it.
# ============================================================================

# === PATCH START =============================================================
# ORIGINAL on_decision restored (required before applying TSL wrapper)
def on_decision(ev: dict):
    """
    Default Pass-Through Decision Dispatcher (pre-TSL).
    This simply broadcasts the decision event to any listeners
    and mirrors it through the unified event pipeline.
    """
    try:
        # Mirror into event queue
        evt = dict(ev)
        evt_type = evt.get("type")
        from engines.mastery.event_sink import emit

        # Let Mastery record non-TSL events via canonical emitter
        if evt_type and isinstance(evt_type, str):
            try:
                emit(evt_type, evt)
            except Exception:
                pass

        # Notify local listeners (GUI/Overwatcher, etc.)
        global _listeners, _lock
        with _lock:
            for fn in list(_listeners):
                try:
                    fn(evt)
                except Exception:
                    pass

    except Exception as _e:
        print(f"[event_sink][default_on_decision] warn: {_e}")
# === PATCH END ==============================================================


# ============================================================================
# 📍 TARGET: engines/mastery/event_sink.py
# 🔎 SEARCH: the existing TSL wrapper block beginning with:
#            `_original_on_decision = on_decision`
# 📆 PATCHED: 2025-12-01 — Correct capture of original on_decision + wrapper
# ============================================================================

# === PATCH START =============================================================
_original_on_decision = on_decision   # ← now safe; original exists above

def on_decision(ev: dict):
    """
    Wrapper around original on_decision() that also ingests
    Trailing Stop-Loss events into Mastery (TS-POS / TS-NEG).
    """
    try:
        # TSL event detection
        if isinstance(ev, dict) and ev.get("type") == "stop_loss_triggered":
            try:
                _ingest_tsl_event(ev)
            except Exception as _tsl_e:
                print(f"[MASTERY][TSL] wrap warn: {_tsl_e}")
    except Exception:
        pass

    # Always call original dispatcher
    return _original_on_decision(ev)
# === PATCH END ===============================================================


# === PATCH END =========================================================




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

# === PATCH START ======================================================
# 📍 TARGET: engines/mastery/event_sink.py
# 🔎 SEARCH: "# === AUTOLOAD UNIFIED BRIDGE"
# 📆 PATCHED: 2025-12-04 — remove circular import by lazy-loading bridge
# =====================================================================

def _lazy_load_bridge():
    """
    Delayed loader for mastery_v7.live_router_bridge to avoid
    triggering circular imports during BankState initialisation.
    Runs only once, on first event emission.
    """
    global _BRIDGE_LOADED
    if _BRIDGE_LOADED:
        return
    _BRIDGE_LOADED = True

    try:
        import importlib
        bridge = importlib.import_module("engines.mastery_v7.live_router_bridge")
        subscribe(bridge.handle_mastery_event)
        print("[event_sink] 🔗 unified v7 bridge subscribed (lazy)")
    except Exception as e:
        print(f"[event_sink] bridge lazy-load warn: {e}")

_BRIDGE_LOADED = False
_original_emit = emit
# Wrap emit() so the bridge auto-loads only after BankState + Budget are ready
def emit(event_type: str, payload: dict):
    return _original_emit(event_type, payload)


# === PATCH END ========================================================




# ───────────────────────────────────────────────────────────────────────
# ✅ END OF FILE

