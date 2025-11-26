import time
import logging
import json
from signal_monitor import signal_queue
from market_monitor_signals import fetch_market_data
from daily_config import headers
from toggles import TOGGLES
from bet_core import place_bet, calculate_tick, round_to_valid_odds

# --- TEMP Risk Override for Testing ---
def evaluate_signal(signal):
    print(f"🔍 Risk Check: Strategy={signal['strategy_name']} | Selection={signal['selectionId']}")
    return True

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# --- Shared Lay Logic ---

def get_market_book(session_token, marketId):
    import requests
    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketBook",
        "params": {
            "marketIds": [marketId],
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}
        },
        "id": 1
    }]
    local_headers = {
        'X-Application': headers['X-Application'],
        'X-Authentication': session_token,
        'Accept': 'application/json',
        'Content-Type': 'application/json'
    }
    response = requests.post(
        "https://api.betfair.com/exchange/betting/json-rpc/v1",
        headers=local_headers,
        data=json.dumps(payload)
    ).json()
    return response[0]['result'][0] if 'result' in response[0] else None
def place_lay_strategy(signal, tag):
    # Override stake logic based on strategy
    total_stake = 10.0
    anchor_stake = total_stake / 5
    ladder_stake = total_stake / 5
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    base_odds = signal.get("odds")
    # stake already overridden above

    # Fetch best available lay odds from market
    market_book = get_market_book(session_token, marketId)
    anchor_odds = None
    for runner in market_book['runners']:
        if runner['selectionId'] == selectionId and runner['ex']['availableToLay']:
            anchor_odds = round_to_valid_odds(runner['ex']['availableToLay'][0]['price'] + calculate_tick(runner['ex']['availableToLay'][0]['price']))
            break
    if anchor_odds is None:
        logging.warning(f"❌ [{tag}] No lay price available for {selectionId}")
        return
    bet_id = place_bet(session_token, marketId, selectionId, anchor_stake, anchor_odds, "LAY")
    if bet_id:
        logging.info(f"✅ [{tag} Anchor] {selectionId} @ {anchor_odds} (£{anchor_stake})")
    else:
        logging.info(f"❌ [{tag} Anchor] Failed @ {anchor_odds}")

    # Place 4 descending ladder legs
    for i in range(1, 5):
        ladder_odds = round_to_valid_odds(anchor_odds - i * calculate_tick(anchor_odds))
        ladder_stake = total_stake / 5
        bet_id = place_bet(session_token, marketId, selectionId, ladder_stake, ladder_odds, "LAY")
        if bet_id:
            logging.info(f"🪜 [{tag} Leg {i}] {selectionId} @ {ladder_odds} (£{ladder_stake})")
        else:
            logging.info(f"🔴 [{tag} Leg {i}] Failed @ {ladder_odds}")

# --- Individual Strategy Wrappers ---
def place_basic_lay_bet(signal):
    place_lay_strategy(signal, tag="BasicLay")

def place_ladder_lay_bet(signal):
    place_lay_strategy(signal, tag="LadderLay")

def place_scalp_trade(signal):
    place_lay_strategy(signal, tag="Scalp")

def place_greenup_exit(signal):
    from bet_core import green_up
    logging.info(f"🟩 [GreenUp] Triggered for market {signal['marketId']}")
    green_up(signal['session_token'], signal['marketId'], target_profit=1)

# --- Signal Injection & Placement ---
def inject_signal_and_place(marketId, selectionId, strategy_name, stake, odds):
    signal = {
        'marketId': marketId,
        'selectionId': selectionId,
        'signal_type': 'Manual Injection',
        'bot_name': 'OGHeadTrader',
        'strategy_name': strategy_name,
        'time_session': 'anytime',
        'stake': stake,
        'odds': odds,
        'session_token': headers["X-Authentication"]
    }

    try:
        signal_queue.put(signal)
        print(f"✅ Injected: {strategy_name} | Selection ID: {selectionId} | Market: {marketId}")

        if TOGGLES.get("place_orders", False):
            if evaluate_signal(signal):
                try:
                    if strategy_name == 'Basic Lay':
                        place_basic_lay_bet(signal)
                    elif strategy_name == 'Ladder Lays':
                        place_ladder_lay_bet(signal)
                    elif strategy_name == 'Scalping (Lay to Back)':
                        place_scalp_trade(signal)
                    elif strategy_name == 'Green-Up':
                        place_greenup_exit(signal)
                    else:
                        print(f"❓ No handler for strategy: {strategy_name}")
                    print(f"🚀 Bet Placed: {strategy_name} | Selection ID: {selectionId}")
                except Exception as e:
                    print(f"❌ Placement Error: {strategy_name} | Selection ID: {selectionId} | Error: {e}")
            else:
                print(f"⚠️ Rejected by Risk Manager: {strategy_name} | Selection ID: {selectionId}")
        else:
            print(f"🔒 Skipped - place_orders OFF: {strategy_name} | Selection ID: {selectionId}")

    except Exception as e:
        print(f"❌ Injection Error: {strategy_name} | Selection ID: {selectionId} | Error: {e}")

# --- Main Routine ---
if __name__ == '__main__':
    try:
        markets = fetch_market_data(headers)
        if not markets:
            print("⚠️ No UK/IE WIN market found.")
            exit()
    except Exception as e:
        print(f"❌ Fetch Error: {e}")
        exit()

    market = markets[0]
    marketId = market['marketId']
    runners = market['runners'][:4]

    for runner in runners:
        selectionId = runner['selectionId']

        inject_signal_and_place(marketId, selectionId, 'Basic Lay', stake=2.0, odds=2.0)
        time.sleep(2)

        inject_signal_and_place(marketId, selectionId, 'Basic Back', stake=2.0, odds=2.0)
        time.sleep(2)

        inject_signal_and_place(marketId, selectionId, 'Ladder Lays', stake=2.0, odds=2.0)
        time.sleep(2)

        inject_signal_and_place(marketId, selectionId, 'Scalping (Lay to Back)', stake=5.0, odds=2.0)
        time.sleep(2)

        

    place_greenup_exit({'marketId': marketId, 'session_token': headers["X-Authentication"]})
    print("🎯 Final System Test Complete.")
