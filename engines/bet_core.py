# ✅ bet_core.py – Unified Token Logic

from upgrade_import_patch import get_session_token, get_app_key

import sqlite3
import logging
import requests
import json
import hashlib
import time
from datetime import datetime, timedelta

APP_KEY = "CZHojduNWa3kxWIn"
DB_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

logging.basicConfig(
    filename='/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.log',
    level=logging.INFO,
    format='%(asctime)s %(message)s'
)

def generate_customer_ref(marketId, selectionId):
    unique_str = f"{marketId}_{selectionId}_{datetime.utcnow().strftime('%H%M%S')}"
    return hashlib.md5(unique_str.encode()).hexdigest()[:32]

def calculate_tick(odds):
    if odds < 2.0:
        return 0.01
    elif odds < 3.0:
        return 0.02
    elif odds < 4.0:
        return 0.05
    elif odds < 6.0:
        return 0.1
    elif odds < 10.0:
        return 0.2
    elif odds < 20.0:
        return 0.5
    elif odds < 30.0:
        return 1.0
    elif odds < 50.0:
        return 2.0
    elif odds < 100.0:
        return 5.0
    else:
        return 10.0

def round_to_valid_odds(odds):
    tick = calculate_tick(odds)
    rounded_odds = round(round(odds / tick) * tick, 2)
    return float(f"{rounded_odds:.2f}")

def place_bet(session_token, marketId, selectionId, stake, odds, bet_type='LAY', customer_order_ref=None):
    odds = round_to_valid_odds(odds)

    if not customer_order_ref:
        customer_order_ref = generate_customer_ref(marketId, selectionId)

    headers = {
        'X-Application': APP_KEY,
        'X-Authentication': session_token,
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/placeOrders",
        "params": {
            "marketId": marketId,
            "instructions": [{
                "selectionId": selectionId,
                "side": bet_type,
                "orderType": "LIMIT",
                "limitOrder": {
                    "size": stake,
                    "price": odds,
                    "persistenceType": "PERSIST"
                },
                "customerOrderRef": customer_order_ref
            }]
        },
        "id": 1
    }]

    print(f"📡 [DEBUG] Sending bet payload: {json.dumps(payload)}")
    print(f"📡 [DEBUG] Using headers: {json.dumps(headers)}")

    response = requests.post(
        "https://api.betfair.com/exchange/betting/json-rpc/v1",
        headers=headers,
        data=json.dumps(payload)
    ).json()

    print(f"📡 [DEBUG] Response: {response}")

    if 'result' in response[0] and response[0]['result']['status'] == 'SUCCESS':
        return response[0]['result']['instructionReports'][0]['betId']
    else:
        logging.info(f"Bet placement failed: {response}")
        return None

def cancel_bet(session_token, bet_id):
    headers = {
        'X-Application': APP_KEY,
        'X-Authentication': session_token,
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/cancelOrders",
        "params": {"betIds": [bet_id]},
        "id": 1
    }]
    requests.post(
        "https://api.betfair.com/exchange/betting/json-rpc/v1",
        headers=headers,
        data=json.dumps(payload)
    )

def check_bet_matched(session_token, bet_id):
    headers = {
        'X-Application': APP_KEY,
        'X-Authentication': session_token,
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listCurrentOrders",
        "params": {"betIds": [bet_id]},
        "id": 1
    }]
    response = requests.post(
        "https://api.betfair.com/exchange/betting/json-rpc/v1",
        headers=headers,
        data=json.dumps(payload)
    ).json()

    if 'result' in response[0]:
        orders = response[0]['result']['currentOrders']
        if orders and orders[0]['sizeMatched'] >= 0.01:
            return True
    return False

def execute_scalp(session_token, marketId, selectionId, stake, initial_odds, initial_bet_type, market_start_time):
    opposite_bet_type = 'LAY' if initial_bet_type == 'BACK' else 'BACK'
    first_bet_id = place_bet(session_token, marketId, selectionId, stake, initial_odds, initial_bet_type)

    if first_bet_id:
        logging.info(f"✅ First scalp bet placed successfully: {first_bet_id}")
        matched = False
        current_odds = initial_odds
        market_time = datetime.strptime(market_start_time, "%Y-%m-%dT%H:%M:%S.%fZ")

        while not matched:
            time.sleep(3)
            matched = check_bet_matched(session_token, first_bet_id)

            if not matched:
                cancel_bet(session_token, first_bet_id)
                tick = calculate_tick(current_odds)
                current_odds = round_to_valid_odds(
                    current_odds + tick if initial_bet_type == 'LAY' else current_odds - tick
                )
                first_bet_id = place_bet(session_token, marketId, selectionId, stake, current_odds, initial_bet_type)

        adjusted_odds = round_to_valid_odds(
            current_odds + calculate_tick(current_odds) if initial_bet_type == 'LAY' else current_odds - calculate_tick(current_odds)
        )
        second_bet_id = place_bet(session_token, marketId, selectionId, stake, adjusted_odds, opposite_bet_type)

        if second_bet_id:
            logging.info(f"✅ Opposite scalp bet placed successfully: {second_bet_id}")
        else:
            logging.warning("🔴 Opposite scalp bet placement failed")
    else:
        logging.warning("🔴 First scalp bet placement failed")

def green_up(session_token, marketId, target_profit=10):
    headers = {
        'X-Application': APP_KEY,
        'X-Authentication': session_token,
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }

    def get_current_orders():
        payload = [{
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listCurrentOrders",
            "params": {"marketIds": [marketId]},
            "id": 1
        }]
        response = requests.post(
            "https://api.betfair.com/exchange/betting/json-rpc/v1",
            headers=headers,
            data=json.dumps(payload)
        ).json()
        return response[0]['result']['currentOrders'] if 'result' in response[0] else []

    def get_market_book():
        payload = [{
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {
                "marketIds": [marketId],
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}
            },
            "id": 1
        }]
        response = requests.post(
            "https://api.betfair.com/exchange/betting/json-rpc/v1",
            headers=headers,
            data=json.dumps(payload)
        ).json()
        return response[0]['result'][0] if 'result' in response[0] else None

    current_orders = get_current_orders()
    market_book = get_market_book()

    if not market_book or not current_orders:
        logging.warning("🔴 No market data or open positions found.")
        return

    for order in current_orders:
        selectionId = order['selectionId']
        side = order['side']
        matched_odds = order['averagePriceMatched']
        matched_stake = order['sizeMatched']

        for runner in market_book['runners']:
            if runner['selectionId'] == selectionId:
                if side == 'BACK':
                    current_lay_odds = runner['ex']['availableToLay'][0]['price']
                    hedge_stake = round((matched_odds / current_lay_odds) * matched_stake, 2)
                    place_bet(session_token, marketId, selectionId, hedge_stake, current_lay_odds, 'LAY')
                elif side == 'LAY':
                    current_back_odds = runner['ex']['availableToBack'][0]['price']
                    hedge_stake = round((matched_stake * matched_odds) / current_back_odds, 2)
                    place_bet(session_token, marketId, selectionId, hedge_stake, current_back_odds, 'BACK')
