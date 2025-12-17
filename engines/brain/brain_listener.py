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

    etype = ev.get("type") or ev.get("event")
    if not etype:
        return

    # store timeline
    _BRAIN_WINDOW.append(ev)

    # light structural accounting
    if etype == "parent_queued":
        _STATS["parents"] += 1

    elif etype in ("HEDGE_EXIT",):
        _STATS["hedges"] += 1

    elif etype in ("STOPLOSS_EXIT",):
        _STATS["stoploss"] += 1

    elif etype == "plan_outcome":
        outcome = ev.get("outcome")
        if outcome in _STATS["outcomes"]:
            _STATS["outcomes"][outcome] += 1

    # ultra-low-noise heartbeat (once every ~30s)
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
