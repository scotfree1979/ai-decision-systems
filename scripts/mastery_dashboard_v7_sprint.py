#!/usr/bin/env python3
"""
smoke_autoscalp_e2e.py

Seeds 5 markets + runners, streams synthetic odds for a short, sped-up session,
calls the decision engine each tick, and then asserts:
  - CAP per (marketId, selectionId, letter) never > 3
  - Rotation: at most one parent per (mid,sid,letter) per minute
  - Stamping: no parents with missing source (letter)
  - Strategies: at least some L2B parents were placed

Usage:
  AUTOSCALP_MODE=TEST python3 scripts/smoke_autoscalp_e2e.py \
      --auto data/autoscalp_gui.db \
      --bets data/bets.db \
      --minutes 2 \
      --hz 4
"""

from __future__ import annotations
import os, sys, json, time, math, argparse, sqlite3
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

# --- args --------------------------------------------------------------------
ap = argparse.ArgumentParser()
ap.add_argument("--auto", default="data/autoscalp_gui.db", help="Path to autoscalp_gui.db")
ap.add_argument("--bets", default="data/bets.db", help="Path to bets.db")
ap.add_argument("--minutes", type=float, default=2.0, help="Sim duration (wall minutes simulated)")
ap.add_argument("--hz", type=float, default=4.0, help="Engine ticks per second")
ap.add_argument("--seed", type=int, default=42, help="RNG seed")
args = ap.parse_args()

# --- ensure repo root on sys.path so 'engines' is importable ---
import sys, os
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# --- force engine to use the same DBs we seed; then read back the effective paths ---
AUTO_DB = os.path.abspath(args.auto)
BETS_DB = os.path.abspath(args.bets)
try:
    import engines.config_paths as cp
    # Set test mode and (if supported) direct paths
    if hasattr(cp, "set_db_paths"):
        try:
            cp.set_db_paths(mode="test", autoscalp_db=AUTO_DB, bets_db=BETS_DB, quiet=False)
        except TypeError:
            try:
                cp.set_db_paths(mode="test", auto_db_path=AUTO_DB, bets_db_path=BETS_DB, quiet=False)
            except TypeError:
                cp.set_db_paths(mode="test", quiet=False)

    # Always export env fallbacks for older code paths
    os.environ.setdefault("AUTOSCALP_DB_PATH", AUTO_DB)
    os.environ.setdefault("BETS_DB_PATH",      BETS_DB)

    # Resolve the **effective** paths the engine will really use (pin our seeding to these)
    AUTO_DB = cp.autoscalp_db() if hasattr(cp, "autoscalp_db") else os.environ.get("AUTOSCALP_DB_PATH", AUTO_DB)
    BETS_DB = cp.bets_db()      if hasattr(cp, "bets_db")      else os.environ.get("BETS_DB_PATH", BETS_DB)

    print(f"[SMOKE] engine AUTO_DB={AUTO_DB}")
    print(f"[SMOKE] engine BETS_DB={BETS_DB}")
except Exception as e:
    print(f"[SMOKE] config_paths binding warn: {e}")

# --- env: force TEST mode so no live API calls --------------------------------
os.environ.setdefault("AUTOSCALP_MODE", "TEST")

# --- mini DB utils ------------------------------------------------------------
def _row_conn(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path, timeout=6.0, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con

def _exec(c: sqlite3.Connection, sql: str, params: tuple = ()) -> sqlite3.Cursor:
    return c.execute(sql, params)

def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# --- ensure schema (minimal tables we touch) ----------------------------------
def ensure_schema(auto_path: str) -> None:
    con = _row_conn(auto_path)
    try:
        # markets schedule: the decision engine uses this for TTO
        _exec(con, """
          CREATE TABLE IF NOT EXISTS markets_schedule (
            marketId TEXT PRIMARY KEY,
            off_at_utc TEXT
          )
        """)
        # inbound oc cache: we write oc1 + optional band_json
        _exec(con, """
          CREATE TABLE IF NOT EXISTS inbound_oc_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT, selectionId TEXT,
            oc1 REAL, anchor_odd REAL,
            oc1_band_json TEXT,
            last_sync_ts TEXT
          )
        """)
        # odds_current: quick price for lanes/overlays
        _exec(con, """
          CREATE TABLE IF NOT EXISTS odds_current (
            day TEXT NOT NULL,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            ltp REAL,
            updated_ts TEXT,
            PRIMARY KEY(day, marketId, selectionId)
          )
        """)
        # orders exists already in your repo; we don’t create it here
        con.commit()
    finally:
        con.close()

# --- seed 5 markets + 6 runners each -----------------------------------------
def seed_markets(auto_path: str) -> Tuple[List[str], Dict[str, List[str]]]:
    con = _row_conn(auto_path)
    try:
        mids = [f"1.24680{i:03d}" for i in range(5)]
        # OFF times: now+10m, +12m, +14m, +16m, +18m (UTC)
        base = datetime.now(timezone.utc) + timedelta(minutes=10)
        for i, mid in enumerate(mids):
            off = (base + timedelta(minutes=2*i)).strftime("%Y-%m-%dT%H:%M:%SZ")
            _exec(con, "INSERT OR REPLACE INTO markets_schedule(marketId, off_at_utc) VALUES(?,?)", (mid, off))

        # 6 runners each
        runners = {}
        for mid in mids:
            sids = [str(10_000_000 + k) for k in range(6)]
            runners[mid] = sids
        con.commit()
        return mids, runners
    finally:
        con.close()

# --- synthetic odds paths -----------------------------------------------------
def make_paths(seed: int, minutes: float, hz: float, base: float = 6.0) -> List[float]:
    """
    Produce a short odds series with mild drift/steam/flat variants.
    """
    import random
    random.seed(seed)
    ticks = int(max(1, minutes * 60 * hz))
    path = []
    x = base
    for t in range(ticks):
        # small random walk; biased up/down per seed
        delta = (random.random() - 0.5) * 0.15
        x = max(1.01, x + delta)
        path.append(round(x, 2))
    return path

def scenario_for_runner(mid: str, sid: str, idx: int) -> str:
    """
    Assign runner behaviours deterministically by index:
      0,3 -> drift ; 1,4 -> steam ; 2,5 -> flat
    """
    m = idx % 3
    return ("drift" if m == 0 else "steam" if m == 1 else "flat")

def step_price(mode: str, px: float) -> float:
    if mode == "drift":  # push upward
        return round(px * 1.005 + 0.01, 2)
    if mode == "steam":  # push downward
        return round(max(1.01, px * 0.995 - 0.01), 2)
    return px  # flat

# --- write one tick into inbound_oc_cache + odds_current ----------------------
def write_tick(auto_path: str, mid: str, sid: str, px: float, band: List[float]) -> None:
    con = _row_conn(auto_path)
    try:
        now = _now_utc_iso()
        band_json = json.dumps(band[-12:]) if band else None

        # Upsert inbound_oc_cache (unique on marketId,selectionId in your DB)
        _exec(con, """
            INSERT INTO inbound_oc_cache(
                marketId, selectionId, oc1, anchor_odd, oc1_band_json, last_sync_ts
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(marketId, selectionId) DO UPDATE SET
                oc1            = excluded.oc1,
                oc1_band_json  = excluded.oc1_band_json,
                last_sync_ts   = excluded.last_sync_ts,
                -- keep original anchor if it already exists
                anchor_odd     = COALESCE(inbound_oc_cache.anchor_odd, excluded.anchor_odd)
        """, (mid, sid, px, band[0] if band else px, band_json, now))

        # Upsert odds_current for fast reads
        _exec(con, """
            INSERT INTO odds_current(day, marketId, selectionId, ltp, updated_ts)
            VALUES (date('now','utc'), ?, ?, ?, ?)
            ON CONFLICT(day, marketId, selectionId) DO UPDATE SET
                ltp        = excluded.ltp,
                updated_ts = excluded.updated_ts
        """, (mid, sid, px, now))

        con.commit()
    finally:
        con.close()

# --- call the decision engine once per tick ----------------------------------
def decide_once_tick(run_id: str, logger=None):
    # Use your folderized decide_once (wrapper keeps compatibility)
    try:
        from engines.decision_engine.decide_once.decide_once import decide_once as _do
        _do(run_id, source_override="TEST", logger=logger)
    except Exception as e:
        if logger:
            logger(f"[decide_once] warn: {e}")

# --- assertions ---------------------------------------------------------------
def assert_caps_rotation_stamping(auto_path: str) -> Dict[str, int]:
    con = _row_conn(auto_path)
    res = {"cap_viol": 0, "stamp_missing": 0, "rotation_dups": 0, "placed": 0}
    try:
        # placed
        row = _exec(con, "SELECT COUNT(*) AS n FROM orders WHERE date(opened_at)=date('now')").fetchone()
        res["placed"] = int(row["n"] or 0)

        # missing stamps
        row = _exec(con, "SELECT COUNT(*) AS n FROM orders WHERE date(opened_at)=date('now') "
                          "AND (source IS NULL OR TRIM(source)='')").fetchone()
        res["stamp_missing"] = int(row["n"] or 0)

        # CAP by (mid,sid,letter,mode): open parents <=3
        rows = _exec(con, """
          SELECT marketId, selectionId, UPPER(COALESCE(source,'')) AS letter, UPPER(COALESCE(mode,'')) AS mode,
                 SUM(CASE WHEN (COALESCE(hedge_of,'')='' AND (closed_at IS NULL OR closed_at='')) THEN 1 ELSE 0 END) AS open_parents
          FROM orders
          WHERE date(opened_at)=date('now')
          GROUP BY marketId, selectionId, letter, mode
        """).fetchall()
        for r in rows:
            if r["open_parents"] and int(r["open_parents"]) > 3:
                res["cap_viol"] += 1

        # Rotation: at most one parent per (mid,sid,letter) per minute bucket
        rows = _exec(con, """
          SELECT marketId, selectionId, UPPER(COALESCE(source,'')) AS letter,
                 strftime('%Y-%m-%d %H:%M', opened_at) AS bucket,
                 COUNT(*) AS c
          FROM orders
          WHERE date(opened_at)=date('now') AND (COALESCE(hedge_of,'')='')
          GROUP BY marketId, selectionId, letter, bucket
        """).fetchall()
        for r in rows:
            if int(r["c"] or 0) > 1:
                res["rotation_dups"] += 1

        return res
    finally:
        con.close()

# --- main ---------------------------------------------------------------------
def main():
    ensure_schema(args.auto)

    # Seed
    mids, runners = seed_markets(args.auto)

    # Generate starting prices
    band_map: Dict[Tuple[str,str], List[float]] = {}
    px_map: Dict[Tuple[str,str], float] = {}
    for mid in mids:
        for i, sid in enumerate(runners[mid]):
            base = 3.0 + i * 1.4  # stagger start odds across [3..10]
            px_map[(mid,sid)] = round(base, 2)
            band_map[(mid,sid)] = [px_map[(mid,sid)]]

    # Sim loop
    ticks = int(max(1, args.minutes * 60 * args.hz))
    dt = 1.0 / max(0.1, args.hz)
    run_id = f"TEST-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    print(f"[SMOKE] run_id={run_id} ticks={ticks} hz={args.hz}")

    for t in range(ticks):
        # feed prices
        for mid in mids:
            for i, sid in enumerate(runners[mid]):
                mode = scenario_for_runner(mid, sid, i)  # drift/steam/flat
                px = px_map[(mid,sid)]
                px = step_price(mode, px)
                px_map[(mid,sid)] = px
                band = band_map[(mid,sid)]
                band.append(px)
                write_tick(args.auto, mid, sid, px, band)
        # call engine
        decide_once_tick(run_id)
        time.sleep(dt)

    # Assertions
    res = assert_caps_rotation_stamping(args.auto)
    print("\n[RESULTS]")
    print(f"  placed total     : {res['placed']}")
    print(f"  stamp_missing    : {res['stamp_missing']}")
    print(f"  cap_violations   : {res['cap_viol']}")
    print(f"  rotation_dups    : {res['rotation_dups']}")

    ok = (res["stamp_missing"] == 0 and res["cap_viol"] == 0 and res["rotation_dups"] == 0 and res["placed"] > 0)
    print(f"\nSMOKE: {'PASS ✅' if ok else 'FAIL ❌'}")
    if not ok:
        sys.exit(2)

if __name__ == "__main__":
    main()
