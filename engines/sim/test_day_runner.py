from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json, math, random, sqlite3, threading, time
from typing import Dict, List, Tuple, Callable, Optional
from engines.config_paths import connect_db, autoscalp_db

# --- schema helpers for inbound_oc_cache (TEST safe) -------------------------
import sqlite3 as _sqlite3

def _has_col(db: _sqlite3.Connection, table: str, col: str) -> bool:
    try:
        cur = db.execute(f"PRAGMA table_info({table})")
        for r in cur:
            name = r["name"] if isinstance(r, _sqlite3.Row) else r[1]
            if name == col:
                return True
        return False
    except Exception:
        return False

def _ensure_cache_schema(db: _sqlite3.Connection) -> None:
    """
    Bring inbound_oc_cache up to current shape:
      marketId, selectionId, anchor_odd, oc1..oc20, oc1_band_json..oc20_band_json, last_sync_ts.
    Idempotent and safe on old/new DBs.
    """
    # make sure table exists
    db.execute("""
        CREATE TABLE IF NOT EXISTS inbound_oc_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL
        )
    """)
    # anchor + last_sync_ts
    if not _has_col(db, "inbound_oc_cache", "anchor_odd"):
        db.execute("ALTER TABLE inbound_oc_cache ADD COLUMN anchor_odd REAL")
    if not _has_col(db, "inbound_oc_cache", "last_sync_ts"):
        db.execute("ALTER TABLE inbound_oc_cache ADD COLUMN last_sync_ts TEXT")
    # ocN + ocN_band_json
    for n in range(1, 21):
        oc = f"oc{n}"
        bj = f"oc{n}_band_json"
        if not _has_col(db, "inbound_oc_cache", oc):
            db.execute(f"ALTER TABLE inbound_oc_cache ADD COLUMN {oc} REAL")
        if not _has_col(db, "inbound_oc_cache", bj):
            db.execute(f"ALTER TABLE inbound_oc_cache ADD COLUMN {bj} TEXT")
    db.commit()

def _upsert_cache_anchor(db: sqlite3.Connection, marketId: str, selectionId: str, anchor: float | None) -> None:
    """
    Idempotent write into inbound_oc_cache:
      - guarantees row exists for (marketId, selectionId)
      - sets anchor_odd once (if NULL)
      - seeds oc1 and oc1_band_json once (if NULL)
      - bumps last_sync_ts each call
    """
    _ensure_cache_schema(db)

    # clamp anchor to realistic pre-off bounds (if provided)
    anchor_val = None
    if anchor is not None:
        try:
            anchor_val = float(anchor)
            anchor_val = max(1.8, min(80.0, anchor_val))
        except Exception:
            anchor_val = None

    # ensure row exists
    db.execute("""
        INSERT INTO inbound_oc_cache (marketId, selectionId, last_sync_ts)
        SELECT ?, ?, datetime('now','utc')
        WHERE NOT EXISTS (
            SELECT 1 FROM inbound_oc_cache WHERE marketId=? AND selectionId=?
        )
    """, (marketId, selectionId, marketId, selectionId))

    # set anchor + oc1/oc1_band_json if still NULL; always update last_sync_ts
    db.execute("""
        UPDATE inbound_oc_cache
           SET anchor_odd    = COALESCE(anchor_odd, ?),
               oc1           = COALESCE(oc1, ?),
               oc1_band_json = COALESCE(oc1_band_json, ?),
               last_sync_ts  = datetime('now','utc')
         WHERE marketId=? AND selectionId=?
    """, (
        anchor_val,
        anchor_val,
        json.dumps([anchor_val]) if anchor_val is not None else None,
        marketId, selectionId
    ))

# --- mark runners active in BETS_DB (so TEST gate never blocks) --------------

def _ensure_runner_activity_active(db: sqlite3.Connection, marketId: str, selectionId: str) -> None:
    """
    Ensure BETS_DB.runner_activity marks this runner as 'active' using the SAME
    open connection to avoid a second writer (and the 'database is locked' error).
    """
    db.execute("""
        CREATE TABLE IF NOT EXISTS runner_activity(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            status TEXT NOT NULL,
            ts TEXT NOT NULL
        )
    """)
    db.execute(
        "INSERT INTO runner_activity(marketId, selectionId, status, ts) "
        "VALUES (?, ?, 'active', datetime('now','utc'))",
        (marketId, str(selectionId))
    )


# --- data structures ---------------------------------------------------------

@dataclass
class PathPoint:
    t_min: float   # minutes to off (negative pre-off, positive in-play)
    price: float

@dataclass
class RunnerPath:
    marketId: str
    selectionId: str
    series: List[PathPoint]          # dense enough that linear interp is ok
    _band: List[float]               # we keep a short band for oc1_band_json

def _clip_preoff(x: float) -> float:
    return max(1.8, min(80.0, x))

def _clip_inplay(x: float, is_winner: bool) -> float:
    return max(1.01, min(1000.0, x if not is_winner else min(x, 20.0)))

def _gbm_step(p: float, dt: float, sigma: float, rng: random.Random) -> float:
    # simple geometric step with mild mean-reversion via random walk drift sign flip
    z = rng.normalvariate(0.0, 1.0)
    return max(1.01, p * math.exp((0.0 - 0.5*sigma*sigma)*dt + sigma*math.sqrt(dt)*z))

def _make_preoff_track(base: float, minutes: int, rng: random.Random) -> List[PathPoint]:
    """Pre-off oscillation around base with gentle trend; t in [-minutes..0]."""
    pts: List[PathPoint] = []
    p = base
    sigma = 0.10 if base < 6.0 else 0.07 if base < 15.0 else 0.05
    for k in range(minutes, -1, -1):               # minutes → 0
        p = _clip_preoff(_gbm_step(p, 1.0, sigma, rng))
        pts.append(PathPoint(t_min=-float(k), price=p))
    return pts

def _make_inplay_tracks(
    preoff_last: Dict[str, float],
    winner_sid: str,
    rng: random.Random
) -> Dict[str, List[PathPoint]]:
    """
    In-play for +2 min:
      - winner collapses  → 1.01 via fast logistic
      - others drift      → 1000 via faster drift if short pre-off, slower if big price
    """
    tracks: Dict[str, List[PathPoint]] = {}
    T = 2  # +2 minutes
    for sid, start_p in preoff_last.items():
        is_win = (sid == winner_sid)
        pts: List[PathPoint] = []
        for m in range(1, T+1):
            if is_win:
                # fast collapse: 1.01 + (start-1.01)*exp(-k*t)
                k = 2.4
                p = 1.01 + (start_p - 1.01) * math.exp(-k * (m / T))
            else:
                # blow out; slower for big pre-off prices
                k = 1.6 if start_p < 6.0 else 1.2 if start_p < 15.0 else 1.0
                p = start_p * math.exp(k * (m / T))
            p = _clip_inplay(p, is_win)
            pts.append(PathPoint(t_min=float(m), price=p))
        tracks[sid] = pts
    return tracks

# --- DB IO --------------------------------------------------------------

def _band_append(db: sqlite3.Connection, marketId: str, selectionId: str, price: float):
    row = db.execute(
        "SELECT oc1_band_json FROM inbound_oc_cache WHERE marketId=? AND selectionId=?",
        (marketId, selectionId)
    ).fetchone()
    band: List[float] = []
    if row and row[0]:
        try:
            band = json.loads(row[0])
        except Exception:
            band = []
    band = (band + [float(price)])[-40:]  # keep last ~40 points
    db.execute(
        "UPDATE inbound_oc_cache SET oc1=?, oc1_band_json=?, last_sync_ts=datetime('now','utc') "
        "WHERE marketId=? AND selectionId=?",
        (float(price), json.dumps(band), marketId, selectionId)
    )

def _ensure_cache_row(db: sqlite3.Connection, marketId: str, selectionId: str, anchor: float):
    _upsert_cache_anchor(db, marketId, selectionId, anchor)

# --- Runner -------------------------------------------------------------

class TestDayRunner:
    def __init__(self, markets, seconds: int, hz: int, logger=None):
        self.markets = markets             # MarketPlan list (from day_blueprint)
        self.seconds = seconds
        self.hz = max(1, hz)
        self.logger = logger
        self._stop = threading.Event()
        self._paths: Dict[Tuple[str,str], RunnerPath] = {}
        self._rng = random.Random(99)

        # precalc paths per runner
        for m in markets:
            # choose winner among top 4 on base_odds
            sorted_r = sorted(m.runners, key=lambda r: r.base_odds)
            winner_sid = sorted_r[self._rng.randint(0, min(3, len(sorted_r)-1))].selectionId

            pre_last: Dict[str, float] = {}
            for r in m.runners:
                pre = _make_preoff_track(r.base_odds, minutes=80, rng=self._rng)  # we care from -80→0
                pre_last[r.selectionId] = pre[-1].price
                self._paths[(m.marketId, r.selectionId)] = RunnerPath(
                    marketId=m.marketId, selectionId=r.selectionId, series=pre, _band=[]
                )
            post = _make_inplay_tracks(pre_last, winner_sid, self._rng)
            # append in-play points
            for r in m.runners:
                self._paths[(m.marketId, r.selectionId)].series.extend(post[r.selectionId])

    def start(self):
        self._thr = threading.Thread(target=self._run, name="TestDayRunner", daemon=True)
        self._thr.start()

    def stop(self, join=False):
        self._stop.set()
        if join:
            try: self._thr.join(timeout=2.0)
            except Exception: pass

    def _run(self):
        db = connect_db(ro=False)
        try:
            # Make reads/writes coexist nicely with the orchestrator
            try:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("PRAGMA synchronous=NORMAL")
                db.execute("PRAGMA busy_timeout=8000")
            except Exception:
                pass

            # Ensure the inbound_oc_cache shape once (no DDL inside per-runner loop)
            _ensure_cache_schema(db)

            # Ensure runner_activity table once
            db.execute("""
                CREATE TABLE IF NOT EXISTS runner_activity (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    marketId TEXT,
                    selectionId TEXT,
                    status TEXT,
                    ts TEXT
                )
            """)

            # ---------- One-time seeding (anchors + activity) ----------
            # (1) cache rows + anchors (also seeds oc1/oc1_band_json if still NULL)
            for m in self.markets:
                for r in m.runners:
                    _upsert_cache_anchor(db, m.marketId, r.selectionId, r.base_odds)

            # (2) mark all runners ACTIVE for the gate on the SAME connection
            rows = []
            for m in self.markets:
                for r in m.runners:
                    rows.append((m.marketId, str(r.selectionId)))
            db.executemany(
                "INSERT INTO runner_activity(marketId, selectionId, status, ts) "
                "VALUES (?, ?, 'active', datetime('now','utc'))",
                rows
            )
            db.commit()

            # ---------- Precompute streaming ----------
            # stride through virtual -80 → +2 minutes linearly during real seconds
            total_samples = self.seconds * self.hz
            # self._paths was built in __init__; map to (mid,sid)->series just once
            all_pts: Dict[Tuple[str, str], List[PathPoint]] = {
                (rp.marketId, rp.selectionId): rp.series for rp in self._paths.values()
            }
            max_len = max(len(s) for s in all_pts.values())
            tick = 1.0 / float(self.hz)

            # ---------- Stream prices into inbound_oc_cache ----------
            for k in range(total_samples):
                if self._stop.is_set():
                    break
                idx = min(int(k * max_len / total_samples), max_len - 1)
                for (marketId, selectionId), series in all_pts.items():
                    price = series[idx].price
                    _band_append(db, marketId, selectionId, price)
                db.commit()
                time.sleep(tick)

        finally:
            try:
                db.close()
            except Exception:
                pass

# compatibility shim used by orchestrator
def _seed_day(markets, speed_min_per_sec: float):
    db = connect_db(ro=False)  # BETS_DB
    try:
        # sim speed config (used for minutes-to-off)
        db.execute("CREATE TABLE IF NOT EXISTS sim_params (k TEXT PRIMARY KEY, v TEXT)")
        db.execute("INSERT OR REPLACE INTO sim_params(k, v) VALUES ('speed_min_per_sec', ?)", (str(speed_min_per_sec),))
        # schedule for tto_window lookup
        db.execute("CREATE TABLE IF NOT EXISTS markets_schedule (marketId TEXT PRIMARY KEY, off_at_utc TEXT)")
        # runner activity table for TEST gating
        db.execute("""
            CREATE TABLE IF NOT EXISTS runner_activity(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                marketId TEXT NOT NULL,
                selectionId TEXT NOT NULL,
                status TEXT NOT NULL,
                ts TEXT NOT NULL
            )
        """)
        for m in markets:
            db.execute("INSERT OR REPLACE INTO markets_schedule(marketId, off_at_utc) VALUES (?, ?)",
                       (m.marketId, m.off_at_utc.strftime("%Y-%m-%d %H:%M:%S")))
            # Mark every seeded runner 'active' so the gate does not block TEST.
            for r in m.runners:
                db.execute(
                    "INSERT INTO runner_activity(marketId, selectionId, status, ts) "
                    "VALUES (?,?, 'active', datetime('now','utc'))",
                    (m.marketId, str(r.selectionId))
                )
        db.commit()
    finally:
        try: db.close()
        except Exception: pass
