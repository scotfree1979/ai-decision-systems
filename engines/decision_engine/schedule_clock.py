from __future__ import annotations
import sqlite3, os
from datetime import datetime, timezone
from typing import Dict, Tuple, Optional
from engines.config_paths import connect_db

# in-memory schedule: mid -> off_at_utc (ISO)
_SCHED: Dict[str, str] = {}
_BOOTED_DAY: Optional[str] = None

def _utc_now():
    return datetime.now(timezone.utc)

def _to_epoch(ts_iso: str) -> Optional[float]:
    if not ts_iso:
        return None
    try:
        # tolerate 'Z' or plain
        s = ts_iso.strip().replace("Z", "+00:00")
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None

def _today_utc() -> str:
    return _utc_now().date().isoformat()

def boot_bet_schedule(print_summary: bool = False) -> int:
    """Load today's markets from BETS DB markets_schedule into memory once per day."""
    global _BOOTED_DAY, _SCHED
    day = _today_utc()
    if _BOOTED_DAY == day and _SCHED:
        return len(_SCHED)

    _SCHED = {}
    _BOOTED_DAY = day
    try:
        with connect_db(ro=True) as con:
            con.row_factory = sqlite3.Row
            rows = con.execute(
                "SELECT marketId, off_at_utc "
                "FROM markets_schedule "
                "WHERE date(off_at_utc)=date('now','utc')"
            ).fetchall()
            for r in rows:
                mid = str(r["marketId"])
                off = r["off_at_utc"] or ""
                _SCHED[mid] = off
    except Exception as e:
        if print_summary:
            print(f"[SCHED] load warn: {e}")

    if print_summary:
        n = len(_SCHED)
        print(f"[SCHED] loaded {n} markets for {day} (from BETS DB)")
    return len(_SCHED)

def minutes_to_off(market_id: str) -> Tuple[Optional[float], str, str]:
    """
    Return (minutes_to_off, window_label, source_tag='CLOCK').
    window_label: INP | S3 | S2 | S1 | 60 | 120 | 120+ | UNK
    """
    if not _SCHED:
        boot_bet_schedule(print_summary=False)

    off_iso = _SCHED.get(str(market_id))
    if not off_iso:
        return (None, "UNK", "CLOCK")

    off_epoch = _to_epoch(off_iso)
    if off_epoch is None:
        return (None, "UNK", "CLOCK")

    now_epoch = _utc_now().timestamp()
    diff_min = (off_epoch - now_epoch) / 60.0
    if diff_min <= 0:
        return (diff_min, "INP", "CLOCK")
    # window bucketing
    if diff_min <= 5:   win = "S3"
    elif diff_min <= 10: win = "S2"
    elif diff_min <= 20: win = "S1"
    elif diff_min <= 60: win = "60"
    elif diff_min <= 120: win = "120"
    else: win = "120+"
    return (diff_min, win, "CLOCK")

def set_market_off(market_id: str, off_at_utc: str) -> None:
    """Optional override / late add."""
    _SCHED[str(market_id)] = off_at_utc

def debug_dump(limit: int = 10) -> None:
    day = _today_utc()
    print(f"[SCHED] day={day} total={len(_SCHED)} sample:")
    for i, (mid, ts) in enumerate(list(_SCHED.items())[:limit]):
        print(" ", i+1, mid, ts)
