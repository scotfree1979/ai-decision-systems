#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engines/betfair_form_book.py
────────────────────────────
Fetch extended runner metadata (trainer, jockey, age, weight, rating)
directly from Betfair's Exchange API and store it into bf_runner_info.
"""

import os, sys, requests, json, sqlite3

# --- repo-root shim ---------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, "..", ".."))  # → analytics_beta_dev
if _root not in sys.path:
    sys.path.insert(0, _root)

try:
    from engines.config_paths import settlements_db
except ModuleNotFoundError:
    # fallback for direct execution
    sys.path.insert(0, os.path.dirname(_root))
    from engines.config_paths import settlements_db
# ----------------------------------------------------------------------



def main():
    print("=== Betfair Form Book Enricher ===")
    market_id = input("📦 Enter Betfair marketId: ").strip()
    app_key = input("🔐 Enter your Betfair APP_KEY: ").strip()
    session = input("🔑 Enter your Betfair SESSION TOKEN: ").strip()

    url = "https://api.betfair.com/exchange/betting/rest/v1.0/listMarketCatalogue/"
    headers = {
        "X-Application": app_key,
        "X-Authentication": session,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    payload = {
        "filter": {"marketIds": [market_id]},
        "maxResults": 100,
        "marketProjection": [
            "EVENT",
            "RUNNER_DESCRIPTION",
            "MARKET_DESCRIPTION",
            "COMPETITION"
        ],
    }

    print(f"[probe] Fetching Betfair runner metadata for {market_id} …")
    resp = requests.post(url, headers=headers, data=json.dumps(payload), timeout=20)

    if resp.status_code != 200:
        print(f"[probe] ❌ HTTP {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)

    try:
        data = resp.json()
    except Exception as e:
        print(f"[probe] ❌ JSON decode error: {e}")
        print(resp.text[:400])
        sys.exit(1)

    if not data:
        print("[probe] ❌ No data returned — check credentials or marketId.")
        sys.exit(1)

    cat = data[0]
    runners = cat.get("runners", [])
    print(f"[probe] got {len(runners)} runners from Betfair.")

    con = sqlite3.connect(settlements_db())
    con.row_factory = sqlite3.Row
    updated = 0

    for r in runners:
        sid = str(r.get("selectionId"))
        name = r.get("runnerName")
        meta = r.get("metadata") or {}

        trainer = meta.get("TRAINER_NAME") or ""
        jockey = meta.get("JOCKEY_NAME") or ""
        age = meta.get("AGE")
        weight = meta.get("WEIGHT_VALUE")
        rating = meta.get("OFFICIAL_RATING")

        print(f"  {sid:>10} | {name:<25} | Jockey={jockey or '?':<20} | "
              f"Trainer={trainer or '?':<20} | Age={age or '-'} | Wt={weight or '-'} | OR={rating or '-'}")

        if any([trainer, jockey, rating]):
            con.execute("""
                UPDATE bf_runner_info
                   SET trainerName=?, jockeyName=?, age=?, weightCarried=?, officialRating=?, raw_json=COALESCE(raw_json,'{}')
                 WHERE selectionId=? AND marketId=?
            """, (trainer, jockey, age, weight, rating, sid, market_id))
            updated += 1

    con.commit()
    con.close()
    print(f"[probe] ✅ updated {updated} rows in bf_runner_info")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[probe] Aborted by user.")
