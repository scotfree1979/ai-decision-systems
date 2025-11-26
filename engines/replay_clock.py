# ─────────────────────────────────────────────────────────────────────────────
# engines/replay_clock.py — compressed "now" for REPLAY (mode="test")
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
import datetime as dt
from typing import Optional

# Internal state
_R_START_REAL: Optional[dt.datetime] = None      # real anchor (utc, naive)
_R_ANCHOR_SIM: Optional[dt.datetime] = None      # simulated anchor (utc, naive)
_R_SPEED_X: float = 60.0
_R_DAY_ISO: Optional[str] = None                 # "YYYY-MM-DD"

def configure_replay(*, replay_day_iso: str, start_at_iso: str, speed_x: float = 60.0) -> None:
    """
    replay_day_iso: "YYYY-MM-DD" (yesterday)
    start_at_iso:   "YYYY-MM-DDTHH:MM:SSZ" (anchor point within replay day)
    speed_x:        60 means 1 real sec -> 1 simulated minute
    """
    global _R_START_REAL, _R_ANCHOR_SIM, _R_SPEED_X, _R_DAY_ISO
    _R_START_REAL = dt.datetime.utcnow()
    _R_ANCHOR_SIM = dt.datetime.fromisoformat(start_at_iso.replace("Z", "+00:00")).astimezone(dt.timezone.utc).replace(tzinfo=None)
    _R_SPEED_X = max(0.1, float(speed_x))
    _R_DAY_ISO = replay_day_iso

def replay_now_utc() -> dt.datetime:
    if _R_START_REAL is None or _R_ANCHOR_SIM is None:
        return dt.datetime.utcnow()
    real_delta = dt.datetime.utcnow() - _R_START_REAL
    sim_delta = dt.timedelta(seconds=real_delta.total_seconds() * _R_SPEED_X)
    return _R_ANCHOR_SIM + sim_delta

def replay_day_iso() -> str:
    return _R_DAY_ISO or dt.datetime.utcnow().date().isoformat()




