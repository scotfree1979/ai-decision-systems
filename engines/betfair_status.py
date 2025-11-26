# engines/betfair_status.py
from __future__ import annotations
import json, sqlite3, requests
from datetime import datetime, timezone
from typing import Optional, Tuple
import engines.daily_config as daily_config
from engines.config_paths import autoscalp_db

API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _keys() -> Tuple[str, str]:
    import os
    import engines.daily_config as dc
    from engines.session_token import get_app_key as _get_app, get_session_token as _get_tok

    # 1) daily_config first (single source of truth you control at Step-1)
    app_key = getattr(dc, "APP_KEY", None) or getattr(dc, "BETFAIR_APP_KEY", None)
    tok = None
    get_tok = getattr(dc, "get_session_token", None)
    if callable(get_tok):
        try:
            v = get_tok()
            tok = v.strip() if isinstance(v, str) else None
        except Exception:
            tok = None

    # 2) env (for spawned subprocesses that inherit)
    app_key = app_key or os.getenv("BETFAIR_APP_KEY") or ""
    tok     = tok     or os.getenv("BETFAIR_SESSION_TOKEN") or ""

    # 3) session store (file/DB) last
    app_key = app_key or (_get_app() or "")
    tok     = tok     or (_get_tok() or "")

    if not app_key:
        raise RuntimeError("APP_KEY not set (daily_config/env/session store)")
    if not tok:
        raise RuntimeError("No session token (daily_config/env/session store)")
    return app_key, tok



def _rpc(method: str, params: dict) -> dict:
    app_key, tok = _keys()
    headers = {
        "X-Application": app_key,
        "X-Authentication": tok,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = [{"jsonrpc":"2.0","method":f"SportsAPING/v1.0/{method}","params":params,"id":1}]
    r = requests.post(API_URL, headers=headers, data=json.dumps(payload), timeout=8)
    r.raise_for_status()
    resp = r.json()[0]
    if "error" in resp:
        raise RuntimeError(f"Betfair {method} error: {resp['error']}")
    return resp["result"]

def get_market_phase(market_id: str) -> str:
    """
    -> 'PRE' (not in-play), 'OFF' (in-play or closed).
    Uses listMarketBook (status + inplay flag). Conservative: SUSPENDED/CLOSED => OFF.
    """
    try:
        res = _rpc("listMarketBook", {"marketIds": [str(market_id)]})
        if not res:
            return "PRE"
        mb = res[0]
        # API fields: 'inplay' (bool), 'status' in {'OPEN','SUSPENDED','CLOSED'}
        if mb.get("inplay"):
            return "OFF"
        st = str(mb.get("status","")).upper()
        if st in ("SUSPENDED","CLOSED"):
            return "OFF"
        return "PRE"
    except Exception:
        # on any API hiccup, don't break UI
        return "PRE"

def _con() -> sqlite3.Connection:
    c = sqlite3.connect(autoscalp_db(), timeout=30)
    c.row_factory = sqlite3.Row
    # mitigate writer contention
    try:
        c.execute("PRAGMA journal_mode=WAL;")
        c.execute("PRAGMA busy_timeout=5000;")
        c.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return c

def write_race_status(market_id: str, phase: str) -> None:
    """
    Persist latest phase so the dashboard can read without blocking on network.
    Retries briefly if the DB is momentarily locked by another writer.
    """
    import time, sqlite3
    con = _con(); cur = con.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS race_status(
              marketId TEXT PRIMARY KEY,
              raceStatus TEXT,
              last_seen_ts TEXT
            )
        """)
        attempts = 0
        while True:
            try:
                cur.execute("""
                    INSERT INTO race_status(marketId, raceStatus, last_seen_ts)
                    VALUES(?,?,?)
                    ON CONFLICT(marketId) DO UPDATE SET
                      raceStatus=excluded.raceStatus,
                      last_seen_ts=excluded.last_seen_ts
                """, (str(market_id), str(phase), _utcnow()))
                con.commit()
                break
            except sqlite3.OperationalError as e:
                msg = str(e).lower()
                if "locked" in msg and attempts < 5:
                    attempts += 1
                    time.sleep(0.2 * attempts)  # backoff up to ~1s
                    continue
                # Give up quietly; it's a cache, not critical path
                # print(f"[race_status] upsert skipped: {e}")
                break
    finally:
        try:
            con.close()
        except Exception:
            pass


def get_or_update_phase(market_id: str) -> str:
    """
    Read local race_status if it's fresh; otherwise hit Betfair and refresh.
    """
    # try cached first (≤2 minutes considered fresh)
    try:
        con = _con()
        row = con.execute("SELECT raceStatus, last_seen_ts FROM race_status WHERE marketId=?", (str(market_id),)).fetchone()
        if row and row["last_seen_ts"]:
            ts = row["last_seen_ts"]
            # accept cache if within ~120s
            from datetime import datetime, timezone
            try:
                dt = datetime.fromisoformat(ts.replace("Z","+00:00")) if "T" in ts else datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                age = (datetime.now(timezone.utc) - dt).total_seconds()
                if age <= 120:
                    return str(row["raceStatus"])
            except Exception:
                pass
        con.close()
    except Exception:
        pass
    # refresh from Betfair
    phase = get_market_phase(market_id)
    try:
        write_race_status(market_id, phase)
    except Exception:
        pass
    return phase
