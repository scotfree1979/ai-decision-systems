#!/usr/bin/env python3
"""
Betfair Match Surface — Unified (CURRENT ⊔ CLEARED)

• DB-driven: today's PARENT betIds
• APP KEY: daily_config.get_app_key()
• SESSION TOKEN: env first, prompt fallback
• Queries BOTH listCurrentOrders and listClearedOrders
• Prints authoritative matched surface
• No DB writes, no side effects
"""

import os
import json
import sqlite3
import requests
import getpass
from datetime import datetime, timezone
from typing import Dict, Any

from engines.daily_config import get_app_key
from engines.config_paths import autoscalp_db

API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"


# --------------------------------------------------
# Betfair RPC
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
# Resolve session token (env → prompt)
# --------------------------------------------------
def resolve_session_token() -> str:
    tok = os.getenv("SESSION_TOKEN") or os.getenv("BETFAIR_SESSION_TOKEN")
    if tok:
        return tok.strip()
    return getpass.getpass("Enter Betfair SESSION TOKEN: ").strip()


# --------------------------------------------------
# Load today's betIds from DB (PARENTS + CHILDREN)
# --------------------------------------------------
def load_today_betids_by_role() -> Dict[str, Dict[str, Dict[str, Any]]]:
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT
            role,
            id              AS order_id,
            customerOrderRef,
            entry_bet_id,
            marketId,
            selectionId,
            side,
            entry_stake,
            entry_odds,
            opened_at
        FROM orders
        WHERE role IN ('PARENT','CHILD')
          AND entry_bet_id IS NOT NULL
          AND date(opened_at)=date('now','utc')
        ORDER BY opened_at ASC
    """).fetchall()
    con.close()

    out = {"PARENT": {}, "CHILD": {}}
    for r in rows:
        bet_id = str(r["entry_bet_id"]).strip()
        out[r["role"]][bet_id] = dict(r)

    return out


# --------------------------------------------------
# Fetch CURRENT orders (live)
# --------------------------------------------------
def fetch_current(app_key: str, token: str, bet_ids):
    res = bf_rpc(app_key, token, "listCurrentOrders", {"betIds": bet_ids})
    return {o["betId"]: o for o in (res.get("currentOrders") or [])}


def fetch_cleared(app_key: str, token: str):
    frm = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    to  = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

    res = bf_rpc(
        app_key,
        token,
        "listClearedOrders",
        {
            "betStatus": "CANCELLED",   # ← REQUIRED
            "settledDateRange": {"from": frm, "to": to},
            "includeItemDescription": True
        }
    )

    cleared = res.get("clearedOrders") or []
    return {str(o.get("betId")): o for o in cleared}

def query_bet_match_surface(*, bet_id: str, app_key: str, token: str) -> dict:
    """
    Canonical Betfair match truth for ONE bet_id.

    Returns:
        {
          "exists": bool,
          "matched": float,
          "placed": float,
          "fraction": float,     # matched / placed
          "state": "LIVE" | "TERMINAL" | "UNKNOWN",
          "source": "CURRENT" | "CLEARED" | "NONE"
        }
    """
    # this is literally the same logic you already wrote,
    # just scoped to one bet_id and returning a dict



# --------------------------------------------------
# Main
# --------------------------------------------------
def main():
    print("\n=== Betfair Match Surface (CURRENT ⊔ CLEARED) ===\n")

    app_key = get_app_key()
    if not app_key:
        raise RuntimeError("APP_KEY missing from daily_config")

    token = resolve_session_token()
    if not token:
        raise RuntimeError("SESSION_TOKEN not available")

    by_role = load_today_betids_by_role()

    parents  = by_role["PARENT"]
    children = by_role["CHILD"]

    all_bet_ids = list(parents.keys()) + list(children.keys())

    current = fetch_current(app_key, token, all_bet_ids)
    cleared = fetch_cleared(app_key, token)

    print(f"Parents in DB today  : {len(parents)}")
    print(f"Children in DB today : {len(children)}")
    print(f"Current orders       : {len(current)}")
    print(f"Cleared ledger hits  : {len(cleared)}\n")

    def print_section(title, items):
        print(f"=== {title} ===")

        for bet_id, p in items.items():

            # 🔒 Policy filter: hide TIMEOUT rows
            if str(p.get("entry_status", "")).upper() == "TIMEOUT":
                continue

            if bet_id in current:
                o = current[bet_id]
                size_matched = float(o.get("sizeMatched") or 0.0)
                size_placed  = float(o.get("sizePlaced") or 0.0) or float(p.get("entry_stake") or 0.0)
                state, source = "LIVE", "CURRENT"

            elif bet_id in cleared:
                o = cleared[bet_id]
                size_matched = float(o.get("sizeSettled") or o.get("sizeMatched") or 0.0)
                size_placed  = float(p.get("entry_stake") or 0.0)
                state, source = "TERMINAL", "CLEARED"

            else:
                size_matched = 0.0
                size_placed  = float(p.get("entry_stake") or 0.0)
                state, source = "UNKNOWN", "NONE"

            frac = (size_matched / size_placed) if size_placed > 0 else 0.0

            print(
                f"betId={bet_id} | "
                f"matched={size_matched:.2f}/{size_placed:.2f} "
                f"({frac:.0%}) | "
                f"state={state} | source={source} | "
                f"market={p['marketId']} sid={p['selectionId']}"
            )

        print()


    print_section("PARENT ORDERS", parents)
    print_section("CHILD ORDERS", children)

    print("=== End Match Surface ===\n")



if __name__ == "__main__":
    main()
