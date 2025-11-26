from __future__ import annotations
import argparse, json, os, random, time
from datetime import datetime, timezone, timedelta
from engines.config_paths import autoscalp_db, connect_db, auto_conn, q_retry as _q
# --- repo-root import bootstrap ---
import os, sys
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlite3, time, math, json, argparse
from dataclasses import dataclass
from typing import List, Dict, Tuple, Optional
from datetime import datetime, timedelta, timezone
from engines.sqlite_guard import ensure_dashboard_indexes
import requests
from engines.upgrade_import_patch import get_session_token, get_app_key

# settlements (new API names aliased to feeder expectations)
from engines.live.settlements import (
    ensure_schema as ensure_settlements_schema,
    fetch_cleared_orders_api as fetch_cleared,
    upsert_cleared,
    reconcile_orders as reconcile_from_cleared,
    )

# default ON so the timeline can light up even if MIRROR hasn't written yet
FEEDER_ENABLE_DIRECT_ODDS = (os.getenv("AUTOSCALP_ENABLE_FEEDER_DIRECT_ODDS", "1") == "1")

import requests, logging, os, json
from datetime import datetime, timezone

def keep_alive_once(app_key: str | None = None,
                    session: str | None = None,
                    timeout_s: int = 8) -> bool:
    """
    Direct Betfair keepAlive call. Returns True if the session is valid.
    Reads creds from args or env (BETFAIR_APP_KEY / BETFAIR_SESSION).
    """
    ak = (app_key or os.environ.get("BETFAIR_APP_KEY") or "").strip()
    ss = (session or os.environ.get("BETFAIR_SESSION") or "").strip()
    if not ak or not ss:
        logging.warning("[Session] keep-alive skipped (missing app key or session)")
        return False

    try:
        r = requests.post(
            "https://identitysso.betfair.com/api/keepAlive",
            headers={
                "X-Application": ak,
                "X-Authentication": ss,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=timeout_s,
        )
    except Exception as e:
        logging.error(f"[Session] keep-alive network error: {e}")
        return False

    if r.status_code != 200:
        logging.warning(f"[Session] keep-alive HTTP {r.status_code}: {r.text[:160]}")
        return False

    try:
        js = r.json()
    except Exception:
        js = {}
    status = (js.get("status") if isinstance(js, dict) else
              (js[0].get("status") if isinstance(js, list) and js else "")) or ""
    ok = str(status).upper() == "SUCCESS"
    if ok:
        logging.info(f"✅ [Session] keep-alive OK @ {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    else:
        logging.warning(f"⚠️ [Session] keep-alive failed payload: {json.dumps(js)[:160]}")
    return ok

def _auto() -> sqlite3.Connection:
    con = auto_conn()
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def _bets(ro: bool = True) -> sqlite3.Connection:
    con = connect_db(ro=not not ro)  # True => ro
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _utc_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _ensure_odds_current_schema() -> None:
    con = _auto()
    try:
        _q(con, """
        CREATE TABLE IF NOT EXISTS odds_current(
          day TEXT NOT NULL,
          marketId TEXT NOT NULL,
          selectionId TEXT NOT NULL,
          updated_ts TEXT,
          ltp REAL, back1 REAL, lay1 REAL,
          PRIMARY KEY(day, marketId, selectionId)
        )""")
        cols = {r["name"] for r in _q(con, "PRAGMA table_info(odds_current)").fetchall()}
        if "updated_ts" not in cols:
            _q(con, "ALTER TABLE odds_current ADD COLUMN updated_ts TEXT")
        if "back1" not in cols:
            _q(con, "ALTER TABLE odds_current ADD COLUMN back1 REAL")
        if "lay1" not in cols:
            _q(con, "ALTER TABLE odds_current ADD COLUMN lay1 REAL")
        con.commit()
    finally:
        con.close()

def _get_off_utc(mid: str) -> datetime | None:
    """
    Canonical OFF resolver: Bets DB only.
    Prefer markets_schedule.off_at_utc; fallback to bets.marketStartTime if present.
    """
    b = _bets(ro=True)
    try:
        row = _q(b, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        if row and row["off_at_utc"]:
            s = str(row["off_at_utc"]).strip()
            if s.endswith("Z"):
                return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
            try:
                return datetime.fromisoformat(s).astimezone(timezone.utc)
            except Exception:
                pass
        # fallback: bets table with marketStartTime if your schema has it
        try:
            r2 = _q(b, "SELECT marketStartTime FROM bets WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
            if r2 and r2["marketStartTime"]:
                s = str(r2["marketStartTime"]).strip()
                if s.endswith("Z"):
                    return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
                return datetime.fromisoformat(s).astimezone(timezone.utc)
        except Exception:
            pass
        return None
    finally:
        b.close()

def _minutes_to_off(mid: str, *, now_utc: datetime | None = None) -> float | None:
    now = now_utc or _now_utc()
    off = _get_off_utc(mid)
    if not off:
        return None
    return (off - now).total_seconds() / 60.0

def _pick_scope_days(*, days: list[str], last: int) -> list[str]:
    if days: return list(dict.fromkeys(days))
    if last and last > 0:
        out = []
        base = _now_utc().date()
        for i in range(last):
            out.append((base - timedelta(days=i)).strftime("%Y-%m-%d"))
        return list(dict.fromkeys(out))
    return [_now_utc().strftime("%Y-%m-%d")]

def _markets_for_days(day_list: list[str], *, inplay_window_min: int = 15) -> list[tuple[str, datetime]]:
    """
    Return sorted [(marketId, off_utc)] for requested days, future + grace IP window.
    """
    b = _bets(ro=True)
    try:
        q = """
        SELECT marketId, off_at_utc
        FROM markets_schedule
        WHERE substr(off_at_utc,1,10) IN ({ph})
        ORDER BY datetime(off_at_utc) ASC
        """.format(ph=",".join("?"*len(day_list)))
        rows = _q(b, q, tuple(day_list)).fetchall() or []
        out = []
        now = _now_utc()
        for r in rows:
            off = None
            s = str(r["off_at_utc"] or "").strip()
            if not s: continue
            try:
                off = datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc) if s.endswith("Z") \
                      else datetime.fromisoformat(s).astimezone(timezone.utc)
            except Exception:
                continue
            # keep anything in day window or within inplay_window after off
            if off >= now or (now - off).total_seconds() <= inplay_window_min*60:
                out.append((str(r["marketId"]), off))
        return out
    finally:
        b.close()

# ------------------- TEST mode simulator (writes into AUTO_DB only) ----------
class _SimPump:
    def __init__(self, *, days: list[str], hz: float = 2.0):
        self.days = days
        self.hz = max(0.25, float(hz))
        self.mids: list[str] = []  # active test markets in Bets DB
        self._sids: dict[str, list[str]] = {}  # mid -> sids
        self._last_parent_for: dict[tuple[str,str], float] = {}
        self._seed_schedule_if_needed()

    def _seed_schedule_if_needed(self):
        b = _bets(ro=False)
        try:
            # If no upcoming markets for today, insert 2 test markets ~+2m and +7m
            today = self.days[0]
            rows = _q(b, "SELECT marketId FROM markets_schedule WHERE substr(off_at_utc,1,10)=? LIMIT 1", (today,)).fetchall()
            if not rows:
                base = _now_utc()
                mids = [f"1.TEST.{base.strftime('%H%M%S')}.A", f"1.TEST.{base.strftime('%H%M%S')}.B"]
                offs = [base + timedelta(minutes=2), base + timedelta(minutes=7)]
                for m, off in zip(mids, offs):
                    _q(b, "INSERT OR REPLACE INTO markets_schedule(marketId, off_at_utc) VALUES(?, ?)",
                       (m, off.strftime("%Y-%m-%dT%H:%M:%SZ")))
                b.commit()
            # refresh self.mids
            mids = _q(b, "SELECT marketId FROM markets_schedule WHERE substr(off_at_utc,1,10)=? ORDER BY off_at_utc ASC", (today,)).fetchall()
            self.mids = [str(r["marketId"]) for r in mids]
        finally:
            b.close()
        # seed runner lists
        for mid in self.mids:
            self._sids[mid] = [f"{10000000+i}" for i in range(1, 9)]  # 8 simple runners

    def _odds_walk(self, px: float) -> float:
        # very simple walk to move bars
        r = random.random()
        if px <= 1.05: return 1.01
        if px >= 1000: return 1000.0
        if r < 0.45:   # drift up a touch
            return min(1000.0, round(px * (1.0 + random.uniform(0.002, 0.01)), 2))
        elif r < 0.9:  # steam down a touch
            return max(1.01,  round(px * (1.0 - random.uniform(0.002, 0.01)), 2))
        else:
            return px

    def _upsert_odds_current(self, mid: str, sid: str, px: float):
        _ensure_odds_current_schema()
        con = _auto()
        try:
            now_s = _utc_iso(_now_utc())
            day = _now_utc().strftime("%Y-%m-%d")
            _q(con, """
            INSERT INTO odds_current(day, marketId, selectionId, ltp, back1, updated_ts)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(day, marketId, selectionId) DO UPDATE SET
              ltp=excluded.ltp, back1=excluded.back1, updated_ts=excluded.updated_ts
            """, (day, str(mid), str(sid), float(px), float(px), now_s))
            con.commit()
        finally:
            con.close()

    def _maybe_place_and_close(self, mid: str, sid: str):
        """
        Occasionally place a matched parent and, later, a matched child to drive P&L tiles.
        """
        now = time.time()
        key = (mid, sid)
        last = self._last_parent_for.get(key, 0.0)
        if now - last < 6.0:
            return
        self._last_parent_for[key] = now

        con = _auto()
        try:
            con.row_factory = sqlite3.Row
            # entry odds from ltp
            row = _q(con, "SELECT ltp FROM odds_current WHERE day=date('now','utc') AND marketId=? AND selectionId=? LIMIT 1",
                     (str(mid), str(sid))).fetchone()
            if not row or row["ltp"] is None:
                return
            E = float(row["ltp"])
            side = "LAY" if E >= 4.0 else "BACK"
            stake = 3.0
            # create PARENT matched row (TEST)
            now_s = _utc_iso(_now_utc())
            cor = f"TEST-{mid}-{sid}-{int(now)}"
            _q(con, """
                INSERT INTO orders(customerOrderRef, mode, marketId, selectionId,
                                   side, entry_odds, entry_stake, entry_status, opened_at, role, source, notes, status)
                VALUES(?, 'TEST', ?, ?, ?, ?, ?, 'matched', ?, 'PARENT', 'A', 'A01', 'matched')
            """, (cor, str(mid), str(sid), side, E, stake, now_s))
            pid = _q(con, "SELECT last_insert_rowid()").fetchone()[0]

            # compute small exit and close after a short delay (async-ish but fine inline)
            ticks = 1
            H = E * (0.99 if side == "BACK" else 1.01)
            S2 = stake
            realized = ( (E-1.0)*stake - (H-1.0)*S2 ) if side=="BACK" else ( S2*(H-1.0) - stake*(E-1.0) )
            realized = round(min(realized, -stake + S2) if side=="BACK" else min(realized, stake - S2), 2)

            _q(con, """
              UPDATE orders
                 SET exit_status='matched',
                     exit_odds=?, exit_stake=?,
                     realized_pnl=?, net_pl=COALESCE(net_pl,0.0)+?,
                     closed_at=?
               WHERE id=?
            """, (float(H), float(S2), float(realized), float(realized), _utc_iso(_now_utc()+timedelta(seconds=2)), int(pid)))
            con.commit()
        finally:
            con.close()

    def tick(self):
        # animate odds + drip P&L
        for mid in self.mids:
            sids = self._sids.get(mid) or []
            base = 6.0
            for idx, sid in enumerate(sids, start=1):
                px0 = base + idx * 0.2
                px = self._odds_walk(px0)
                self._upsert_odds_current(mid, sid, px)
                if random.random() < 0.05:
                    self._maybe_place_and_close(mid, sid)

    def run(self):
        while True:
            self.tick()
            time.sleep(self.hz)

# ─────────────────────────────────────────────────────────────────────────────
# BETS_DB day plan helpers (fix for _markets_today_from_bets missing)
# ─────────────────────────────────────────────────────────────────────────────
def _markets_today_from_bets(con_unused: sqlite3.Connection, day: str) -> list[tuple[str, str, str]]:
    """
    Prefer BETS_DB.markets_schedule for (marketId, course, off_at_utc) on `day`.
    Fallback 1: build from BETS_DB.bets (marketStartTime).
    Fallback 2: use GUI/AUTO schedule (_markets_today_from_gui).
    Returns: [(marketId, course, off_at_utc_iso), ...], ordered by off time.
    """
    rows_out: list[tuple[str, str, str]] = []

    # Try BETS_DB first
    try:
        from engines.config_paths import connect_db
        bdb = connect_db(ro=True)
        try:
            bdb.row_factory = sqlite3.Row
        except Exception:
            pass

        # If markets_schedule exists in bets.db, use it
        has_sched = bool(_q_retry(bdb,
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='markets_schedule'"
        ).fetchone())
        if has_sched:
            rws = _q_retry(bdb, """
                SELECT marketId,
                       COALESCE(venue, course, market_name, '-') AS course,
                       off_at_utc
                FROM markets_schedule
                WHERE date(off_at_utc)=date(?)
                GROUP BY marketId
                ORDER BY datetime(off_at_utc) ASC
            """, (day,)).fetchall()
            if rws:
                rows_out = [(str(r["marketId"]), str(r["course"]), str(r["off_at_utc"])) for r in rws if r["marketId"]]
        # Else: derive from bets table (marketStartTime)
        if not rows_out:
            has_bets = bool(_q_retry(bdb,
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bets'"
            ).fetchone())
            if has_bets:
                rws2 = _q_retry(bdb, """
                    SELECT marketId,
                           COALESCE(event_name, market_name, race_name, '-') AS course,
                           marketStartTime AS off_at_utc
                    FROM bets
                    WHERE date(marketStartTime)=date(?)
                      AND marketId IS NOT NULL
                      AND marketStartTime IS NOT NULL
                    GROUP BY marketId
                    ORDER BY datetime(marketStartTime) ASC
                """, (day,)).fetchall()
                if rws2:
                    rows_out = [(str(r["marketId"]), str(r["course"]), str(r["off_at_utc"])) for r in rws2 if r["marketId"]]
        try:
            bdb.close()
        except Exception:
            pass
    except Exception:
        pass

    # Final fallback: use the AUTO/GUI schedule
    if not rows_out:
        try:
            rows_out = _markets_today_from_gui(con_unused, day)
        except Exception:
            rows_out = []

    return rows_out


# ─────────────────────────────────────────────────────────────────────────────
# PATCH: local SQLite open + retry (no _auto_conn dependency)
# ─────────────────────────────────────────────────────────────────────────────
import sqlite3 as _sqlite
import time as _time
import os as _os
from datetime import datetime, timezone
try:
    from engines.config_paths import autoscalp_db as _adb_path
except Exception:
    def _adb_path():
        # conservative fallback; adjust if your path helper is elsewhere
        return os.path.join(os.path.dirname(__file__), "..", "Data", "autoscalp_gui.db")

def _q_retry(conn: _sqlite.Connection, sql: str, params: tuple = (), tries: int = 6, delay_s: float = 0.08) -> _sqlite.Cursor:
    """
    Execute SQL with simple 'database is locked' backoff. Returns a cursor.
    """
    last = None
    for i in range(max(1, tries)):
        try:
            cur = conn.cursor()
            cur.execute(sql, params)
            return cur
        except _sqlite.OperationalError as e:
            last = e
            if "locked" in str(e).lower() and i < tries - 1:
                _time.sleep(delay_s * (i + 1))
                continue
            raise
    raise last  # pragma: no cover

def _adb(retries: int = 6, delay_s: float = 0.08) -> _sqlite.Connection | None:
    """
    Open autoscalp_gui.db with retries and sane pragmas.
    Returns a sqlite3.Connection or None if we couldn't open after retries.
    """
    try:
        path = _autoscalp_db_path()
    except Exception:
        # last-resort: repo-root /data/autoscalp_gui.db
        _ROOT = _os.path.abspath(_os.path.join(_os.path.dirname(__file__), _os.pardir))
        path = _os.path.join(_ROOT, "data", "autoscalp_gui.db")

    try:
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
    except Exception:
        pass

    last_err = None
    for i in range(max(1, retries)):
        try:
            con = _sqlite.connect(path, timeout=15, isolation_level=None)  # autocommit
            try:
                con.row_factory = _sqlite.Row
            except Exception:
                pass
            # Pragmas via retry helper so we share the same backoff
            try:
                _q_retry(con, "PRAGMA foreign_keys=ON")
                _q_retry(con, "PRAGMA journal_mode=WAL")
                _q_retry(con, "PRAGMA synchronous=NORMAL")
                _q_retry(con, "PRAGMA busy_timeout=12000")
            except Exception:
                pass
            return con
        except _sqlite.OperationalError as e:
            last_err = e
            msg = str(e).lower()
            if (("unable to open database file" in msg) or ("database is locked" in msg)) and i < retries - 1:
                _time.sleep(delay_s * (i + 1))
                continue
            break
        except Exception as e:
            last_err = e
            break

    try:
        print(f"[DDL] _adb open failed: {last_err}")
    except Exception:
        pass
    return None


# ─────────────────────────────────────────────────────────────────────────────
# rate-limit DNS/HTTP error spam per host
_dns_next_log: dict[str, float] = {}
def _warn_throttled(key: str, msg: str, every_s: float = 60.0):
    import time
    now = time.monotonic()
    nxt = _dns_next_log.get(key, 0.0)
    if now >= nxt:
        print(msg)
        _dns_next_log[key] = now + every_s


# keep the existing imports above
from engines.decision_engine import adapters as AD

def _ensure_creds_or_log() -> bool:
    """
    Resolve creds and verify with keep-alive. Returns True if ready; False to exit.
    """
    try:
        ak, ss = _resolve_keys()
    except Exception as e:
        print(f"[FEEDER] creds resolve error: {e}")
        return False

    if not keep_alive_once(ak, ss):
        print("[FEEDER] keep-alive failed — exiting")
        return False

    return True


from engines.decision_engine import adapters as AD
AD.lock_cred_source("DB")   # feeder will not fall back to stale ENV/JSON later


FEEDER_APP_KEY: str | None = None
FEEDER_SESSION: str | None = None

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/dashboard_feeder.py
# 🔎 SEARCH: ^def _resolve_keys\(\) -> tuple\[str, str\]:
def _resolve_keys() -> tuple[str, str]:
    """Unified creds: CLI override > DB > ENV > JSON > legacy."""
    # 0) CLI overrides (set in main)
    ak = globals().get("FEEDER_APP_KEY")
    ss = globals().get("FEEDER_SESSION")
    if ak and ss:
        print(f"[FEEDER] creds source=CLI app={str(ak)[:6]}…{str(ak)[-4:]} sess={str(ss)[:6]}…{str(ss)[-4:]}")
        return str(ak), str(ss)

    # 1) DB (single source of truth)
    try:
        from engines.session_secrets import load_betfair_creds
        ak, ss = load_betfair_creds()
        if ak and ss:
            print(f"[FEEDER] creds source=DB app={ak[:6]}…{ak[-4:]} sess={ss[:6]}…{ss[-4:]}")
            return str(ak), str(ss)
    except Exception:
        pass

    # 2) ENV
    ak = os.environ.get("BETFAIR_APP_KEY")
    ss = os.environ.get("BETFAIR_SESSION")
    if ak and ss:
        print(f"[FEEDER] creds source=ENV app={ak[:6]}…{ak[-4:]} sess={ss[:6]}…{ss[-4:]}")
        return str(ak), str(ss)

    # 3) JSON
    ak2, ss2 = _creds_from_file()
    if ak2 and ss2:
        print(f"[FEEDER] creds source=JSON app={ak2[:6]}…{ak2[-4:]} sess={ss2[:6]}…{ss2[-4:]}")
        return str(ak2), str(ss2)

    # 4) Legacy helper (last resort)
    try:
        from engines.betfair_status import _keys
        ak3, ss3 = _keys()
        if ak3 and ss3:
            print(f"[FEEDER] creds source=LEGACY app={ak3[:6]}…{ak3[-4:]} sess={ss3[:6]}…{ss3[-4:]}")
            return str(ak3), str(ss3)
    except Exception:
        pass

    raise RuntimeError("Betfair credentials not found in CLI, DB, ENV, JSON, or legacy")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def iso(ts: datetime | None) -> str:
    if ts is None: return ""
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")



import sqlite3 as _sqlite
def _exec_retry(con: sqlite3.Connection, sql: str, params=(), tries: int = 6, delay_s: float = 0.08):
    for i in range(max(1, tries)):
        try:
            return _q_retry(con, sql, params)
        except _sqlite.OperationalError as e:
            if "locked" in str(e).lower() and i < tries - 1:
                time.sleep(delay_s * (i + 1))
                continue
            raise
# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/dashboard_feeder.py
# 🔎 SEARCH: ^def _exec_retry\(con: sqlite3.Connection, sql: str,
def _commit_retry(con: sqlite3.Connection, tries: int = 6, delay_s: float = 0.08):
    import sqlite3 as _sqlite, time as _time
    for i in range(max(1, tries)):
        try:
            con.commit()
            return
        except _sqlite.OperationalError as e:
            if "locked" in str(e).lower() and i < tries - 1:
                _time.sleep(delay_s * (i + 1))
                continue
            raise


def _tbl_exists(con: sqlite3.Connection, name: str) -> bool:
    return bool(_q_retry(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

def _creds_from_file() -> tuple[str | None, str | None]:
    """
    Read persisted Betfair creds from data/betfair_creds.json if it exists.
    Returns (app_key, session) or (None, None).
    """
    try:
        # place the file next to autoscalp_gui.db
        from engines.config_paths import autoscalp_db
        base = os.path.dirname(autoscalp_db())
        path = os.path.join(base, "betfair_creds.json")
        if not os.path.exists(path):
            return (None, None)
        with open(path, "r", encoding="utf-8") as f:
            obj = json.load(f)
        ak = obj.get("app_key") or obj.get("application_key")
        ss = obj.get("session") or obj.get("session_token") or obj.get("ssoid")
        return (str(ak) if ak else None, str(ss) if ss else None)
    except Exception:
        return (None, None)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/dashboard_feeder.py (place with other helpers)
# ⛳ INTENT: slope (odds change per minute) & update count as volume proxy
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _flow_and_vol(con: sqlite3.Connection, market_id: str, selection_id: str, day: str, window_sec: int = 60) -> tuple[float, float, str]:
    """
    Compute flow (odds change per minute), a simple volume proxy (update count in the window),
    and a categorical trend over the last `window_sec` seconds for (market_id, selection_id).
    """
    if not _tbl_exists(con, "oc_series"):
        return (0.0, 0.0, "FLAT")

    # Use sqlite's datetime('now', ?) with a bound delta like "-60 seconds"
    delta = f"-{int(max(1, window_sec))} seconds"

    rows = _q_retry(con, "SELECT odd, snapshot_ts FROM oc_series "
        "WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?) "
        "  AND datetime(snapshot_ts) >= datetime('now', ?) "
        "ORDER BY datetime(snapshot_ts) ASC",
        (market_id, selection_id, day, delta)
    ).fetchall()

    if not rows:
        return (0.0, 0.0, "FLAT")

    o0 = float(rows[0]["odd"])
    o1 = float(rows[-1]["odd"])

    # per-minute change in odds (negative = steaming)
    # approximate time span in minutes: use the requested window length
    dt_min = max(window_sec, 1) / 60.0
    slope_per_min = (o1 - o0) / dt_min

    # simple volume proxy: number of observations in the window
    vol = float(len(rows))

    trend = "STEAM" if slope_per_min < -0.05 else ("DRIFT" if slope_per_min > 0.05 else "FLAT")
    return (slope_per_min, vol, trend)



# ─────────────────────────────────────────────────────────────────────────────
# 🧩 PATCH: engines/dashboard_feeder.py — add T0 + concurrent columns + migration
# 📍 TARGET: place near the top-level constants/helpers (after ensure_feeder_tables)
# ─────────────────────────────────────────────────────────────────────────────
# Feeder constants for clock/overlap
CONCURRENT_WINDOW_MIN = 6
END_ASSUMED_SEC = 30 * 60

def _add_column_if_missing(con: sqlite3.Connection, table: str, col: str, ddl: str) -> None:
    have = [r["name"] for r in _q_retry(con, f"PRAGMA table_info({table})")]
    if col not in have:
        _q_retry(con, f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

def ensure_feeder_columns():
    con = _adb()
    if con is None:
        print("[DDL] skipped: ensure_feeder_columns (DB unavailable)")
        return
    try:
        _add_column_if_missing(con, "dashboard_markets", "t0_phase", "TEXT")
        _add_column_if_missing(con, "dashboard_markets", "t0_color", "TEXT")
        _add_column_if_missing(con, "dashboard_markets", "t0_sec", "INTEGER")
        _add_column_if_missing(con, "dashboard_markets", "inplay_start_ts", "TEXT")
        _add_column_if_missing(con, "dashboard_markets", "late_since_ts", "TEXT")
        _add_column_if_missing(con, "dashboard_markets", "is_concurrent", "INTEGER")
        _add_column_if_missing(con, "dashboard_markets", "concurrent_rank", "INTEGER")
        con.commit()
    finally:
        try: con.close()
        except Exception: pass

# ─────────────────────────────────────────────────────────────────────────────
# DDL: feeder tables (idempotent)
# ─────────────────────────────────────────────────────────────────────────────

def ensure_feeder_tables():
    con = _adb()
    if con is None:
        print("[DDL] skipped: could not open autoscalp_gui.db (will retry later)")
        return
    try:
        cur = con.cursor()
        try:
            cur.executescript("""
            CREATE TABLE IF NOT EXISTS dashboard_runs(
              run_id TEXT PRIMARY KEY,
              mode TEXT,
              source_mode TEXT,
              day TEXT,
              speed_x REAL,
              now_ts TEXT,
              status TEXT,
              last_heartbeat_ts TEXT
            );

            CREATE TABLE IF NOT EXISTS dashboard_markets(
              day TEXT,
              marketId TEXT,
              course TEXT,
              market_name TEXT,
              off_at_utc TEXT,
              status TEXT,
              t_minus_sec INTEGER,
              is_next INTEGER,
              runners_total INTEGER,
              source TEXT,
              betfair_status TEXT,
              betfair_status_mapped TEXT,
              betfair_status_age_sec INTEGER,
              status_source TEXT,
              last_api_ok_ts TEXT,
              last_api_err TEXT,
              discovered_ts TEXT,
              last_refreshed_ts TEXT,
              t0_phase TEXT,
              t0_color TEXT,
              t0_sec INTEGER,
              inplay_start_ts TEXT,
              late_since_ts TEXT,
              is_concurrent INTEGER,
              concurrent_rank INTEGER,
              PRIMARY KEY(day, marketId)
            );

            CREATE TABLE IF NOT EXISTS dashboard_runners(
              day TEXT,
              marketId TEXT,
              selectionId TEXT,
              runner_name TEXT,
              odd REAL,
              implied_prob REAL,
              band_low REAL,
              band_high REAL,
              last_snapshot_ts TEXT,
              source TEXT,
              top6_rank INTEGER,
              first_seen_rank INTEGER,
              first_seen_ts TEXT,
              in_top6 INTEGER,
              bar_pre_norm REAL,
              bar_post_norm REAL,
              bar_display_norm REAL,
              bar_color TEXT,
              flow_ppm REAL,
              vol_1m REAL,
              trend TEXT,
              PRIMARY KEY(day, marketId, selectionId)
            );

            CREATE TABLE IF NOT EXISTS dashboard_tiles(
              day TEXT,
              mode TEXT,
              total REAL,
              yesterday REAL,
              today REAL,
              d7 REAL,
              d30 REAL,
              hit_rate REAL,
              pnl_per_hour REAL,
              avg_gain REAL,
              win_pct_mkt REAL,
              bank REAL,
              used_exposure REAL,
              last_refreshed_ts TEXT,
              PRIMARY KEY(day, mode)
            );

            CREATE TABLE IF NOT EXISTS dashboard_orders_tape(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT,
              marketId TEXT,
              selectionId TEXT,
              runner_name TEXT,
              side TEXT,
              status TEXT,
              price REAL,
              amount REAL,
              realized_pnl REAL,
              order_id TEXT,
              mode TEXT,
              day TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_dash_orders_tape_day_ts
              ON dashboard_orders_tape(day, ts DESC);
            """)
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                print("[DDL] locked during execscripts; will retry later")
                return
            raise

        # post-DDL adds (yesterday KPI)
        try:
            _add_column_if_missing(con, "dashboard_tiles", "yesterday", "REAL")
            con.commit()
        except sqlite3.OperationalError as e:
            if "locked" in str(e).lower():
                print("[DDL] commit locked; will retry next tick")
            else:
                raise
    finally:
        try:
            con.close()
        except Exception:
            pass



def _markets_today_from_gui(con: sqlite3.Connection, day: str) -> list[tuple[str, str, str]]:
    """
    Read (marketId, course, off_at_utc) for date(day) from autoscalp_gui.markets_schedule.
    This is the canonical schedule for MIRROR/DIRECT; bets.db is only used for legacy seeding.
    """
    if not _tbl_exists(con, "markets_schedule"):
        return []
    rows = _q_retry(con, "SELECT marketId, COALESCE(venue, course, market_name, '-') AS course, off_at_utc "
        "FROM markets_schedule WHERE date(off_at_utc)=date(?) "
        "GROUP BY marketId ORDER BY datetime(off_at_utc) ASC",
        (day,)
    ).fetchall()
    return [(str(r["marketId"]), str(r["course"]), str(r["off_at_utc"])) for r in rows if r["marketId"]]


# ─────────────────────────────────────────────────────────────────────────────
# Account funds (bank/exposure) – refresh sparingly (e.g., every 60s)
# ─────────────────────────────────────────────────────────────────────────────
_ACCOUNT_URL = "https://api.betfair.com/exchange/account/json-rpc/v1"
_FUNDS_BANK: float = 0.0
_FUNDS_USED: float = 0.0

def _fetch_account_funds(timeout_s: int = 8) -> tuple[float, float]:
    """
    Returns (available_to_bet_balance, exposure).
    """
    try:
        app_key, tok = _resolve_keys()
    except Exception as e:
        print(f"[FUNDS] creds error: {e}")
        return (0.0, 0.0)
    try:
        headers = {
            "X-Application": app_key,
            "X-Authentication": tok,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = [{
            "jsonrpc": "2.0",
            "method": "AccountAPING/v1.0/getAccountFunds",
            "params": {},   # default wallet
            "id": 1
        }]
        r = requests.post(_ACCOUNT_URL, headers=headers, data=json.dumps(payload), timeout=timeout_s)
        r.raise_for_status()
        resp = r.json()[0]
        if "error" in resp:
            print(f"[FUNDS] API error: {resp['error']}")
            return (0.0, 0.0)
        res = resp.get("result") or {}
        bank = float(res.get("availableToBetBalance") or 0.0)
        exposure = float(res.get("exposure") or 0.0)
        return (bank, exposure)
    # in _fetch_account_funds(...)
    except Exception as e:
        _warn_throttled("api.betfair.com", f"[FUNDS] warn: {e}")
        return (0.0, 0.0)



# ─────────────────────────────────────────────────────────────────────────────
# Betfair catalogue (DIRECT) → fill gaps: markets_schedule + runners
# ─────────────────────────────────────────────────────────────────────────────

CATALOGUE_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"

def _iso_z(dt_utc: datetime) -> str:
    return dt_utc.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def _ensure_schedule_and_runner_cols(con: sqlite3.Connection) -> None:
    _q_retry(con, """
        CREATE TABLE IF NOT EXISTS markets_schedule(
          marketId TEXT PRIMARY KEY,
          venue TEXT,
          course TEXT,
          event_name TEXT,
          market_name TEXT,
          off_at_utc TEXT,
          country_code TEXT
        )
    """)
    _add_column_if_missing(con, "markets_schedule", "venue", "TEXT")
    _add_column_if_missing(con, "markets_schedule", "course", "TEXT")
    _add_column_if_missing(con, "markets_schedule", "event_name", "TEXT")
    _add_column_if_missing(con, "markets_schedule", "market_name", "TEXT")
    _add_column_if_missing(con, "markets_schedule", "off_at_utc", "TEXT")
    _add_column_if_missing(con, "markets_schedule", "country_code", "TEXT")

    _q_retry(con, """
        CREATE TABLE IF NOT EXISTS runners(
          marketId TEXT,
          selectionId TEXT,
          runner_name TEXT,
          PRIMARY KEY (marketId, selectionId)
        )
    """)
    _add_column_if_missing(con, "runners", "runner_name", "TEXT")
    con.commit()


def refresh_catalogue_schedule_and_runners(con: sqlite3.Connection, now_utc: datetime,
                                           back_hours: int = 1, fwd_hours: int = 36,
                                           max_results: int = 1000) -> None:
    """
    Fetch listMarketCatalogue to fill off times (off_at_utc), course/venue, and runner names.
    Leaves odds to MIRROR (oc_series/inbound_oc_cache). Safe to call periodically.
    """
    
    _ensure_schedule_and_runner_cols(con)

    frm = _iso_z(now_utc - timedelta(hours=back_hours))
    to  = _iso_z(now_utc + timedelta(hours=fwd_hours))

    app_key, tok = _resolve_keys()
    headers = {
        "X-Application": app_key,
        "X-Authentication": tok,
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    params = {
        "filter": {
            "eventTypeIds": ["7"],                 # Horse Racing
            "marketStartTime": {"from": frm, "to": to},
            "marketCountries": ["GB", "IE"]        # 🇬🇧 + 🇮🇪 only
            # (optionally add "marketTypeCodes": ["WIN"] later if you want)
        },
        "maxResults": max_results,
        "marketProjection": ["MARKET_START_TIME", "RUNNER_DESCRIPTION", "EVENT"]
    }

    payload = [{"jsonrpc": "2.0", "method": "SportsAPING/v1.0/listMarketCatalogue", "params": params, "id": 1}]

    try:
        r = requests.post(CATALOGUE_URL, headers=headers, data=json.dumps(payload), timeout=10)
        r.raise_for_status()
        resp = r.json()[0]
        if "error" in resp:
            raise RuntimeError(str(resp["error"]))
        cats = resp.get("result", []) or []
    # in refresh_catalogue_schedule_and_runners(...)
    except Exception as e:
        _warn_throttled("api.betfair.com", f"[CATALOGUE] warn: {e}")
        return


    # Upserts
    for mc in cats:
        mid = str(mc.get("marketId") or "")
        if not mid:
            continue
        mst = mc.get("marketStartTime")  # e.g., "2025-08-26T14:05:00.000Z"
        # normalize to "YYYY-MM-DDTHH:MM:SSZ"
        off_at = None
        if mst:
            try:
                off_at = mst.replace(".000Z", "Z")
                if off_at.endswith("Z") and len(off_at) == 20:
                    pass
                else:
                    # last resort: parse to Z
                    off_at = _iso_z(datetime.fromisoformat(mst.replace("Z","+00:00")).astimezone(timezone.utc))
            except Exception:
                off_at = None

        ev = mc.get("event") or {}
        venue = ev.get("venue")
        event_name = ev.get("name")
        market_name = mc.get("marketName")
        country = (ev.get("countryCode") or "").strip() or None
        course = venue or None

        _q_retry(con, """
            INSERT INTO markets_schedule(marketId, venue, course, event_name, market_name, off_at_utc, country_code)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(marketId) DO UPDATE SET
              venue=COALESCE(excluded.venue, markets_schedule.venue),
              course=COALESCE(excluded.course, markets_schedule.course),
              event_name=COALESCE(excluded.event_name, markets_schedule.event_name),
              market_name=COALESCE(excluded.market_name, markets_schedule.market_name),
              off_at_utc=COALESCE(excluded.off_at_utc, markets_schedule.off_at_utc),
              country_code=COALESCE(excluded.country_code, markets_schedule.country_code)
        """, (mid, venue, course, event_name, market_name, off_at, country))


        # runners
        for rd in (mc.get("runners") or []):
            sel_id = str(rd.get("selectionId"))
            rname = rd.get("runnerName")
            if not sel_id:
                continue
            _q_retry(con, """
                INSERT INTO runners(marketId, selectionId, runner_name)
                VALUES(?,?,?)
                ON CONFLICT(marketId, selectionId) DO UPDATE SET
                  runner_name=COALESCE(excluded.runner_name, runners.runner_name)
            """, (mid, sel_id, rname))

    con.commit()
    print(f"[CATALOGUE] upserted {len(cats)} markets "
          f"({sum(len(mc.get('runners') or []) for mc in cats)} runners)")


# ─────────────────────────────────────────────────────────────────────────────
# Sources (MIRROR = DB readers; DIRECT = Betfair API for schedule/odds)
# ─────────────────────────────────────────────────────────────────────────────

def _markets_today_from_schedule(con: sqlite3.Connection, day: str) -> List[sqlite3.Row]:
    if not _tbl_exists(con, "markets_schedule"):
        return []
    return _q_retry(con, "SELECT marketId, COALESCE(venue,course,market_name,'-') AS course, off_at_utc "
        "FROM markets_schedule WHERE date(off_at_utc)=date(?) ORDER BY datetime(off_at_utc) ASC", (day,)
    ).fetchall()

def _markets_today_from_inbound(con: sqlite3.Connection, day: str) -> List[str]:
    if not _tbl_exists(con, "inbound_oc_cache"):
        return []
    rows = _q_retry(con, "SELECT DISTINCT marketId FROM inbound_oc_cache WHERE date(last_sync_ts)=date(?) ORDER BY id ASC", (day,)
    ).fetchall()
    return [r["marketId"] for r in rows]

def _latest_series_for_market(con_unused: sqlite3.Connection, market_id: str, day: str) -> Dict[str, sqlite3.Row]:
    """
    Return latest oc_series row per selectionId for given market/day from BETS_DB (not autoscalp_gui.db).
    """
    out: Dict[str, sqlite3.Row] = {}
    try:
        from engines.config_paths import connect_db
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    except Exception:
        return out
    try:
        rows = _q_retry(bdb, "SELECT selectionId, odd, band_low, band_high, snapshot_ts "
            "FROM oc_series WHERE marketId=? AND date(snapshot_ts)=date(?) "
            "ORDER BY datetime(snapshot_ts) ASC",
            (market_id, day)
        ).fetchall()
        for r in rows:
            out[str(r["selectionId"])] = r
    finally:
        try: bdb.close()
        except Exception: pass
    return out


def _latest_inbound_for_market(con_unused: sqlite3.Connection, market_id: str, day: str) -> Dict[str, sqlite3.Row]:
    """
    Return latest inbound_oc_cache row per selectionId for market/day, from BETS_DB.
    """
    out: Dict[str, sqlite3.Row] = {}
    try:
        from engines.config_paths import connect_db
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    except Exception:
        return out
    try:
        rows = _q_retry(bdb, "SELECT t.selectionId, t.anchor_odd, t.last_sync_ts FROM inbound_oc_cache t "
            "JOIN (SELECT selectionId, MAX(id) AS max_id FROM inbound_oc_cache "
            "      WHERE marketId=? AND date(last_sync_ts)=date(?) GROUP BY selectionId) x "
            "ON x.max_id = t.id ORDER BY t.selectionId ASC",
            (market_id, day)
        ).fetchall()
        for r in rows:
            out[str(r['selectionId'])] = r
    finally:
        try: bdb.close()
        except Exception: pass
    return out



def _runner_name(con: sqlite3.Connection, market_id: str, selection_id: str) -> str:
    """
    Best-effort runner name lookup for (marketId, selectionId).
    We only reference columns that actually exist to avoid sqlite 'no such column' errors.
    """
    if not _tbl_exists(con, "runners"):
        return str(selection_id)

    # Discover available columns
    cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(runners)")]
    # Preference order for name-like columns
    for col in ("runner_name", "selectionName", "name"):
        if col in cols:
            row = _q_retry(con, f"SELECT {col} AS nm FROM runners WHERE marketId=? AND selectionId=? LIMIT 1",
                (market_id, selection_id)
            ).fetchone()
            if row and row["nm"]:
                return str(row["nm"])
    # Fallback: just show the selectionId
    return str(selection_id)
# ─────────────────────────────────────────────────────────────────────────────
# DIRECT odds fallback: best offers from Betfair for a single market
# ─────────────────────────────────────────────────────────────────────────────
def _fetch_best_offers_for_market(market_id: str, timeout_s: int = 8) -> Dict[str, float]:
    """
    Return {selectionId(str): odd(float)} for current best lay/back.
    Uses listMarketBook with EX_BEST_OFFERS.
    """
    try:
        app_key, tok = _resolve_keys()
    except Exception as e:
        print(f"[DIRECT-ODDS] cannot resolve creds: {e}")
        return {}

    try:
        headers = {
            "X-Application": app_key,
            "X-Authentication": tok,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = [{
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {
                "marketIds": [str(market_id)],
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True}
            },
            "id": 1
        }]
        r = requests.post(CATALOGUE_URL, headers=headers, data=json.dumps(payload), timeout=timeout_s)
        r.raise_for_status()
        resp = r.json()[0]
        if "error" in resp:
            print(f"[DIRECT-ODDS] API error: {resp['error']}")
            return {}
        books = resp.get("result") or []
        if not books:
            return {}
        runners = books[0].get("runners") or []
        out: Dict[str, float] = {}
        for ru in runners:
            sid = str(ru.get("selectionId"))
            ex = ru.get("ex") or {}
            lays = ex.get("availableToLay") or []
            backs = ex.get("availableToBack") or []
            # prefer lay (visuals/bars use lay), else back
            price = None
            if lays: price = lays[0].get("price")
            if price is None and backs: price = backs[0].get("price")
            if isinstance(price, (int, float)) and price > 0:
                out[sid] = float(price)
        return out
    except Exception as e:
        _warn_throttled("api.betfair.com", f"[DIRECT-ODDS] warn: {e}")
        return {}

# ─────────────────────────────────────────────────────────────────────────────
# Feeder core
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FeederCfg:
    mode: str            # LIVE | REPLAY | TEST
    source_mode: str     # MIRROR | DIRECT
    day: str             # YYYY-MM-DD
    speed_x: float       # 1.0 live; ignored here but recorded
    tick_sec: float      # loop sleep
    window_hours: int    # for activity block

def map_status_from_betfair(raw_phase: str, book_status: Optional[str]=None) -> str:
    # Your helper returns PRE|OFF; treat OFF as IN_PLAY minimally.
    rp = (raw_phase or "").upper()
    if rp == "PRE":
        return "PRE"
    if rp == "OFF":
        # If you later add book_status == CLOSED, map to ENDED.
        return "IN_PLAY"
    return "UNKNOWN"

# ─────────────────────────────────────────────────────────────────────────────
# 🧩 PATCH: engines/dashboard_feeder.py — choose primary & concurrent and compute T0
# 📍 TARGET: replace choose_next_market(...) with choose_primary_and_concurrent(...)
# 🔎 SEARCH: def choose_next_market(
# ─────────────────────────────────────────────────────────────────────────────
def choose_primary_and_concurrent(rows: List[Tuple[str, str, Optional[str]]], now_utc: datetime
                                  ) -> Tuple[Optional[Tuple[str,str,Optional[str]]],
                                             Optional[Tuple[str,str,Optional[str]]]]:
    with_off, without_off = [], []
    for mid, course, off in rows:
        if off:
            try:
                off_dt = datetime.fromisoformat(off.replace("Z","+00:00")).astimezone(timezone.utc)
            except Exception:
                off_dt = None
            with_off.append((mid, course, off, off_dt))
        else:
            without_off.append((mid, course, off, None))

    with_off = [r for r in with_off if r[3] is not None]
    with_off.sort(key=lambda x: x[3])

    primary = None
    for r in with_off:
        if r[3] and r[3] >= now_utc:
            primary = (r[0], r[1], r[2]); break
    if primary is None and with_off:
        primary = (with_off[0][0], with_off[0][1], with_off[0][2])
    if primary is None and rows:
        primary = rows[0]

    secondary = None
    if primary:
        p_off_dt = None
        for r in with_off:
            if r[0] == primary[0]:
                p_off_dt = r[3]; break
        if p_off_dt:
            window = timedelta(minutes=CONCURRENT_WINDOW_MIN)
            candidates = [r for r in with_off if r[0] != primary[0] and r[3] and abs(r[3] - p_off_dt) <= window]
            if candidates:
                c = candidates[0]
                secondary = (c[0], c[1], c[2])

    return primary, secondary

def upsert_dashboard_kpis(con: sqlite3.Connection, mode: str, day: str):
    """
    Today-only actionable KPIs (robust to schema variants):
      - parents_* and children_* by entry_status
      - open_parents: parents with NO matched child (requires hedge link col)
      - open_exposure: sum(entry_liability) for open parents (if present)
      - realized_today: sum(net_pl/realized_pnl) of parents matched today
    Writes into dashboard_tiles(day, mode).  Reuses 'today' to store realized_today.
    """
    def _has(tbl, col):
        try:
            return any(r["name"] == col for r in _q_retry(con, f"PRAGMA table_info({tbl})").fetchall())
        except Exception:
            return False

    cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)").fetchall()]
    has_mode = ("mode" in cols)
    link     = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)

    # --- helpers -------------------------------------------------------------
    def _count(sql, args=()):
        return int(_q_retry(con, sql, args).fetchone()[0] or 0)

    def _parent_pred(alias: str) -> str:
        # Prefer link-based parent test; if missing, fall back to all rows
        if link:
            return f"({alias}.{link} IS NULL OR {alias}.{link}='')"
        return "1=1"

    def _child_pred(alias: str) -> str:
        if link:
            return f"({alias}.{link} IS NOT NULL AND {alias}.{link}<>'')"
        return "0=1"  # can't separate without link; treat as none

    # --- counts by entry_status (queued/placed/matched) ----------------------
    def _count_status(pred_sql: str, status: str) -> int:
        sql = (f"SELECT COUNT(*) FROM orders o WHERE {pred_sql} "
               f"AND date(o.opened_at)=date(?) AND UPPER(COALESCE(o.entry_status,''))=UPPER(?)")
        args = (day, status)
        if has_mode:
            sql += " AND UPPER(COALESCE(o.mode,''))=UPPER(?)"; args += (mode.upper(),)
        return _count(sql, args)

    parents_queued  = _count_status(_parent_pred("o"), "queued")
    parents_placed  = _count_status(_parent_pred("o"), "placed")
    parents_matched = _count_status(_parent_pred("o"), "matched")

    children_queued  = _count_status(_child_pred("o"), "queued")
    children_placed  = _count_status(_child_pred("o"), "placed")
    children_matched = _count_status(_child_pred("o"), "matched")

    # --- open parents (requires link) ----------------------------------------
    if link:
        sql_open = (
          f"SELECT COUNT(*) FROM orders p WHERE date(p.opened_at)=date(?) "
          f"AND {_parent_pred('p')} "
          "AND NOT EXISTS (SELECT 1 FROM orders c "
          f"                 WHERE c.{link}=p.id AND UPPER(COALESCE(c.entry_status,''))='MATCHED')"
        )
        args_open = (day,)
        if has_mode: sql_open += " AND UPPER(COALESCE(p.mode,''))=UPPER(?)"; args_open += (mode.upper(),)
        open_parents = _count(sql_open, args_open)
    else:
        open_parents = 0

    # --- open exposure (sum entry_liability of open parents) -----------------
    open_exposure = 0.0
    if link and _has("orders","entry_liability"):
        sql_ex = (
          f"SELECT COALESCE(SUM(p.entry_liability),0.0) "
          f"FROM orders p WHERE date(p.opened_at)=date(?) AND {_parent_pred('p')} "
          "AND NOT EXISTS (SELECT 1 FROM orders c "
          f"                 WHERE c.{link}=p.id AND UPPER(COALESCE(c.entry_status,''))='MATCHED')"
        )
        args_ex = (day,)
        if has_mode: sql_ex += " AND UPPER(COALESCE(p.mode,''))=UPPER(?)"; args_ex += (mode.upper(),)
        open_exposure = float(_q_retry(con, sql_ex, args_ex).fetchone()[0] or 0.0)

    # --- realized today (sum P&L of matched parents closed today) -----------
    pnl_col = "net_pl" if _has("orders","net_pl") else ("realized_pnl" if _has("orders","realized_pnl") else None)
    realized_today = 0.0
    if pnl_col and _has("orders","closed_at"):
        sql_real = (f"SELECT COALESCE(SUM(o.{pnl_col}),0.0) "
                    f"FROM orders o WHERE {_parent_pred('o')} AND o.exit_status='matched' AND date(o.closed_at)=date(?)")
        args_real = (day,)
        if has_mode: sql_real += " AND UPPER(COALESCE(o.mode,''))=UPPER(?)"; args_real += (mode.upper(),)
        realized_today = float(_q_retry(con, sql_real, args_real).fetchone()[0] or 0.0)

    # --- upsert tiles --------------------------------------------------------
    _q_retry(con, """
        CREATE TABLE IF NOT EXISTS dashboard_tiles(
          day TEXT, mode TEXT,
          total REAL, today REAL, d7 REAL, d30 REAL,
          hit_rate REAL, pnl_per_hour REAL, avg_gain REAL, win_pct_mkt REAL,
          bank REAL, used_exposure REAL, last_refreshed_ts TEXT,
          PRIMARY KEY(day, mode)
        )
    """)
    _q_retry(con, """
        INSERT INTO dashboard_tiles(day, mode, total, today, d7, d30, hit_rate, pnl_per_hour, avg_gain, win_pct_mkt,
                                    bank, used_exposure, last_refreshed_ts)
        VALUES(date('now'), ?, 0.0, ?, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0,
               0.0, ?, datetime('now'))
        ON CONFLICT(day, mode) DO UPDATE SET
          today=excluded.today,
          used_exposure=excluded.used_exposure,
          last_refreshed_ts=excluded.last_refreshed_ts
    """, (mode.upper(), realized_today, open_exposure))

    print(f"[KPI] P(par/chi) q/p/m = {parents_queued}/{parents_placed}/{parents_matched} | "
          f"{children_queued}/{children_placed}/{children_matched} | "
          f"open={open_parents} exp=£{open_exposure:.2f} realized=£{realized_today:.2f}")



# ─────────────────────────────────────────────────────────────────────────────
# 🧩 PATCH: engines/dashboard_feeder.py — upsert_dashboard_markets with T0 + concurrent
# 📍 TARGET: replace the entire upsert_dashboard_markets(...) function
# 🔎 SEARCH: def upsert_dashboard_markets(con: sqlite3.Connection, cfg: FeederCfg, now_utc: datetime) -> Optional[str]:
# ─────────────────────────────────────────────────────────────────────────────
def upsert_dashboard_markets(con: sqlite3.Connection, cfg: FeederCfg, now_utc: datetime) -> Optional[str]:
    day = cfg.day

    # Build candidate markets: prefer GUI schedule, else inbound presence
    markets: List[Tuple[str, str, Optional[str]]] = []
    schedule = _markets_today_from_gui(con, day)
    if schedule:
        markets = schedule
        source = "SCHEDULE"
    else:
        mids = _markets_today_from_inbound(con, day)
        markets = [(m, "-", None) for m in mids]
        source = "INBOUND"

    # Keep only markets within ±36h window (or those without an off time)
    pruned: List[Tuple[str, str, Optional[str]]] = []
    for (mid, course, off_at) in markets:
        if not off_at:
            pruned.append((mid, course, off_at))
            continue
        try:
            off_dt = datetime.fromisoformat(off_at.replace("Z", "+00:00")).astimezone(timezone.utc)
            if abs((off_dt - now_utc).total_seconds()) <= 36 * 3600:
                pruned.append((mid, course, off_at))
        except Exception:
            # If we can’t parse, keep it — downstream code handles None/unknown safely
            pruned.append((mid, course, off_at))
    markets = pruned

    if not markets:
        return None

    # Choose primary (next) and concurrent (within window) markets
    primary, concurrent = choose_primary_and_concurrent(markets, now_utc)
    next_mid = primary[0] if primary else None
    sec_mid  = concurrent[0] if concurrent else None

    # Clear day flags before writing fresh rows
    _exec_retry(con, "UPDATE dashboard_markets SET is_next=0, is_concurrent=0, concurrent_rank=NULL WHERE day=?", (day,))

    # Upsert each market’s row + T0 fields
    for idx, (mid, course, off_at) in enumerate(markets[:50]):
        now = now_utc
        try:
            off_dt = datetime.fromisoformat(off_at.replace("Z", "+00:00")).astimezone(timezone.utc) if off_at else None
        except Exception:
            off_dt = None

        t_minus = int((off_dt - now).total_seconds()) if off_dt is not None else None

        # External phase mapping (best-effort)
        bf_raw = None
        mapped = "UNKNOWN"
        try:
            bf_raw = get_or_update_phase(mid)
            mapped = map_status_from_betfair(bf_raw)
        except Exception:
            mapped = "UNKNOWN"

        # T0 phase/seconds (PRE / WAIT_INPLAY / IN_PLAY / UNKNOWN)
        if off_dt is None:
            t0_phase, t0_sec = "UNKNOWN", None
        else:
            if now < off_dt:
                t0_phase = "PRE"
                t0_sec   = int(max(0, (off_dt - now).total_seconds()))
            else:
                t0_phase = "IN_PLAY" if mapped == "IN_PLAY" else "WAIT_INPLAY"
                t0_sec   = int(max(0, (now - off_dt).total_seconds()))

        t0_color = "BLUE" if t0_phase == "PRE" else ("GREEN" if t0_phase == "IN_PLAY" else "RED")

        is_next   = 1 if (next_mid and mid == next_mid) else 0
        is_conc   = 1 if (sec_mid and mid == sec_mid) else 0
        conc_rank = 1 if is_conc else None

        _exec_retry(con, """
            INSERT INTO dashboard_markets(
              day, marketId, course, market_name, off_at_utc, status, t_minus_sec,
              is_next, runners_total, source, betfair_status, betfair_status_mapped,
              betfair_status_age_sec, status_source, last_api_ok_ts, last_api_err,
              discovered_ts, last_refreshed_ts,
              t0_phase, t0_color, t0_sec, inplay_start_ts, late_since_ts,
              is_concurrent, concurrent_rank
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(day, marketId) DO UPDATE SET
              course=excluded.course,
              off_at_utc=excluded.off_at_utc,
              t_minus_sec=excluded.t_minus_sec,
              is_next=excluded.is_next,
              source=excluded.source,
              last_refreshed_ts=excluded.last_refreshed_ts,
              t0_phase=excluded.t0_phase,
              t0_color=excluded.t0_color,
              t0_sec=excluded.t0_sec,
              is_concurrent=excluded.is_concurrent,
              concurrent_rank=excluded.concurrent_rank
        """, (
            day, mid, course, None, off_at, "PRE", t_minus,
            is_next, None, source, bf_raw, mapped,
            None, source, None, None,
            iso(now), iso(now),
            t0_phase, t0_color, t0_sec, None, None,
            is_conc, conc_rank
        ))

    _commit_retry(con)
    return next_mid


def upsert_dashboard_runners(con: sqlite3.Connection, cfg: FeederCfg, now_utc: datetime, market_id: Optional[str]):
    if not market_id:
        return

    day = cfg.day
    rows: Dict[str, Dict] = {}

    # Prefer MIRROR oc_series; fallback to inbound anchors
    latest = _latest_series_for_market(con, market_id, day)
    fallback = _latest_inbound_for_market(con, market_id, day) if not latest else {}

    def _latest_ts(d: Dict[str, Dict]) -> datetime | None:
        try:
            ts = max(
                datetime.fromisoformat(v["snapshot_ts"].replace("Z", "+00:00"))
                for v in d.values() if v.get("snapshot_ts")
            )
            return ts.astimezone(timezone.utc)
        except Exception:
            return None

    # If MIRROR exists but looks stale, try DIRECT odds once
    stale = False
    if latest:
        try:
            lt = _latest_ts({k: {"snapshot_ts": v["snapshot_ts"]} for k, v in latest.items() if "snapshot_ts" in v})
            if lt and (now_utc - lt).total_seconds() > 90:
                stale = True
        except Exception:
            stale = False

    if stale and FEEDER_ENABLE_DIRECT_ODDS and not rows:
        direct = _fetch_best_offers_for_market(market_id)
        if direct:
            ts_now = iso(now_utc)
            for sid, price in direct.items():
                rows[str(sid)] = {
                    "odd": float(price),
                    "band_low": None,
                    "band_high": None,
                    "ts": ts_now,
                    "source": "DIRECT_ODDS",
                }

    # Build rows from preferred data source
    if latest:
        for sid, r in latest.items():
            rows[str(sid)] = {
                "odd": float(r["odd"]) if r["odd"] is not None else math.nan,
                "band_low": r.get("band_low"),
                "band_high": r.get("band_high"),
                "ts": r.get("snapshot_ts"),
                "source": "OC_SERIES",
            }
    elif fallback:
        for sid, r in fallback.items():
            rows[str(sid)] = {
                "odd": float(r["anchor_odd"]) if r["anchor_odd"] is not None else math.nan,
                "band_low": None,
                "band_high": None,
                "ts": r.get("last_sync_ts"),
                "source": "INBOUND",
            }
    else:
        # No MIRROR; optionally use DIRECT odds for today only
        if not FEEDER_ENABLE_DIRECT_ODDS:
            return
        off_row = _q_retry(con, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (market_id,)).fetchone()
        is_today = False
        if off_row and off_row["off_at_utc"]:
            try:
                off_dt = datetime.fromisoformat(str(off_row["off_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc)
                is_today = (off_dt.date().isoformat() == day)
            except Exception:
                is_today = False
        direct = _fetch_best_offers_for_market(market_id) if is_today else {}
        if direct:
            ts_now = iso(now_utc)
            for sid, price in direct.items():
                rows[str(sid)] = {
                    "odd": float(price),
                    "band_low": None,
                    "band_high": None,
                    "ts": ts_now,
                    "source": "DIRECT_ODDS",
                }
        else:
            return

    # ---- Compute top-6 + bars ------------------------------------------------
    valid = [(sid, r["odd"]) for sid, r in rows.items() if r["odd"] and r["odd"] > 0]
    valid.sort(key=lambda x: float(x[1]))  # lowest odds first
    top6 = [sid for sid, _ in valid[:6]]

    max_ip_top6 = 0.0
    for sid in top6:
        try:
            ip = 1.0 / float(rows[sid]["odd"])
            if ip > max_ip_top6:
                max_ip_top6 = ip
        except Exception:
            pass

    max_ip_field = 0.0
    for _, v in valid:
        try:
            ip = 1.0 / float(v)
            if ip > max_ip_field:
                max_ip_field = ip
        except Exception:
            pass

    # Determine color from market status / phase
    mrow = _q_retry(
        con,
        "SELECT betfair_status_mapped, t0_phase FROM dashboard_markets WHERE day=? AND marketId=?",
        (day, market_id),
    ).fetchone()
    mstatus = (mrow["betfair_status_mapped"] if mrow else None)
    t0 = ((mrow["t0_phase"] or "") if mrow else "").upper()

    if isinstance(mstatus, str) and mstatus.upper() in ("PRE", "IN_PLAY"):
        color = "BLUE" if mstatus.upper() == "PRE" else "GREEN"
    elif t0 in ("PRE", "WAIT_INPLAY"):
        color = "BLUE"
    elif t0 == "IN_PLAY":
        color = "GREEN"
    else:
        color = "BLUE"

    for rank, sid in enumerate(top6, start=1):
        r = rows[sid]
        odd = float(r["odd"])
        ip = (1.0 / odd) if odd > 0 else 0.0
        pre_norm = (ip / max_ip_top6) if max_ip_top6 > 0 else 0.0
        post_norm = (ip / max_ip_field) if max_ip_field > 0 else 0.0
        bar_display = pre_norm if (isinstance(mstatus, str) and mstatus.upper() == "PRE") or (t0 in ("PRE", "WAIT_INPLAY")) else post_norm
        name = _runner_name(con, market_id, sid)
        flow_ppm, vol_1m, trend = _flow_and_vol(con, market_id, sid, day)

        _q_retry(
            con,
            """
            INSERT INTO dashboard_runners(
              day, marketId, selectionId, runner_name, odd, implied_prob,
              band_low, band_high, last_snapshot_ts, source,
              top6_rank, first_seen_rank, first_seen_ts, in_top6,
              bar_pre_norm, bar_post_norm, bar_display_norm, bar_color,
              flow_ppm, vol_1m, trend
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(day, marketId, selectionId) DO UPDATE SET
              runner_name=excluded.runner_name,
              odd=excluded.odd,
              implied_prob=excluded.implied_prob,
              band_low=excluded.band_low,
              band_high=excluded.band_high,
              last_snapshot_ts=excluded.last_snapshot_ts,
              source=excluded.source,
              top6_rank=excluded.top6_rank,
              in_top6=excluded.in_top6,
              bar_pre_norm=excluded.bar_pre_norm,
              bar_post_norm=excluded.bar_post_norm,
              bar_display_norm=excluded.bar_display_norm,
              bar_color=excluded.bar_color,
              flow_ppm=excluded.flow_ppm,
              vol_1m=excluded.vol_1m,
              trend=excluded.trend
            """,
            (
                day, market_id, sid, name, odd, ip,
                r["band_low"], r["band_high"], r["ts"], r["source"],
                rank, rank, iso(now_utc), 1,
                pre_norm, post_norm, bar_display, color,
                flow_ppm, vol_1m, trend,
            ),
        )

    print(f"[RUNNERS] upserted top6={len(top6)} for {market_id} (day={day})", flush=True)
    con.commit()

    # ---- Window activity snapshot (optional dashboard_activity row) ----------
    win_start = (now_utc - timedelta(hours=cfg.window_hours)).strftime("%Y-%m-%d %H:%M:%S")
    out = dict(
        runners_scanned=0, decisions=0, proposals=0,
        parents_placed=0, hedges_matched=0, cancels=0, timeouts=0,
        open_parents=0, recent_events_json="[]", gate_reasons_json="[]",
    )

    # decisions
    if _tbl_exists(con, "decisions"):
        cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(decisions)").fetchall()]
        ts_col = "decided_at" if "decided_at" in cols else ("opened_at" if "opened_at" in cols else None)
        if ts_col:
            out["decisions"] = int(
                _q_retry(con, f"SELECT COUNT(*) FROM decisions WHERE datetime({ts_col})>=datetime(?)", (win_start,)).fetchone()[0] or 0
            )
            out["runners_scanned"] = int(
                _q_retry(con, f"SELECT COUNT(DISTINCT marketId||':'||selectionId) FROM decisions WHERE datetime({ts_col})>=datetime(?)", (win_start,)).fetchone()[0] or 0
            )
            if "blueprint_match" in cols:
                out["proposals"] = int(
                    _q_retry(con, f"SELECT COUNT(*) FROM decisions WHERE datetime({ts_col})>=datetime(?) AND blueprint_match IS NOT NULL", (win_start,)).fetchone()[0] or 0
                )

    # orders (window-scoped)
    if _tbl_exists(con, "orders"):
        cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)").fetchall()]
        have_opened = "opened_at" in cols
        have_closed = "closed_at" in cols

        if have_opened:
            out["parents_placed"] = int(
                _q_retry(con, "SELECT COUNT(*) FROM orders WHERE datetime(opened_at)>=datetime(?)", (win_start,)).fetchone()[0] or 0
            )
        if have_closed:
            out["hedges_matched"] = int(
                _q_retry(con, "SELECT COUNT(*) FROM orders WHERE exit_status='matched' AND datetime(closed_at)>=datetime(?)", (win_start,)).fetchone()[0] or 0
            )

        out["open_parents"] = int(
            _q_retry(
                con,
                "SELECT COUNT(*) FROM orders "
                "WHERE entry_status='matched' AND (exit_status IS NULL OR exit_status<>'matched') "
                "AND ( (opened_at IS NOT NULL AND datetime(opened_at)>=datetime(?)) "
                "   OR (closed_at IS NOT NULL AND datetime(closed_at)>=datetime(?)) )",
                (win_start, win_start),
            ).fetchone()[0] or 0
        )
        out["cancels"] = int(
            _q_retry(
                con,
                "SELECT COUNT(*) FROM orders WHERE (entry_status='cancelled' OR exit_status='cancelled') "
                "AND ( (opened_at IS NOT NULL AND datetime(opened_at)>=datetime(?)) "
                "   OR (closed_at IS NOT NULL AND datetime(closed_at)>=datetime(?)) )",
                (win_start, win_start),
            ).fetchone()[0] or 0
        )

    # events (recent) + gate reasons
    if _tbl_exists(con, "events"):
        ev = _q_retry(
            con,
            "SELECT ts, source, message FROM events "
            "WHERE datetime(ts) >= datetime(?) ORDER BY datetime(ts) DESC LIMIT 200",
            (win_start,),
        ).fetchall()
        out["recent_events_json"] = json.dumps([[str(r["ts"]), str(r["source"]), str(r["message"])] for r in ev])

        raw_counts: Dict[str, int] = {}
        for r in ev:
            msg = (r["message"] or "")
            if "gate block |" in msg:
                seg = msg.split("gate block |", 1)[1].strip()
                key = seg.split()[0].strip().lower().strip("|")
                # normalize common variants
                if key in ("interactive-odds", "inactive-odd", "inactive"):
                    key = "inactive-odds"
                if key in ("run", "run_active"):
                    key = "run-active"
                if key in ("run_passive",):
                    key = "run-passive"
                raw_counts[key] = raw_counts.get(key, 0) + 1

        label_map = {
            "too_early": "Too early (TTO > 20m)",
            "inactive-odds": "Inactive odds (no tape/liquidity)",
            "run-active": "Run (active)",
            "run-passive": "Run (passive)",
            "too_late": "Too late / missed window",
            "fav_steam_block": "Fav steaming – LAY blocked",
            "mild_steam_downsized": "Fav mildly shortening – downsized",
        }
        labelled = [[label_map.get(k, k.replace("_", " ").title()), int(v)] for k, v in raw_counts.items()]
        labelled.sort(key=lambda x: (-x[1], x[0]))
        out["gate_reasons_json"] = json.dumps(labelled)

    # Upsert into dashboard_activity (guarded DDL; fixes prior syntax/column errors)
    _q_retry(con, """
        CREATE TABLE IF NOT EXISTS dashboard_activity(
            day TEXT PRIMARY KEY,
            window_start_ts TEXT,
            runners_scanned INTEGER,
            decisions INTEGER,
            proposals INTEGER,
            parents_placed INTEGER,
            hedges_matched INTEGER,
            cancels INTEGER,
            timeouts INTEGER,
            open_parents INTEGER,
            recent_events_json TEXT,
            gate_reasons_json TEXT,
            last_refreshed_ts TEXT
        )
    """)
    _q_retry(
        con,
        """
        INSERT INTO dashboard_activity(
          day, window_start_ts, runners_scanned, decisions, proposals,
          parents_placed, hedges_matched, cancels, timeouts, open_parents,
          recent_events_json, gate_reasons_json, last_refreshed_ts
        )
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(day) DO UPDATE SET
          window_start_ts     = excluded.window_start_ts,
          runners_scanned     = excluded.runners_scanned,
          decisions           = excluded.decisions,
          proposals           = excluded.proposals,
          parents_placed      = excluded.parents_placed,
          hedges_matched      = excluded.hedges_matched,
          cancels             = excluded.cancels,
          timeouts            = excluded.timeouts,
          open_parents        = excluded.open_parents,
          recent_events_json  = excluded.recent_events_json,
          gate_reasons_json   = excluded.gate_reasons_json,
          last_refreshed_ts   = excluded.last_refreshed_ts
        """,
        (
            day, win_start, out["runners_scanned"], out["decisions"], out["proposals"],
            out["parents_placed"], out["hedges_matched"], out["cancels"], out["timeouts"],
            out["open_parents"], out["recent_events_json"], out["gate_reasons_json"], iso(utcnow()),
        ),
    )
    con.commit()


def upsert_dashboard_tiles(con: sqlite3.Connection, cfg: FeederCfg, now_utc: datetime):
    """
    KPIs for the panel. All metrics are filtered by mode (e.g., LIVE).
    total   := SUM(net_pl or realized_pnl) for matched exits in this mode (all time)
    yesterday/today/d7/d30 := date-scoped sums in this mode
    win_pct_mkt := % of markets (last 30d, this mode) with positive aggregate P&L
    """
    day = cfg.day
    mode = cfg.mode.upper()

    total = yday = today = d7 = d30 = hit = avg = pnlph = winpct = 0.0
    n = w = 0

    # hours since local midnight (for pnl/hr)
    midnight = datetime.combine(now_utc.date(), datetime.min.time(), tzinfo=timezone.utc)
    hours = max((now_utc - midnight).total_seconds() / 3600.0, 1e-9)

    if _tbl_exists(con, "orders"):
        cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)")]
        pnl_col = "net_pl" if "net_pl" in cols else ("realized_pnl" if "realized_pnl" in cols else None)
        have_ts = "closed_at" in cols
        if pnl_col:
            def _sum(where_sql: str, args: tuple = ()) -> float:
                row = _q_retry(con, f"SELECT COALESCE(SUM({pnl_col}),0.0) FROM orders WHERE {where_sql}", args).fetchone()
                return float(row[0] or 0.0)

            # ALL TIME — THIS MODE ONLY
            total = _sum("exit_status='matched' AND UPPER(COALESCE(mode,''))=UPPER(?)", (mode,))

            # Date-scoped
            if have_ts:
                today = _sum("exit_status='matched' AND date(closed_at)=date(?) AND UPPER(COALESCE(mode,''))=UPPER(?)",
                             (day, mode))
                yday  = _sum("exit_status='matched' AND date(closed_at)=date(?, '-1 day') AND UPPER(COALESCE(mode,''))=UPPER(?)",
                             (day, mode))
                d7    = _sum("exit_status='matched' AND datetime(closed_at)>=datetime('now','-7 days') "
                             "AND UPPER(COALESCE(mode,''))=UPPER(?)", (mode,))
                d30   = _sum("exit_status='matched' AND datetime(closed_at)>=datetime('now','-30 days') "
                             "AND UPPER(COALESCE(mode,''))=UPPER(?)", (mode,))

                # Hit-rate / avg gain (all time in this mode)
                n = int(_q_retry(con, "SELECT COUNT(*) FROM orders WHERE exit_status='matched' AND UPPER(COALESCE(mode,''))=UPPER(?)",
                    (mode,)
                ).fetchone()[0] or 0)
                w = int(_q_retry(con, f"SELECT COUNT(*) FROM orders WHERE exit_status='matched' AND {pnl_col}>0 "
                    "AND UPPER(COALESCE(mode,''))=UPPER(?)", (mode,)
                ).fetchone()[0] or 0)
                hit = (w / n) if n > 0 else 0.0
                avg = (total / n) if n > 0 else 0.0

                # Win % by market (last 30d, this mode)
                win_mkts = _q_retry(con, f"SELECT COUNT(*) FROM (SELECT marketId, SUM({pnl_col}) s "
                    "FROM orders WHERE exit_status='matched' AND UPPER(COALESCE(mode,''))=UPPER(?) "
                    "AND datetime(closed_at)>=datetime('now','-30 days') "
                    "GROUP BY marketId HAVING s>0)", (mode,)
                ).fetchone()[0]
                tot_mkts = _q_retry(con, "SELECT COUNT(DISTINCT marketId) FROM orders "
                    "WHERE exit_status='matched' AND UPPER(COALESCE(mode,''))=UPPER(?) "
                    "AND datetime(closed_at)>=datetime('now','-30 days')", (mode,)
                ).fetchone()[0]
                winpct = (float(win_mkts) / float(tot_mkts)) if tot_mkts else 0.0

    # pnl/hour since midnight
    pnlph = today / hours

    # Bank / used exposure from cached account funds (already refreshed in main loop)
    global _FUNDS_BANK, _FUNDS_USED
    bank = float(_FUNDS_BANK or 0.0)
    used = float(_FUNDS_USED or 0.0)

    _exec_retry(con, """
        INSERT INTO dashboard_tiles(day, mode, total, yesterday, today, d7, d30, hit_rate, pnl_per_hour, avg_gain, win_pct_mkt,
                                    bank, used_exposure, last_refreshed_ts)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(day, mode) DO UPDATE SET
          total=excluded.total,
          yesterday=excluded.yesterday,
          today=excluded.today,
          d7=excluded.d7,
          d30=excluded.d30,
          hit_rate=excluded.hit_rate,
          pnl_per_hour=excluded.pnl_per_hour,
          avg_gain=excluded.avg_gain,
          win_pct_mkt=excluded.win_pct_mkt,
          bank=excluded.bank,
          used_exposure=excluded.used_exposure,
          last_refreshed_ts=excluded.last_refreshed_ts
    """, (day, mode, float(total), float(yday), float(today), float(d7), float(d30),
          float(hit), float(pnlph), float(avg), float(winpct),
          float(bank), float(used), iso(now_utc)))
    _commit_retry(con)


def _print_day_plan_once(con: sqlite3.Connection, cfg: FeederCfg, now_utc: datetime):
    """
    Print the day's schedule (GB/IE WIN from bets) once, with a concurrent marker.
    If there are no future races today, show the first race tomorrow.
    """
    if getattr(_print_day_plan_once, "_printed", False):
        return
    day = cfg.day
    today = _markets_today_from_bets(con, day)
    if not today:
        # roll to tomorrow for plan
        day = (datetime.fromisoformat(day).date() + timedelta(days=1)).isoformat()
        today = _markets_today_from_bets(con, day)

    if not today:
        print("[PLAN] No GB/IE WIN markets in bets for today/tomorrow.")
        _print_day_plan_once._printed = True
        return

    print(f"[PLAN] Day plan for {day} ({len(today)} markets)")
    # concurrent marker: within CONCURRENT_WINDOW_MIN from previous off
    prev_off = None
    for (mid, course, off) in today:
        try:
            off_dt = datetime.fromisoformat(off.replace("Z","+00:00")).astimezone(timezone.utc) if off else None
        except Exception:
            off_dt = None
        flag = ""
        if prev_off and off_dt:
            dt_min = abs((off_dt - prev_off).total_seconds()) / 60.0
            if dt_min <= float(CONCURRENT_WINDOW_MIN):
                flag = " (CONC)"
        print(f"  • {off or '-'}  {course or '-'}  {mid}{flag}")
        prev_off = off_dt or prev_off
    _print_day_plan_once._printed = True


# ─────────────────────────────────────────────────────────────────────────────
# Console report
# ─────────────────────────────────────────────────────────────────────────────

def print_report(con: sqlite3.Connection, cfg: FeederCfg, now_utc: datetime, next_mid: Optional[str]):
    # Primary (next) market snapshot incl. T0 fields
    _print_day_plan_once(con, cfg, now_utc)

    m = _q_retry(con, "SELECT marketId, course, off_at_utc, status, t_minus_sec, "
        "betfair_status_mapped, status_source, t0_phase, t0_color, t0_sec "
        "FROM dashboard_markets "
        "WHERE day=? "
        "ORDER BY is_next DESC, (off_at_utc IS NULL), datetime(off_at_utc) ASC, marketId ASC LIMIT 1",
        (cfg.day,)
    ).fetchone()
    # Secondary concurrent market (if any)
    s = _q_retry(con, "SELECT marketId, course, off_at_utc, betfair_status_mapped, status_source, t0_phase, t0_sec "
        "FROM dashboard_markets "
        "WHERE day=? AND is_concurrent=1 "
        "ORDER BY datetime(off_at_utc) ASC LIMIT 1",
        (cfg.day,)
    ).fetchone()

    # Tiles & Activity
    tiles = _q_retry(con, "SELECT total, today, d7, d30, hit_rate, pnl_per_hour, avg_gain, win_pct_mkt, bank, used_exposure "
        "FROM dashboard_tiles WHERE day=? AND mode=?",
        (cfg.day, cfg.mode)
    ).fetchone()
    activity = _q_retry(con, "SELECT runners_scanned, decisions, proposals, parents_placed, hedges_matched, cancels, timeouts, open_parents "
                             "FROM dashboard_activity WHERE day=?", (cfg.day,)
    ).fetchone()

    def _fmt_t0(phase: Optional[str], sec: Optional[int]) -> str:
        # Human-readable T0 line per your spec
        if not phase or sec is None:
            return "-"
        p = phase.upper()
        mins = max(sec, 0) // 60
        if p == "PRE":
            return f"T- {mins}m"
        if p == "WAIT_INPLAY":
            return f"RED T+ {mins}m"
        if p == "IN_PLAY":
            return f"GREEN +{mins}m"
        if p == "ENDED":
            return "ENDED"
        return "-"

    print(f"\n[FEEDER] run mode={cfg.mode} source={cfg.source_mode} day={cfg.day} now={iso(now_utc)}")

    if m:
        # Combine your original fields + the new T0 clock text
        print(
            f"NEXT {m['marketId']} | {m['course']} | off={m['off_at_utc'] or '-'} "
            f"| status={m['betfair_status_mapped'] or m['status']} ({m['status_source']}) "
            f"| T-={m['t_minus_sec'] if m['t_minus_sec'] is not None else '-'}s "
            f"| { _fmt_t0(m['t0_phase'], m['t0_sec']) }"
        )
    else:
        print("NEXT (none)")

    if s:
        # Show concurrent with its T0 too
        print(
            f"CONC {s['marketId']} | {s['course']} | off={s['off_at_utc'] or '-'} "
            f"| status={s['betfair_status_mapped'] or '-'} ({s['status_source']}) "
            f"| { _fmt_t0(s['t0_phase'], s['t0_sec']) }"
        )

    # Top-6 for the primary market
    if next_mid:
        rows = _q_retry(con, "SELECT top6_rank, runner_name, odd, implied_prob, bar_display_norm, bar_color "
            "FROM dashboard_runners "
            "WHERE day=? AND marketId=? AND in_top6=1 "
            "ORDER BY top6_rank ASC",
            (cfg.day, next_mid)
        ).fetchall()
        if rows:
            print("Top-6:")
            # Pre & wait-in-play use 0–25%; in-play/post use 0–100%
            is_pre = bool(m and (m["t0_phase"] in ("PRE", "WAIT_INPLAY")))
            scale = 25.0 if is_pre else 100.0
            for r in rows:
                pct = round((r["bar_display_norm"] or 0.0) * scale, 1)
                print(
                    f"  {r['top6_rank']:>1}  {r['runner_name']:<22}  odd={r['odd']:<7}  "
                    f"p={r['implied_prob']:.3f}  bar={pct:>5}%  {r['bar_color']}"
                )
        else:
            print("Top-6: (no runner rows yet)")
    else:
        print("Top-6: (no next market)")

    # KPIs
    if tiles:
        print(
            f"KPIs total={tiles['total']:.2f} today={tiles['today']:.2f} d7={tiles['d7']:.2f} d30={tiles['d30']:.2f} "
            f"hit%={tiles['hit_rate']*100:.1f} pnl/hr={tiles['pnl_per_hour']:.2f} avg={tiles['avg_gain']:.2f} "
            f"win%mkt={tiles['win_pct_mkt']*100:.1f} bank={tiles['bank']:.2f} used={tiles['used_exposure']:.2f}"
        )
    else:
        print("KPIs (none)")

    # Activity
    if activity:
        print(
            f"ACT  scan={activity['runners_scanned']} dec={activity['decisions']} prop={activity['proposals']} "
            f"parents={activity['parents_placed']} hedge={activity['hedges_matched']} cancels={activity['cancels']} "
            f"timeouts={activity['timeouts']} open={activity['open_parents']}"
        )
    else:
        print("ACT (none)")

    # Strategy counts (today)
    try:
        closed, wins = _strategy_counts_today(con, day=cfg.day, mode=cfg.mode)
        print(_fmt_counts_line("STRATS closed:", closed))
        print(_fmt_counts_line("STRATS wins:  ", wins))
    except Exception as e:
        print(f"[STRATS] count error: {e}")

    # Orders tape (last 10)
    tape = _q_retry(con, "SELECT ts, side, status, price, amount, realized_pnl, runner_name, marketId "
        "FROM dashboard_orders_tape "
        "WHERE day=? "
        "ORDER BY datetime(ts) DESC, id DESC LIMIT 10",
        (cfg.day,)
    ).fetchall()
    if tape:
        print("ORDERS (last 10):")
        for r in tape:
            pnl = (f" pnl={r['realized_pnl']:.2f}" if r['realized_pnl'] is not None else "")
            prc = (f" @{r['price']:.2f}" if r['price'] is not None else "")
            amt = (f" x{r['amount']:.2f}" if r['amount'] is not None else "")
            print(f"  {r['ts']}  {r['marketId']}  {r['runner_name']:<22}  {r['side'] or '-':<4}  {r['status']:<8}{prc}{amt}{pnl}")
          
    else:
        print("ORDERS: (none)")
        



# ─────────────────────────────────────────────────────────────────────────────
# Main loop
# ─────────────────────────────────────────────────────────────────────────────

def main():
    
    from engines.dashboard_schema import ensure_dashboard_schema as _ensure_dash
    try:
        _ensure_dash(verbose=True)  # create dashboard_* tables, odds_current, etc.
    except Exception as e:
        print(f"[FEEDER] schema ensure warn: {e}")
    ap = argparse.ArgumentParser("dashboard_feeder")
    ap.add_argument("--mode", choices=["LIVE","TEST"], default=os.environ.get("DASH_MODE","LIVE").upper())
    ap.add_argument("--source", default="MIRROR", choices=["MIRROR","DIRECT"])
    ap.add_argument("--day", action="append", help="YYYY-MM-DD (repeatable)")
    ap.add_argument("--tick", type=float, default=1.0)
    ap.add_argument("--speed", type=float, default=1.0)
    ap.add_argument("--window-hours", type=int, default=12)
    ap.add_argument("--app-key", default=None)
    ap.add_argument("--session", default=None)
    ap.add_argument("--last", type=int, default=1, help="look back N days (default 1)")
    ap.add_argument("--hz", type=float, default=2.0)
    ap.add_argument("--scope-inplay-window", type=int, default=15)
    args = ap.parse_args()

    days = _pick_scope_days(days=args.day or [], last=args.last)
    if args.mode == "TEST":
        pump = _SimPump(days=days, hz=args.hz)
        pump.run()
        return

    # Route DBs for the chosen mode (GUI and feeder must share the same files)
    from engines.config_paths import set_db_paths
    try:
        set_db_paths(mode=(args.mode or "LIVE").lower(), quiet=True)
    except Exception:
        pass

    # capture optional CLI creds
    global FEEDER_APP_KEY, FEEDER_SESSION
    FEEDER_APP_KEY = args.app_key or None
    FEEDER_SESSION = args.session or None

    mode = args.mode.upper()
    day = (args.day or datetime.now(timezone.utc).date().isoformat())
    cfg = FeederCfg(mode=mode, source_mode=args.source.upper(), day=day,
                    speed_x=args.speed, tick_sec=args.tick, window_hours=args.window_hours)

    # Route DBs for the chosen mode
    from engines.config_paths import set_db_paths
    try:
        ensure_dashboard_indexes(max_wait_s=10.0, quiet=False)
    except Exception as e:
        print(f"[DDL] index init warn: {e}")

 


    # Must have live creds + keep-alive
    if not _ensure_creds_or_log():
        return

    # Ensure schema
    try:
        ensure_feeder_tables()
        try:
            ensure_settlements_schema()
        except Exception as e:
            print(f"[SETTLE] init warn: {e}")

        ensure_feeder_columns()
    except Exception as e:
        print(f"[DDL] init warn: {e}")

    con = _adb()
    if con is None:
        print("[FEEDER] DB open failed; will retry on next launch")
        return

    try:
        # one-off catalogue backfill is best-effort
        try:
            refresh_catalogue_schedule_and_runners(con, utcnow(), back_hours=1, fwd_hours=36)
        except Exception as e:
            print(f"[CATALOGUE] initial refresh error: {e}")

        run_id = f"FEEDER-{day}-{datetime.now(timezone.utc).strftime('%H%M%S')}"
        _q_retry(con, "INSERT OR REPLACE INTO dashboard_runs(run_id, mode, source_mode, day, speed_x, now_ts, status, last_heartbeat_ts) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (run_id, mode, cfg.source_mode, day, cfg.speed_x, iso(utcnow()), "starting", iso(utcnow()))
        )
        con.commit()

        next_funds_refresh     = time.monotonic() + 60.0
        next_catalogue_refresh = time.monotonic() + 300.0
        next_live_sync         = time.monotonic() + 5.0
        next_settlements_refresh = time.monotonic() + 60.0
        tick = 0  # ← add this

        while True:
            now_utc = utcnow()

            # periodic catalogue refresh
            if time.monotonic() >= next_catalogue_refresh:
                try:
                    refresh_catalogue_schedule_and_runners(con, now_utc, back_hours=1, fwd_hours=36)
                except Exception as e:
                    print(f"[CATALOGUE] periodic refresh error: {e}")
                next_catalogue_refresh = time.monotonic() + 300.0

            # account funds
            if time.monotonic() >= next_funds_refresh:
                try:
                    global _FUNDS_BANK, _FUNDS_USED
                    _FUNDS_BANK, _FUNDS_USED = _fetch_account_funds()
                except Exception as e:
                    print(f"[FUNDS] periodic refresh error: {e}")
                next_funds_refresh = time.monotonic() + 60.0

            # markets / runners
            try:
                next_mid = upsert_dashboard_markets(con, cfg, now_utc)
            except Exception as e:
                print(f"[MARKETS] write error: {e}")
                next_mid = None

            try:
                upsert_dashboard_runners(con, cfg, now_utc, next_mid)
            except Exception as e:
                print(f"[RUNNERS] write error: {e}")

            # stats + tiles
            try:
                upsert_dashboard_kpis(con, cfg.mode, cfg.day)
            except Exception as e:
                if "locked" in str(e).lower():
                    print("[KPI] locked; will retry next tick")
                else:
                    print(f"[KPI] error: {e}")


            try:
                upsert_dashboard_tiles(con, cfg, now_utc)
            except Exception as e:
                if "locked" in str(e).lower():
                    print("[TILES] locked; will retry next tick")
                else:
                    print(f"[TILES] error: {e}")

            # orders → tape (this is what lights the UI tables)
            try:
                upsert_dashboard_orders_tape(con, cfg, now_utc, limit=10)
            except Exception as e:
                if "locked" in str(e).lower():
                    print("[TAPE] locked; will retry next tick")
                else:
                    print(f"[TAPE] error: {e}")

            # finalize hedges periodically
            if time.monotonic() >= next_live_sync:
                try:
                    from engines.live.live_router import _sync_hedge_matches
                    fixed = _sync_hedge_matches(limit=200)
                    if fixed:
                        print(f"[LIVE SYNC] hedges finalized={fixed}")
                except Exception as e:
                    print(f"[LIVE SYNC] error: {e}")
                next_live_sync = time.monotonic() + 5.0

            # settlements pull + reconcile
            if time.monotonic() >= next_settlements_refresh:
                try:
                    to_iso   = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    from_iso = (datetime.now(timezone.utc) - timedelta(minutes=90)).strftime("%Y-%m-%dT%H:%M:%SZ")
                    try:
                        rows = fetch_cleared(from_iso, to_iso)  # listClearedOrders: from/to settlement date (UTC ISO)
                    except TypeError:
                        # fallback if your alias expects named args
                        rows = fetch_cleared(from_iso=from_iso, to_iso=to_iso)

                    if rows:
                        n = upsert_cleared(rows)
                        if n:
                            fixed = reconcile_from_cleared()
                            if fixed:
                                print(f"[SETTLE] upsert={n} fixed_parents={fixed}")

                except Exception as e:
                     print(f"[SETTLE] error: {e}")
                next_settlements_refresh = time.monotonic() + 60.0


            # heartbeat + console snapshot
            _q_retry(con, "UPDATE dashboard_runs SET now_ts=?, status=?, last_heartbeat_ts=? WHERE run_id=?",
                        (iso(now_utc), "running", iso(now_utc), run_id))
            con.commit()
            tick += 1  # ← add this
            if tick % 30 == 0:  # once per minute
                print_report(con, cfg, now_utc, next_mid)

            time.sleep(max(cfg.tick_sec, 0.2))
    finally:
        try:
            con.close()
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# 🧩 PATCH: engines/dashboard_feeder.py — add upsert_dashboard_orders_tape()
# 📍 TARGET: place below upsert_dashboard_tiles (new function)
# 🔎 SEARCH: def upsert_dashboard_tiles(
# ─────────────────────────────────────────────────────────────────────────────
def upsert_dashboard_orders_tape(con: sqlite3.Connection, cfg: FeederCfg, now_utc: datetime, limit: int = 10):
    """
    Build a compact last-10 orders tape for the UI from orders:
      price := COALESCE(exit_odds, entry_odds)
      size  := COALESCE(exit_stake, entry_stake)
      pnl   := COALESCE(net_pl, realized_pnl)
      ts    := COALESCE(closed_at, opened_at)
    """
    day = cfg.day
    if not _tbl_exists(con, "orders"):
        _exec_retry(con, "DELETE FROM dashboard_orders_tape WHERE day=?", (day,))
        _commit_retry(con)
        return

    cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)")]
    has = lambda c: c in cols

    # Expressions guarding missing columns
    ts_expr     = ("COALESCE(closed_at, opened_at)" if has("closed_at") and has("opened_at")
                   else ("closed_at" if has("closed_at")
                   else ("opened_at" if has("opened_at") else "NULL")))
    price_expr  = ("COALESCE(exit_odds, entry_odds)" if has("exit_odds") and has("entry_odds")
                   else ("exit_odds" if has("exit_odds")
                   else ("entry_odds" if has("entry_odds") else "NULL")))
    size_expr   = ("COALESCE(exit_stake, entry_stake)" if has("exit_stake") and has("entry_stake")
                   else ("exit_stake" if has("exit_stake")
                   else ("entry_stake" if has("entry_stake") else "NULL")))
    pnl_expr    = ("COALESCE(net_pl, realized_pnl)" if has("net_pl") and has("realized_pnl")
                   else ("net_pl" if has("net_pl")
                   else ("realized_pnl" if has("realized_pnl") else "NULL")))
    side_expr   = "side" if has("side") else "NULL"
    mode_expr   = "mode" if has("mode") else "NULL"
    market_expr = "marketId" if has("marketId") else "NULL"
    sel_expr    = "selectionId" if has("selectionId") else "NULL"
    role_expr   = "role" if has("role") else "NULL"
    src_expr    = "source" if has("source") else "NULL"
    es_expr     = "exit_status" if has("exit_status") else "NULL"
    en_expr     = "entry_status" if has("entry_status") else "NULL"
    oid_expr    = "order_id" if has("order_id") else "NULL"

    sel = f"""
        SELECT
          {ts_expr}        AS ts,
          {market_expr}    AS marketId,
          {sel_expr}       AS selectionId,
          {side_expr}      AS side,
          {price_expr}     AS price,
          {size_expr}      AS size,
          {pnl_expr}       AS pnl,
          {oid_expr}       AS order_id,
          {mode_expr}      AS mode,
          {es_expr}        AS exit_status,
          {en_expr}        AS entry_status,
          {role_expr}      AS role,
          {src_expr}       AS source
        FROM orders
        WHERE {("date(" + ts_expr + ")=date(?)") if ts_expr != "NULL" else "1=1"}
        ORDER BY datetime({ts_expr}) DESC
        LIMIT {int(limit)}
    """
    params = (day,) if ts_expr != "NULL" else ()
    rows = _q_retry(con, sel, params).fetchall()

    # Rebuild tape
    _exec_retry(con, "DELETE FROM dashboard_orders_tape WHERE day=?", (day,))

    for r in rows:
        ts_val = r["ts"]
        mid    = str(r["marketId"]) if r["marketId"] is not None else None
        sid    = str(r["selectionId"]) if r["selectionId"] is not None else None
        side   = (str(r["side"]).upper() if r["side"] else None)
        price  = float(r["price"]) if r["price"] is not None else None
        size   = float(r["size"]) if r["size"] is not None else None
        pnl    = float(r["pnl"]) if r["pnl"] is not None else None
        mode   = (str(r["mode"]).upper() if r["mode"] else None)
        order_id = str(r["order_id"]) if r["order_id"] is not None else None

        # Status preference
        es = (str(r["exit_status"]).upper() if r["exit_status"] else "")
        en = (str(r["entry_status"]).upper() if r["entry_status"] else "")
        status = es or en or "OPEN"

        # Best-effort name
        runner_name = _runner_name(con, mid, sid) if (mid and sid) else (sid or "-")

        _exec_retry(con, """
            INSERT INTO dashboard_orders_tape(ts, marketId, selectionId, runner_name, side, status,
                                              price, amount, realized_pnl, order_id, mode, day)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """, (ts_val, mid, sid, runner_name, side, status, price, size, pnl, order_id, mode, day))

    _commit_retry(con)


# ─────────────────────────────────────────────────────────────────────────────
# Open positions counts (parents/child) — robust to schema variants
# ─────────────────────────────────────────────────────────────────────────────
from typing import Dict, Any


def risk_open_counts(mode: str = "LIVE") -> Dict[str, Any]:
    out = {"open_parents": 0, "open_child": 0}
    with connect_db(ro=True) as conn:
        try:
            cols = [r["name"] for r in _q_retry(conn, "PRAGMA table_info(orders)")]
        except Exception:
            return out

        has_mode   = "mode" in cols
        has_role   = "role" in cols
        has_hedge  = "hedge_of" in cols or "parent_id" in cols
        hedge_col  = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        st_entry   = "entry_status" if "entry_status" in cols else None
        st_exit    = "exit_status"  if "exit_status"  in cols else None
        st_status  = "status" if "status" in cols else None

        # Parents: matched entry, not (matched) exit
        if st_entry:
            cond_parent = "(role='PARENT')" if has_role else ("(%s IS NULL)" % hedge_col if hedge_col else "1")
            where = f"{cond_parent} AND UPPER({st_entry})='MATCHED' AND ({st_exit} IS NULL OR UPPER({st_exit})<>'MATCHED')" if st_exit else f"{cond_parent} AND UPPER({st_entry})='MATCHED'"
            sql = f"SELECT COUNT(*) FROM orders WHERE {where}"
            if has_mode:
                sql += " AND COALESCE(mode,?)=?"
                out["open_parents"] = int(_q_retry(conn, sql, (mode, mode)).fetchone()[0] or 0)
            else:
                out["open_parents"] = int(_q_retry(conn, sql).fetchone()[0] or 0)

        # Child: staged/live hedge orders (unfilled hedges)
        cond_child = "(role='CHILD')" if has_role else (f"({hedge_col} IS NOT NULL)" if hedge_col else "0")
        status_expr = None
        if st_status:
            status_expr = f"UPPER({st_status}) IN ('STAGED','LIVE')"
        elif st_entry:
            status_expr = f"UPPER({st_entry}) IN ('STAGED','LIVE')"
        if cond_child and status_expr:
            sql = f"SELECT COUNT(*) FROM orders WHERE {cond_child} AND {status_expr}"
            if has_mode:
                sql += " AND COALESCE(mode,?)=?"
                out["open_child"] = int(_q_retry(conn, sql, (mode, mode)).fetchone()[0] or 0)
            else:
                out["open_child"] = int(_q_retry(conn, sql).fetchone()[0] or 0)

    return out

# ─────────────────────────────────────────────────────────────────────────────
# Strategy outcome counters (closed scalps + wins) grouped by strategy family
# ─────────────────────────────────────────────────────────────────────────────
def _letter_family_map() -> Dict[str, str]:
    return {
        "X": "S4_CROSSOVER",
        "B": "S5_BREAKOUT",
        "F": "S6_STEAM_FADE",
        "L": "LADDER_STRATEGY",
        "D": "IP1_SHOCK_DRIFT",
        "T": "IP2_TIRED_LEADER",
        "C": "IP3_CLOSE_FINISH",
        "E": "IP4_FENCE_ERROR",
        "K": "IP5_COLLAPSE_FADE",
        "S": "LEGACY",
    }


# 📍 TARGET: engines/dashboard_feeder.py
# 🔎 SEARCH: def _family_from_source(
def _family_from_source(src: Optional[str]) -> str:
    """
    Map orders.source to a readable family name.

    Explicit prefixes first (BTL_, OG_), then LEGACY, then 1-letter families.
    """
    if not src:
        return "UNKNOWN"
    s = str(src).upper()

    # explicit families
    if s.startswith("BTL"):
        return "BTL"
    if s.startswith("OG"):
        return "OG_BIAS"
    if s.startswith("LEGACY"):
        return "LEGACY"

    # Ignore mode-only sources written by queue_order
    if s in ("LIVE", "TEST", "LEARNING", "LEGACY_STRATEGY"):
        return "UNKNOWN"

    letter = s[:1]
    return _letter_family_map().get(letter, "UNKNOWN")

# 📍 TARGET: engines/dashboard_feeder.py
# 🔎 SEARCH: def _strategy_counts_today(con: sqlite3.Connection, *, day: str, mode: str = "LIVE") -> Tuple[Dict[str, int], Dict[str, int]]:
def _strategy_counts_today(con: sqlite3.Connection, *, day: str, mode: str = "LIVE") -> Tuple[Dict[str, int], Dict[str, int]]:
    """Return (closed_counts, win_counts) for today grouped by strategy family."""
    closed: Dict[str, int] = {}
    wins: Dict[str, int] = {}

    # Discover available columns safely
    try:
        cols_rows = _q_retry(con, "PRAGMA table_info(orders)").fetchall()
    except Exception:
        return closed, wins
    if not cols_rows:
        return closed, wins

    cols = { (r[1] if isinstance(r, tuple) else r["name"]) for r in cols_rows }
    has = lambda c: c in cols

    # Build expressions only for columns that exist
    src_expr   = "COALESCE(source,'UNKNOWN')" if has("source") else "'UNKNOWN'"
    exit_expr  = "COALESCE(exit_status,'')"   if has("exit_status") else "''"

    # Timestamp for “today” filter
    if has("closed_at") and has("updated_at"):
        ts_expr = "COALESCE(closed_at, updated_at)"
    elif has("closed_at"):
        ts_expr = "closed_at"
    elif has("updated_at"):
        ts_expr = "updated_at"
    else:
        ts_expr = None  # no date filter possible

    # Parent filter
    if has("role"):
        where_parents = "role='PARENT'"
    elif has("hedge_of"):
        where_parents = "hedge_of IS NULL"
    elif has("parent_id"):
        where_parents = "parent_id IS NULL"
    else:
        where_parents = "1=1"

    # Select list — include only present measures
    select_cols = [f"{src_expr} AS src", f"{exit_expr} AS exit_status"]
    if has("realized_ticks"):
        select_cols.append("COALESCE(realized_ticks,0) AS realized_ticks")
    elif has("net_pl"):
        select_cols.append("COALESCE(net_pl,0.0) AS net_pl")
    elif has("realized_pnl"):
        select_cols.append("COALESCE(realized_pnl,0.0) AS realized_pnl")

    select_sql = "SELECT " + ", ".join(select_cols) + " FROM orders WHERE " + where_parents
    params: list = []

    if ts_expr:
        select_sql += f" AND date({ts_expr})=date(?)"
        params.append(day)

    if has("mode"):
        select_sql += " AND UPPER(COALESCE(mode,''))=UPPER(?)"
        params.append(mode)

    rows = _q_retry(con, select_sql, tuple(params)).fetchall()

    def _family_of(src: str) -> str:
        s = (src or "").strip().upper()
        if not s:
            return "UNKNOWN"
        if s.startswith(("S1","S2","S3")) or s.startswith("LEGACY"):
            return "S"
        return _letter_family_map().get(s[:1], "UNKNOWN")

    for r in rows:
        # sqlite3.Row or tuple support
        get = (lambda k, idx=None: (r[k] if isinstance(r, sqlite3.Row) else (r[idx] if idx is not None else None)))

        src          = get("src",          0)
        exit_status  = get("exit_status",  1)
        realized_t   = r["realized_ticks"] if isinstance(r, sqlite3.Row) and "realized_ticks" in r.keys() else None
        net_pl       = r["net_pl"]         if isinstance(r, sqlite3.Row) and "net_pl"         in r.keys() else None
        realized_pnl = r["realized_pnl"]   if isinstance(r, sqlite3.Row) and "realized_pnl"   in r.keys() else None

        fam = _family_of(src)
        closed[fam] = closed.get(fam, 0) + 1

        # Win heuristic: prefer realized_ticks>0; else positive P&L; else no win.
        is_win = False
        try:
            if realized_t is not None:
                is_win = float(realized_t) > 0
            elif net_pl is not None:
                is_win = float(net_pl) > 0.0
            elif realized_pnl is not None:
                is_win = float(realized_pnl) > 0.0
        except Exception:
            is_win = False

        if str(exit_status).lower() == "matched" and is_win:
            wins[fam] = wins.get(fam, 0) + 1

    return closed, wins



def _fmt_counts_line(title: str, d: Dict[str, int]) -> str:
    # Compact, stable order: PRE families, LADDER, then IP, then LEGACY/UNKNOWN
    order = ["S4_CROSSOVER","S5_BREAKOUT","S6_STEAM_FADE","BTL","OG_BIAS","LADDER_STRATEGY",
             "IP1_SHOCK_DRIFT","IP2_TIRED_LEADER","IP3_CLOSE_FINISH","IP4_FENCE_ERROR","IP5_COLLAPSE_FADE",
             "S","UNKNOWN"]
    parts = [f"{k.split('_')[0]}={d.get(k,0)}" for k in order if k in d]
    return f"{title} " + (" | ".join(parts) if parts else "(none)")

# === PATCH START: TEST mode seeder ===
def _utcnow_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _gui_conn() -> sqlite3.Connection:
    p = _adb_path()
    con = sqlite3.connect(p, timeout=8, isolation_level=None, check_same_thread=False)
    try:
        con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def _ensure_orders(con: sqlite3.Connection) -> None:
    con.execute("""
      CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customerOrderRef TEXT UNIQUE,
        mode TEXT, marketId TEXT, selectionId TEXT,
        side TEXT,
        entry_odds REAL, entry_stake REAL, entry_status TEXT,
        entry_bet_id TEXT,
        exit_status TEXT, exit_bet_id TEXT, exit_odds REAL, exit_stake REAL,
        opened_at TEXT, closed_at TEXT,
        net_pl REAL, role TEXT, source TEXT, notes TEXT
      )
    """)

def seed_test_flow(*, day_utc: str | None = None, runners: int = 3) -> None:
    """
    Inserts a tiny PARENT/CHILD life-cycle into AUTO_DB.orders so the dashboard
    KPIs and tiles move *without* touching the betting engine.
    """
    con = _gui_conn(); _ensure_orders(con)
    day_tag = (day_utc or datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    now = _utcnow_str()

    # pick a fake market and 3 runners
    mid = f"1.TEST.{day_tag.replace('-','')}"
    sids = [f"SID{100+i}" for i in range(max(1, int(runners)))]

    # parent entries (placed → matched)
    for i, sid in enumerate(sids, start=1):
        side = "LAY" if i % 2 else "BACK"
        odds = 3.6 + (i * 0.2)
        stake = 3.0 + i
        cor = f"T{day_tag}-{mid}-{sid}-{random.randint(100,999)}"
        con.execute("""
          INSERT OR IGNORE INTO orders(customerOrderRef, mode, marketId, selectionId,
            side, entry_odds, entry_stake, entry_status, opened_at, role, source, notes)
          VALUES(?, 'LIVE', ?, ?, ?, ?, ?, 'placed', ?, 'PARENT', 'A', 'TEST')
        """, (cor, mid, sid, side, odds, stake, now))
        # match the parent so tiles show open exposure
        con.execute("UPDATE orders SET entry_status='matched' WHERE customerOrderRef=?", (cor,))

        # hedge child (simulate one matched, one working, one missing)
        if i == 1:
            # matched hedge -> realized P&L
            hedge_odds = (odds - 0.2) if side == "LAY" else (odds + 0.2)
            pnl = (stake if side == "LAY" else -(stake))  # coarse, dashboard is the goal
            con.execute("""
              UPDATE orders SET exit_status='matched', exit_odds=?, exit_stake=?, closed_at=?, net_pl=COALESCE(net_pl,0)+?
              WHERE customerOrderRef=?
            """, (hedge_odds, stake, now, pnl, cor))
        elif i == 2:
            # child working -> parent remains open exposure
            pass
        else:
            # no child yet
            pass

    con.commit(); con.close()

def maybe_start_test_mode():
    """
    Enable with FEEDER_TEST_MODE=1 or --mode test (CLI). Safe no-op otherwise.
    """
    import argparse
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--mode", choices=["live","test"], default=None)
    parser.add_argument("--date", help="YYYY-MM-DD (UTC) for seeded day")
    args, _ = parser.parse_known_args()
    mode_env = (os.environ.get("FEEDER_TEST_MODE") or "").strip()
    if (args.mode == "test") or (mode_env == "1"):
        seed_test_flow(day_utc=(args.date or os.environ.get("DASH_DATE")))
# === PATCH END ===



if __name__ == "__main__":
    main()
