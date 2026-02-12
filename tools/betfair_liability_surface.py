#!/usr/bin/env python3
"""
Betfair Liability Surface — Phase 1 (Exchange Truth World Builder)

GOAL:
- Build authoritative Betfair execution surface
- Include:
    betId
    marketId
    selectionId
    side
    matched size
    average matched price
- No BankState mutation
- No DB writes
- No refund logic

This builds the world for Phase 2.
"""

import os
import sqlite3
import getpass
from collections import defaultdict
from datetime import datetime, timezone

from engines.daily_config import get_app_key
from engines.config_paths import autoscalp_db
from tools.betfair_match_surface import bf_rpc


API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"


# --------------------------------------------------
# Resolve session token
# --------------------------------------------------

def resolve_token() -> str:
    tok = os.getenv("SESSION_TOKEN") or os.getenv("BETFAIR_SESSION_TOKEN")
    if tok:
        return tok.strip()
    return getpass.getpass("Enter Betfair SESSION TOKEN: ").strip()


# --------------------------------------------------
# Load today's parent betIds from DB
# (DB only used to discover betIds + side mapping)
# --------------------------------------------------

def load_today_parent_betids():
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row

    rows = con.execute("""
        SELECT
            id,
            entry_bet_id,
            marketId,
            selectionId,
            side,
            engine
        FROM orders
        WHERE role='PARENT'
          AND entry_bet_id IS NOT NULL
          AND date(opened_at)=date('now','utc')
    """).fetchall()

    con.close()
    return rows

# --------------------------------------------------
# Bind Betfair surface to DB metadata (Phase 2 prep)
# --------------------------------------------------

def enrich_surface_with_db(surface):
    """
    Attach DB identity fields:
        order_id
        engine
        role
        parent_id
    No DB mutation.
    """

    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    enriched = []

    for row in surface:

        db = cur.execute("""
            SELECT
                id,
                role,
                hedge_of,
                engine
            FROM orders
            WHERE entry_bet_id=?
            LIMIT 1
        """, (row["bet_id"],)).fetchone()

        if not db:
            continue

        enriched.append({
            **row,
            "order_id": db["id"],
            "role": db["role"],
            "parent_id": db["hedge_of"] if db["role"] == "CHILD" else db["id"],
            "engine": db["engine"],
        })

    con.close()
    return enriched

# --------------------------------------------------
# Fetch CURRENT + CLEARED from Betfair
# --------------------------------------------------

def fetch_current(app_key: str, token: str, bet_ids, chunk_size: int = 150):
    result = {}

    for i in range(0, len(bet_ids), chunk_size):
        chunk = bet_ids[i:i+chunk_size]

        res = bf_rpc(
            app_key,
            token,
            "listCurrentOrders",
            {"betIds": chunk}
        )

        for o in (res.get("currentOrders") or []):
            result[str(o["betId"])] = o

    return result


def fetch_cleared(app_key: str, token: str, bet_ids, chunk_size: int = 150):
    result = {}

    frm = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    to  = datetime.now(timezone.utc).strftime("%Y-%m-%dT23:59:59Z")

    for i in range(0, len(bet_ids), chunk_size):
        chunk = bet_ids[i:i+chunk_size]

        res = bf_rpc(
            app_key,
            token,
            "listClearedOrders",
            {
                "betStatus": "SETTLED",
                "settledDateRange": {"from": frm, "to": to},
                "betIds": chunk,
                "includeItemDescription": True
            }
        )

        for o in (res.get("clearedOrders") or []):
            result[str(o["betId"])] = o

    return result



# --------------------------------------------------
# Build enriched Betfair execution surface
# --------------------------------------------------

def build_betfair_surface(parents, app_key, token):

    bet_ids = [str(p["entry_bet_id"]) for p in parents]

    current = fetch_current(app_key, token, bet_ids)
    cleared = fetch_cleared(app_key, token, bet_ids)

    surface = []

    for p in parents:

        bet_id = str(p["entry_bet_id"])
        side   = (p["side"] or "").upper()

        matched = 0.0
        avg_price = 0.0
        source = "NONE"

        # CURRENT takes priority
        if bet_id in current:
            o = current[bet_id]
            matched   = float(o.get("sizeMatched") or 0.0)
            avg_price = float(o.get("averagePriceMatched") or 0.0)
            source    = "CURRENT"

        # CLEARED fallback
        elif bet_id in cleared:
            o = cleared[bet_id]
            matched   = float(o.get("sizeSettled") or o.get("sizeMatched") or 0.0)
            avg_price = float(o.get("priceMatched") or o.get("averagePriceMatched") or 0.0)
            source    = "CLEARED"

        if matched <= 0:
            continue

        surface.append({
            "bet_id": bet_id,
            "marketId": str(p["marketId"]),
            "selectionId": str(p["selectionId"]),
            "side": side,
            "matched_size": matched,
            "avg_price": avg_price,
            "source": source,
        })

    return surface

def compute_true_market_liability(surface):

    from collections import defaultdict

    # market → runner → pnl
    market_pnl = defaultdict(lambda: defaultdict(float))

    # collect unique runners per market
    runners_by_market = defaultdict(set)
    for row in surface:
        runners_by_market[row["marketId"]].add(row["selectionId"])

    # simulate
    for row in surface:

        mid = row["marketId"]
        sid = row["selectionId"]
        side = row["side"]
        matched = float(row["matched_size"])
        odds = float(row["avg_price"])

        for winner in runners_by_market[mid]:

            if winner == sid:
                # this bet's runner wins
                if side == "LAY":
                    pnl = -matched * (odds - 1)
                else:  # BACK
                    pnl = matched * (odds - 1)
            else:
                # some other runner wins
                if side == "LAY":
                    pnl = matched
                else:
                    pnl = -matched

            market_pnl[mid][winner] += pnl

    total_liability = 0.0

    print("\n=== TRUE MARKET LIABILITY (EXCHANGE SIM) ===\n")

    for mid, outcomes in market_pnl.items():

        print(f"--- MARKET {mid} ---")

        worst_runner = None
        worst_loss = 0.0

        for runner, pnl in outcomes.items():
            print(f"Runner {runner} wins → PnL = {pnl:.2f}")

            if pnl < worst_loss:
                worst_loss = pnl
                worst_runner = runner

        print(f"Worst case runner: {worst_runner}")
        print(f"Worst case loss : {-worst_loss:.2f}\n")

        total_liability += -worst_loss

    print("--------------------------------------------")
    print(f"Total TRUE worst-case liability : {total_liability:.2f}")
    print("--------------------------------------------\n")

    return total_liability

def persist_surface(surface_rows):

    con = sqlite3.connect(autoscalp_db())
    cur = con.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS betfair_execution_surface(
            bet_id TEXT PRIMARY KEY,
            order_id INTEGER,
            engine TEXT,
            role TEXT,
            marketId TEXT,
            selectionId TEXT,
            side TEXT,
            matched_size REAL,
            avg_price REAL,
            source TEXT,
            last_seen TEXT
        )
    """)

    seen_bet_ids = set()

    # --------------------------------------------
    # 1️⃣ UPSERT CURRENT SNAPSHOT
    # --------------------------------------------
    for r in surface_rows:

        seen_bet_ids.add(r["bet_id"])

        cur.execute("""
            INSERT INTO betfair_execution_surface(
                bet_id,
                order_id,
                engine,
                role,
                marketId,
                selectionId,
                side,
                matched_size,
                avg_price,
                source,
                last_seen
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,datetime('now','utc'))
            ON CONFLICT(bet_id) DO UPDATE SET
                matched_size=excluded.matched_size,
                avg_price=excluded.avg_price,
                source=excluded.source,
                last_seen=datetime('now','utc')
        """, (
            r["bet_id"],
            r["order_id"],
            r["engine"],
            r["role"],
            r["marketId"],
            r["selectionId"],
            r["side"],
            r["matched_size"],
            r["avg_price"],
            r["source"],
        ))

    # --------------------------------------------
    # 2️⃣ DOWNGRADE STALE CURRENT ROWS
    # --------------------------------------------
    # If a bet was CURRENT before but is not seen now,
    # it must no longer be live exposure.
    #
    # We mark it CLEARED with zero exposure.
    # --------------------------------------------

    cur.execute("""
        SELECT bet_id
        FROM betfair_execution_surface
        WHERE source='CURRENT'
    """)
    existing_current = {row[0] for row in cur.fetchall()}

    stale = existing_current - seen_bet_ids

    for bet_id in stale:
        cur.execute("""
            UPDATE betfair_execution_surface
               SET source='CLEARED',
                   matched_size=0,
                   avg_price=0,
                   last_seen=datetime('now','utc')
             WHERE bet_id=?
        """, (bet_id,))

    con.commit()
    con.close()



# --------------------------------------------------
# MAIN
# --------------------------------------------------

def main():

    print("\n=== BETFAIR LIABILITY SURFACE (PHASE 1) ===\n")

    app_key = get_app_key()
    token = resolve_token()

    parents = load_today_parent_betids()

    if not parents:
        print("No parent betIds found today.")
        return


    surface_raw = build_betfair_surface(parents, app_key, token)

    if not surface_raw:
        print("No matched exposure found on Betfair.")
        return

    surface = enrich_surface_with_db(surface_raw)

    if not surface:
        print("No matched exposure found on Betfair.")
        return

    print("=== BETFAIR EXECUTION SURFACE ===\n")

    for row in surface:
        print(
            f"betId={row['bet_id']} | "
            f"order_id={row['order_id']} | "
            f"engine={row['engine']} | "
            f"role={row['role']} | "
            f"market={row['marketId']} | "
            f"runner={row['selectionId']} | "
            f"side={row['side']} | "
            f"matched={row['matched_size']:.2f} | "
            f"avg_odds={row['avg_price']:.2f} | "
            f"source={row['source']}"
        )

    # Persist full surface (CURRENT + CLEARED)
    persist_surface(surface)

    # Liability must be calculated from CURRENT only
    current_only = [r for r in surface if r["source"] == "CURRENT"]

    if not current_only:
        print("\nNo CURRENT exposure found.")
        total_liability = 0.0
    else:
        total_liability = compute_true_market_liability(current_only)


    print("\n--------------------------------------------")
    print(f"Total matched bets in surface: {len(surface)}")
    print("--------------------------------------------\n")


if __name__ == "__main__":
    main()
