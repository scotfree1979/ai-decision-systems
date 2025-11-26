# betfair_api.py (Clean Integration with Anchor Odds and Enhanced Logging)

import requests
import logging
import sqlite3
from datetime import datetime, timedelta
from enhanced_real_time_bot_feed import log_session_management
from itertools import count
import json
from anchor_odds_logic import fetch_market_data, generate_customer_order_ref, explicitly_store_runner_details_in_db
from queue import PriorityQueue

# Global settings explicitly defined
APP_KEY = "CZHojduNWa3kxWIn"
SESSION_TOKEN = None
DB_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

def store_session_token(token):
    global SESSION_TOKEN
    SESSION_TOKEN = token

# Explicit manual token entry
def manual_session_token():
    global SESSION_TOKEN
    if SESSION_TOKEN is None:
        SESSION_TOKEN = input("🔐 Enter your Betfair session token: ").strip()
    return SESSION_TOKEN

def get_headers():
    token = manual_session_token()
    if not token:
        logging.error("🔴 No session token available, manual input required.")
    return {
        "X-Application": APP_KEY,
        "X-Authentication": token,
        "Content-Type": "application/json"
    }

class BetfairAPI:
    def __init__(self):
        self.headers = get_headers()
        self.queue = PriorityQueue()
        self.counter = count()

    def add_request(self, priority, method, params, req_id):
        payload = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params,
            "id": req_id
        }
        self.queue.put((priority, next(self.counter), payload))

    def send_batch(self):
        url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
        batch_payload = []
        while not self.queue.empty():
            _, _, payload = self.queue.get()
            batch_payload.append(payload)

        try:
            response = requests.post(url, headers=self.headers, data=json.dumps(batch_payload), timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as e:
            logging.error(f"🔴 API batch request failed: {e}")
            return None

    def fetch_markets(self):
        today = datetime.utcnow().strftime('%Y-%m-%dT00:00:00Z')
        tomorrow = (datetime.utcnow() + timedelta(days=1)).strftime('%Y-%m-%dT00:00:00Z')
        params = {
            "filter": {
                "eventTypeIds": ["7"],
                "marketStartTime": {"from": today, "to": tomorrow}
            },
            "maxResults": "200",
            "sort": "FIRST_TO_START",
            "marketProjection": ["RUNNER_METADATA", "MARKET_START_TIME"]
        }
        self.add_request(priority=2, method="SportsAPING/v1.0/listMarketCatalogue", params=params, req_id=2)

    def process_market_catalogue(self, response):
        try:
            market_catalogue = response[0].get('result', [])
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()

                for market in market_catalogue:
                    marketId = market['marketId']
                    market_start_time = market['marketStartTime']

                    for runner in market['runners']:
                        selectionId = runner['selectionId']
                        horse_name = runner['runnerName']

                        # Explicit anchor odds fetch
                        anchor_odd = fetch_market_data(marketId, selectionId)

                        customerOrderRef = generate_customer_order_ref(
                            marketId, selectionId, datetime.utcnow().date().isoformat()
                        )

                        cursor.execute("""
                            INSERT OR IGNORE INTO bets (
                                marketId, selectionId, horse_name, anchor_odd, marketStartTime, date, customerOrderRef
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """, (
                            marketId,
                            selectionId,
                            horse_name,
                            anchor_odd,
                            market_start_time,
                            datetime.utcnow().date().isoformat(),
                            customerOrderRef
                        ))

                        logging.info(f"✅ Successfully integrated market data explicitly for runner {horse_name} in market {marketId} with anchor odds {anchor_odd}")

                conn.commit()

        except Exception as e:
            logging.error(f"🔴 Explicit error integrating market data: {e}")
