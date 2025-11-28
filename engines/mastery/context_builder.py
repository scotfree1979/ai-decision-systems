# engines/mastery/context_builder.py
from __future__ import annotations

import os, json, time, sqlite3, random
from typing import Tuple, Dict, Any, Optional, List
from datetime import datetime, timedelta, timezone

# Canonical DB paths
from engines.config_paths import connect_db, autoscalp_db

# Optional: live odds API fallback (resolves session token internally)
try:
    from engines.utils.api_tools import fetch_live_odds  # (session_token=None resolves from stores)
except Exception:  # keep builder resilient even if module missing in some envs
    fetch_live_odds = None  # type: ignore

# --- safe access for sqlite3.Row or dict -------------------------------------
def _rget(row, key, default=None):
    """
    Safe getter that works for sqlite3.Row (row['k']) and dict (row.get('k')).
    Never raises; returns default when key absent.
    """
    try:
        if hasattr(row, "keys"):   # sqlite3.Row path
            return row[key] if key in row.keys() else default
        if isinstance(row, dict): # dict path
            return row.get(key, default)
    except Exception:
        pass
    return default


# ─────────────────────────────────────────────────────────────────────────────
# Constants / Policy knobs
# ─────────────────────────────────────────────────────────────────────────────
CONCURRENT_WINDOW_MIN = 6         # overlap window (minutes) to mark a secondary race
COOLDOWN_SEC = 20                 # don't revisit same (marketId, selectionId) within 20s
MAX_PER_RUNNER = 3                # open parent cap per runner (per current run)
ODDS_MIN = 1.50
ODDS_MAX = 8.00

# ─────────────────────────────────────────────────────────────────────────────
# Small helpers
# ─────────────────────────────────────────────────────────────────────────────
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _iso(ts: Optional[datetime]) -> str:
    if not ts: return ""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# === PATCH START ===
# 📍 TARGET: engines/mastery/context_builder.py:_current_source_upper
# 📆 PATCHED: 2025-11-27 — unify source resolver without importing orchestrator

def _current_source_upper() -> str:
    """
    Unified mode resolver — identical to orchestrator._current_source()
    but implemented locally to avoid circular imports.
    
    Order:
      1) engines.upgrade_import_patch.get_mode()
      2) AUTOSCALP_MODE / MODE env vars
      3) engines.daily_config.MODE
      4) default TEST
    """
    # 1) upgrade_import_patch.get_mode()
    try:
        from engines.upgrade_import_patch import get_mode  # type: ignore
        m = get_mode()
        if m:
            mu = str(m).upper()
            if mu in ("TEST", "LEARNING", "LIVE"):
                return mu
    except Exception:
        pass

    # 2) environment variables
    import os
    m = os.environ.get("AUTOSCALP_MODE") or os.environ.get("MODE")
    if m:
        mu = str(m).upper()
        if mu in ("TEST", "LEARNING", "LIVE"):
            return mu
        if mu == "REPLAY":
            return "TEST"

    # 3) daily_config fallback
    try:
        import engines.daily_config as dc
        m = getattr(dc, "MODE", None)
        if m:
            mu = str(m).upper()
            if mu in ("TEST", "LEARNING", "LIVE"):
                return mu
    except Exception:
        pass

    # 4) default
    return "TEST"
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/context_builder.py:_open_adb
# 📆 PATCHED: 2025-11-21 — replace raw sqlite.connect with DAL opener

from engines.decision_engine.decide_once.helpers import open_auto_db as _dal_auto_db
from engines.decision_engine.decide_once.helpers import q_retry as _dal_q_retry

def _open_adb(timeout: float = 10.0, retries: int = 6, delay_s: float = 0.08) -> sqlite3.Connection:
    """
    DAL-safe AUTO_DB opener.
    Replaces raw sqlite.connect() with open_auto_db(ro=True),
    keeping identical behaviour to the original helper.
    """
    con = _dal_auto_db(ro=True)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA read_uncommitted=1;")
        con.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return con

# Re-export q_retry so existing call sites continue to work
_q_retry = _dal_q_retry
# === PATCH END ===


def _auto_markets_for_day(day_iso: str) -> list[sqlite3.Row]:
    con = _open_adb()
    try:
        if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='markets_schedule'").fetchone():
            return []
        rows = _q_retry(con,
            "SELECT marketId, COALESCE(venue, course, market_name, '-') AS course, off_at_utc "
            "FROM markets_schedule WHERE date(off_at_utc)=date(?) "
            "GROUP BY marketId ORDER BY datetime(off_at_utc) ASC", (day_iso,)
        ).fetchall()
        return rows or []
    finally:
        try: con.close()
        except Exception: pass


def _q_retry(con: sqlite3.Connection, sql: str, params=(), tries: int = 6, delay_s: float = 0.08):
    """Retry a single statement on SQLite busy/open conditions."""
    import sqlite3 as _sqlite, time as _time
    last = None
    for i in range(max(1, tries)):
        try:
            return con.execute(sql, params)
        except _sqlite.OperationalError as e:
            last = e
            msg = str(e).lower()
            if (("locked" in msg) or ("unable to open database file" in msg)) and i < tries - 1:
                _time.sleep(delay_s * (i + 1))
                continue
            raise
    raise last  # pragma: no cover

from datetime import datetime, timezone

def _minutes_to_off_utc(mid: str) -> float | None:
    """Return minutes to OFF (UTC) for this market, or None if unknown."""
    try:
        con = _open_adb(); con.row_factory = sqlite3.Row
        r = con.execute("SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        con.close()
        if not (r and r["off_at_utc"]):
            return None
        off = datetime.fromisoformat(str(r["off_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        return (off - datetime.now(timezone.utc)).total_seconds() / 60.0
    except Exception:
        try: con.close()
        except Exception: pass
        return None

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/mastery/context_builder.py
# 🔎 SEARCH: def _latest_price(mid: str, sid: str) -> Tuple[Optional[float], Optional[list]]:
# (keep your _latest_price as-is; we’ll add a sibling helper and call it in build_context)
# ─────────────────────────────────────────────────────────────────────────────
from datetime import datetime, timezone

def _tape_snapshot(mid: str, sid: str) -> tuple[Optional[float], Optional[int]]:
    """
    Read AUTO_DB.odds_current for (mid,sid) today:
      returns (tape_px, tape_age_sec) where tape_px := ltp→back1→lay1.
    """
    px, age = None, None
    try:
        con = _open_adb()
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='odds_current'").fetchone():
            r = con.execute(
                """
                SELECT ltp, back1, lay1, COALESCE(updated_ts, updated_at) AS uts
                FROM odds_current
                WHERE day IN (date('now','utc'), date('now'))
                  AND marketId=? AND selectionId=?
                LIMIT 1
                """, (str(mid), str(sid))
            ).fetchone()
            if r:
                # prefer ltp, then back, then lay
                raw = r["ltp"] if r["ltp"] is not None else (r["back1"] if r["back1"] is not None else r["lay1"])
                if raw is not None:
                    px = float(raw)
                uts = r["uts"]
                if uts:
                    # compute age vs UTC now
                    ts = datetime.fromisoformat(str(uts).replace("Z","+00:00"))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    age = int((datetime.now(timezone.utc) - ts).total_seconds())
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass
    return px, age


# ─────────────────────────────────────────────────────────────────────────────
# Bets schedule (GetMarkets) — canonical market set for today/tomorrow
# ─────────────────────────────────────────────────────────────────────────────
# Schedule from autoscalp_gui.markets_schedule (canonical for MIRROR/DIRECT)
def _gui_markets_for_day(day_iso: str) -> List[sqlite3.Row]:
    con = _open_adb()
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='markets_schedule'").fetchone():
            rows = _q_retry(
                con,
                "SELECT marketId, COALESCE(venue, course, market_name, '-') AS course, off_at_utc "
                "FROM markets_schedule WHERE date(off_at_utc)=date(?) "
                "GROUP BY marketId ORDER BY datetime(off_at_utc) ASC",
                (day_iso,)
            ).fetchall()
            return rows or []
        return []
    finally:
        try: con.close()
        except Exception: pass

def _choose_primary_and_concurrent(rows: List[sqlite3.Row], now_utc: datetime
                                   ) -> Tuple[Optional[sqlite3.Row], Optional[sqlite3.Row]]:
    """Choose earliest future market as primary; any within CONCURRENT_WINDOW_MIN as secondary."""
    with_dt: List[tuple[sqlite3.Row, Optional[datetime]]] = []
    for r in rows:
        off = r["off_at_utc"]
        off_dt = None
        if off:
            try:
                off_dt = datetime.fromisoformat(str(off).replace("Z", "+00:00")).astimezone(timezone.utc)
            except Exception:
                off_dt = None
        with_dt.append((r, off_dt))

    future = [(r, dt) for (r, dt) in with_dt if dt and dt >= now_utc]
    past = [(r, dt) for (r, dt) in with_dt if dt and dt < now_utc]
    unknown = [(r, dt) for (r, dt) in with_dt if dt is None]

    primary: Optional[sqlite3.Row] = None
    secondary: Optional[sqlite3.Row] = None

    if future:
        future.sort(key=lambda x: x[1])
        primary = future[0][0]
        p_dt = future[0][1]
        # find any within window
        if p_dt:
            for (r, dt) in future[1:]:
                if dt and abs((dt - p_dt).total_seconds()) <= CONCURRENT_WINDOW_MIN * 60:
                    secondary = r
                    break
    elif unknown:
        primary = unknown[0][0]
    elif past:
        # all done today — leave primary None; we'll roll to tomorrow outside
        primary = None

    return primary, secondary

# 📍 TARGET: engines/mastery/context_builder.py
# 🔎 SEARCH: def _roll_to_tomorrow_if_needed\(today_rows: List\[sqlite3\.Row\], now_utc: datetime\) -> Tuple\[List\[sqlite3\.Row\], str\]:
# ⛏️ ACTION: replace the body to use _auto_markets_for_day only
# 📆 PATCHED: 2025-10-05T12:15Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _roll_to_tomorrow_if_needed(today_rows: List[sqlite3.Row], now_utc: datetime) -> Tuple[List[sqlite3.Row], str]:
    """Return (rows, day_iso). Rolls to tomorrow only if no future off_at_utc remain."""
    day = now_utc.date().isoformat()
    if today_rows:
        for r in today_rows:
            off = r["off_at_utc"]
            try:
                if off and datetime.fromisoformat(str(off).replace("Z","+00:00")).astimezone(timezone.utc) >= now_utc:
                    # Still have future races today → keep today
                    return today_rows, day
            except Exception:
                continue
    # Otherwise roll to tomorrow
    day2 = (now_utc.date() + timedelta(days=1)).isoformat()
    rows2 = _auto_markets_for_day(day2)
    return rows2, day2
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# ─────────────────────────────────────────────────────────────────────────────
# Price path (current): odds_current → oc_series(odd) → API best offers
# Baseline/bands (optional): inbound_oc_cache.oc1 (+ oc1_band_json)
# ─────────────────────────────────────────────────────────────────────────────
def _latest_price(mid: str, sid: str) -> Tuple[Optional[float], Optional[list]]:
    """
    Return (current_price, band_json_or_None).

    Priority:
      0) odds_current.ltp (fast 'now')
      1a) (mto>=80) inbound anchor_odd  → band=None
      1b) (mto<80)  inbound oc1         → band=oc1_band_json
      2) oc_series latest odd (band=None)
      3) API best offers (band=None)
    """
    import sqlite3, json
    ltp_now: Optional[float] = None
    band: Optional[list] = None

    # 0) odds_current.ltp (today)
    try:
        con = _open_adb(); con.row_factory = sqlite3.Row
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='odds_current'").fetchone():
            r = con.execute("""
                SELECT ltp FROM odds_current
                 WHERE day IN (date('now'),date('now','utc'))
                   AND marketId=? AND selectionId=?
                 LIMIT 1
            """, (mid, sid)).fetchone()
            if r and r["ltp"] is not None:
                ltp_now = float(r["ltp"])
        con.close()
    except Exception:
        pass

    # minutes-to-off to decide ANCHOR vs OC1 branch
    try:
        mto = _minutes_to_off_utc(mid)
    except Exception:
        mto = None

    # 1) inbound: ANCHOR before OC1 window (>=80m), OC1 (with band) after
    inbound_px: Optional[float] = None
    inbound_band: Optional[list] = None
    try:
        con = _open_adb(); con.row_factory = sqlite3.Row
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_oc_cache'").fetchone():
            row = con.execute("""
                SELECT anchor_odd, oc1, oc1_band_json
                  FROM inbound_oc_cache
                 WHERE marketId=? AND selectionId=?
                 ORDER BY id DESC LIMIT 1
            """, (mid, sid)).fetchone()
        else:
            row = None
        con.close()
    except Exception:
        row = None

    if row:
        if (mto is not None) and (mto >= 80.0):
            # anchor pre-OC1
            if row["anchor_odd"] is not None:
                inbound_px = float(row["anchor_odd"])
                inbound_band = None
        else:
            # oc1 (+ band) post-OC1
            if row["oc1"] is not None:
                inbound_px = float(row["oc1"])
                try:
                    inbound_band = json.loads(row["oc1_band_json"]) if row["oc1_band_json"] else None
                except Exception:
                    inbound_band = None
        # fallback cross-over if preferred field missing
        if inbound_px is None:
            if row["oc1"] is not None:
                inbound_px = float(row["oc1"])
                try:
                    inbound_band = json.loads(row["oc1_band_json"]) if row["oc1_band_json"] else None
                except Exception:
                    inbound_band = None
            elif row["anchor_odd"] is not None:
                inbound_px = float(row["anchor_odd"])
                inbound_band = None

    if ltp_now is not None:
        return ltp_now, inbound_band
    if inbound_px is not None:
        return inbound_px, inbound_band

    # 2) oc_series (latest today)
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        r = bdb.execute("""
            SELECT odd FROM oc_series
             WHERE marketId=? AND selectionId=?
             ORDER BY datetime(snapshot_ts) DESC LIMIT 1
        """, (mid, sid)).fetchone()
        bdb.close()
        if r and r["odd"] is not None:
            return float(r["odd"]), None
    except Exception:
        pass

    # 3) direct API last resort
    try:
        if fetch_live_odds:
            odds = fetch_live_odds(None, mid, sid) or {}
            px = odds.get("lay") or odds.get("back")
            if px is not None:
                return float(px), None
    except Exception:
        pass

    return None, None

# public alias
latest_price = _latest_price


# ─────────────────────────────────────────────────────────────────────────────
# Runner selection within a market
# ─────────────────────────────────────────────────────────────────────────────
def _runner_ids_for_market_by_odds(mid: str, limit: int = 12) -> List[str]:
    """Return selectionIds for a market ordered by current price ascending (best 12)."""
    con = _open_adb()
    try:
        # oc1 latest per runner today
        rows = _q_retry(
            con,
            "SELECT t.selectionId AS sid, t.oc1 AS odd FROM inbound_oc_cache t "
            "JOIN (SELECT selectionId, MAX(id) AS max_id FROM inbound_oc_cache "
            "      WHERE marketId=? GROUP BY selectionId) x ON x.max_id = t.id "
            "WHERE t.oc1 IS NOT NULL "
            "ORDER BY t.oc1 ASC LIMIT ?",
            (mid, limit)
        ).fetchall()

        if rows:
            return [str(r["sid"]) for r in rows]
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass

    # Fallback: any runners recorded in runners table
    con2 = _open_adb()
    try:
        if con2.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runners'").fetchone():
            rs = _q_retry(con2, "SELECT selectionId FROM runners WHERE marketId=? LIMIT ?", (mid, limit)).fetchall()

            return [str(r[0]) for r in rs] if rs else []
    except Exception:
        pass
    finally:
        try: con2.close()
        except Exception: pass

    return []

def _open_parent_count(mid: str, sid: str) -> int:
    """Count open parent entries for (mid,sid) in AUTO_DB.orders."""
    con = _open_adb()
    try:
        cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
        link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        parent_pred = f"({link} IS NULL OR {link}='')" if link else "1=1"
        row = _q_retry(
            con,
            f"SELECT COUNT(*) AS n FROM orders "
            f"WHERE {parent_pred} AND (closed_at IS NULL OR closed_at='') "
            f"AND marketId=? AND selectionId=?",
            (mid, sid)
        ).fetchone()
        return int(row["n"] if row and "n" in row.keys() else (row[0] if row else 0))
    except Exception:
        return 0
    finally:
        try: con.close()
        except Exception: pass

def _cooldown_set() -> set[tuple[str, str]]:
    """Return set of (mid,sid) decided in the last COOLDOWN_SEC seconds."""
    con = _open_adb()
    try:
        if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone():
            rows = _q_retry(
                con,
                "SELECT DISTINCT marketId, selectionId FROM decisions "
                "WHERE datetime(COALESCE(decided_at,'')) >= datetime('now', ?, 'utc')",
                (f"-{COOLDOWN_SEC} seconds",)
            ).fetchall()
            return {(str(r["marketId"]), str(r["selectionId"])) for r in rows}
        return set()
    except Exception:
        return set()
    finally:
        try: con.close()
        except Exception: pass

# ─────────────────────────────────────────────────────────────────────────────
# Bank / Exposure (memoized ~60s)
# ─────────────────────────────────────────────────────────────────────────────
_LAST_FUNDS = {"t": 0.0, "bank": 0.0, "used": 0.0}

def _get_account_funds() -> Tuple[float, float]:
    # memoize for 60s to avoid chatty calls
    now = time.time()
    if now - float(_LAST_FUNDS["t"]) <= 60.0:
        return float(_LAST_FUNDS["bank"]), float(_LAST_FUNDS["used"])
    try:
        # reuse the feeder's approach
        from engines.betfair_status import _keys
        app_key, tok = _keys()
        import requests
        headers = {
            "X-Application": app_key,
            "X-Authentication": tok,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = [{
            "jsonrpc": "2.0",
            "method": "AccountAPING/v1.0/getAccountFunds",
            "params": {},
            "id": 1
        }]
        r = requests.post("https://api.betfair.com/exchange/account/json-rpc/v1",
                          headers=headers, data=json.dumps(payload), timeout=8)
        r.raise_for_status()
        resp = r.json()[0].get("result", {}) if isinstance(r.json(), list) else {}
        bank = float(resp.get("availableToBetBalance") or 0.0)
        used = float(resp.get("exposure") or 0.0)
        _LAST_FUNDS.update({"t": now, "bank": bank, "used": used})
        return bank, used
    except Exception:
        return float(_LAST_FUNDS["bank"]), float(_LAST_FUNDS["used"])

# --- NEW: slope & OC-momentum derivation from inbound_oc_cache ---------------
import json, sqlite3, math
from engines.decision_engine.decide_once.helpers import open_auto_db as _adb, _q_retry as _q

# Betfair tick ladder helper (subset; enough for 1.50..12.00)
def _odds_to_tick(px: float) -> int:
    bands = [
        (1.01, 2.00, 0.01, 0),
        (2.02, 3.00, 0.02, 100),
        (3.05, 4.00, 0.05, 150),
        (4.10, 6.00, 0.10, 170),
        (6.20, 10.00, 0.20, 190),
        (10.50, 20.00, 0.50, 210),
        (21.00, 30.00, 1.00, 230),
        (32.00, 50.00, 2.00, 240),
        (55.00, 100.00, 5.00, 250),
        (110.00, 200.00, 10.00, 260),
        (220.00, 1000.00, 20.00, 270),
    ]
    for lo, hi, step, off in bands:
        if lo <= px <= hi:
            return int(round((px - lo) / step)) + off
    return None

def _latest_inbound_series(mid: str, sid: str, depth: int = 3):
    """
    Return the most recent 'depth' snapshots for this runner from inbound_oc_cache.
    Each snapshot contributes an OC list [anchor, oc1..oc20] as available.
    """
    con = _adb(ro=True); con.row_factory = sqlite3.Row
    rows = _q(con, """
        SELECT id, last_sync_ts,
               anchor_odd,
               oc1, oc2, oc3, oc4, oc5, oc6, oc7, oc8, oc9, oc10,
               oc11, oc12, oc13, oc14, oc15, oc16, oc17, oc18, oc19, oc20
        FROM inbound_oc_cache
        WHERE marketId=? AND selectionId=?
        ORDER BY datetime(COALESCE(last_sync_ts,'')) DESC, id DESC
        LIMIT ?
    """, (str(mid), str(sid), int(max(1, depth)))).fetchall() or []
    try: con.close()
    except Exception: pass

    series = []
    for r in rows:
        oc_vals = [
            r["anchor_odd"],
            r["oc1"], r["oc2"], r["oc3"], r["oc4"], r["oc5"],
            r["oc6"], r["oc7"], r["oc8"], r["oc9"], r["oc10"],
            r["oc11"], r["oc12"], r["oc13"], r["oc14"], r["oc15"],
            r["oc16"], r["oc17"], r["oc18"], r["oc19"], r["oc20"],
        ]
        oc = [float(x) for x in oc_vals if x is not None]
        if oc:
            series.append((r["last_sync_ts"], oc))
    return series


def _oc_momentum_ticks(oc: list[float]) -> int:
    """
    Monotonic net ticks across OC0..OCk (k>=1).
    Positive => drift (odds up, L2B), negative => steam (odds down, B2L).
    Returns 0 if the path is not monotonic (i.e., both up & down steps present).
    """
    if not oc or len(oc) < 2:
        return 0
    # Convert to ticks once; reuse for speed
    ticks = [_odds_to_tick(px) for px in oc]
    if any(t is None for t in ticks):
        return 0
    diffs = [ticks[i+1] - ticks[i] for i in range(len(ticks)-1)]
    has_up  = any(d > 0 for d in diffs)
    has_down = any(d < 0 for d in diffs)
    if has_up and has_down:
        return 0  # not monotonic
    return int(ticks[-1] - ticks[0])


def _slope_ppm_from_inbound(mid: str, sid: str, horizon_rows: int = 3) -> tuple[float, int]:
    """
    Approximate slope (ticks per minute) from the last N inbound rows by connecting
    the earliest OC0 and latest OCk and dividing by elapsed minutes between rows.
    Returns (slope_ppm, recent_momentum_ticks).
    """
    series = _latest_inbound_series(mid, sid, depth=horizon_rows)
    if len(series) < 2:
        oc = series[0][1] if series else []
        return (0.0, _oc_momentum_ticks(oc))

    # earliest and latest (series is newest-first due to ORDER BY; earliest is last element)
    ts0, oc0 = series[-1]
    ts1, oc1 = series[0]

    try:
        from datetime import datetime
        # Parse ISO timestamps (strip 'Z' to keep math consistent)
        t0 = datetime.fromisoformat(str(ts0).replace("Z", ""))
        t1 = datetime.fromisoformat(str(ts1).replace("Z", ""))
        mins = max((t1 - t0).total_seconds() / 60.0, 1e-9)
    except Exception:
        mins = 1.0

    t0_tick = _odds_to_tick(oc0[0]) if oc0 else None
    t1_tick = _odds_to_tick(oc1[-1]) if oc1 else None
    if t0_tick is None or t1_tick is None:
        # fall back to instantaneous OC momentum on latest row
        return (0.0, _oc_momentum_ticks(oc1 if oc1 else oc0))

    net = int(t1_tick - t0_tick)
    slope = float(net) / float(mins)
    return (slope, net)

# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/mastery/context_builder.py
# 🔎 SEARCH: ^def build_context\(source.*$
# 📆 PATCHED: 2025-10-06T20:45Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# === PATCH 3 START (unified Scope ContextBuilder) ===
def build_context(source: str | None = None) -> tuple[dict, dict]:
    """
    Unified context builder using global Scope + MarketMonitor + Bias.
    Always defers market/runner selection to Scope.
    """
    from engines.mastery.mastery_policy import _SCOPE_STATE

    from engines.market_monitor.monitor import get_market_state
    from engines.bias.engine import compute_bias

    src = (source or _current_source_upper()).upper()
    now = _now_utc()

    mids = _SCOPE_STATE.get("markets", [])
    if not mids:
        return {"source": src}, {}

    mid = mids[0]
    sids = list(map(str, _SCOPE_STATE.get("active_sids", {}).get(mid, [])))
    if not sids:
        return {"source": src, "marketId": mid}, {}

    sid = sids[0]
    try:
        state = get_market_state(mid) or {}
    except Exception:
        state = {}

    runners = state.get("runners", {})
    if runners:
        # prefer lowest price
        sid = min(runners.keys(), key=lambda k: runners[k].get("px", 9999)) or sid

    ctx = {
        "source": src,
        "marketId": mid,
        "selectionId": sid,
        "minutes_to_off": 10.0,
        "phase": "PRE",
        "depth_total": 900.0,
        "matched_per_min": 280.0,
        "direction": "LAY->BACK",
    }

    if sid in runners:
        info = runners[sid]
        ctx["odds"] = info.get("px")
        ctx["band"] = info.get("band")
        ctx["is_fav"] = info.get("is_fav", False)

    try:
        bias = compute_bias(ctx)
        ctx.update(bias.as_plan_fields())
        ctx["bias_why"] = bias.why
    except Exception as e:
        ctx["bias"] = 0.0
        ctx["bias_dir"] = "FLAT"
        ctx["bias_conf"] = 0.0
        ctx["bias_why"] = f"bias_fail:{e}"

    try:
        slope_ppm, net_ticks = _slope_ppm_from_inbound(mid, sid, horizon_rows=3)
        ctx["slope_ppm"] = slope_ppm
        ctx["recent_net_ticks"] = net_ticks
    except Exception:
        ctx["slope_ppm"] = 0.0
        ctx["recent_net_ticks"] = 0

    try:
        bank, used = _get_account_funds()
        ctx["bank"] = bank
        ctx["used_exposure"] = used
    except Exception:
        pass

    meta = {
        "marketId": mid,
        "selectionId": sid,
        "scope_count": len(mids),
        "runner_count": len(sids),
        "updated_at": now.isoformat(),
    }

    return ctx, meta
# === PATCH 3 END (unified Scope ContextBuilder) ===


# ─────────────────────────────────────────────────────────────────────────────
# TEST builder (kept, unchanged except for imports)
# ─────────────────────────────────────────────────────────────────────────────
def build_context_from_test_db() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    TEST context builder:
      1) samples several ACTIVE runners (oc1 in [1.5, 8.0]) from inbound_bets_min.active=1 + inbound_oc_cache,
      2) prefers the runner with the FEWEST open parent scalps (least-loaded),
      3) falls back to any OC row (and then to any oc1) if no active-band rows exist,
      4) computes compressed minutes_to_off and tto_window from markets_schedule using sim_params.speed_min_per_sec,
      5) enriches ctx with current_odds, fav_rank/is_fav, and NEW: slope_ppm + oc_momentum_ticks + recent_net_ticks.
    Returns (ctx, meta). If nothing suitable this tick, returns ctx/meta without IDs.
    """
    import random, sqlite3
    from datetime import datetime

    ctx: Dict[str, Any] = {
        "distance_band": "5-7f",
        "code": "FLAT",
        "tto_window": "30-10",
        "class_band": "mid",
        "fav_rank_bin": "fav",
        "sigma": 0.25,
        "depth_total": 900.0,
        "matched_per_min": 280.0,
        # Bias the default; per-letter logic enforces L2B and guards B2L:
        "direction": "LAY->BACK",
        "source": "TEST",
    }
    meta: Dict[str, Any] = {}

    def _classify_band(px: Optional[float], lo=1.5, hi=8.0, hi2=12.0) -> str:
        if px is None or not (px == px):  # None/NaN
            return "UNKNOWN"
        if px < lo:
            return "PASSIVE"   # sub-1.5 not ACTIVE in our pre-off philosophy
        if px <= hi:
            return "ACTIVE"    # 8.00 inclusive ACTIVE
        if px <= hi2:
            return "PASSIVE"
        return "IGNORED"

    def _latest_oc1(bdb, mid: str, sid: str) -> Optional[float]:
        row = bdb.execute(
            """
            SELECT oc1, anchor_odd
            FROM inbound_oc_cache
            WHERE marketId=? AND selectionId=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (mid, sid),
        ).fetchone()
        if not row:
            return None
        oc1, anchor = row[0], row[1]
        return float(oc1 if oc1 is not None else anchor) if (oc1 is not None or anchor is not None) else None

    def _fav_rank_for_market(bdb, mid: str) -> Dict[str, int]:
        """
        Returns {sid: rank}, rank=1 is fav by lowest oc1; falls back to anchor_odd; ignores NULLs.
        """
        rows = bdb.execute(
            """
            WITH latest AS (
              SELECT selectionId, MAX(id) AS mx
              FROM inbound_oc_cache
              WHERE marketId=?
              GROUP BY selectionId
            )
            SELECT c.selectionId AS sid,
                   COALESCE(c.oc1, c.anchor_odd) AS px
            FROM inbound_oc_cache c
            JOIN latest l ON l.selectionId=c.selectionId AND l.mx=c.id
            WHERE c.marketId=? AND COALESCE(c.oc1, c.anchor_odd) IS NOT NULL
            """,
            (mid, mid),
        ).fetchall() or []
        pairs = [(str(r["sid"]), float(r["px"])) for r in rows if r["px"] is not None]
        pairs.sort(key=lambda t: (t[1], t[0]))
        return {sid: i + 1 for i, (sid, _) in enumerate(pairs)}

    # --- Connect to the TEST/Sim DB
    bdb = connect_db(ro=True)
    try:
        bdb.row_factory = sqlite3.Row

        def exists(tab: str) -> bool:
            return bool(
                bdb.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                    (tab,),
                ).fetchone()
            )

        SAMPLE_N = 8
        candidates: list[tuple[str, str]] = []

        has_ibm = exists("inbound_bets_min")
        has_ioc = exists("inbound_oc_cache")

        # Prefer ACTIVE-band candidates first (oc1 in [1.5, 8.0])
        if has_ibm and has_ioc:
            rows = bdb.execute(
                """
                SELECT ibm.marketId AS mid, ibm.selectionId AS sid
                FROM inbound_bets_min ibm
                JOIN inbound_oc_cache oc
                  ON oc.marketId=ibm.marketId AND oc.selectionId=ibm.selectionId
                WHERE COALESCE(ibm.active,1)=1
                  AND oc.oc1 IS NOT NULL
                  AND oc.oc1 BETWEEN 1.5 AND 8.0
                ORDER BY RANDOM()
                LIMIT ?
                """,
                (SAMPLE_N,),
            ).fetchall()
            candidates = [(str(r["mid"]), str(r["sid"])) for r in rows]

        # If none found in ACTIVE band, fall back to any oc1 row
        if not candidates and has_ioc:
            rows = bdb.execute(
                """
                SELECT marketId AS mid, selectionId AS sid
                FROM inbound_oc_cache
                WHERE oc1 IS NOT NULL
                ORDER BY RANDOM()
                LIMIT ?
                """,
                (SAMPLE_N,),
            ).fetchall()
            candidates = [(str(r["mid"]), str(r["sid"])) for r in rows]

        if not candidates:
            # Nothing schedulable for this tick
            return ctx, meta

        # --- Least-loaded within AUTO_DB (prefer runners with fewer open parent scalps)
        loads: dict[tuple[str, str], int] = {}
        acon = _open_adb()
        try:
            acon.row_factory = sqlite3.Row
            cols = [r["name"] for r in acon.execute("PRAGMA table_info(orders)")]
            link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
            parent_pred = f"({link} IS NULL OR {link}='')" if link else "1=1"
            for (mid, sid) in candidates:
                row = acon.execute(
                    f"""
                    SELECT COUNT(*) AS n
                    FROM orders
                    WHERE {parent_pred}
                      AND (closed_at IS NULL OR closed_at='')
                      AND marketId=? AND selectionId=?
                    """,
                    (mid, sid),
                ).fetchone()
                loads[(mid, sid)] = int(row["n"]) if row is not None else 0
        finally:
            try:
                acon.close()
            except Exception:
                pass

        min_load = min(loads.values()) if loads else 0
        least_loaded = [p for p, n in loads.items() if n == min_load]
        mid, sid = random.choice(least_loaded) if least_loaded else random.choice(candidates)
        mid, sid = str(mid), str(sid)

        # Attach identities
        ctx["marketId"] = meta["marketId"] = mid
        ctx["selectionId"] = meta["selectionId"] = sid

        # --- Compressed minutes_to_off & tto_window (use sim_params.speed_min_per_sec)
        try:
            row = bdb.execute("SELECT v FROM sim_params WHERE k='speed_min_per_sec'").fetchone()
            speed = float(row[0]) if row else 0.5
        except Exception:
            speed = 0.5

        try:
            row = bdb.execute(
                "SELECT off_at_utc FROM markets_schedule WHERE marketId=?",
                (mid,),
            ).fetchone()
            if row and row[0]:
                # Convert to minutes and apply compression speed
                off = datetime.strptime(str(row[0])[:19], "%Y-%m-%d %H:%M:%S")
                mto_min = ((off - datetime.utcnow()).total_seconds() / 60.0) * float(speed)
            else:
                mto_min = None
        except Exception:
            mto_min = None

        if mto_min is not None:
            ctx["minutes_to_off"] = float(mto_min)
            ctx["tto_window"] = (
                "120-80" if mto_min >= 120 else
                "80-60"  if mto_min >=  80 else
                "60-40"  if mto_min >=  60 else
                "40-20"  if mto_min >=  40 else
                "20-10"  if mto_min >=  20 else
                "10-5"   if mto_min >=  10 else
                "5-2"    if mto_min >=   5 else
                "2-0"
            )
            ctx["phase"] = "PRE" if mto_min >= 0 else "IP"

        # --- Current odds + band hint
        px = _latest_oc1(bdb, mid, sid)
        if px is not None:
            ctx["current_odds"] = float(px)
            ctx.setdefault("px", ctx.get("current_odds"))
            ctx["band_hint"] = _classify_band(ctx["current_odds"])

        # --- Fav rank / is_fav (based on latest oc1/anchor across market)
        try:
            ranks = _fav_rank_for_market(bdb, mid)
            fr = ranks.get(sid)
            if fr is not None:
                ctx["fav_rank"] = int(fr)
                ctx["is_fav"] = (fr == 1)
        except Exception:
            pass

        # --- NEW: Momentum features (AFTER mid+sid)
        try:
            slope_ppm, net_ticks = _slope_ppm_from_inbound(mid, sid, horizon_rows=3)
            series = _latest_inbound_series(mid, sid, depth=1)
            last_oc = series[0][1] if series else []
            oc_momentum = _oc_momentum_ticks(last_oc)
            ctx["slope_ppm"] = float(slope_ppm)
            ctx["recent_net_ticks"] = int(net_ticks)
            ctx["oc_momentum_ticks"] = int(oc_momentum)
        except Exception:
            ctx.setdefault("slope_ppm", 0.0)
            ctx.setdefault("recent_net_ticks", 0)
            ctx.setdefault("oc_momentum_ticks", 0)

        return ctx, meta

    finally:
        try:
            bdb.close()
        except Exception:
            pass
