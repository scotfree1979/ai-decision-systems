#!/usr/bin/env python3
"""
Betfair Runner Trend Surface (MARKET TRUTH)

• Standalone
• APP KEY: daily_config.get_app_key()
• SESSION TOKEN: env → prompt fallback
• Uses listMarketBook (EX_TRADED)
• NO DB writes
• NO dependency on orders
• Queryable by (marketId, selectionId)

Purpose:
    Given a marketId + selectionId, report
    which way price has ACTUALLY moved.
"""

import os
import json
import requests
import getpass
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from engines.daily_config import get_app_key

API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

# --------------------------------------------------
# Betfair RPC (same semantics as Match Surface)
# --------------------------------------------------
def bf_rpc(app_key: str, token: str, method: str, params: dict) -> dict:
    headers = {
        "X-Application": app_key,
        "X-Authentication": token,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    payload = [{
        "jsonrpc": "2.0",
        "method": f"SportsAPING/v1.0/{method}",
        "params": params,
        "id": 1
    }]
    r = requests.post(API_URL, headers=headers, data=json.dumps(payload), timeout=10)
    r.raise_for_status()
    resp = r.json()[0]
    if "error" in resp:
        raise RuntimeError(resp["error"])
    return resp.get("result", {})

# --------------------------------------------------
# Resolve SESSION TOKEN (identical pattern)
# --------------------------------------------------
def resolve_session_token() -> str:
    tok = os.getenv("SESSION_TOKEN") or os.getenv("BETFAIR_SESSION_TOKEN")
    if tok:
        return tok.strip()
    return getpass.getpass("Enter Betfair SESSION TOKEN: ").strip()

# --------------------------------------------------
# Core: fetch market runner book
# --------------------------------------------------
def fetch_market_book(app_key: str, token: str, market_id: str) -> dict:
    res = bf_rpc(
        app_key,
        token,
        "listMarketBook",
        {
            "marketIds": [market_id],
            "priceProjection": {
                "priceData": ["EX_TRADED"]
            }
        }
    )

    # 🔑 Betfair returns a LIST here
    if not isinstance(res, list) or not res:
        raise RuntimeError(f"No marketBook for marketId={market_id}")

    return res[0]

# --------------------------------------------------
# Public API: get runner trend
# --------------------------------------------------
def get_runner_trend(market_id: str, selection_id: str) -> Dict[str, Any]:
    """
    Return market-truth trend for ONE runner.

    Output:
        {
          direction: "BACK->LAY" | "LAY->BACK" | "FLAT",
          ticks_moved: int,
          from_price: float,
          to_price: float,
          confidence: float
        }
    """

    app_key = get_app_key()
    if not app_key:
        raise RuntimeError("APP_KEY missing from daily_config")

    token = resolve_session_token()
    if not token:
        raise RuntimeError("SESSION_TOKEN not available")

    book = fetch_market_book(app_key, token, market_id)

    for r in book.get("runners") or []:
        if str(r.get("selectionId")) != str(selection_id):
            continue

        ltp = r.get("lastPriceTraded")
        traded = r.get("ex", {}).get("tradedVolume") or []

        if not ltp or not traded:
            return {
                "direction": "FLAT",
                "ticks_moved": 0,
                "from_price": None,
                "to_price": None,
                "confidence": 0.0,
            }

        # First traded price = reference
        from_price = float(traded[0]["price"])
        to_price   = float(ltp)

        # Direction semantics (EXACTLY what you described)
        if to_price < from_price:
            direction = "BACK->LAY"   # price came in
        elif to_price > from_price:
            direction = "LAY->BACK"   # price drifted
        else:
            direction = "FLAT"

        # Tick distance (coarse for now; ladder-accurate later)
        ticks_moved = abs(to_price - from_price)

        # Confidence heuristic (can evolve)
        confidence = min(1.0, ticks_moved / max(from_price, 1.0))

        return {
            "direction": direction,
            "ticks_moved": ticks_moved,
            "from_price": from_price,
            "to_price": to_price,
            "confidence": round(confidence, 3),
        }

    # Runner not found
    return {
        "direction": "FLAT",
        "ticks_moved": 0,
        "from_price": None,
        "to_price": None,
        "confidence": 0.0,
    }

# --------------------------------------------------
# Standalone test entrypoint
# --------------------------------------------------
def main():
    print("\n=== Betfair Runner Trend Surface ===\n")

    market_id = input("MarketId: ").strip()
    selection_id = input("SelectionId: ").strip()

    out = get_runner_trend(market_id, selection_id)

    print("\n--- Runner Trend ---")
    for k, v in out.items():
        print(f"{k}: {v}")

    print("\n=== End Runner Trend ===\n")

if __name__ == "__main__":
    main()
