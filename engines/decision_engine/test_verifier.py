#!/usr/bin/env python3
"""
TEST Verifier — compressed full-day simulation (OC0..OC20 + bands), events & verdict.

- Writes to the DBs currently configured in engines.config_paths (TEST DBs after GUI Step 1).
- Seeds 10 markets (default), 8–12 runners each.
- Emits OC1..OC20 bands quickly (accelerated minutes).
- Produces decisions/orders via the running DecisionEngine.
- Prints PASS/FAIL to events with detailed metrics.
"""

from __future__ import annotations
import os, sqlite3, random, json, time
from typing import Optional, Iterable, Tuple
from datetime import timedelta

from engines.config_paths import BETS_DB_PATH, AUTOSCALP_DB_PATH
from engines.utils.time_utils import now_utc

# -------------- small SQL helpers --------------
def _con(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=10, isolation_level=None)
    con.row_factory = sqlite3.Row
    return con

def _exec(path: str, sql: str, params: Iterable | None = None):
    with _con(path) as con: con.execute(sql, params or [])

def _fetchall(path: str, sql: str, params: Iterable | None = None) -> list[sqlite3.Row]:
    with _con(path) as con: return con.execute(sql, params or []).fetchall()

def _log_event(msg: str, level: str = "INFO", run_id: Optional[int] = None, source: str = "TEST"):
    ts = now_utc().isoformat()
    _exec(AUTOSCALP_DB_PATH, "INSERT INTO events(run_id, ts, level, source, message) VALUES (?,?,?,?,?)",
          [run_id, ts, level, source, msg])

# -------------- scenario generation --------------
def _mk_market_id(i: int) -> str: return f"1.TEST.MKT.{i:03d}"

_TRACKS = ["ASC","HAY","DON","KEM","EPS","NEW","NCS","YOR","LIN","WOL","CHE","SAN"]
_DISTS  = ["5F","6F","7F","1M","1M2F","1M4F","2M","2M4F","NHF","HUR","CHS"]

def _mk_runner_name(i: int, j: int) -> str: return f"Runner {i:03d}-{j:02d}"

def _create_markets(markets: int, runners_per_market: Tuple[int,int], start_in_minutes: float) -> list[dict]:
    out = []
    base = now_utc()
    for i in range(1, markets+1):
        mid = _mk_market_id(i)
        start = (base + timedelta(minutes=start_in_minutes + i * 0.2)).isoformat()
        runners = random.randint(runners_per_market[0], runners_per_market[1])
        out.append({
            "marketId": mid,
            "marketStartTime": start,
            "event_name": random.choice(_TRACKS),
            "distance": random.choice(_DISTS),
            "runners": [{"selectionId": 100000 + i*100 + j, "runnerName": _mk_runner_name(i,j)} for j in range(1, runners+1)]
        })
    return out

def _seed_bets_and_inbound(markets: list[dict]):
    # bets (minimal columns used by dashboard/engine)
    with _con(BETS_DB_PATH) as con:
        for m in markets:
            mid, start = m["marketId"], m["marketStartTime"]
            for r in m["runners"]:
                sid, name = r["selectionId"], r["runnerName"]
                con.execute(
                    "INSERT INTO bets(marketId, selectionId, horse_name, marketStartTime, timestamp) VALUES (?,?,?,?,?)",
                    [mid, sid, name, start, now_utc().isoformat()]
                )
    # inbound_bets_min
    with _con(AUTOSCALP_DB_PATH) as con:
        for m in markets:
            mid, start = m["marketId"], m["marketStartTime"]
            for r in m["runners"]:
                sid, name = r["selectionId"], r["runnerName"]
                con.execute(
                    "INSERT INTO inbound_bets_min(marketId, selectionId, placed_at, anchor_odd, horse_name, meta_json) "
                    "VALUES (?,?,?,?,?,?)",
                    [mid, sid, None, None, name, json.dumps({"src":"TEST"})]
                )

def _set_anchor(mid: str, sid: int, odd: float):
    # cache + oc_series OC0
    ts = now_utc().isoformat()
    with _con(AUTOSCALP_DB_PATH) as con:
        con.execute(
            "INSERT INTO inbound_oc_cache(marketId, selectionId, anchor_odd, last_sync_ts) "
            "VALUES (?,?,?,?) "
            "ON CONFLICT(marketId, selectionId) DO UPDATE SET anchor_odd=excluded.anchor_odd, last_sync_ts=excluded.last_sync_ts",
            [mid, sid, odd, ts]
        )
    with _con(BETS_DB_PATH) as con:
        con.execute(
            "UPDATE bets SET anchor_odd=COALESCE(anchor_odd, ?), timestamp=? WHERE marketId=? AND selectionId=?",
            [odd, ts, mid, sid]
        )

def _write_oc(mid: str, sid: int, n: int, value: float, band: list[float]):
    ts = now_utc().isoformat()
    key_v = f"oc{n}"; key_b = f"oc{n}_band_json"
    with _con(AUTOSCALP_DB_PATH) as con:
        con.execute(
            f"INSERT INTO inbound_oc_cache(marketId, selectionId, {key_v}, {key_b}, last_sync_ts) "
            f"VALUES (?,?,?,?,?) "
            f"ON CONFLICT(marketId, selectionId) DO UPDATE SET {key_v}=excluded.{key_v}, {key_b}=excluded.{key_b}, last_sync_ts=excluded.last_sync_ts",
            [mid, sid, value, json.dumps(band), ts]
        )

# band constructors: drift, steam, flat/noisy
def _band_series(anchor: float, npoints: int, pattern: str) -> list[float]:
    out = []
    cur = float(anchor)
    for k in range(npoints):
        if pattern == "drift":
            cur += random.uniform(0.01, 0.06)
        elif pattern == "steam":
            cur -= random.uniform(0.01, 0.06)
        else:  # flat/noise
            cur += random.uniform(-0.03, 0.03)
        cur = max(1.01, round(cur, 2))
        out.append(cur)
    return out

def run_test_scenario(*, markets: int = 10, runners_per_market: Tuple[int,int] = (8,12),
                      accel: float = 30.0, minutes_span: float = 8.0) -> None:
    """
    accel: how many 'race minutes' per 1s wall time (30.0 → 1m = 2s)
    minutes_span: total test 'clock' minutes to cover OC1..OC20 emission pace
    """
    # 1) New TEST run (DecisionEngine will also open its own run row)
    run_rows = _fetchall(AUTOSCALP_DB_PATH, "SELECT id FROM runs WHERE mode='TEST' ORDER BY id DESC LIMIT 1")
    run_id = run_rows[0]["id"] if run_rows else None
    _log_event("TEST start — seeding markets/runners", run_id=run_id)

    # 2) Create compressed markets & runners
    scenario = _create_markets(markets, runners_per_market, start_in_minutes=5.0)
    _seed_bets_and_inbound(scenario)

    # 3) Seed anchors & OC bands on accelerated cadence
    total_steps = 20
    points_per_band = 6
    patterns = ["drift","steam","flat"]
    # assign a market pattern, then per runner add slight variance
    for i, m in enumerate(scenario, 1):
        mpat = patterns[(i-1) % len(patterns)]
        for r in m["runners"]:
            sid = r["selectionId"]
            base = round(random.uniform(3.0, 9.0), 2)
            _set_anchor(m["marketId"], sid, base)
            # gradually reveal OC1..OC20
    _log_event("Anchors seeded", run_id=run_id)

    # accelerated emission
    wall_total_seconds = max(10, int((minutes_span / accel) * 60))
    emit_interval = max(1, wall_total_seconds // total_steps)

    t0 = time.time()
    for step in range(1, total_steps + 1):
        # for each market/runner emit OCn (+grow band list)
        for i, m in enumerate(scenario, 1):
            mpat = patterns[(i-1) % len(patterns)]
            for r in m["runners"]:
                sid = r["selectionId"]
                # deterministic yet varied bands
                anchor_row = _fetchall(AUTOSCALP_DB_PATH,
                    "SELECT anchor_odd FROM inbound_oc_cache WHERE marketId=? AND selectionId=? LIMIT 1",
                    [m["marketId"], sid])
                anchor = float(anchor_row[0]["anchor_odd"]) if anchor_row and anchor_row[0]["anchor_odd"] is not None else 5.0
                band = _band_series(anchor, points_per_band, mpat)
                val = band[-1]
                _write_oc(m["marketId"], sid, step, val, band)

        # small sleep to let the engine tick & GUI poll
        # (compressed: emit_interval seconds per OC window)
        time.sleep(emit_interval)

    _log_event("OC1..OC20 emission complete", run_id=run_id)

    # 4) Score: decisions/orders/chapters coverage
    dec_cnt = _fetchall(AUTOSCALP_DB_PATH, "SELECT COUNT(*) AS c FROM decisions")[0]["c"]
    ord_cnt = _fetchall(AUTOSCALP_DB_PATH, "SELECT COUNT(*) AS c FROM orders WHERE mode='LEARNING'")[0]["c"]
    chap_cnt = _fetchall(AUTOSCALP_DB_PATH, "SELECT COUNT(*) AS c FROM chapters")[0]["c"]
    oc1_ready = _fetchall(AUTOSCALP_DB_PATH, "SELECT SUM(json_array_length(oc1_band_json) >= 4) AS c FROM inbound_oc_cache")[0]["c"] or 0

    # thresholds
    pass_flags = []
    pass_flags.append(("OC bands ready", oc1_ready >= markets * 6))  # a healthy proportion
    pass_flags.append(("Decisions",      dec_cnt >= 10))
    pass_flags.append(("Orders",         ord_cnt >= 3))
    pass_flags.append(("Chapters",       chap_cnt >= markets * 5))

    # 5) Verdict
    failures = [name for (name, ok) in pass_flags if not ok]
    if failures:
        _log_event(f"TEST FAIL — {', '.join(failures)}", level="ERROR", run_id=run_id)
    else:
        _log_event("PHASE 1: READY FOR LEARNING", level="INFO", run_id=run_id)
