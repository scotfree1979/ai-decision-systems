#!/usr/bin/env python3
# repair_missing_betids.py
import sqlite3, json, requests, os
from engines.config_paths import autoscalp_db

BETFAIR_APP_KEY = "CZHojduNWa3kxWIn"
URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

# === PATCH START ===
# 📍 TARGET: engines/live/repair_missing_betids.py:main
# 🔎 SEARCH: def main():
# 📆 PATCHED: 2025-11-21

from engines.config_paths import auto_conn

def main():
    token = os.getenv("SESSION_TOKEN") or input("🔐 Session token: ").strip()
    if not token:
        print("Token required."); 
        return

    headers = {
        "X-Application": BETFAIR_APP_KEY,
        "X-Authentication": token,
        "Content-Type": "application/json",
    }

    # 1️⃣ fetch cleared orders from Betfair
    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listClearedOrders",
        "params": {"betStatus": "SETTLED"},
        "id": 1
    }])
    r = requests.post(URL, headers=headers, data=payload, timeout=15)
    r.raise_for_status()
    bets = r.json()[0].get("result", {}).get("clearedOrders", [])
    print(f"[FETCH] {len(bets)} cleared orders from Betfair")

    # 2️⃣ fill missing betIds — DAL writer (CLOUD)
    con = auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    n_upd = 0

    for b in bets:
        mid  = b.get("marketId")
        sid  = str(b.get("selectionId"))
        bid  = str(b.get("betId"))
        if not (mid and sid and bid):
            continue
        cur.execute("""
            UPDATE orders
               SET entry_bet_id=?
             WHERE marketId=? AND selectionId=? AND entry_bet_id IS NULL
        """, (bid, mid, sid))
        n_upd += cur.rowcount

    con.commit()
    con.close()

    print(f"[UPDATE] filled {n_upd} missing betIds")
    print("Done.")

# === PATCH END ===


if __name__ == "__main__":
    main()
