#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Market Phase Clock — Standalone
--------------------------------
Prints:
- Market
- Internal phase (PRE_*/OFF_PENDING/IN_PLAY/SUSPENDED/CLOSED)
- OC phase (numeric, MSC-compatible)
- Betfair truth (status, inPlay, betDelay)
- Runners + live odds (via api_tools)

Requirements:
- SESSION_TOKEN in env
- BETFAIR_APP_KEY in env or daily_config
"""

# --- ensure project root is on sys.path ---
import os
import sys

HERE = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


def _get_session_token():
    tok = os.getenv("SESSION_TOKEN")
    if tok:
        return tok.strip()

    tok = os.getenv("BETFAIR_SESSION_TOKEN")
    if tok:
        os.environ["SESSION_TOKEN"] = tok.strip()
        return tok.strip()

    tok = input("🔐 Enter your Betfair session token: ").strip()
    if not tok:
        raise RuntimeError("No session token provided")

    os.environ["SESSION_TOKEN"] = tok
    return tok

import json
import requests
from datetime import datetime, timedelta, timezone

# Reuse your existing Betfair helpers
from engines.utils.api_tools import fetch_live_odds, check_market_off_flag

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------

def _utc_now():
    return datetime.now(timezone.utc)

def _parse_utc(ts: str) -> datetime:
    # Betfair returns ISO-Z
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))

def compute_phase(start_time: datetime, *, in_play: bool, status: str):
    """
    Returns (phase_str, oc_phase_float, minutes_to_off_float)
    """
    now = _utc_now()
    minutes = (start_time - now).total_seconds() / 60.0

    if status == "CLOSED":
        return "CLOSED", 9.0, minutes
    if status == "SUSPENDED":
        return "SUSPENDED", 8.0, minutes
    if in_play:
        return "IN_PLAY", 7.0, minutes

    if minutes > 60:
        return "PRE_EARLY", 1.0, minutes
    if minutes > 30:
        return "PRE_MID", 3.0, minutes
    if minutes > 10:
        return "PRE_LATE", 5.0, minutes
    if minutes > 0:
        return "PRE_FINAL", 6.0, minutes

    # clock hit zero but Betfair not in-play yet
    return "OFF_PENDING", 6.5, minutes

# -----------------------------------------------------------------------------
# Betfair calls (catalogue + book)
# -----------------------------------------------------------------------------

def list_market_catalogue(session_token: str, app_key: str, *, max_results=20):
    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": app_key,
        "X-Authentication": session_token,
        "Content-Type": "application/json",
    }
    now = _utc_now()
    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketCatalogue",
        "params": {
            "filter": {
                "eventTypeIds": ["7"],              # Horse Racing
                "marketCountries": ["GB", "IE"],
                "marketTypeCodes": ["WIN"],
                "marketStartTime": {
                    "from": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            },
            "marketProjection": [
                "RUNNER_DESCRIPTION",
                "MARKET_START_TIME",
                "EVENT"
            ],
            "sort": "FIRST_TO_START",
            "maxResults": str(max_results)
        },
        "id": 1
    }])
    r = requests.post(url, headers=headers, data=payload, timeout=10)
    r.raise_for_status()
    return r.json()[0].get("result", [])

def list_market_book(session_token: str, app_key: str, market_ids):
    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    headers = {
        "X-Application": app_key,
        "X-Authentication": session_token,
        "Content-Type": "application/json",
    }
    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketBook",
        "params": {
            "marketIds": market_ids,
            "priceProjection": {},
            "virtualise": True
        },
        "id": 1
    }])
    r = requests.post(url, headers=headers, data=payload, timeout=10)
    r.raise_for_status()
    return r.json()[0].get("result", [])

def map_tto_window(minutes_to_off: float, phase: str) -> str:
    """
    Canonical execution buckets used by ALL engines.
    """
    if phase == "IN_PLAY":
        return "INP"

    if minutes_to_off <= 0:
        return "INP"
    if minutes_to_off <= 5:
        return "S3"
    if minutes_to_off <= 10:
        return "S2"
    if minutes_to_off <= 20:
        return "S1"
    if minutes_to_off <= 60:
        return "60"
    return "120+"

def oc_from_minutes(minutes_to_off: float) -> int:
    """
    Time-derived OC fallback (approximate, reporting only).
    """
    if minutes_to_off > 80:
        return 0
    if minutes_to_off > 60:
        return 1
    if minutes_to_off > 40:
        return 2
    if minutes_to_off > 20:
        return 3
    if minutes_to_off > 10:
        return 4
    if minutes_to_off > 5:
        return 5
    if minutes_to_off > 0:
        return 6

    # post-off, one OC per minute
    return 7 + int(abs(minutes_to_off))

def oc_from_inbound(mid: str) -> int | None:
    """
    Return highest OC index seen for this market today, or None.
    """
    try:
        from engines.config_paths import open_auto_db
        con = open_auto_db(ro=True)
        row = con.execute(
            """
            SELECT MAX(oc_phase)
            FROM inbound_oc_cache
            WHERE marketId=?
            """,
            (mid,)
        ).fetchone()
        con.close()

        if row and row[0] is not None:
            return int(row[0])
    except Exception:
        pass

    return None



# -----------------------------------------------------------------------------
# MarketPhaseClock — BUS API
# -----------------------------------------------------------------------------

class MarketPhase:
    def __init__(self, *, phase, oc_phase, minutes_to_off, in_play):
        self.phase = phase
        self.oc_phase = oc_phase
        self.minutes_to_off = minutes_to_off
        self.in_play = in_play


class MarketPhaseClock:
    """
    Lightweight BUS-facing API.
    No printing. No prompts. No side effects.
    """

    @staticmethod
    def get(market_id: str) -> MarketPhase:
        """
        Return MarketPhase for a marketId using Betfair truth.
        Raises if data unavailable (BUS will fallback).
        """
        # Lazy imports to avoid circulars / heavy load
        from engines.utils.api_tools import check_market_off_flag
        from engines.config_paths import open_bets_db

        # 1) Fetch market start time from local DB (already synced)
        con = con = open_bets_db(ro=True)

        con.row_factory = None
        row = con.execute(
            "SELECT marketStartTime FROM bets WHERE marketId=? "
            "ORDER BY datetime(marketStartTime) DESC LIMIT 1",
            (market_id,)
        ).fetchone()
        con.close()

        if not row or not row[0]:
            raise LookupError(f"No marketStartTime for marketId={market_id}")


        start_time = _parse_utc(str(row[0]))

        # 2) Betfair inPlay flag (authoritative)
        in_play = bool(check_market_off_flag(market_id, os.getenv("SESSION_TOKEN")))

        # 3) Status — default OPEN (suspensions handled elsewhere if needed)
        status = "OPEN"

        phase, oc, mins = compute_phase(
            start_time,
            in_play=in_play,
            status=status
        )

        tto_window = map_tto_window(mins, phase)

        return MarketPhase(
            phase=phase,
            oc_phase=oc,
            minutes_to_off=mins,
            in_play=in_play,
        ), tto_window


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def main():
    session_token = _get_session_token()
    if not session_token:
        raise RuntimeError("SESSION_TOKEN not set")

    # Resolve app key (env first, then daily_config)
    app_key = os.getenv("BETFAIR_APP_KEY")
    if not app_key:
        try:
            from engines.daily_config import BETFAIR_APP_KEY as _AK
            app_key = _AK
        except Exception:
            raise RuntimeError("BETFAIR_APP_KEY not set")

    markets = list_market_catalogue(session_token, app_key, max_results=20)
    if not markets:
        print("No markets returned.")
        return

    market_ids = [m["marketId"] for m in markets]
    books = list_market_book(session_token, app_key, market_ids)
    books_by_id = {b["marketId"]: b for b in books}

    print("\nMARKET PHASE CLOCK — UTC")
    print("-" * 100)

    for m in markets:
        mid = m["marketId"]
        name = m.get("marketName", "")
        start = _parse_utc(m["marketStartTime"])

        book = books_by_id.get(mid, {})
        status = book.get("status", "OPEN")
        in_play = bool(book.get("inPlay", False))
        bet_delay = book.get("betDelay", 0)

        # --- Phase + time ---
        phase, oc_time, mins = compute_phase(start, in_play=in_play, status=status)

        # --- OC diagnostics ---
        oc_inbound = oc_from_inbound(mid)
        oc_time_calc = oc_from_minutes(mins)
        oc_final = oc_inbound if oc_inbound is not None else oc_time_calc
        tto_window = map_tto_window(mins, phase)

        print(
            f"{start:%H:%M} | {name:<24} | "
            f"TTO={mins:7.2f}m | "
            f"OC_final={oc_final:<3} "
            f"(inbound={oc_inbound}, time={oc_time_calc}) | "
            f"{phase:<11} | "
            f"TTO_WIN={tto_window:<4} | "
            f"BF: {status:<9} inPlay={in_play} delay={bet_delay}"
        )


        # Runners + odds
        for r in m.get("runners", []):
            sel_id = r["selectionId"]
            rname = r.get("runnerName", f"#{sel_id}")
            odds = fetch_live_odds(session_token, mid, sel_id)
            if odds:
                print(f"   • {rname:<20} back={odds.get('back')} lay={odds.get('lay')}")

    print("-" * 100)

if __name__ == "__main__":
    main()
