# =============================================
# 📈 VOLATILITY ENGINE – FULL RESTORE CANVAS
# =============================================

"""
This canvas combines the legacy and partially overwritten volatility logic.
It will serve as the foundation to reassemble the complete working version
of the volatility tracking system, including:

- Live Betfair API market scanning
- Anchor odds lookup
- Real-time swing/volatility classification
- Output of volatility signals
- Compatibility with scalp signal engine (range_low/high now integrated)

Next step: test this file standalone to verify range values before reintegrating with main tool.
"""

# ✳️ All core imports from previous versions retained
import requests
import json
import sqlite3
import time
import os
from datetime import datetime, timedelta
from utils.api_tools import fetch_live_odds
from config_paths import DB_PATH

APP_KEY = "CZHojduNWa3kxWIn"
API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

HEADERS_TEMPLATE = {
    "X-Application": APP_KEY,
    "X-Authentication": None,
    "Content-Type": "application/json"
}

volatility_thresholds = {
    "LOW": 10,
    "MEDIUM": 25
}

# ✅ Utility Functions

# === PATCH START ===
# 📍 TARGET: volatility_engine.py:get_recent_odds
# 🔎 SEARCH: def get_recent_odds(
# 📆 PATCHED: 2025-11-21

def get_recent_odds(market_id, selection_id, limit=6):
    """
    Fetch recent odds history for (marketId, selectionId) using DAL-safe RO connection.
    Falls back to live odds if DB lookup fails.
    """
    try:
        from engines.config_paths import connect_db as _connect_db
        con = _connect_db(DB_PATH, ro=True)
        con.row_factory = sqlite3.Row

        cursor = con.cursor()
        cursor.execute("""
            SELECT odds_check_1, odds_check_2, odds_check_3,
                   odds_check_4, odds_check_5, odds_check_6
            FROM bets
            WHERE marketId = ? AND selectionId = ?
            LIMIT 1
        """, (market_id, selection_id))
        row = cursor.fetchone()
        con.close()

        if row:
            return [float(v) for v in row if v is not None]
        return []
    except Exception as e:
        print(f"⚠️ Error fetching odds from bets table for {selection_id}: {e}")
        try:
            odds = fetch_live_odds(None, market_id, selection_id)
            if odds and "lay" in odds:
                print(f"🔁 Using live odds fallback for {selection_id}: {odds['lay']}")
                return [odds['lay']] * 6
        except Exception as ex:
            print(f"❌ Fallback odds fetch failed for {selection_id}: {ex}")
        return []
# === PATCH END ===


def sanitize_headers(headers):
    return {k: v.encode('latin-1', 'ignore').decode('latin-1') for k, v in headers.items()}

def calculate_volatility(anchor_odd, live_odds):
    if anchor_odd and live_odds and anchor_odd > 0:
        swing = ((anchor_odd - live_odds) / anchor_odd) * 100
        abs_swing = abs(swing)
        if abs_swing < volatility_thresholds["LOW"]:
            level = "LOW"
        elif abs_swing < volatility_thresholds["MEDIUM"]:
            level = "MEDIUM"
        else:
            level = "HIGH"
        return round(swing, 2), level
    return 0.0, "UNKNOWN"

# === PATCH START ===
# 📍 TARGET: volatility_engine.py:get_market_start_time
# 🔎 SEARCH: def get_market_start_time(
# 📆 PATCHED: 2025-11-21

def get_market_start_time(market_id):
    """
    Return marketStartTime from bets.db via DAL-safe RO connection.
    """
    try:
        from engines.config_paths import connect_db as _connect_db
        con = _connect_db(DB_PATH, ro=True)
        con.row_factory = sqlite3.Row

        cursor = con.cursor()
        cursor.execute(
            "SELECT marketStartTime FROM bets WHERE marketId=? LIMIT 1",
            (market_id,)
        )
        row = cursor.fetchone()
        con.close()

        return row[0] if row else None
    except Exception as e:
        print(f"DB error fetching start time for market {market_id}: {e}")
        return None
# === PATCH END ===


# ✅ Core Volatility Engine (Per-Runner Call)
def get_volatility_meta(market_id, selection_id, odds_list):
    from price_math import get_tick_size
    from upgrade_import_patch import get_session_token

    def get_range_from_traded_volume():
        try:
            payload = {
                "jsonrpc": "2.0",
                "method": "SportsAPING/v1.0/listMarketBook",
                "params": {
                    "marketIds": [market_id],
                    "priceProjection": {
                        "priceData": ["EX_TRADED"]
                    }
                },
                "id": 1
            }
            headers = dict(HEADERS_TEMPLATE)
            headers["X-Authentication"] = get_session_token()
            response = requests.post(API_URL, data=json.dumps(payload), headers=headers)
            data = response.json()

            runners = data['result'][0].get('runners', [])
            for runner in runners:
                if runner.get('selectionId') == selection_id:
                    traded = runner.get('ex', {}).get('tradedVolume', [])
                    filtered = [p['price'] for p in traded if p['size'] >= 25]
                    if filtered:
                        return round(min(filtered), 2), round(max(filtered), 2)
            return None, None
        except Exception as e:
            print(f"⚠️ API range fetch failed for {selection_id}: {e}")
            return None, None

    if isinstance(odds_list, dict) and "lay" in odds_list:
        odds_list = [odds_list["lay"]]

    cleaned_odds = []
    for o in odds_list:
        if isinstance(o, dict):
            if "lay" in o and isinstance(o["lay"], (int, float)):
                cleaned_odds.append(o["lay"])
            elif "price" in o and isinstance(o["price"], (int, float)):
                cleaned_odds.append(o["price"])
        elif isinstance(o, (int, float)):
            cleaned_odds.append(o)
    odds_list = cleaned_odds

    if not odds_list:
        return {
            "volatile": False,
            "avg_volatility": 0.0,
            "range_low": None,
            "range_high": None
        }

    try:
        mm_low, mm_high = get_range_from_traded_volume()
        if mm_low is not None and mm_high is not None:
            range_low, range_high = mm_low, mm_high
        else:
            tick = get_tick_size(odds_list[-1]) if odds_list[-1] else 0.01
            range_low = round(min(odds_list) - 2 * tick, 2)
            range_high = round(max(odds_list) + 2 * tick, 2)

        diffs = [
            abs(odds_list[i] - odds_list[i - 1]) / odds_list[i - 1]
            for i in range(1, len(odds_list)) if odds_list[i - 1] != 0
        ]
        avg_volatility = round(sum(diffs) / len(diffs), 4) if diffs else 0.0

        return {
            "volatile": avg_volatility > 0.15,
            "avg_volatility": avg_volatility,
            "range_low": range_low,
            "range_high": range_high
        }
    except Exception as e:
        print(f"❌ Volatility meta error for {selection_id}: {e}")
        return {
            "volatile": False,
            "avg_volatility": 0.0,
            "range_low": None,
            "range_high": None
        }

# ✅ Market-wide Volatility Scanner (Global Monitoring)
# === PATCH START ===
# 📍 TARGET: volatility_engine.py:real_time_volatility_check
# 🔎 SEARCH: def real_time_volatility_check(
# 📆 PATCHED: 2025-11-21

def real_time_volatility_check():
    """
    Monitor all tracked runners for volatility using DAL-safe DB reads.
    """
    from upgrade_import_patch import get_session_token
    from utils.api_tools import fetch_live_odds

    print("\n📡 Real-Time Volatility Check Monitoring Markets...\n")

    try:
        from engines.config_paths import connect_db as _connect_db
        con = _connect_db(DB_PATH, ro=True)
        con.row_factory = sqlite3.Row

        cursor = con.cursor()
        cursor.execute("""
            SELECT DISTINCT marketId, selectionId FROM bets
            WHERE anchor_odd IS NOT NULL AND marketStartTime IS NOT NULL
        """)
        tracked = cursor.fetchall()
        con.close()
    except Exception as e:
        print(f"DB query failed: {e}")
        return []

    session_token = get_session_token()

    for market_id, selection_id in tracked:
        try:
            live = fetch_live_odds(session_token, market_id, selection_id)
            if not live or "lay" not in live:
                continue

            odds_list = get_recent_odds(market_id, selection_id)
            vol = get_volatility_meta(market_id, selection_id, odds_list)

            print(
                f"🧪 {selection_id} | Live: {live['lay']} | "
                f"Range: [{vol['range_low']}, {vol['range_high']}] | "
                f"Volatile: {vol['volatile']} ({vol['avg_volatility']})"
            )

        except Exception as e:
            print(f"❌ Vol check failed for {selection_id}: {e}")
# === PATCH END ===


# ✅ Optional fallback for manual test
if __name__ == "__main__":
    SESSION_TOKEN = input("🔐 Enter your Betfair session token: ").strip()
    try:
        # Persist + export so other modules (feeder, status, etc.) see it
        from session_token import mark_token_from_prompt
        mark_token_from_prompt(SESSION_TOKEN)
        # Back-compat: also set the in-memory legacy registry so older calls work immediately
        try:
            from engines.upgrade_import_patch import set_session_token as _legacy_set
            _legacy_set(SESSION_TOKEN)
        except Exception:
            pass
        print("✅ Session token stored for this run.")
    except Exception as e:
        print(f"⚠️ Could not persist token (will use in-memory only): {e}")
    real_time_volatility_check()


