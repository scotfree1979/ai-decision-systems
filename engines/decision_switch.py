# engines/decision_switch.py
from __future__ import annotations
from typing import Optional
from datetime import datetime, timezone
import sqlite3

from engines.config_paths import autoscalp_db
from engines.betfair_status import get_or_update_phase  # PRE | OFF

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _adb() -> sqlite3.Connection:
    c = sqlite3.connect(autoscalp_db(), timeout=6)
    c.row_factory = sqlite3.Row
    return c

def _off_at_utc(con: sqlite3.Connection, market_id: str) -> Optional[datetime]:
    try:
        r = con.execute("SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (market_id,)).fetchone()
        if not r or not r["off_at_utc"]:
            return None
        return datetime.fromisoformat(str(r["off_at_utc"]).replace("Z","+00:00")).astimezone(timezone.utc)
    except Exception:
        return None

def should_skip_market_for_decisions(market_id: str, *, now: Optional[datetime]=None, grace_after_off_sec: int = 300) -> tuple[bool, str]:
    """
    Returns (skip, reason).
    Skip when:
      - Betfair reports OFF (in-play/closed), OR
      - No Betfair but schedule says off_at_utc < now - grace, OR
      - No schedule + no inbound activity in the last 10 minutes (stale feed).
    """
    now_utc = now or utcnow()

    # 1) Betfair status
    try:
        phase = (get_or_update_phase(market_id) or "").upper()  # PRE|OFF
        if phase == "OFF":
            return True, "betfair_off"
    except Exception:
        pass

    # 2) Schedule grace
    try:
        con = _adb()
        off_dt = _off_at_utc(con, market_id)
        if off_dt and (now_utc - off_dt).total_seconds() > max(0, grace_after_off_sec):
            con.close()
            return True, "schedule_after_off_grace"
        # 3) Inbound freshness fallback
        fresh_ok = True
        if off_dt is None:
            # If no schedule, ensure inbound cache is fresh
            if bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_oc_cache'").fetchone()):
                r = con.execute("SELECT MAX(last_sync_ts) AS ts FROM inbound_oc_cache WHERE marketId=?", (market_id,)).fetchone()
                if r and r["ts"]:
                    try:
                        ts = datetime.fromisoformat(str(r["ts"]).replace("Z","+00:00")).astimezone(timezone.utc)
                        fresh_ok = (now_utc - ts).total_seconds() <= 600
                    except Exception:
                        fresh_ok = False
                else:
                    fresh_ok = False
        con.close()
        if not fresh_ok:
            return True, "stale_inbound_no_schedule"
    except Exception:
        pass

    return False, ""
