#!/usr/bin/env python3
# ✅ api_tools.py – WOM Extension

import json
import requests
import logging
import os
import time
from datetime import datetime, timedelta, timezone
datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

# -----------------------------
# ✅ LIVE ODDS FETCH (Refactored)
# -----------------------------

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/api_tools.py
# 🔎 SEARCH: ^def fetch_live_odds\(session_token, marketId, selectionId\):\n(?:[ \t].*\n)+?
# ─────────────────────────────────────────────────────────────────────────────
def fetch_live_odds(session_token, marketId, selectionId):
    """
    Return {'back': float|None, 'lay': float|None} for a runner,
    or {} if unavailable. Works in LIVE/LEARNING only.
    """

    # Short-circuit in TEST
    try:
        from engines.upgrade_import_patch import get_mode  # type: ignore
        if (get_mode() or "learning").lower() == "test":
            return {}
    except Exception:
        pass

    # Resolve token if missing
    tok = (session_token or "").strip()
    if not tok:
        try:
            from engines.session_token import get_session_token as _central
            tok = (_central() or "").strip()
        except Exception:
            tok = os.getenv("BETFAIR_SESSION_TOKEN", "").strip()
    if not tok:
        logging.warning("⚠️ fetch_live_odds: no session_token; returning {}")
        return {}

    # Resolve app key
    try:
        from engines.session_token import get_app_key as _ak
        app_key = _ak() or os.getenv("BETFAIR_APP_KEY") or "CZHojduNWa3kxWIn"
    except Exception:
        app_key = os.getenv("BETFAIR_APP_KEY") or "CZHojduNWa3kxWIn"

    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": app_key,
        "X-Authentication": tok,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketBook",
        "params": {
            "marketIds": [str(marketId)],
            "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True}
        },
        "id": 1
    }])

    try:
        resp = requests.post(url, headers=headers, data=payload, timeout=8)
        resp.raise_for_status()
        j = resp.json()
    except Exception as e:
        logging.error(f"❌ fetch_live_odds network error: {e}")
        return {}

    if not isinstance(j, list) or not j or not j[0].get("result"):
        return {}

    books = j[0]["result"]
    if not books:
        return {}

    runners = books[0].get("runners") or []
    sid = int(selectionId)
    for r in runners:
        if int(r.get("selectionId", 0)) == sid:
            ex = r.get("ex") or {}
            lay = ex.get("availableToLay") or []
            back = ex.get("availableToBack") or []
            return {
                "lay": lay[0]["price"] if lay else None,
                "back": back[0]["price"] if back else None,
            }

    logging.warning(f"⚠️ Runner {selectionId} not found in market {marketId}")
    return {}

def fetch_wom_for_runner(marketId: str, selectionId: int, *, depth: int = 6, session_token: str | None = None) -> dict:
    """Return {'back_sum6': float, 'lay_sum6': float, 'levels': [...]} or {}."""


# -----------------------------
# ✅ CHECK IF MARKET IS IN-PLAY (Refactored)
# -----------------------------

def check_market_off_flag(marketId, session_token):
    if not session_token:
        raise ValueError("❌ check_market_off_flag() requires a session_token")

    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": "CZHojduNWa3kxWIn",
        "X-Authentication": session_token,
        "Content-Type": "application/json"
    }

    payload = json.dumps([
        {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {
                "marketIds": [marketId],
                "priceProjection": {},
                "virtualise": True
            },
            "id": 1
        }
    ])

    try:
        response = requests.post(url, headers=headers, data=payload)
        if response.status_code != 200:
            logging.error(f"❌ OFF flag fetch failed: {response.status_code}: {response.text}")
            return False

        result = response.json()[0].get("result", [])
        if not result:
            logging.warning(f"⚠️ Empty OFF result for {marketId}")
            return False

        return result[0].get("inplay", False)

    except Exception as e:
        logging.error(f"❌ Error checking in-play flag: {e}")
        return False

if __name__ == "__main__":
    import os
    import json
    import requests
    from datetime import datetime, timedelta, timezone

    # --- creds (unchanged) ---
    SESSION_TOKEN = os.getenv("SESSION_TOKEN") or input("🔐 Enter your Betfair session token: ").strip()
    APP_KEY = "CZHojduNWa3kxWIn"
    BETTING_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    HEADERS = {
        "X-Application": APP_KEY,
        "X-Authentication": SESSION_TOKEN,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # --- 1) discover 3 markets (+ runners) ---
    print("⏳ Initializing...")
    print("📱 Fetching live UK/IE WIN markets with 7+ runners...")
    payload_cat = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketCatalogue",
        "params": {
            "filter": {
                "eventTypeIds": ["7"],
                "marketCountries": ["GB", "IE"],
                "marketTypeCodes": ["WIN"],
                "marketStartTime": {
                    "from": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
                }
            },
            "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_START_TIME", "EVENT"],
            "sort": "FIRST_TO_START",
            "maxResults": "3"
        },
        "id": 1
    }])
    resp_cat = requests.post(BETTING_URL, headers=HEADERS, data=payload_cat, timeout=8)
    resp_cat.raise_for_status()
    jcat = resp_cat.json()
    markets = (jcat and isinstance(jcat, list) and jcat[0].get("result")) or []
    if not markets:
        print("⚠️ No markets returned. Are you logged in?")
        raise SystemExit(0)

    marketIds = [m["marketId"] for m in markets]
    print("✅ Markets:", ", ".join(marketIds))

    # --- 2) fetch one book per market (not per runner) with depth=6 for +5/+6 ticks ---
    ladder_levels = 6  # we need >5 so we can read 5–6 ticks away
    price_projection = {
        "priceData": ["EX_BEST_OFFERS"],                  # minimal, fast, allowed
        "exBestOffersOverrides": {"bestPricesDepth": ladder_levels},
        "virtualise": True
    }
    payload_book = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketBook",
        "params": {
            "marketIds": marketIds,   # note plural: the list
            "priceProjection": {
                "priceData": ["EX_BEST_OFFERS"],
                "exBestOffersOverrides": {"bestPricesDepth": 6},
                "virtualise": True
            },
            "orderProjection": "ALL",
            "matchProjection": "NO_ROLLUP"
        },
        "id": 1
    }])

    resp_book = requests.post(BETTING_URL, headers=HEADERS, data=payload_book, timeout=8)
    resp_book.raise_for_status()
    jbook = resp_book.json()

    # If Betfair returns an error list, show it and bail early.
    if not (isinstance(jbook, list) and jbook and jbook[0].get("result")):
        print("⚠️ listMarketBook returned no result. Raw body:")
        print(json.dumps(jbook, indent=2)[:1500], "...\n")
        raise SystemExit(1)

    books = jbook[0]["result"]
    by_marketId = {b.get("marketId"): b for b in books}

    # --- helpers for WOM and +5/+6 sums ---
    def _sum_depth(arr, start_idx, end_idx):
        s = 0.0
        if not arr:
            return 0.0
        for i in range(start_idx, min(end_idx + 1, len(arr))):
            try:
                s += float(arr[i].get("size") or 0.0)
            except Exception:
                pass
        return s

    def _fmt(x):
        return f"{x:.2f}" if isinstance(x, (int, float)) else "--"

    # --- 3) print top-3 runners per market with WOM and +5/+6 levels ---
    for m in markets:
        marketId = m["marketId"]
        venue = (m.get("event") or {}).get("venue", "")
        start = m.get("marketStartTime", "")
        book = by_marketId.get(marketId)

        if not book:
            print(f"\n✅ Market: {marketId} — {venue} @ {start}\n   ⚠️ No book data in this call.")
            continue

        print(f"\n✅ Market: {marketId} — {venue} @ {start}")
        print(f"{'Runner':<22} {'Back':>7} {'Lay':>7} {'LTP':>7} {'WOM(B/L)':>10} {'B@+5-6':>8} {'L@+5-6':>8}")

        # map selectionId -> runnerName from catalogue
        names = {rc["selectionId"]: rc.get("runnerName", "") for rc in (m.get("runners") or [])}

        enriched = []
        for r in (book.get("runners") or []):
            sid = r.get("selectionId")
            ex = r.get("ex") or {}
            atb = ex.get("availableToBack") or []
            atl = ex.get("availableToLay") or []

            best_back = atb[0]["price"] if atb else None
            best_lay = atl[0]["price"] if atl else None
            ltp = r.get("lastPriceTraded")

            # WOM from top-3 levels (fast, robust)
            b_top = _sum_depth(atb, 0, 2)
            l_top = _sum_depth(atl, 0, 2)
            wom = "--"
            if (b_top + l_top) > 0:
                lay_pct = int(round((l_top / (b_top + l_top)) * 100))
                wom = f"{100 - lay_pct:>2d}%/{lay_pct:>2d}%"

            # cash sitting 5–6 ticks away from current best (indices 4..5)
            b_5_6 = _sum_depth(atb, 4, 5)
            l_5_6 = _sum_depth(atl, 4, 5)

            # order key: prefer best_back, else ltp, else best_lay
            ord_px = best_back if best_back is not None else (ltp if ltp is not None else best_lay)
            if ord_px is None:
                continue

            enriched.append({
                "name": names.get(sid, str(sid)),
                "bb": best_back,
                "bl": best_lay,
                "ltp": ltp,
                "wom": wom,
                "b56": b_5_6,
                "l56": l_5_6,
                "ord": float(ord_px)
            })

        # pick top-3 by shortest price
        enriched.sort(key=lambda x: x["ord"])
        for row in enriched[:3]:
            nm = (row["name"][:20] + "…") if len(row["name"]) > 21 else row["name"]
            print(f"{nm:<22} {_fmt(row['bb']):>7} {_fmt(row['bl']):>7} {_fmt(row['ltp']):>7} {row['wom']:>10} {_fmt(row['b56']):>8} {_fmt(row['l56']):>8}")
