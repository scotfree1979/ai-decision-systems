#!/usr/bin/env python3
# === PATCH START ===
# 📍 TARGET: engines/form/form_reconcile_betfair.py
# 📆 PATCHED: 2025-10-30Z — Betfair winner collector (official reconciliation)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, sys, json, sqlite3, requests
from datetime import datetime, timezone

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from engines.config_paths import autoscalp_db

BETFAIR_APP_KEY = "CZHojduNWa3kxWIn"
URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

def fetch_official_winners(days: int = 7):
    """Fetch official winners via Betfair API for last N days of markets."""
    print(f"[form] Fetching official winners (last {days} days) …")
    con = sqlite3.connect("data/settlements.db")
    con.row_factory = sqlite3.Row

    rows = con.execute(f"""
        SELECT DISTINCT marketId
          FROM bf_cleared_orders
         WHERE date(settledDate) >= date('now','-{days} day')
    """).fetchall()
    mids = [r["marketId"] for r in rows]
    if not mids:
        print("[form] no settled markets to check.")
        return 0

    print(f"[form] scanning {len(mids)} markets …")

    print("🔐 Enter your Betfair session token:")
    token = input("> ").strip()
    if not token:
        print("❌ Session token required.")
        return 0

    headers = {
        "X-Application": BETFAIR_APP_KEY,
        "X-Authentication": token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    found = 0
    cur = con.cursor()
    for i in range(0, len(mids), 40):
        batch = mids[i:i+40]
        payload = json.dumps([{
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {"marketIds": batch},
            "id": 1
        }])
        try:
            r = requests.post(URL, headers=headers, data=payload, timeout=15)
            r.raise_for_status()
            data = r.json()[0].get("result", [])
        except Exception as e:
            print(f"[form] API warn batch {i//40+1}: {e}")
            continue

        for m in data:
            mid = m.get("marketId")
            for runner in m.get("runners", []):
                if runner.get("status") == "WINNER":
                    sid = str(runner["selectionId"])
                    cur.execute("""
                        INSERT OR REPLACE INTO bf_runner_info
                            (marketId, selectionId, runnerName, stallDraw,
                             trainerName, jockeyName, age, weightCarried,
                             officialRating, raw_json, status)
                        SELECT marketId, selectionId, runnerName, stallDraw,
                               trainerName, jockeyName, age, weightCarried,
                               officialRating, raw_json, 'WINNER'
                          FROM bf_runner_info
                         WHERE marketId=? AND selectionId=?
                    """, (mid, sid))
                    found += 1
        con.commit()

    con.close()
    print(f"[form] ✅ recorded {found:,} official Betfair winners.")
    return found

if __name__ == "__main__":
    fetch_official_winners(7)
# === PATCH END ===
