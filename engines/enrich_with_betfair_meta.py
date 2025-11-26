# =============================================
# ✅ betfair_meta_tester.py – Backdoor-Compatible Metadata Verifier
# =============================================

import os
import sys
import json
import time
import logging
import requests
import sqlite3
from datetime import datetime, timedelta
from upgrade_import_patch import APP_KEY, set_session_token

# ✅ Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# ✅ Prompt for Session Token and Store
def prompt_for_token():
    token = input("🔐 Enter your Betfair session token: ").strip()
    os.environ["SESSION_TOKEN"] = token
    set_session_token(token)
    logging.info("✅ Session token set in environment and activated")
    return token

# ✅ Keep-Alive Ping
def keep_alive():
    try:
        headers = {
            "X-Application": APP_KEY,
            "X-Authentication": os.environ.get("SESSION_TOKEN"),
            "Content-Type": "application/json"
        }
        payload = json.dumps([
            {
                "jsonrpc": "2.0",
                "method": "AccountAPING/v1.0/getAccountFunds",
                "params": {},
                "id": 1
            }
        ])
        response = requests.post("https://api.betfair.com/exchange/account/json-rpc/v1", headers=headers, data=payload)
        if response.status_code == 200:
            logging.info("✅ Keep-alive ping successful")
        else:
            logging.warning(f"⚠️ Keep-alive failed with status {response.status_code}")
    except Exception as e:
        logging.error(f"❌ Keep-alive exception: {e}")

# ✅ Standard Market Fetch (Aligned with Tool)
def fetch_markets():
    logging.info("📡 Fetching live WIN markets with 7+ runners...")
    try:
        headers = {
            "X-Application": APP_KEY,
            "X-Authentication": os.environ.get("SESSION_TOKEN"),
            "Content-Type": "application/json"
        }
        payload = json.dumps([
            {
                "jsonrpc": "2.0",
                "method": "SportsAPING/v1.0/listMarketCatalogue",
                "params": {
                    "filter": {
                        "eventTypeIds": ["7"],
                        "marketCountries": ["GB", "IE"],
                        "marketTypeCodes": ["WIN"],
                        "marketStartTime": {
                            "from": datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z"),
                            "to": (datetime.utcnow() + timedelta(days=1)).strftime("%Y-%m-%dT00:00:00Z")
                        }
                    },
                    "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_START_TIME", "EVENT"],
                    "sort": "FIRST_TO_START",
                    "maxResults": "50"
                },
                "id": 1
            }
        ])

        response = requests.post("https://api.betfair.com/exchange/betting/json-rpc/v1", headers=headers, data=payload)
        if response.status_code != 200:
            logging.warning(f"⚠️ Market fetch failed: {response.status_code}")
            return []

        result = response.json()[0].get("result", [])
        filtered = [m for m in result if len(m.get("runners", [])) >= 7]
        logging.info(f"🎯 Filter complete: {len(filtered)} markets ready")

        if not filtered:
            return []

        market = filtered[0]
        market_id = market["marketId"]
        selection_id = market["runners"][0]["selectionId"]
        return [(market_id, selection_id)]

    except Exception as e:
        logging.error(f"❌ Fetch markets failed: {e}")
        return []

# ✅ Metadata Fetch (Runner Metadata)
def get_betfair_runner_meta(marketId, selectionId):
    try:
        headers = {
            "X-Application": APP_KEY,
            "X-Authentication": os.environ.get("SESSION_TOKEN"),
            "Content-Type": "application/json"
        }

        payload = json.dumps([
            {
                "jsonrpc": "2.0",
                "method": "SportsAPING/v1.0/listMarketCatalogue",
                "params": {
                    "filter": {"marketIds": [marketId]},
                    "marketProjection": ["RUNNER_METADATA"],
                    "maxResults": "1"
                },
                "id": 1
            }
        ])

        response = requests.post("https://api.betfair.com/exchange/betting/json-rpc/v1", headers=headers, data=payload)
        result = response.json()[0].get("result", [])
        runners = result[0].get("runners", [])
        for runner in runners:
            if str(runner.get("selectionId")) == str(selectionId):
                logging.info(json.dumps(runner.get("metadata", {}), indent=2))
                return
        logging.warning("⚠️ Runner metadata not found in result")
    except Exception as e:
        logging.error(f"❌ Metadata fetch failed: {e}")

import sqlite3
import time
from database_hijack_monitor import enqueue_write
from config_paths import DB_PATH

def enrich_with_betfair_meta():
    logging.info("🧠 [Thread] enrich_with_betfair_meta launched.")
    while True:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT marketId, selectionId 
                    FROM bets 
                    WHERE meta_json IS NULL AND status = 'awaiting_signal'
                """)
                rows = cursor.fetchall()

            logging.info(f"🔍 Found {len(rows)} runners missing meta_json")

            for marketId, selectionId in rows:
                meta = get_betfair_runner_meta(marketId, selectionId)
                if not meta:
                    continue

                meta_json = json.dumps(meta)
                enqueue_write("""
                    UPDATE bets 
                    SET meta_json = ? 
                    WHERE marketId = ? AND selectionId = ? AND meta_json IS NULL
                """, (meta_json, marketId, selectionId))

                logging.info(f"📥 Injected meta_json for {marketId}-{selectionId} → {meta.get('jockey')} / {meta.get('trainer')}")

        except Exception as e:
            logging.error(f"❌ enrich_with_betfair_meta exception: {e}")

        time.sleep(30)


# ✅ Entrypoint
if __name__ == "__main__":
    logging.info("🚀 Starting Betfair Meta Tester")
    prompt_for_token()

    time.sleep(3)
    keep_alive()

    targets = fetch_markets()
    if not targets:
        logging.warning("⚠️ No valid markets returned from fetch")
        sys.exit(0)

    market1, sel1 = targets[0]
    logging.info(f"📡 Fetching meta for first target: {market1} - {sel1}")
    get_betfair_runner_meta(market1, sel1)

    logging.info("✅ Metadata test complete")

