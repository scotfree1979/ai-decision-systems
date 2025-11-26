#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engines/racingpost_results_enricher.py
──────────────────────────────────────────────
Scrape yesterday's Racing Post **results** pages to
extract trainer, jockey, weight, OR and update bf_runner_info.

Usage:
    export PYTHONPATH=/Users/malachikelly/Dev/analytics_beta_dev
    python3 engines/racingpost_results_enricher.py
"""

import os, sys, re, sqlite3, datetime, requests
from bs4 import BeautifulSoup

# --- repo root shim ----------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)
# -----------------------------------------------------------------------------
from engines.config_paths import settlements_db
# -----------------------------------------------------------------------------


def _yesterday_str() -> str:
    return (datetime.date.today() - datetime.timedelta(days=1)).isoformat()


def _fetch_result_urls(day: str) -> list[str]:
    """Collect all meeting result URLs for a given day."""
    base = f"https://www.racingpost.com/results/{day.replace('-', '/')}"
    print(f"[rpost] scanning {base}")
    r = requests.get(base, headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code != 200:
        print(f"[rpost] warn: got {r.status_code}")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if re.search(r"/results/\d+/\d+", h):
            links.append("https://www.racingpost.com" + h.split("#")[0])
    return sorted(set(links))


def _parse_result(url: str) -> list[dict]:
    """Parse one race result page → list of runners with trainer/jockey."""
    r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"})
    if r.status_code != 200:
        print(f"[rpost] warn: {r.status_code} for {url}")
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    table = soup.find("table")
    if not table:
        return []
    runners = []
    for tr in table.find_all("tr"):
        cols = [td.get_text(" ", strip=True) for td in tr.find_all("td")]
        if len(cols) < 6:
            continue
        runner = cols[1]
        jockey = cols[2] if len(cols) > 2 else "?"
        trainer = cols[3] if len(cols) > 3 else "?"
        weight = cols[4] if len(cols) > 4 else "?"
        orating = re.sub(r"[^\d]", "", cols[5]) if len(cols) > 5 else ""
        if runner:
            runners.append({
                "runnerName": runner,
                "trainerName": trainer,
                "jockeyName": jockey,
                "weightCarried": weight,
                "officialRating": orating or None
            })
    print(f"[rpost] parsed {len(runners):>2} runners from {url}")
    return runners


def _update_runner_info(runners: list[dict]) -> int:
    """Upsert runners into bf_runner_info by name."""
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
        con.execute("""
            INSERT INTO bf_runner_info(marketId, selectionId, runnerName,
                                       trainerName, jockeyName,
                                       weightCarried, officialRating)
            VALUES(NULL,NULL,?,?,?, ?, ?)
            ON CONFLICT(marketId,selectionId) DO UPDATE SET
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
    print("=== Racing Post Results Enricher ===")
    day = _yesterday_str()
    urls = _fetch_result_urls(day)
    if not urls:
        print(f"[rpost] no result URLs for {day}")
        return
    all_runners = []
    for u in urls[:20]:   # cap for speed; remove slice for full day
        all_runners.extend(_parse_result(u))
    n = _update_runner_info(all_runners)
    print(f"[rpost] ✅ updated {n} runner rows in bf_runner_info")


if __name__ == "__main__":
    main()
