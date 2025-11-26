# ✅ REBUILD – market_monitor_signals_v2.py (All Signals Enabled)

import requests
import json
import time
import sqlite3
import logging
from datetime import datetime, timedelta
from risk_manager import RiskManager
from volatility_check import real_time_volatility_check  # 🧠 new volatility logic
from upgrade_import_patch import build_betfair_headers, set_session_token, SESSION_TOKEN

BETFAIR_ENDPOINT = "https://api.betfair.com/exchange/betting/rest/v1.0/"
ACCOUNT_ENDPOINT = "https://api.betfair.com/exchange/account/rest/v1.0/getAccountFunds/"
APP_KEY = "CZHojduNWa3kxWIn"
db_path = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

risk_manager = RiskManager(
    max_liability_per_race=50,
    max_daily_loss_per_bot=100,
    global_max_exposure=200,
    scalping_max_exposure=100,
    scalping_liability_multiplier=1.5,
    max_concurrent_races=5,
    stop_loss_threshold=0.2,
    profit_lock_threshold=0.4
)

SIGNAL_BOT_MAPPING = {
    'Market Drifter Signal': ['OGPreoff', 'OGHeadTrader'],
    'Market Steamer Signal': ['OGPreoff', 'OGHeadTrader'],
    'Favourite Vulnerability Signal': ['OGPreoff', 'OGHeadTrader'],
    'Early Value Signal': ['OGPreoff', 'OGHeadTrader'],
    'Fast Pace Collapse Signal': ['OGInPlay', 'OGHeadTrader'],
    'Drifter Recovery Signal': ['OGInPlay', 'OGHeadTrader'],
    'Late Surge Fade Signal': ['OGInPlay', 'OGHeadTrader'],
    'Odds Spike Reversal Signal': ['OGInPlay', 'OGHeadTrader'],
    'Historical Edge Signal': ['OGHeadTrader'],
    'Volume Surge Signal': ['OGHeadTrader'],
    'Market Inefficiency Signal': ['OGHeadTrader'],
    'Early Leader Stamina Doubt Signal': ['OGInPlay', 'OGHeadTrader'],
    'Multiple Beacon Confirmation Signal': ['OGHeadTrader']
}

def percentage_change(anchor_odds, current_odds):
    return ((1 / current_odds) - (1 / anchor_odds)) / (1 / anchor_odds) * 100

def detect_signal(anchor_odds, current_odds, volatility_meta, time_signal):
    signals = []

    # ✅ Scalp Range Filter – Only for odds between 1.5 and 10
    if 1.5 <= current_odds <= 10:
        range_low = volatility_meta.get("range_low")
        range_high = volatility_meta.get("range_high")

        if range_low and range_high and range_low < range_high:
            position_ratio = (current_odds - range_low) / (range_high - range_low)

            if position_ratio <= 0.3:
                signals.append("Scalp Lay Signal – Entry")
            elif position_ratio >= 0.7:
                signals.append("Scalp Lay Signal – Exit")

    # 📈 Market Shift Signals
    change = percentage_change(anchor_odds, current_odds)
    if change >= 7.5:
        signals.append('Market Steamer Signal')
    elif change <= -7.5:
        signals.append('Market Drifter Signal')

    # ✅ Reversal-Based Signal Logic (Memory Flags)
    if current_odds >= anchor_odds + 3:
        volatility_meta["spike_active"] = True
    if volatility_meta.get("spike_active") and current_odds <= anchor_odds + 1:
        signals.append("Odds Spike Reversal Signal")
        volatility_meta["spike_active"] = False

    if current_odds <= anchor_odds - 2:
        volatility_meta["steam_active"] = True
    if volatility_meta.get("steam_active") and current_odds >= anchor_odds - 1:
        signals.append("Late Surge Fade Signal")
        volatility_meta["steam_active"] = False

    if current_odds >= anchor_odds + 2:
        volatility_meta["drift_active"] = True
    if volatility_meta.get("drift_active") and anchor_odds + 1 >= current_odds >= anchor_odds:
        signals.append("Drifter Recovery Signal")
        volatility_meta["drift_active"] = False

    # 🧠 Pattern Recognition Signals from volatility_meta
    if volatility_meta.get('volume_spike'):
        signals.append('Volume Surge Signal')
    if volatility_meta.get('odds_discrepancy'):
        signals.append('Market Inefficiency Signal')
    if volatility_meta.get('beacon_combo'):
        signals.append('Multiple Beacon Confirmation Signal')
    if volatility_meta.get('fav_weakening'):
        signals.append('Favourite Vulnerability Signal')
    if volatility_meta.get('early_value'):
        signals.append('Early Value Signal')
    if volatility_meta.get('pace_collapse'):
        signals.append('Fast Pace Collapse Signal')
    if volatility_meta.get('stamina_doubt'):
        signals.append('Early Leader Stamina Doubt Signal')

    # ⏱️ Filter signals based on time_signal rules
    filtered_signals = []
    for signal in signals:
        strategy_key = signal.lower().replace(" ", "_").replace("–", "-")
        if strategy_key == "scalp_lay_signal_entry" or strategy_key == "scalp_lay_signal_exit":
            if time_signal == "time_10":
                filtered_signals.append(strategy_key)
        elif strategy_key in [
            "fast_pace_collapse_signal",
            "drifter_recovery_signal",
            "late_surge_fade_signal",
            "odds_spike_reversal_signal",
            "early_leader_stamina_doubt_signal"
        ]:
            if time_signal == "time_0":
                filtered_signals.append(strategy_key)
        else:
            filtered_signals.append(strategy_key)

    return filtered_signals


def store_signal_to_bets(marketId, selectionId, runner_name, signal_type, bot, odds):
    ref = f"{marketId}_{selectionId}_{signal_type[:6]}"
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO bets (
                marketId, selectionId, horse_name, signal_type, strategy_name,
                bot_name, odds, timestamp, status, customerOrderRef
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            marketId, selectionId, runner_name, signal_type, signal_type,
            bot, odds, datetime.utcnow().isoformat(), 'signal_assigned', ref[:32]
        ))
        conn.commit()

def fetch_market_data():
    headers = build_betfair_headers()
    now = datetime.utcnow()
    payload = {
        "filter": {
            "eventTypeIds": ["7"],
            "marketCountries": ["GB", "IE"],
            "marketTypeCodes": ["WIN"],
            "marketStartTime": {
                "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": (now + timedelta(hours=12)).strftime("%Y-%m-%dT%H:%M:%SZ")
            }
        },
        "maxResults": "50",
        "marketProjection": ["RUNNER_METADATA", "MARKET_DESCRIPTION", "MARKET_START_TIME"],
        "sort": "FIRST_TO_START"
    }
    return requests.post(BETFAIR_ENDPOINT + "listMarketCatalogue/", headers=headers, json=payload).json()

def fetch_live_odds(marketId, selectionId):
    headers = build_betfair_headers()
    payload = {
        "marketIds": [marketId],
        "priceProjection": {"priceData": ["EX_BEST_OFFERS"]}
    }
    r = requests.post(BETFAIR_ENDPOINT + "listMarketBook/", headers=headers, json=payload).json()
    if r and 'runners' in r[0]:
        for runner in r[0]['runners']:
            if runner['selectionId'] == selectionId:
                lay = runner['ex']['availableToLay']
                if lay:
                    return lay[0]['price']
    return None

def monitor_markets():
    while True:
        markets = fetch_market_data()
        for market in markets:
            marketId = market['marketId']
            for runner in market.get('runners', []):
                selectionId = runner['selectionId']
                runner_name = runner['runnerName']
                live_odds = fetch_live_odds(marketId, selectionId)
                if not live_odds:
                    continue

                volatility_meta = real_time_volatility_check(marketId, selectionId, live_odds)
                anchor_odd = volatility_meta.get("initial_odds") or volatility_meta.get("anchor_odd")
                if not anchor_odd:
                    continue  # Skip signal detection without anchor
                signals = detect_signal(anchor_odd, live_odds, volatility_meta)

                for signal_type in signals:
                    for bot in SIGNAL_BOT_MAPPING.get(signal_type, []):
                        store_signal_to_bets(marketId, selectionId, runner_name, signal_type, bot, live_odds)
                        print(f"[SIGNAL] {signal_type} | Market: {marketId} | Runner: {runner_name} | Bot: {bot} | Odds: {live_odds}")

        print("[DEBUG] Cycle complete. Waiting 60s...")
        time.sleep(60)

if __name__ == "__main__":
    token = input("🔐 Enter your Betfair session token: ").strip()
    set_session_token(token)
    print("✅ Market Monitor Running...")
    monitor_markets()
