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
    Phase-1 Brain Listener
    ----------------------
    Observes EventSync traffic and maintains internal telemetry.

    Counts ONLY:
      • Parent queued
      • Hedge exits
      • Stoploss exits
      • Final outcomes (GOOD / BAD)

    Everything else is ignored by design.
    """

    if not isinstance(ev, dict):
        return

    # --------------------------------------------------
    # Normalise event name (emitters use `type` or `event`)
    # --------------------------------------------------
    etype = ev.get("type") or ev.get("event")
    if not etype:
        return

    etype = str(etype).upper()

    # store rolling timeline (debug only)
    _BRAIN_WINDOW.append(ev)

    # --------------------------------------------------
    # Parent lifecycle
    # --------------------------------------------------
    if etype == "PARENT_QUEUED":
        _STATS["parents"] += 1

    # --------------------------------------------------
    # Child exits — HEDGE (many aliases)
    # --------------------------------------------------
    elif etype in {
        "HEDGE_EXIT",
        "CHILD_MATCHED",
        "GREENUP",
        "GREENUP_ENFORCED",
        "LEGACY_BOUNDARY_EXIT",
    }:
        _STATS["children"] += 1
        _STATS["hedges"] += 1

    # --------------------------------------------------
    # Child exits — STOPLOSS (many aliases)
    # --------------------------------------------------
    elif etype in {
        "STOPLOSS_EXIT",
        "STOPLOSS",
        "STOP_LOSS_TRIGGERED",
        "STOP_LOSS_THRESHOLD_HIT",
        "MSC_STOPLOSS_CLOSE",
        "CHILD_STOPLOSS_CREATED",
    }:
        _STATS["children"] += 1
        _STATS["stoploss"] += 1

    # --------------------------------------------------
    # Final outcome accounting (authoritative)
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
