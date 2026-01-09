# engines/brain/brain_listener.py

from collections import deque
from datetime import datetime, timezone
from engines.mastery.event_sink import subscribe

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

def _brain_on_event(ev: dict):
    """
    Phase-1 Brain Listener
    ----------------------
    Observes system events and builds internal state.
    NEVER emits plans.
    NEVER blocks execution.
    """

    if not isinstance(ev, dict):
        return

    # --------------------------------------------------
    # Normalise event name (router emits in two shapes)
    # --------------------------------------------------
    etype = ev.get("type") or ev.get("event")
    if not etype:
        return

    etype = str(etype).upper()

    # store timeline
    _BRAIN_WINDOW.append(ev)

    # --------------------------------------------------
    # Parent lifecycle
    # --------------------------------------------------
    if etype == "PARENT_QUEUED":
        _STATS["parents"] += 1

    # --------------------------------------------------
    # Child lifecycle (hedge / stoploss)
    # --------------------------------------------------
    elif etype == "HEDGE_EXIT":
        _STATS["children"] += 1
        _STATS["hedges"] += 1

    elif etype == "STOPLOSS_EXIT":
        _STATS["children"] += 1
        _STATS["stoploss"] += 1

    # --------------------------------------------------
    # Outcome accounting
    # --------------------------------------------------
    elif etype == "PLAN_OUTCOME":
        outcome = str(ev.get("outcome") or "").upper()
        if outcome in _STATS["outcomes"]:
            _STATS["outcomes"][outcome] += 1

    # --------------------------------------------------
    # Heartbeat (proof of life)
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


def start_brain_listener():
    subscribe(_brain_on_event)
    print("[BRAIN] 🧠 brain listener online")
