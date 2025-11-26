#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engines/racingpost_form_book.py
──────────────────────────────────────────────────────────
Scrape today's Racing Post racecards for trainer, jockey,
weight and official rating (OR) and enrich bf_runner_info.

Usage:
    python3 engines/racingpost_form_book.py
"""

import os, sys, re, sqlite3, datetime, requests, bs4

# --- repo-root shim ----------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)
# -----------------------------------------------------------------------------
from engines.config_paths import settlements_db
# -----------------------------------------------------------------------------


def _today_str() -> str:
    return datetime.date.today().isoformat()


def _fetch_today_cards_json(date_str: str) -> list[dict]:
    """Pull today's meetings/races via Racing Post GraphQL endpoint."""
    url = "https://www.racingpost.com/graphql"
    payload = {
        "operationName": "raceCardsByDate",
        "variables": {"date": date_str},
        "query": (
            "query raceCardsByDate($date:String!){"
            " raceCardsByDate(date:$date){meetingId meetingName "
            " races{raceId raceTitle runners{name trainerName jockeyName weight officialRating}}}}"
        )
    }
    print(f"[racingpost] fetching GraphQL cards → {url}")
    r = requests.post(url, json=payload, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    data = r.json()
    cards = (data.get("data") or {}).get("raceCardsByDate") or []
    return cards



def _parse_racecards(cards: list[dict]) -> list[dict]:
    """Flatten raceCards JSON → list of runner dicts."""
    runners = []
    for meeting in cards:
        for race in meeting.get("races", []):
            for runner in race.get("runners", []):
                runners.append({
                    "runnerName": runner.get("name", "?"),
                    "trainerName": runner.get("trainer", {}).get("name", "?"),
                    "jockeyName": runner.get("jockey", {}).get("name", "?"),
                    "weightCarried": runner.get("weight", "?"),
                    "officialRating": runner.get("officialRating", "?"),
                })
    print(f"[racingpost] parsed {len(runners)} runners total")
    return runners


def _update_runner_info(runners: list[dict]) -> int:
    """Upsert parsed data into bf_runner_info."""
    if not runners:
        return 0
    con = sqlite3.connect(settlements_db()); con.row_factory = sqlite3.Row
    con.execute("""
        CREATE TABLE IF NOT EXISTS bf_runner_info(
            marketId TEXT,
            selectionId TEXT,
            runnerName TEXT,
            trainerName TEXT,
            jockeyName TEXT,
            weightCarried TEXT,
            officialRating TEXT,
            PRIMARY KEY(marketId, selectionId)
        )
    """)
    updated = 0
    for r in runners:
        # We don't know marketId here, so we match by runnerName (later join)
        con.execute("""
            INSERT INTO bf_runner_info(marketId, selectionId, runnerName,
                                       trainerName, jockeyName,
                                       weightCarried, officialRating)
            VALUES(NULL, NULL, ?, ?, ?, ?, ?)
            ON CONFLICT(marketId, selectionId) DO UPDATE SET
              trainerName=excluded.trainerName,
              jockeyName=excluded.jockeyName,
              weightCarried=excluded.weightCarried,
              officialRating=excluded.officialRating
        """, (r["runnerName"], r["trainerName"], r["jockeyName"],
              r["weightCarried"], r["officialRating"]))
        updated += 1
    con.commit(); con.close()
    return updated


def main():
    print("=== Racing Post Form-Book Enricher ===")
  

    today = datetime.date.today().isoformat()
    cards = _fetch_today_cards_json(today)
    runners = _parse_racecards(cards)
    n = _update_runner_info(runners)
    print(f"[racingpost] ✅ updated {n} runner rows in bf_runner_info")

    for u in urls:
        print(f"[racingpost] parsing {u}")
        runners = _parse_race(u)
        if runners:
            n = _update_runner_info(runners)
            print(f"  ↳ stored {n} runners")
            total += n
    print(f"[racingpost] ✅ updated {total} runner rows in bf_runner_info")


if __name__ == "__main__":
    main()
