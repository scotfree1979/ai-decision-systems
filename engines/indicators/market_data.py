#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_data.py — Standalone Market Data Indicator

Clones the stable pattern from api_tools.py:
1. Prompts for SESSION_TOKEN (if not set).
2. Fetches the next available UK/IRE WIN market.
3. Prints a test runner + odds (for sanity).
4. Fetches enriched market catalogue with runner metadata.
5. Stores results into autoscalp_gui.db.market_data.
"""

import os, sys, json, time, logging, sqlite3, requests
from datetime import datetime, timedelta

AUTO_DB = os.path.join("data", "autoscalp_gui.db")

# === PATCH START ===
# 📍 TARGET: engines/indicators/market_data.py:ensure_table
# 🔎 SEARCH: def ensure_table(
# 📆 PATCHED: 2025-11-21 — DAL-safe writer

from engines.config_paths import auto_conn as _auto_conn

def ensure_table():
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("""
            CREATE TABLE IF NOT EXISTS market_data (
              marketId        TEXT NOT NULL,
              selectionId     INTEGER NOT NULL,
              runnerName      TEXT,
              trainerName     TEXT,
              jockeyName      TEXT,
              age             TEXT,
              stallDraw       TEXT,
              officialRating  TEXT,
              weightValue     TEXT,
              venue           TEXT,
              eventName       TEXT,
              marketName      TEXT,
              marketStartTime TEXT,
              distance        TEXT,
              going           TEXT,
              raw_json        TEXT,
              PRIMARY KEY(marketId, selectionId)
            );
        """)
        con.commit()
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/indicators/market_data.py
# 🔎 SEARCH: def fetch_market_catalogue\(session_token.*\):\n(?:[ \t].*\n)+?\s+return result\[0\] if result else None
# ─────────────────────────────────────────────────────────────────────────────
def fetch_market_catalogue(session_token, market_id=None, projection=None):
    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": "CZHojduNWa3kxWIn",
        "X-Authentication": session_token,
        "Content-Type": "application/json"
    }

    params = {
        "filter": {
            "eventTypeIds": ["7"],
            "marketCountries": ["GB","IE"],
            "marketTypeCodes": ["WIN"],
            "marketStartTime": {
                "from": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            }
        },
        "sort": "FIRST_TO_START",
        "maxResults": "1",
        # 🔑 Changed: request only runner metadata, which includes TRAINER_NAME, JOCKEY_NAME, AGE, OR, STALL, WEIGHT
        "marketProjection": projection or ["RUNNER_DESCRIPTION"]
    }
    if market_id:
        params["filter"] = {"marketIds": [market_id]}

    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketCatalogue",
        "params": params,
        "id": 1
    }])

    r = requests.post(url, headers=headers, data=payload, timeout=10)
    r.raise_for_status()
    result = r.json()[0].get("result", [])
    return result[0] if result else None

# === PATCH START ===
# 📍 TARGET: engines/indicators/market_data.py:fill_market_data_from_bets
# 🔎 SEARCH: def fill_market_data_from_bets(
# 📆 PATCHED: 2025-11-21 — DAL-safe bets reader + DAL writer

from engines.config_paths import connect_db as _connect_db
from engines.config_paths import auto_conn as _auto_conn

def fill_market_data_from_bets():
    con_bets = _connect_db(ro=True)
    con_bets.row_factory = sqlite3.Row

    con_auto = _auto_conn(rw=True)
    con_auto.row_factory = sqlite3.Row

    cur_bets = con_bets.cursor()
    cur_auto = con_auto.cursor()

    cur_bets.execute("""
        SELECT b.marketId, b.selectionId, b.horse_name, b.race_name,
               b.market_name, b.event_name, b.date, b.meta_json
        FROM bets b
        WHERE NOT EXISTS (
          SELECT 1 FROM market_data m
          WHERE m.marketId = b.marketId AND m.selectionId = b.selectionId
        );
    """)
    rows = cur_bets.fetchall()

    print(f"🧩 Fallback enrichment: {len(rows)} entries missing metadata")

    for r in rows:
        mid, sid, horse, race, mkt, evt, date, meta = r
        venue = evt or race
        distance = None
        race_type = None
        going = None

        try:
            j = json.loads(meta) if meta else {}
            event = j.get("market", {}).get("event", {})
            desc = j.get("market", {})
            venue = event.get("venue") or venue
            mname = desc.get("marketName") or mkt
            if mname:
                parts = mname.split()
                for p in parts:
                    if "f" in p and p.replace("f","").isdigit():
                        distance = p
                    if "Hcap" in p or "H'cap" in p or "Handicap" in p:
                        race_type = "Handicap"
                    if "Novice" in p:
                        race_type = "Novice"
        except Exception as e:
            print(f"[warn] failed JSON parse for {mid}-{sid}: {e}")

        row = (
            mid,
            sid,
            horse,
            None, None, None, None, None, None,
            venue,
            evt,
            mkt,
            None,
            distance,
            going,
            meta,
        )

        cur_auto.execute("""
            INSERT OR REPLACE INTO market_data
            (marketId, selectionId, runnerName, trainerName, jockeyName, age, stallDraw,
             officialRating, weightValue, venue, eventName, marketName, marketStartTime,
             distance, going, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, row)

    con_auto.commit()
    con_bets.close()
    con_auto.close()
    print("✅ Fallback metadata inserted from bets.db")
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/indicators/market_data.py:insert_market_data
# 🔎 SEARCH: def insert_market_data(
# 📆 PATCHED: 2025-11-21 — DAL-safe writer

from engines.config_paths import auto_conn as _auto_conn

def insert_market_data(market):
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    market_id = market.get("marketId")
    market_name = market.get("marketName")
    market_start = market.get("marketStartTime")
    desc = market.get("description", {}) or {}
    event = market.get("event", {}) or {}
    venue = event.get("venue")
    event_name = event.get("name")
    going = desc.get("going")
    distance = desc.get("marketType")

    for runner in market.get("runners", []):
        md = runner.get("metadata", {}) or {}
        row = (
            market_id,
            runner.get("selectionId"),
            runner.get("runnerName"),
            md.get("TRAINER_NAME"),
            md.get("JOCKEY_NAME"),
            md.get("AGE"),
            md.get("STALL_DRAW"),
            md.get("OFFICIAL_RATING"),
            md.get("WEIGHT_VALUE"),
            venue,
            event_name,
            market_name,
            market_start,
            distance,
            going,
            json.dumps(runner)
        )
        cur.execute("""
            INSERT OR REPLACE INTO market_data
            (marketId, selectionId, runnerName, trainerName, jockeyName, age, stallDraw,
             officialRating, weightValue, venue, eventName, marketName, marketStartTime,
             distance, going, raw_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, row)

    con.commit()
    con.close()
# === PATCH END ===


if __name__ == "__main__":
    ensure_table()
    SESSION_TOKEN = os.getenv("SESSION_TOKEN") or input("🔐 Enter your Betfair session token: ")
    os.environ["SESSION_TOKEN"] = SESSION_TOKEN

    print("⏳ Fetching next UK/IRE WIN market...")
    market = fetch_market_catalogue(SESSION_TOKEN)
    if not market:
        print("⚠️ No market found")
        exit()

    market_id = market["marketId"]
    print(f"✅ Market {market_id}: {market.get('marketName')} at {market.get('event',{}).get('venue')}")

    # Ask for runner metadata (trainer/jockey/age/etc.)
    print("⏳ Fetching runner metadata (jockey/trainer/age/OR/etc)...")
    enriched = fetch_market_catalogue(SESSION_TOKEN, market_id=market_id, projection=["RUNNER_DESCRIPTION"])
    if enriched:
        insert_market_data(enriched)
        print(f"💾 Saved {len(enriched.get('runners', []))} runners into market_data table with metadata.")
    # After trying API enrichment, fill missing entries from bets.db
    try:
        fill_market_data_from_bets()
    except Exception as e:
        print(f"⚠️ Fallback enrichment failed: {e}")



