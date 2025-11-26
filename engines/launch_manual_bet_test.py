# ✅ launch_manual_bet_final.py – Final Standalone Strategy Launcher for Bet Placement

import os
import logging
import time
import requests
import json
from upgrade_import_patch import (
    place_basic_lay_bet,
    place_ladder_lay_bet,
    place_scalp_trade,
    place_greenup_exit,
    place_inplay_lay_bet
)
from bet_core import place_bet
from upgrade.upgrade_20250602_flow_01 import get_markets
from daily_config import APP_KEY  # ✅ Pull APP_KEY directly

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

print("🔐 Manual Test Launcher")
session_token = input("Enter your Betfair session token: ")
os.environ["SESSION_TOKEN"] = session_token

print("⏳ Waiting 3 seconds before keep-alive...")
time.sleep(3)

print("🔄 Performing session keep-alive...")
response = requests.post(
    "https://identitysso.betfair.com/api/keepAlive",
    headers={
        "X-Authentication": session_token,
        "X-Application": APP_KEY
    }
)
if response.status_code == 200:
    print("✅ Keep-alive successful.")
else:
    print(f"⚠️ Keep-alive failed with status: {response.status_code}")

print("🔍 Fetching first UK/IRE WIN market with runners...")
markets = get_markets()

if not markets:
    print("❌ No suitable GB/IRE market found.")
    exit()

selected_market = markets[0]
market_id = selected_market['marketId']
runners = selected_market['runners'][:4]  # Limit to 4 selections for test

print(f"✅ Using Market ID: {market_id} – {selected_market['event']['venue']} {selected_market['marketName']}")

ref_counter = 1

while True:
    for i, runner in enumerate(runners):
        selection_id = runner['selectionId']
        runner_name = runner['runnerName']
        ref = f"ManualTest{ref_counter:03}"

        print(f"\n📋 Strategy Options for {runner_name} [{selection_id}]: [1] Basic Lay [2] Basic Back [3] Ladder Lays [4] Scalp L→B [5] Scalp B→L [6] Green-Up")
        choice = input("Select a strategy number: ").strip()
        odds = float(input("Enter odds: "))
        stake = float(input("Enter stake: "))

        signal = {
            "marketId": market_id,
            "selectionId": selection_id,
            "odds": odds,
            "stake": stake,
            "customerOrderRef": ref,
            "session_token": session_token,
            "strategy_name": "Manual"
        }

        print(f"🧪 Preparing to execute strategy {choice} for {runner_name} ({selection_id})")
        print(f"🧪 Signal payload: {signal}")

        if choice == "1":
            place_basic_lay_bet(signal)
        elif choice == "2":
            bet_id = place_bet(session_token, market_id, selection_id, stake, odds, "BACK", ref)
            print(f"✅ BACK bet result: {bet_id}")
        elif choice == "3":
            place_ladder_lay_bet(signal)
        elif choice == "4":
            signal["scalp_direction"] = "lay_to_back"
            place_scalp_trade(signal)
        elif choice == "5":
            signal["scalp_direction"] = "back_to_lay"
            place_scalp_trade(signal)
        elif choice == "6":
            place_greenup_exit(signal)
        else:
            print("❌ Invalid strategy number selected.")

        ref_counter += 1

    again = input("\n🔁 Do you want to run another test for the next 4 horses? (yes/no): ").strip().lower()
    if again != "yes":
        print("👋 Exiting manual test.")
        break
