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
def load_today_orders_by_role() -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    DB-FIRST loader.

    IMPORTANT:
    - DO NOT filter on entry_bet_id
    - Surface must show parents even before betId stamping
    - Betfair query happens conditionally later
    """

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
            entry_status,
            opened_at
        FROM orders
        WHERE role IN ('PARENT','CHILD')
          AND date(opened_at)=date('now','utc')
        ORDER BY opened_at ASC
    """).fetchall()

    con.close()

    out = {"PARENT": {}, "CHILD": {}}

    for r in rows:
        key = str(r["order_id"])  # use DB identity, not betId
        out[r["role"]][key] = dict(r)

    return out

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
def fetch_current(app_key: str, token: str, bet_ids, chunk_size: int = 150):
    out = {}

    for i in range(0, len(bet_ids), chunk_size):
        chunk = bet_ids[i:i+chunk_size]

        res = bf_rpc(
            app_key,
            token,
            "listCurrentOrders",
            {"betIds": chunk}
        )

        for o in (res.get("currentOrders") or []):
            out[o["betId"]] = o

    return out

def fetch_cleared(app_key: str, token: str):
    frm = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    to  = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

    res = bf_rpc(
        app_key,
        token,
        "listClearedOrders",
        {
            "betStatus": "CANCELLED",
            "settledDateRange": {"from": frm, "to": to},
            "includeItemDescription": True
        }
    )

    cleared = res.get("clearedOrders") or []
    return {str(o.get("betId")): o for o in cleared}



# ============================================================
# 📍 TARGET: tools/betfair_match_surface.py
# 🔎 SEARCH: import sqlite3
# 🧩 ADD: execution_events persistence helper
# ============================================================

def _persist_execution_event(
    *,
    order_id: int | None,
    bet_id: str,
    role: str,
    matched: float,
    placed: float,
    source: str,
):
    """
    Persist Betfair execution truth.

    Invariants:
    - Insert ONCE, never downgrade
    - Parent: any matched > 0 => fully_matched
    - Child: matched == placed => fully_matched
    """
    try:
        con = sqlite3.connect(autoscalp_db())
        cur = con.cursor()

        fully_matched = 0
        if role == "PARENT" and matched > 0:
            fully_matched = 1
        elif role == "CHILD" and placed > 0 and matched >= placed:
            fully_matched = 1

        cur.execute("""
            INSERT OR IGNORE INTO execution_events (
                order_id,
                bet_id,
                role,
                matched_size,
                placed_size,
                fully_matched,
                seen_at,
                source
            )
            VALUES (?, ?, ?, ?, ?, ?, datetime('now','utc'), ?)
        """, (
            order_id,
            str(bet_id),
            role,
            float(matched),
            float(placed),
            int(fully_matched),
            str(source),
        ))

        con.commit()
        con.close()

    except Exception:
        # Betfair surface must NEVER crash
        pass


def query_bet_match_surface(*, bet_id: str, app_key: str, token: str) -> dict:
    """
    Canonical Betfair match truth for ONE bet_id.
    """
    try:
        # CURRENT orders
        cur = bf_rpc(
            app_key,
            token,
            "listCurrentOrders",
            {"betIds": [str(bet_id)]}
        )
        orders = (cur.get("currentOrders") or [])

        if orders:
            o = orders[0]
            matched = float(o.get("sizeMatched") or 0.0)
            placed  = float(o.get("sizePlaced") or 0.0)

            _persist_execution_event(
                order_id=None,
                bet_id=bet_id,
                role="PARENT",   # child callers override upstream
                matched=matched,
                placed=placed,
                source="CURRENT",
            )

            return {
                "exists": True,
                "matched": matched,
                "placed": placed,
                "fraction": (matched / placed) if placed > 0 else 0.0,
                "state": "LIVE",
                "source": "CURRENT",
            }


        # CLEARED orders
        frm = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        to  = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

        clr = bf_rpc(
            app_key,
            token,
            "listClearedOrders",
            {
                "betStatus": "CANCELLED",
                "settledDateRange": {"from": frm, "to": to},
                "betIds": [str(bet_id)],
                "includeItemDescription": True,
            }
        )

        cleared = clr.get("clearedOrders") or []
        if cleared:
            o = cleared[0]
            matched = float(o.get("sizeSettled") or o.get("sizeMatched") or 0.0)
            placed  = matched

            _persist_execution_event(
                order_id=None,
                bet_id=bet_id,
                role="PARENT",
                matched=matched,
                placed=placed,
                source="CLEARED",
            )

            return {
                "exists": True,
                "matched": matched,
                "placed": placed,
                "fraction": 1.0 if placed > 0 else 0.0,
                "state": "TERMINAL",
                "source": "CLEARED",
            }


        # Exists but not visible
        return {
            "exists": True,
            "matched": 0.0,
            "placed": 0.0,
            "fraction": 0.0,
            "state": "UNKNOWN",
            "source": "NONE",
        }

    except Exception:
        return {
            "exists": False,
            "matched": 0.0,
            "placed": 0.0,
            "fraction": 0.0,
            "state": "UNKNOWN",
            "source": "NONE",
        }


def get_direction_confidence(marketId: str, selectionId: str) -> float:
    """
    Return directional confidence for a runner based on
    completed parent→child hedge cycles.

    Confidence is derived from imbalance between:
      - LAY->BACK cycles (drift)
      - BACK->LAY cycles (steam)

    Returns:
        0.0 → no confirmation / choppy
        1.0 → fully one-sided trend
    """

    import sqlite3
    from engines.config_paths import autoscalp_db

    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute(
        """
        SELECT
            p.id            AS parent_id,
            p.side          AS parent_side,
            c.side          AS child_side
        FROM orders p
        JOIN orders c
          ON c.hedge_of = p.id
        WHERE p.role = 'PARENT'
          AND c.role = 'CHILD'
          AND p.marketId = ?
          AND p.selectionId = ?
          AND p.entry_status = 'MATCHED'
          AND c.entry_status = 'MATCHED'
          AND c.exit_kind = 'HEDGE'
          AND date(p.opened_at) = date('now','utc')
        """,
        (marketId, selectionId)
    ).fetchall()

    con.close()

    if not rows:
        return 0.0

    n_lb = 0  # LAY -> BACK
    n_bl = 0  # BACK -> LAY

    for r in rows:
        p_side = (r["parent_side"] or "").upper()
        c_side = (r["child_side"] or "").upper()

        if p_side == "LAY" and c_side == "BACK":
            n_lb += 1
        elif p_side == "BACK" and c_side == "LAY":
            n_bl += 1

    total = n_lb + n_bl
    if total == 0:
        return 0.0

    imbalance_ratio = abs(n_lb - n_bl) / total
    return round(min(1.0, imbalance_ratio), 3)

# --------------------------------------------------
# AUTO-REPAIR — Stamp missing betIds from Betfair
# --------------------------------------------------
def repair_missing_betids(app_key: str, token: str):
    """
    Deterministic repair using customerOrderRef (authoritative join).
    """

    print("\n[REPAIR] Checking for missing betIds...\n")

    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # 1️⃣ Load DB parents missing betId
    db_rows = cur.execute("""
        SELECT id, customerOrderRef
        FROM orders
        WHERE role='PARENT'
          AND entry_bet_id IS NULL
          AND date(opened_at)=date('now','utc')
    """).fetchall()

    if not db_rows:
        con.close()
        print("[REPAIR] No missing betIds.\n")
        return

    # 2️⃣ Pull ALL Betfair current orders
    current = bf_rpc(app_key, token, "listCurrentOrders", {})
    current_orders = current.get("currentOrders") or []

    # 3️⃣ Pull ALL Betfair settled today
    frm = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    to  = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

    cleared = bf_rpc(
        app_key,
        token,
        "listClearedOrders",
        {
            "betStatus": "SETTLED",
            "settledDateRange": {"from": frm, "to": to},
            "includeItemDescription": True
        }
    ).get("clearedOrders") or []

    stamped = 0

    # Combine surfaces
    surface = current_orders + cleared

    # 4️⃣ Deterministic join on customerOrderRef
    for o in surface:
        cor = str(o.get("customerOrderRef") or "").strip()
        bet_id = str(o.get("betId") or "").strip()

        if not cor or not bet_id:
            continue

        for r in db_rows:
            if r["customerOrderRef"] == cor:
                cur.execute("""
                    UPDATE orders
                    SET entry_bet_id=?
                    WHERE id=?
                """, (bet_id, r["id"]))
                stamped += 1

    con.commit()
    con.close()

    print(f"[REPAIR] Stamped {stamped} missing betIds.\n")


def fetch_full_account_surface(app_key: str, token: str):
    """
    Phase 0 — Pure Betfair account surface.
    No DB usage.
    Returns (current_orders, cleared_orders)
    """

    # CURRENT (no filter)
    cur = bf_rpc(
        app_key,
        token,
        "listCurrentOrders",
        {}
    )
    current_orders = cur.get("currentOrders") or []

    # CLEARED (today only)
    frm = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    to  = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

    clr = bf_rpc(
        app_key,
        token,
        "listClearedOrders",
        {
            "betStatus": "SETTLED",   # use SETTLED not CANCELLED
            "settledDateRange": {"from": frm, "to": to},
            "includeItemDescription": True
        }
    )

    cleared_orders = clr.get("clearedOrders") or []

    return current_orders, cleared_orders



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

    # --------------------------------------------------
    # STEP 0 — Auto Repair Before Tables
    # --------------------------------------------------
    repair_missing_betids(app_key, token)


    # ============================================================
    # TABLE 1 — LEGACY WORKING SURFACE (AUTHORITATIVE)
    # ============================================================

    by_role = load_today_betids_by_role()

    parents  = by_role["PARENT"]
    children = by_role["CHILD"]

    all_bet_ids = list(parents.keys()) + list(children.keys())

    # 🔥 DO NOT FILTER BETFAIR BY DB STATE
    current = fetch_current(app_key, token, all_bet_ids)
    cleared = fetch_cleared(app_key, token)

    # ============================================================
    # TABLE 1 — BETFAIR ACCOUNT SURFACE (AUTHORITATIVE)
    # ============================================================

    print("============================================================")
    print("TABLE 1 — BETFAIR ACCOUNT SURFACE (AUTHORITATIVE)")
    print("============================================================\n")

    current_orders, cleared_orders = fetch_full_account_surface(app_key, token)

    print(f"Current Orders  : {len(current_orders)}")
    print(f"Cleared Orders  : {len(cleared_orders)}\n")

    print("---- LIVE ORDERS ----")
    for o in current_orders:
        print(
            f"LIVE | betId={o.get('betId')} "
            f"status={o.get('orderStatus')} "
            f"matched={o.get('sizeMatched')} "
            f"price={o.get('priceSize', {}).get('price')} "
            f"marketId={o.get('marketId')} "
            f"selectionId={o.get('selectionId')}"
        )

    print("\n---- CLEARED ORDERS ----")
    for o in cleared_orders:
        print(
            f"CLEARED | betId={o.get('betId')} "
            f"matched={o.get('sizeSettled') or o.get('sizeMatched')} "
            f"price={o.get('priceMatched')} "
            f"marketId={o.get('marketId')} "
            f"selectionId={o.get('selectionId')}"
        )

    print("\n============================================================\n")


    # ============================================================
    # TABLE 2 — EXTENDED SURFACE (DERIVED FROM TABLE 1)
    # ============================================================

    print("============================================================")
    print("TABLE 2 — EXTENDED MATCH SURFACE (DERIVED)")
    print("============================================================\n")

    def print_extended(title, items):
        print(f"=== {title} ===")

        for bet_id, p in items.items():

            surf = query_bet_match_surface(
                bet_id=str(bet_id),
                app_key=app_key,
                token=token,
            )

            matched = float(surf.get("matched") or 0.0)
            placed  = float(surf.get("placed") or p.get("entry_stake") or 0.0)
            frac    = (matched / placed) if placed > 0 else 0.0

            print(
                f"betId={bet_id} | "
                f"matched={matched:.2f}/{placed:.2f} "
                f"({frac:.0%}) | "
                f"state={surf.get('state')} | "
                f"source={surf.get('source')} | "
                f"fraction={surf.get('fraction'):.3f}"
            )

        print()

    print_extended("PARENT ORDERS", parents)
    print_extended("CHILD ORDERS", children)

    print("=== End Match Surface ===\n")




if __name__ == "__main__":
    main()
