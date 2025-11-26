#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# --- PATH FIX (place at the top) ---------------------------------------------
import os, sys
_THIS = os.path.abspath(__file__)
_ROOT = os.path.abspath(os.path.join(os.path.dirname(_THIS), "..", ".."))
# Try repo root and engines/ parent so "settlements.py" can be imported
for p in (_ROOT, os.path.join(_ROOT, "engines"), os.path.join(_ROOT, "engines", "live")):
    if p not in sys.path:
        sys.path.insert(0, p)
# ----------------------------------------------------------------------------- 
"""
scripts/settlement_probe.py
Standalone settlement fetcher (5-minute loop) that:
  1) fetches today's WIN markets + runners from Betfair (API),
  2) upserts into settlements.db (catalogue + book),
  3) prints the FIRST market's FIRST runner (name, ids) and a meta JSON
     listing all AUTO_DB orders (and dashboard_orders_tape rows if present)
     for that marketId/selectionId,
  4) repeats every 5 minutes.

Env:
  BETFAIR_APP_KEY, BETFAIR_SESSION must be set (same as the live router).
"""

from __future__ import annotations
import os, sys, json, time, sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

# ── reuse paths from your tree when available ─────────────────────────────────
def _autoscalp_db_path() -> str:
    try:
        import engines.config_paths as cp
        return cp.autoscalp_db()
    except Exception:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")
        os.makedirs(base, exist_ok=True)
        return os.path.join(base, "autoscalp_gui.db")

def _data_dir() -> str:
    try:
        import engines.config_paths as cp
        return getattr(cp, "DATA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data"))
    except Exception:
        return os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data")

def _settlements_db_path() -> str:
    return os.path.join(_data_dir(), "settlements.db")

AUTOSCALP_DB = _autoscalp_db_path()
SETTLE_DB    = _settlements_db_path()

# ── tiny db helpers ───────────────────────────────────────────────────────────
def _con(path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    con = sqlite3.connect(path, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA foreign_keys=ON;")
    return con

# ── import the Betfair client and schema writers from settlements.py ──────────
# (We only rely on what we introduced earlier so nothing else needs wiring.)
from settlements import (
    ensure_schema,
    settlements_db_path,                         # same as SETTLE_DB, but keep it consistent
    BetfairClient,
)

# ── lightweight helpers to list TODAY's WIN markets via API ───────────────────
def _iso_day_bounds_utc(day: datetime) -> tuple[str, str]:
    d0 = day.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    d1 = d0 + timedelta(days=1) - timedelta(seconds=1)
    return (d0.strftime("%Y-%m-%dT%H:%M:%SZ"), d1.strftime("%Y-%m-%dT%H:%M:%SZ"))

def _list_today_win_market_ids(client: BetfairClient) -> List[str]:
    """
    Call listMarketCatalogue with a FILTER (WIN markets today). We chunk results
    because the API caps responses by maxResults.
    """
    start_iso, end_iso = _iso_day_bounds_utc(datetime.now(timezone.utc))

    # API expects 'filter' object; use raw rpc to pass filter directly.
    payload = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketCatalogue",
        "params": {
            "filter": {
                "eventTypeIds": ["7"],                    # Horse Racing
                "marketTypeCodes": ["WIN"],
                "marketStartTime": {"from": start_iso, "to": end_iso}
            },
            "maxResults": 200,
            "marketProjection": ["EVENT", "MARKET_START_TIME", "RUNNER_DESCRIPTION"]
        },
        "id": 1
    }]

    import urllib.request, json as _json
    req = urllib.request.Request(
        client.API_URL,
        data=_json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type":"application/json",
            "Accept":"application/json",
            "X-Application": client.app_key,
            "X-Authentication": client.session,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        obj = _json.loads(resp.read())
    cats = (obj[0].get("result") or []) if isinstance(obj, list) and obj else []
    return [c.get("marketId") for c in cats if c.get("marketId")]

def _upsert_catalogue_and_runners(client: BetfairClient, market_ids: List[str]) -> None:
    """Use settlements.fetch_market_metadata_api pieces inline to minimise deps."""
    if not market_ids:
        return
    # Chunk in 100s to be safe
    CHUNK = 100
    with _con(SETTLE_DB) as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS bf_market_catalogue(
          marketId TEXT PRIMARY KEY,
          marketName TEXT, eventName TEXT, competition TEXT, countryCode TEXT,
          venue TEXT, marketStartTime TEXT, totalMatched REAL,
          raceType TEXT, distanceMeters REAL, going TEXT, class TEXT,
          runnersJson TEXT, raw_json TEXT
        );
        CREATE TABLE IF NOT EXISTS bf_runner_info(
          marketId TEXT, selectionId TEXT, runnerName TEXT,
          stallDraw INTEGER, jockeyName TEXT, trainerName TEXT, age INTEGER,
          weightCarried REAL, officialRating INTEGER, raw_json TEXT,
          PRIMARY KEY(marketId, selectionId)
        );
        """)
        for i in range(0, len(market_ids), CHUNK):
            mids = market_ids[i:i+CHUNK]
            cats = client.list_market_catalogue(mids)
            for md in (cats or []):
                runners = [{"selectionId": r.get("selectionId"), "runnerName": r.get("runnerName")} for r in (md.get("runners") or [])]
                venue = ((md.get("event") or {}).get("venue")) or md.get("eventName")
                con.execute("""
                INSERT INTO bf_market_catalogue(
                  marketId, marketName, eventName, competition, countryCode, venue, marketStartTime,
                  totalMatched, raceType, distanceMeters, going, class, runnersJson, raw_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(marketId) DO UPDATE SET
                  marketName=excluded.marketName,
                  eventName=excluded.eventName,
                  competition=excluded.competition,
                  countryCode=excluded.countryCode,
                  venue=excluded.venue,
                  marketStartTime=excluded.marketStartTime,
                  totalMatched=excluded.totalMatched,
                  runnersJson=excluded.runnersJson,
                  raw_json=excluded.raw_json
                """, (
                    md.get("marketId"),
                    md.get("marketName"),
                    ((md.get("event") or {}).get("name")),
                    ((md.get("competition") or {}).get("name")),
                    md.get("countryCode"),
                    venue,
                    md.get("marketStartTime"),
                    md.get("totalMatched"),
                    None, None, None, None,
                    json.dumps(runners, ensure_ascii=False),
                    json.dumps(md, ensure_ascii=False),
                ))
                for r in (md.get("runners") or []):
                    con.execute("""
                    INSERT INTO bf_runner_info(marketId, selectionId, runnerName, stallDraw, raw_json)
                    VALUES (?,?,?,?,?)
                    ON CONFLICT(marketId, selectionId) DO UPDATE SET
                      runnerName=excluded.runnerName,
                      stallDraw=excluded.stallDraw,
                      raw_json=excluded.raw_json
                    """, (
                        md.get("marketId"),
                        str(r.get("selectionId")),
                        r.get("runnerName"),
                        r.get("sortPriority"),
                        json.dumps(r, ensure_ascii=False),
                    ))
        con.commit()

def _first_market_and_runner() -> Optional[Dict[str, str]]:
    """
    Pick FIRST market of the day (by start time asc) and its FIRST runner (sort order).
    Return dict with keys: marketId, runnerName, selectionId, venue, off.
    """
    with _con(SETTLE_DB) as con:
        row = con.execute(
            "SELECT marketId, venue, marketStartTime FROM bf_market_catalogue "
            "WHERE date(substr(marketStartTime,1,19))=date('now','utc') "
            "ORDER BY datetime(marketStartTime) ASC LIMIT 1"
        ).fetchone()
        if not row:
            return None
        mid = row["marketId"]
        venue = row["venue"] or "-"
        off  = row["marketStartTime"] or ""
        # first runner = smallest stallDraw / sortPriority if present, else first in JSON
        r = con.execute(
            "SELECT selectionId, runnerName FROM bf_runner_info WHERE marketId=? "
            "ORDER BY COALESCE(stallDraw, 9999) ASC, selectionId ASC LIMIT 1", (mid,)
        ).fetchone()
        if not r:
            # fallback: parse runnersJson
            j = con.execute("SELECT runnersJson FROM bf_market_catalogue WHERE marketId=?", (mid,)).fetchone()
            if not j or not j["runnersJson"]:
                return {"marketId": mid, "venue": venue, "off": off, "selectionId": "", "runnerName": ""}
            try:
                arr = json.loads(j["runnersJson"]) or []
                first = arr[0] if arr else {}
                return {"marketId": mid, "venue": venue, "off": off,
                        "selectionId": str(first.get("selectionId","")), "runnerName": first.get("runnerName","")}
            except Exception:
                return {"marketId": mid, "venue": venue, "off": off, "selectionId": "", "runnerName": ""}
        return {"marketId": mid, "venue": venue, "off": off,
                "selectionId": str(r["selectionId"]), "runnerName": r["runnerName"]}

# === PATCH START ===
# 📍 TARGET: scripts/settlement_probe.py:_orders_meta_ids
# 🔎 SEARCH: def _orders_meta_ids(
# 📆 PATCHED: 2025-11-21

from engines.config_paths import auto_conn as _auto_conn

def _orders_meta_ids(market_id: str, selection_id: str) -> Dict[str, List[int]]:
    """
    Find all matching row ids for this runner today from orders (and dashboard_orders_tape if present).
    Uses DAL-safe AUTO DB connector (read-only).
    """
    meta: Dict[str, List[int]] = {"orders": [], "orders_tape": []}

    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    try:
        # Detect opened_at or ts column
        cols = {r["name"] for r in con.execute("PRAGMA table_info(orders)")}
        date_col = "opened_at" if "opened_at" in cols else ("ts" if "ts" in cols else None)

        if date_col:
            for r in con.execute(
                f"""
                SELECT id FROM orders
                 WHERE marketId=? AND selectionId=?
                   AND date(COALESCE({date_col}, datetime('now','utc'))) = date('now','utc')
                """,
                (market_id, selection_id)
            ).fetchall():
                meta["orders"].append(int(r["id"]))

        # dashboard_orders_tape
        ok = con.execute("""
            SELECT 1
              FROM sqlite_master
             WHERE type='table' AND name='dashboard_orders_tape'
        """).fetchone()

        if ok:
            for r in con.execute("""
                SELECT id
                  FROM dashboard_orders_tape
                 WHERE marketId=? AND selectionId=?
                   AND date(ts) = date('now','utc')
            """, (market_id, selection_id)).fetchall():
                meta["orders_tape"].append(int(r["id"]))

    finally:
        con.close()

    return meta
# === PATCH END ===


def _print_probe() -> None:
    """
    End-to-end probe: prints the first market/runner and the meta JSON of order ids.
    """
    first = _first_market_and_runner()
    if not first:
        print("[settlement-probe] no markets found for today.")
        return
    mid = first["marketId"]; sid = first["selectionId"]; nm = first["runnerName"]
    meta = _orders_meta_ids(mid, sid) if sid else {"orders": [], "orders_tape": []}
    print(f"[settlement] market={mid} venue={first['venue']} off={first['off']}")
    print(f"[settlement] runner={nm} sid={sid}")
    print(f"[settlement] meta={json.dumps(meta, separators=(',',':'))}")

def main_loop(period_sec: int = 300) -> None:
    """
    5-minute loop: refresh markets and print the probe line each cycle.
    """
    if not (os.environ.get("BETFAIR_APP_KEY") and os.environ.get("BETFAIR_SESSION")):
        print("ERROR: BETFAIR_APP_KEY / BETFAIR_SESSION not set", file=sys.stderr)
        sys.exit(2)

    ensure_schema()  # settlements.db

    client = BetfairClient()  # will raise if creds missing
    print("[settlement-probe] starting… (CTRL-C to stop)")
    while True:
        try:
            # 1) get today's WIN market ids via API
            mids = _list_today_win_market_ids(client)
            # 2) upsert catalogue + runners locally
            _upsert_catalogue_and_runners(client, mids)
            # 3) print the first market’s first runner with meta ids
            _print_probe()
        except KeyboardInterrupt:
            raise
        except Exception as e:
            print(f"[settlement-probe] warn: {e}", file=sys.stderr)
        time.sleep(max(30, int(period_sec)))

# === PATCH START: inspector ===
# === PATCH START ===
# 📍 TARGET: scripts/settlement_probe.py (module-level inspector block)
# 🔎 SEARCH: if __name__ == "__main__":
# 📆 PATCHED: 2025-11-21
if __name__ == "__main__":
    import argparse
    from datetime import datetime, timezone
    from engines.config_paths import auto_conn as _auto_conn

    ap = argparse.ArgumentParser("settlement_probe")
    ap.add_argument("--day", help="YYYY-MM-DD UTC (filter opened_at)")
    args = ap.parse_args()
    day = (args.day or datetime.now(timezone.utc).strftime("%Y-%m-%d"))

    # DAL-safe AUTO DB readonly connection
    con = _auto_conn(rw=False)
    con.row_factory = sqlite3.Row

    q = """
        SELECT entry_status, exit_status, COUNT(*) AS n
          FROM orders
         WHERE date(opened_at)=?
         GROUP BY entry_status, exit_status
    """

    for r in con.execute(q, (day,)).fetchall():
        print(f"{day} entry={r['entry_status'] or ''} "
              f"exit={r['exit_status'] or ''} n={r['n']}")

    con.close()

    try:
        # default = 5 minutes; override with SETTLE_PERIOD_SEC if you want
        per = int(os.environ.get("SETTLE_PERIOD_SEC", "300"))
        main_loop(period_sec=per)
    except KeyboardInterrupt:
        print("\n[settlement-probe] stopped.")
        sys.exit(130)
# === PATCH END ===

