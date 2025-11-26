import requests
import json
import logging
import hashlib
from datetime import datetime

# Setup basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global settings explicitly defined
APP_KEY = "CZHojduNWa3kxWIn"

# Explicit manual session token entry
def manual_session_token():
    return input("🔑 Explicitly enter your Betfair session token: ").strip()

# Generate a unique and valid customerRef (32 chars max)
def generate_customer_ref(marketId, selectionId):
    unique_str = f"{marketId}_{selectionId}_{datetime.utcnow().strftime('%H%M%S')}"
    return hashlib.md5(unique_str.encode()).hexdigest()[:32]

# Explicit bet placement function
def place_bet(session_token, marketId, selectionId, stake, odds):
    headers = {
        'X-Application': APP_KEY,
        'X-Authentication': session_token,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/placeOrders",
        "params": {
            "marketId": marketId,
            "instructions": [{
                "selectionId": selectionId,
                "side": "LAY",
                "orderType": "LIMIT",
                "limitOrder": {
                    "size": stake,
                    "price": odds,
                    "persistenceType": "LAPSE"
                }
            }],
            "customerRef": generate_customer_ref(marketId, selectionId)
        },
        "id": 1
    }]

    response = requests.post(
        "https://api.betfair.com/exchange/betting/json-rpc/v1",
        headers=headers,
        data=json.dumps(payload)
    )

    result = response.json()

    if 'result' in result[0] and result[0]['result']['status'] == 'SUCCESS':
        bet_id = result[0]['result']['instructionReports'][0]['betId']
        logging.info(f"✅ Bet placed successfully, Betfair Bet ID: {bet_id}")
        return bet_id
    else:
        error = result[0].get('error', {}).get('message', 'Unknown error')
        logging.error(f"🔴 Failed to place bet: {error}")
        logging.error(f"Response from Betfair: {json.dumps(result, indent=4)}")
        return None

# Explicit main script
if __name__ == "__main__":
    session_token = manual_session_token()
    marketId = input("🏇 Explicitly enter Market ID: ").strip()
    selectionId = int(input("🐴 Explicitly enter Selection ID: "))
    stake = float(input("💷 Explicitly enter Stake (e.g., 2.0): "))
    odds = float(input("📈 Explicitly enter Odds (e.g., 3.5): "))

    bet_id = place_bet(session_token, marketId, selectionId, stake, odds)

    if bet_id:
        logging.info(f"🎯 Bet successfully placed, ID: {bet_id}")
    else:
        logging.error("🚨 Bet placement failed. Please check logs.")
