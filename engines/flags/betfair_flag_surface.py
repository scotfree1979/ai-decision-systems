#!/usr/bin/env python3
"""
Betfair Race Status Surface (Standalone)

Authoritative race flags via Scores API.
"""

import os
import sqlite3
import getpass
import json
import requests
import time
from datetime import datetime, timezone

from engines.daily_config import get_app_key
from engines.config_paths import connect_db


BETTING_RPC = "https://api.betfair.com/exchange/betting/json-rpc/v1"
SCORES_RPC  = "https://api.betfair.com/exchange/scores/json-rpc/v1"


# --------------------------------------------------
# RPC helper
# --------------------------------------------------

def rpc(url, app_key, token, method, params):
    headers = {
        "X-Application": app_key,
        "X-Authentication": token,
        "Content-Type": "application/json",
    }

    payload = [{
        "jsonrpc": "2.0",
        "method": method,
        "params": params,
        "id": 1
    }]

    r = requests.post(url, headers=headers, data=json.dumps(payload), timeout=10)
    r.raise_for_status()
    resp = r.json()[0]

    if "error" in resp:
        raise RuntimeError(resp["error"])

    return resp.get("result") or []


# --------------------------------------------------
# Resolve token
# --------------------------------------------------

def resolve_token():
    tok = os.getenv("SESSION_TOKEN") or os.getenv("BETFAIR_SESSION_TOKEN")
    if tok:
        return tok.strip()
    return getpass.getpass("Enter Betfair SESSION TOKEN: ").strip()


# --------------------------------------------------
# Load today's markets
# --------------------------------------------------

def load_today_markets():
    con = connect_db(ro=True)
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT DISTINCT marketId
        FROM bets
        WHERE date(marketStartTime)=date('now','utc')
        ORDER BY marketStartTime ASC
    """).fetchall()

    con.close()

    return [str(r["marketId"]) for r in rows]


# --------------------------------------------------
# Get eventId from market
# --------------------------------------------------

def get_event_id(app_key, token, market_id):

    res = rpc(
        BETTING_RPC,
        app_key,
        token,
        "SportsAPING/v1.0/listMarketCatalogue",
        {
            "filter": {
                "marketIds": [market_id]
            },
            "marketProjection": ["EVENT"],
            "maxResults": 1
        }
    )

    if not res:
        return None

    return res[0].get("event", {}).get("id")

# --------------------------------------------------
# Get race status from Scores API
# --------------------------------------------------

def get_race_status(app_key, token, event_id, market_id):

    res = rpc(
        SCORES_RPC,
        app_key,
        token,
        "ScoresAPING/v1.0/listRaceDetails",
        {
            "eventIds": [event_id]
        }
    )

    if not res:
        return None

    # 🔑 CRITICAL FIX:
    # Find the race object that matches THIS marketId
    for race in res:
        if str(race.get("marketId")) == str(market_id):
            return race.get("raceStatus")

    return None



# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    print("\n=== BETFAIR RACE STATUS SURFACE ===\n")

    app_key = get_app_key()
    token   = resolve_token()

    markets = load_today_markets()

    if not markets:
        print("No markets found.")
        return

    print(f"Markets found today: {len(markets)}\n")

    for mid in markets:

        try:
            event_id = get_event_id(app_key, token, mid)

            if not event_id:
                print(f"market={mid} | eventId=NONE")
                continue

            race_status = get_race_status(app_key, token, event_id, mid)


            print(
                f"market={mid} | "
                f"eventId={event_id} | "
                f"raceStatus={race_status}"
            )

        except Exception as e:
            print(f"market={mid} | ERROR: {e}")

    print("\n------------------------------------\n")

_FLAG_CACHE = {}
_LAST_FETCH = 0

def get_race_status_cached(market_id: str) -> str | None:
    global _FLAG_CACHE, _LAST_FETCH

    now = time.time()

    # Refresh every 10 seconds
    if now - _LAST_FETCH > 10:
        try:
            app_key = get_app_key()
            token = resolve_token()

            markets = load_today_markets()

            for mid in markets:
                event_id = get_event_id(app_key, token, mid)
                if not event_id:
                    continue
                status = get_race_status(app_key, token, event_id)
                _FLAG_CACHE[mid] = status

            _LAST_FETCH = now

        except Exception:
            pass

    return _FLAG_CACHE.get(market_id)



if __name__ == "__main__":
    main()
