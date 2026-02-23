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
# === PATCH START ==============================================================
# 📍 TARGET: tools/betfair_runner_trend_surface.py
# 🔎 SEARCH: def get_runner_trend(
# 🛠 ACTION: REPLACE FUNCTION BODY — Cache is Authority
# 📆 PATCHED: 2026-02-23 — Unify trend surface authority (cache-backed)
#
# PURPOSE:
# - get_runner_trend() must return from _TREND_CACHE
# - Prevent duplicate RPC calls
# - Prevent None propagation
# - Preserve standalone behaviour when cache empty
#
# INVARIANT:
# - Never returns None
# - Always returns deterministic dict
# - All engines continue calling get_runner_trend unchanged
# ==============================================================================

def get_runner_trend(market_id: str, selection_id: str) -> Dict[str, Any]:
    """
    Unified authoritative trend getter.

    LIVE MODE:
        Returns cached trend from _TREND_CACHE.
    STANDALONE MODE:
        Falls back to direct RPC if cache empty.
    """

    key = (str(market_id), str(selection_id))

    # --------------------------------------------------
    # 1️⃣ Primary: Cache authority
    # --------------------------------------------------
    cached = _TREND_CACHE.get(key)

    if cached:
        return {
            "direction": cached.get("struct_direction")
                          or cached.get("micro_direction")
                          or "FLAT",

            "ticks_moved": float(
                cached.get("micro_ticks")
                or 0.0
            ),

            "from_price": cached.get("struct_from_price")
                          or cached.get("micro_from_price"),

            "to_price": cached.get("struct_to_price")
                        or cached.get("micro_to_price"),

            "confidence": float(
                cached.get("micro_confidence")
                or 0.0
            ),
        }

    # --------------------------------------------------
    # 2️⃣ Fallback: Standalone RPC mode
    # --------------------------------------------------
    try:
        app_key = get_app_key()
        if not app_key:
            return {
                "direction": "FLAT",
                "ticks_moved": 0.0,
                "from_price": None,
                "to_price": None,
                "confidence": 0.0,
            }

        token = resolve_session_token()
        if not token:
            return {
                "direction": "FLAT",
                "ticks_moved": 0.0,
                "from_price": None,
                "to_price": None,
                "confidence": 0.0,
            }

        book = fetch_market_book(app_key, token, market_id)

        for r in book.get("runners") or []:
            if str(r.get("selectionId")) != str(selection_id):
                continue

            ltp = r.get("lastPriceTraded")
            traded = r.get("ex", {}).get("tradedVolume") or []

            if not ltp or not traded:
                break

            from_price = float(traded[0]["price"])
            to_price   = float(ltp)

            if to_price < from_price:
                direction = "BACK->LAY"
            elif to_price > from_price:
                direction = "LAY->BACK"
            else:
                direction = "FLAT"

            ticks_moved = abs(to_price - from_price)
            confidence = min(1.0, ticks_moved / max(from_price, 1.0))

            return {
                "direction": direction,
                "ticks_moved": float(ticks_moved),
                "from_price": from_price,
                "to_price": to_price,
                "confidence": float(round(confidence, 3)),
            }

    except Exception:
        pass

    # --------------------------------------------------
    # 3️⃣ Absolute safety fallback
    # --------------------------------------------------
    return {
        "direction": "FLAT",
        "ticks_moved": 0.0,
        "from_price": None,
        "to_price": None,
        "confidence": 0.0,
    }

# === PATCH END ==============================================================

# ============================================================
# RUNNER TREND SURFACE LOOP (AUTHORITATIVE)
# ============================================================

import threading
import time

_TREND_CACHE = {}          # (marketId, selectionId) -> trend dict
_TREND_RUNNING = False

def _trend_loop(refresh_s: int = 5):
    global _TREND_RUNNING

    from engines.config_paths import connect_db
    import sqlite3

    _TREND_RUNNING = True

    while _TREND_RUNNING:
        try:
            # ---------------------------------------------
            # 1️⃣ Get today's markets (DB authority)
            # ---------------------------------------------
            con = connect_db(ro=True)
            con.row_factory = sqlite3.Row

            rows = con.execute("""
                SELECT DISTINCT marketId
                FROM bets
                WHERE date(marketStartTime)=date('now','utc')
            """).fetchall()

            con.close()

            if not rows:
                time.sleep(refresh_s)
                continue

            app_key = get_app_key()

            # Resolve once per loop start
            if "_LOOP_TOKEN" not in globals():
                globals()["_LOOP_TOKEN"] = resolve_session_token()

            token = globals()["_LOOP_TOKEN"]

            if not token:
                time.sleep(refresh_s)
                continue

            # ---------------------------------------------
            # 2️⃣ Fetch EX_TRADED per market
            # ---------------------------------------------
            for r in rows:
                mid = str(r["marketId"])
                try:
                    book = fetch_market_book(app_key, token, mid)
                except Exception:
                    continue

                for runner in book.get("runners") or []:
                    sid = str(runner.get("selectionId"))

                    ltp = runner.get("lastPriceTraded")
                    traded = runner.get("ex", {}).get("tradedVolume") or []

                    if not ltp or not traded:
                        continue

                    struct_from = float(traded[0]["price"])
                    struct_to   = float(ltp)

                    # ----------------------------
                    # STRUCTURAL (lifecycle)
                    # ----------------------------
                    if struct_to < struct_from:
                        struct_direction = "BACK->LAY"
                    elif struct_to > struct_from:
                        struct_direction = "LAY->BACK"
                    else:
                        struct_direction = "FLAT"

                    # ----------------------------
                    # MICRO (short horizon)
                    # ----------------------------
                    prev = _TREND_CACHE.get((mid, sid), {})
                    prev_micro_to = prev.get("micro_to_price", struct_to)

                    if struct_to < prev_micro_to:
                        micro_direction = "BACK->LAY"
                    elif struct_to > prev_micro_to:
                        micro_direction = "LAY->BACK"
                    else:
                        micro_direction = "FLAT"

                    micro_ticks = abs(struct_to - prev_micro_to)
                    micro_conf  = min(1.0, micro_ticks / max(prev_micro_to, 1.0))

# =============================================================================
# 📍 TARGET: tools/betfair_runner_trend_surface.py
# 🔎 SEARCH: _TREND_CACHE[(mid, sid)] =
# 🛠 ACTION: Restore full backward-compatible surface contract
# 📆 PATCHED: 2026-02-23 — Contract Restoration (no consumer breakage)
#
# PURPOSE:
# - Reinstate ALL original surface keys
# - Keep struct/micro enhancements
# - Prevent None comparisons
# - Preserve legacy consumers
#
# INVARIANT:
# - Every key that existed before still exists
# - New keys are additive only
# =============================================================================

# =============================================================================
# 📍 TARGET: tools/betfair_runner_trend_surface.py
# 🔎 SEARCH: _TREND_CACHE[(mid, sid)] =
# 📆 PATCHED: 2026-02-23 — Fix broken cache variable mismatch
# =============================================================================

                    # ---- canonical values (must exist before cache write) ----
                    from_price = struct_from
                    to_price   = struct_to

                    if to_price < from_price:
                        direction = "BACK->LAY"
                    elif to_price > from_price:
                        direction = "LAY->BACK"
                    else:
                        direction = "FLAT"

                    ticks_moved = abs(to_price - from_price)
                    confidence  = min(1.0, ticks_moved / max(from_price, 1.0))

                    # ---- cache write (full backward-compatible contract) ----
                    _TREND_CACHE[(mid, sid)] = {

                        # Canonical contract (DO NOT CHANGE)
                        "px": to_price,
                        "direction": direction,
                        "from_price": from_price,
                        "to_price": to_price,
                        "ticks_moved": ticks_moved,
                        "confidence": round(confidence, 3),

                        # Structural layer
                        "struct_direction": direction,
                        "struct_from_price": from_price,
                        "struct_to_price": to_price,

                        # Micro layer
                        "micro_direction": micro_direction,
                        "micro_from_price": prev_micro_to,
                        "micro_to_price": to_price,
                        "micro_ticks": micro_ticks,
                        "micro_confidence": round(micro_conf, 3),

                        "ts": time.time(),
                    }

        except Exception:
            pass

        time.sleep(refresh_s)


def start_runner_trend_surface(refresh_s: int = 5):
    global _TREND_RUNNING
    if _TREND_RUNNING:
        return

    t = threading.Thread(
        target=_trend_loop,
        args=(refresh_s,),
        daemon=True
    )
    t.start()

# --------------------------------------------------
# Standalone test entrypoint
# --------------------------------------------------
def main():
    print("\n=== Betfair Runner Trend Surface LOOP TEST ===\n")

    app_key = get_app_key()
    if not app_key:
        raise RuntimeError("APP_KEY missing from daily_config")

    # resolve once
    token = resolve_session_token()
    globals()["_LOOP_TOKEN"] = token

    print("Starting live trend loop...\n")

    start_runner_trend_surface(refresh_s=2)

    try:
        while True:
            time.sleep(5)
            print(f"\nCACHE SIZE: {len(_TREND_CACHE)}")

            for k, v in list(_TREND_CACHE.items())[:5]:
                print(k, v)

    except KeyboardInterrupt:
        print("\nStopped.\n")

if __name__ == "__main__":
    main()
