# engines/brain/brain_listener.py

from collections import deque
from datetime import datetime, timezone
from engines.mastery.event_sink import subscribe
import sqlite3
import time
from engines.config_paths import autoscalp_db

_LAST_EVENT_ID = [0]

# rolling window (last N events)
_BRAIN_WINDOW = deque(maxlen=200)

# light counters (no DB yet)
_STATS = {
    "parents": 0,
    "children": 0,
    "stoploss": 0,
    "hedges": 0,
    "outcomes": {"GOOD": 0, "BAD": 0},
}

_LAST_HEARTBEAT = [0]


# ======================================================================================================
# 📍 TARGET: engines/brain/brain_listener.py
# 🔎 SEARCH: def _brain_on_event(ev: dict):
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-06 — align Brain counters with LiveRouter EventSync taxonomy
#
# RATIONALE:
# - Router emits BOTH `type` and `event` fields
# - Settlement events wrap `HEDGE_EXIT` / `STOPLOSS_EXIT`
# - Brain must count parents, children, hedges, stoploss accurately
# - No execution logic, telemetry only
# ======================================================================================================
def _poll_events():
    try:
        con = sqlite3.connect(autoscalp_db())
        con.row_factory = sqlite3.Row

        rows = con.execute("""
            SELECT rowid, level, source, message
            FROM events
            WHERE rowid > ?
            ORDER BY rowid ASC
            LIMIT 200
        """, (_LAST_EVENT_ID[0],)).fetchall()

        if not rows:
            return

        for r in rows:
            _LAST_EVENT_ID[0] = r["rowid"]

            _brain_on_event({
                "source": r["source"],
                "level": r["level"],
                "message": r["message"],
            })

    except Exception:
        pass
    finally:
        try:
            con.close()
        except Exception:
            pass

# ======================================================================================================
# 📍 TARGET: engines/brain/brain_listener.py
# 🔎 SEARCH: def _brain_on_event(ev: dict):
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-06 — canonical EventSync normalisation for Brain telemetry
#
# LOCKED CONTRACT:
# - Brain is telemetry-only
# - No execution logic
# - No strategy interpretation
# - Only counts: parents, hedges, stoplosses, outcomes
# ======================================================================================================

def _brain_on_event(ev: dict):
    """
    Phase-1 Brain Listener — FULL PIPELINE AWARE
    -------------------------------------------
    Observes ALL lifecycle events emitted by:
      • BUS
      • Placement
      • Live Router
      • Settlements

    Brain is TELEMETRY-ONLY.
    It never infers intent, never mutates state, never drives execution.

    Everything is derived from emitted event TEXT.
    """

    if not isinstance(ev, dict):
        return

    msg = str(ev.get("message") or "").upper()
    src = str(ev.get("source") or "").upper()

    if not msg:
        return

    # --------------------------------------------------
    # Rolling event window (debug / future replay)
    # --------------------------------------------------
    _BRAIN_WINDOW.append({
        "ts": datetime.now(timezone.utc).isoformat(),
        "source": src,
        "message": msg,
    })

    # --------------------------------------------------
    # PARENT LIFECYCLE
    # --------------------------------------------------

    # Parent created / queued
    if "ORDERS UPSERT QUEUED" in msg or "PARENT_QUEUED" in msg:
        _STATS["parents"] += 1

    # Parent placed
    elif "ORDERS UPDATE PLACED" in msg or "PLACED" in msg and "PARENT" in msg:
        _STATS["parents"] += 0  # visible but not a new parent

    # Parent matched (child should exist next)
    elif "PARENT_MATCHED" in msg:
        _STATS["parents"] += 0

    # --------------------------------------------------
    # CHILD / HEDGE LIFECYCLE
    # --------------------------------------------------

    # Child created (hedge or stoploss)
    elif "INSERT_CHILD" in msg or "CHILD" in msg and "INSERT" in msg:
        _STATS["children"] += 1

    # Hedge matched (normal exit)
    elif (
        "HEDGE_MATCHED" in msg
        or "CHILD_MATCHED" in msg
        or "GREENUP" in msg
    ):
        _STATS["children"] += 1
        _STATS["hedges"] += 1

    # --------------------------------------------------
    # STOPLOSS
    # --------------------------------------------------

    elif (
        "STOPLOSS" in msg
        or "STOP_LOSS" in msg
        or "OVERWATCHER_STOPLOSS" in msg
    ):
        _STATS["children"] += 1
        _STATS["stoploss"] += 1

    # --------------------------------------------------
    # CANCELLATIONS / FAILURES (IMPORTANT NEGATIVE SIGNALS)
    # --------------------------------------------------

    elif (
        "CANCELLED" in msg
        or "TIMEOUT" in msg
        or "STALE_PLACING" in msg
        or "FAILED" in msg
    ):
        # Negative outcome, but not terminal profit/loss
        pass

    # --------------------------------------------------
    # FINAL OUTCOMES (AUTHORITATIVE)
    # --------------------------------------------------

    # Router / mastery good outcome
    elif "TRADE_OUTCOME" in msg and "GOOD" in msg:
        _STATS["outcomes"]["GOOD"] += 1

    elif "TRADE_OUTCOME" in msg and "BAD" in msg:
        _STATS["outcomes"]["BAD"] += 1

    # Settlement truth (Betfair authoritative)
    elif "ORDER_SETTLED" in msg or "CHILD_SETTLED" in msg:
        # Profit sign determines outcome
        if "PROFIT" in msg or "+" in msg:
            _STATS["outcomes"]["GOOD"] += 1
        else:
            _STATS["outcomes"]["BAD"] += 1

    # --------------------------------------------------
    # HEARTBEAT (proof of life)
    # --------------------------------------------------
    now = datetime.now(timezone.utc).timestamp()
    if now - _LAST_HEARTBEAT[0] > 30:
        _LAST_HEARTBEAT[0] = now
        _emit_brain_heartbeat()



def _emit_brain_heartbeat():
    """
    Single-line proof that the brain is alive and aware.
    """
    total = _STATS["outcomes"]["GOOD"] + _STATS["outcomes"]["BAD"]
    winrate = (
        _STATS["outcomes"]["GOOD"] / total
        if total > 0 else 0.0
    )

    print(
        f"[BRAIN] 🧠 observing | "
        f"parents={_STATS['parents']} "
        f"hedges={_STATS['hedges']} "
        f"stoploss={_STATS['stoploss']} "
        f"winrate={winrate:.3f}"
    )

def _brain_loop():
    """
    Background Brain telemetry loop.
    NON-BLOCKING. TELEMETRY ONLY.
    """
    while True:
        try:
            _poll_events()
        except Exception as e:
            print(f"[BRAIN][ERR] {e}")

        time.sleep(1)



def start_brain_listener():
    import threading

    t = threading.Thread(
        target=_brain_loop,
        name="BrainListener",
        daemon=True,
    )
    t.start()
    return

