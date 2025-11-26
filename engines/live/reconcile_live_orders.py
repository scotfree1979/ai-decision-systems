#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reconcile_live_orders.py — stand-alone Betfair reconciliation helper
--------------------------------------------------------------------
Checks all CHILD orders marked "live" in autoscalp_gui.db,
queries Betfair listCurrentOrders, and updates any that are
now fully matched to entry_status='MATCHED'.

Run manually:
    python3 -m engines.live.reconcile_live_orders
"""

import sys, os, json, sqlite3, requests
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
from engines.config_paths import autoscalp_db

BETFAIR_APP_KEY = "CZHojduNWa3kxWIn"
URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

def summarize(con: sqlite3.Connection) -> None:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT entry_status, COUNT(*) AS n "
        "FROM orders WHERE role='CHILD' GROUP BY entry_status ORDER BY entry_status;"
    ).fetchall()
    print("\n[SUMMARY] CHILD order counts:")
    for r in rows:
        print(f"  {r['entry_status']:<12s} {r['n']:>6d}")
    print("")


# === PATCH START ===
# 📍 TARGET: engines/live/reconcile_live_orders.py:main
# 🔎 SEARCH: def main():
# 📆 PATCHED: 2025-11-21

from engines.config_paths import auto_conn

def main():
    print("🔐 Enter your Betfair session token:")
    session_token = input("> ").strip()
    if not session_token:
        print("❌ Session token required.")
        sys.exit(1)

    headers = {
        "X-Application": BETFAIR_APP_KEY,
        "X-Authentication": session_token,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # 1️⃣ BEFORE SUMMARY — DAL read-only
    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    summarize(con)
    con.close()

    # 2️⃣ Collect live child bet IDs — DAL read-only
    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    rows = con.execute("""
        SELECT entry_bet_id
          FROM orders
         WHERE role='CHILD'
           AND entry_status='live'
           AND entry_bet_id IS NOT NULL;
    """).fetchall()
    con.close()

    bet_ids = [str(r[0]) for r in rows if r[0]]
    print(f"[RECONCILE] Checking {len(bet_ids)} live child orders...\n")
    if not bet_ids:
        print("[RECONCILE] Nothing to reconcile.")
        return

    # 3️⃣ Query Betfair -------------------------------------------------------
    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listCurrentOrders",
        "params": {"betIds": bet_ids},
        "id": 1
    }])

    try:
        resp = requests.post(URL, headers=headers, data=payload, timeout=10)
        resp.raise_for_status()
        data = resp.json()[0].get("result", {}).get("currentOrders", [])
    except Exception as e:
        print(f"❌ Betfair API call failed: {e}")
        sys.exit(1)

    print(f"[RECONCILE] Received {len(data)} orders from Betfair\n")

    # 4️⃣ Update DB — MUST be DAL writer
    con = auto_conn(rw=True)
    cur = con.cursor()
    n_upd = 0

    for o in data:
        try:
            betid = str(o.get("betId"))
            status = str(o.get("status"))
            size_rem = float(o.get("sizeRemaining", 1))
            size_match = float(o.get("sizeMatched", 0))
            if status == "EXECUTION_COMPLETE" or size_rem == 0 or (size_match > 0 and size_rem == 0):
                cur.execute("""
                    UPDATE orders
                       SET entry_status='MATCHED'
                     WHERE entry_bet_id=?
                """, (betid,))
                n_upd += 1
        except Exception:
            continue

    con.commit()
    con.close()
    print(f"[RECONCILE] Updated {n_upd} orders → MATCHED")

    # 5️⃣ AFTER SUMMARY — DAL read-only
    con = auto_conn(rw=False)
    con.row_factory = sqlite3.Row
    summarize(con)
    con.close()
    print("[DONE] Reconciliation complete.\n")

# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/live/reconcile_live_orders.py (end of file)
# 📆 PATCHED: 2025-10-30Z — integrate winner reconciliation + form book update
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
try:
    from engines.form.form_reconcile_betfair import fetch_official_winners
    from engines.form.form_book_builder import build_runner_form
    print("\n[form] Updating winner form book …")
    winners = fetch_official_winners(7)
    if winners:
        build_runner_form()
    print(f"[form] ✅ updated runner_form with {winners} official winners.")
except Exception as e:
    print(f"[form] warn: form book update failed → {e}")
# === PATCH END ===


if __name__ == "__main__":
    main()
