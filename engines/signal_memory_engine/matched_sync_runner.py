# 📦 Standalone module to sync bet match status with Betfair
import requests
import json
import sqlite3
import time
from datetime import datetime, timedelta

import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


from config_paths import DB_PATH

APP_KEY = "CZHojduNWa3kxWIn"
API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

HEADERS_TEMPLATE = {
    "X-Application": APP_KEY,
    "X-Authentication": None,
    "Content-Type": "application/json"
}

def fetch_all_unmatched_bet_refs():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT customerOrderRef 
        FROM bets 
        WHERE status != 'matched'
          AND customerOrderRef IS NOT NULL
    """)
    refs = [row[0] for row in cursor.fetchall()]
    conn.close()
    return refs

def update_matched_status(ref, bet_id):
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE bets
        SET status = 'matched',
            matched_at = ?,
            betfair_bet_id = ?
        WHERE customerOrderRef = ?
    """, (datetime.utcnow().isoformat(), bet_id, ref))
    conn.commit()
    conn.close()

def run_match_sync():
    refs = fetch_all_unmatched_bet_refs()
    if not refs:
        print("✅ No bets to sync.")
        return

    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listCurrentOrders",
        "params": {
            "customerOrderRefs": refs
        },
        "id": 1
    }]

    try:
        response = requests.post(API_URL, headers=HEADERS, json=payload)
        data = response.json()
        orders = data.get("result", {}).get("currentOrders", [])

        matched_count = 0
        for order in orders:
            ref = order.get("customerOrderRef")
            status = order.get("status")
            bet_id = order.get("betId")

            if status == "EXECUTION_COMPLETE":
                update_matched_status(ref, bet_id)
                matched_count += 1
                print(f"✅ Updated: {ref} marked as matched.")

        print(f"🔁 Sync complete. {matched_count} bets marked as matched.")
    
    except Exception as e:
        print(f"❌ Error during Betfair sync: {e}")

if __name__ == "__main__":
    while True:
        run_match_sync()
        time.sleep(5)  # Loop delay (adjust as needed)
