# ✅ Upgrade 20250522 – Scoped Utility Functions Only
import logging
import requests
import json

# 🔍 Essential market book getter for downstream compatibility
def get_market_book(session_token, marketId):
    headers = {
        'X-Authentication': session_token,
        'Content-Type': 'application/json'
    }
    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketBook",
        "params": {
            "marketIds": [marketId],
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}
        },
        "id": 1
    }]

    try:
        response = requests.post(
            "https://api.betfair.com/exchange/betting/json-rpc/v1",
            headers=headers,
            data=json.dumps(payload)
        )
        response_data = response.json()
        return response_data[0]['result'][0] if 'result' in response_data[0] else None
    except Exception as e:
        logging.error(f"❌ Error fetching market book for {marketId}: {e}")
        return None

# Legacy stub retained to prevent upstream dependency breakage
def wrap_run_monitor(signal_queue, session_token, markets):
    logging.info("🛑 [Upgrade 22] wrap_run_monitor disabled – now fully routed through v25/26")
    return

def evaluate_signal(signal):
    logging.info("🔍 [Upgrade 22] Delegating evaluate_signal to active risk layer.")
    from risk_manager import RiskManager
    dummy_mgr = RiskManager({}, {}, 500, 1000, 1.5, {}, {}, {})
    return dummy_mgr.evaluate_signal(signal)
