#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_bet_id.py — quick Betfair ID check
---------------------------------------
Allows manual entry of a session token and one Betfair betId, then prints
the result from listCurrentOrders and listClearedOrders for comparison.
Run from project root:

    python3 -m engines.live.test_bet_id
"""
import os, json, requests, sys

BETFAIR_APP_KEY = "CZHojduNWa3kxWIn"
URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

def query(method: str, session_token: str, bet_id: str):
    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": f"SportsAPING/v1.0/{method}",
        "params": {"betIds": [bet_id]},
        "id": 1
    }])
    headers = {
        "X-Application": BETFAIR_APP_KEY,
        "X-Authentication": session_token,
        "Content-Type": "application/json"
    }
    try:
        r = requests.post(URL, headers=headers, data=payload, timeout=10)
        r.raise_for_status()
        j = r.json()[0].get("result", {})
        return j
    except Exception as e:
        return {"error": str(e)}

def main():
    print("🔐 Enter your Betfair session token:")
    token = input("> ").strip()
    if not token:
        print("❌ Token required.")
        sys.exit(1)

    bet_id = input("🎯 Enter one Betfair betId to check (e.g. 403123456789): ").strip()
    if not bet_id:
        print("❌ betId required.")
        sys.exit(1)

    print(f"\n➡️ listCurrentOrders for {bet_id}")
    cur = query("listCurrentOrders", token, bet_id)
    print(json.dumps(cur, indent=2)[:600])

    print(f"\n➡️ listClearedOrders for {bet_id}")
    clr = query("listClearedOrders", token, bet_id)
    print(json.dumps(clr, indent=2)[:600])

if __name__ == "__main__":
    main()
