# gui/dashboard_data.py
from __future__ import annotations
import sqlite3, json
from typing import Optional, Iterable
from datetime import datetime, timezone
import sqlite3, math
from datetime import datetime, timezone
from typing import Any, Dict, List
import sqlite3, math
from typing import Any, Dict, List, Tuple
from datetime import datetime, timezone, timedelta

from engines.config_paths import connect_db, autoscalp_db
import engines.daily_config as daily_config
# add with other imports near the top
from engines.config_paths import auto_conn as _auto_conn, q_retry as _q_retry

# --- PATCH: imports (add near your other imports) ---
import os, sqlite3
from datetime import datetime, timezone
try:
    from engines.config_paths import connect_db  # your canonical bets.db opener
except Exception:
    # shim fallback (keeps module importable if paths not ready)
    def connect_db(ro=True):
        return sqlite3.connect(os.path.join(os.path.dirname(__file__), "..", "Data", "bets.db"))
# ----------------------------------------------------



import os
from datetime import datetime, timezone, timedelta
import sqlite3
from engines.config_paths import connect_db, auto_conn, q_retry as _q

# Configurable day window (UI can set env, or feeder passes via import)
DASH_DAYS = os.environ.get("DASH_DAYS","").split(",") if os.environ.get("DASH_DAYS") else []
DASH_LAST = int(os.environ.get("DASH_LAST","1") or "1")

# 📍 TARGET: dashboard_data.py
# 🔎 SEARCH: import sqlite3
# ✅ ADD (idempotent safe): helpers + robust ISO parsing

import sqlite3
from contextlib import closing

def _df(con, q, params=None):
    """
    Minimal helper returning list[dict] to avoid pandas dependency.
    If you already have a pandas-based helper, keep it and ignore this.
    """
    params = params or []
    with closing(con.cursor()) as cur:
        cur.execute(q, params)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    return [dict(zip(cols, r)) for r in rows]

# 📍 TARGET: dashboard_data.py
# 🔎 SEARCH: def get_dashboard_markets(
# ✅ REPLACE with a window-function dedupe by marketId (SQLite ≥3.25; your 3.39.5 is OK:contentReference[oaicite:8]{index=8})

def get_dashboard_markets(con):
    """
    Today's markets, deduped to one row per marketId, ordered by off_at_utc.
    Columns per autoscalp_gui schema:contentReference[oaicite:9]{index=9}.
    """
    q = """
    SELECT day, marketId, course, market_name, off_at_utc,
           status, is_next, t0_color, t0_sec, inplay_start_ts,
           betfair_status, betfair_status_mapped, betfair_status_age_sec
    FROM (
      SELECT day, marketId, course, market_name, off_at_utc,
             status, is_next, t0_color, t0_sec, inplay_start_ts,
             betfair_status, betfair_status_mapped, betfair_status_age_sec,
             ROW_NUMBER() OVER (PARTITION BY marketId ORDER BY datetime(off_at_utc)) AS rn
      FROM dashboard_markets
      WHERE day = date('now','utc')
    )
    WHERE rn = 1
    ORDER BY datetime(off_at_utc)
    """
    return _df(con, q)

def rebuild_kpi_tiles(*, source="LIVE"):
    return kpi_tiles(source=source)


# === PATCH START ===
# 📍 TARGET: gui/dashboard_data.py:update_live_matched_odds
# 📆 PATCHED: 2025-10-18Z — replace bad import with direct Betfair JSON-RPC call
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import requests
import sqlite3, os, datetime, json
from engines.get_markets import _resolve_creds  # reuse working credential loader

def update_live_matched_odds():
    """
    Fetch live matched orders from Betfair (listCurrentOrders) and mirror them
    into autoscalp_gui.db.bf_matched_live.
    Runs safely inside Step-4 loop before settlement is available.
    """
    app_key, session_token = _resolve_creds()
    if not app_key or not session_token:
        print("[dashboard] live matched odds skipped — missing credentials")
        return

    # --- query Betfair JSON-RPC endpoint --------------------------------
    url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
    body = [{
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listCurrentOrders",
        "params": {"orderProjection": "MATCHED"},
        "id": 1
    }]

    try:
        resp = requests.post(
            url=url,
            headers={
                "X-Application": app_key,
                "X-Authentication": session_token,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            data=json.dumps(body),
            timeout=8,
        )
        resp.raise_for_status()
        payload = resp.json()[0]
        rows = (payload.get("result") or {}).get("currentOrders", [])
    except Exception as e:
        print(f"[dashboard] live matched odds fetch warn: {e}")
        return

    # --- store results ---------------------------------------------------
    db = os.path.join("data", "autoscalp_gui.db")
    con = sqlite3.connect(db)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS bf_matched_live (
        betId TEXT PRIMARY KEY,
        marketId TEXT,
        selectionId INTEGER,
        side TEXT,
        priceMatched REAL,
        sizeMatched REAL,
        matchedDate TEXT,
        updated_at TEXT DEFAULT (datetime('now','utc'))
    );
    """)
    now = datetime.utcnow().isoformat() + "Z"

    for o in rows:
        con.execute("""
            INSERT INTO bf_matched_live(betId, marketId, selectionId, side,
                                        priceMatched, sizeMatched, matchedDate, updated_at)
            VALUES(?,?,?,?,?,?,?,?)
            ON CONFLICT(betId) DO UPDATE SET
                priceMatched=excluded.priceMatched,
                sizeMatched=excluded.sizeMatched,
                matchedDate=excluded.matchedDate,
                updated_at=excluded.updated_at;
        """, (
            o.get("betId"),
            o.get("marketId"),
            o.get("selectionId"),
            o.get("side"),
            o.get("priceMatched"),
            o.get("sizeMatched"),
            o.get("matchedDate"),
            now,
        ))

    con.commit()
    con.close()
    print(f"[dashboard] live matched odds updated ({len(rows)} rows)")
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH: def get_dashboard_runners(
# 📆 PATCHED: 2025-10-17Z — unified live odds + matched + delta + conf data source
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_dashboard_runners(con, marketId: str) -> list[dict]:
    """
    Live dashboard runner feed.

    Combines:
      • bets.db.runners / bets for anchor_odd + horse_name
      • autoscalp_gui.db.inbound_oc_cache.oc1 for live odds
      • autoscalp_gui.db.bf_matched_live for matched/real odds
      • autoscalp_gui.db.decisions for confidence
    Provides dashboard columns:
      { runner, live, real, exp, delta, matched, conf }
    """
    import sqlite3, json, os

    bets_db = os.path.join("data", "bets.db")
    auto_db = os.path.join("data", "autoscalp_gui.db")

    con = sqlite3.connect(bets_db)
    con.row_factory = sqlite3.Row
    con.execute(f"ATTACH DATABASE '{auto_db}' AS auto")

    def _tbl_exists(name: str) -> bool:
        return bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (name,)
        ).fetchone())

    has_match = _tbl_exists("bf_matched_live")
    has_dec   = _tbl_exists("decisions")

    q = f"""
    SELECT
      b.selectionId,
      COALESCE(b.horse_name,b.runner_name) AS runner,
      i.oc1 AS live_odds,
      o.priceMatched AS real_odds,
      ROUND(i.oc1 - COALESCE(b.anchor_odd,i.oc1),2) AS delta,
      COALESCE(o.sizeMatched,0.0) AS matched,
      COALESCE(j.bias_conf,0.0) AS confidence,
      b.anchor_odd AS expected_odds
    FROM bets b
    LEFT JOIN auto.inbound_oc_cache i
           ON i.marketId=b.marketId AND i.selectionId=b.selectionId
    LEFT JOIN {('auto.bf_matched_live o' if has_match else '(SELECT NULL AS priceMatched, NULL AS sizeMatched, NULL AS marketId, NULL AS selectionId) o')}
           ON o.marketId=b.marketId AND o.selectionId=b.selectionId
    LEFT JOIN {('auto.decisions j' if has_dec else '(SELECT NULL AS bias_conf, NULL AS marketId, NULL AS selectionId) j')}
           ON j.marketId=b.marketId AND j.selectionId=b.selectionId
    WHERE b.marketId=?
    ORDER BY i.oc1 ASC;
    """

    rows = con.execute(q, (marketId,)).fetchall()
    con.close()

    out = []
    for r in rows:
        out.append({
            "runner": r["runner"],
            "live": r["live_odds"],
            "real": r["real_odds"],
            "exp": r["expected_odds"],
            "delta": r["delta"],
            "matched": r["matched"],
            "conf": r["confidence"],
        })
    return out
# === PATCH END ===

# 📍 TARGET: dashboard_data.py
# 🔎 SEARCH: def get_orders_tape(
# ✅ REPLACE to cap rows and keep to today (per schema:contentReference[oaicite:12]{index=12})

def get_orders_tape(con, marketId, limit=100):
    """
    Recent orders tape for this market (today), newest first.
    """
    q = f"""
    SELECT ts, runner_name, side, status, price, amount, realized_pnl
    FROM dashboard_orders_tape
    WHERE day = date('now','utc') AND marketId = ?
    ORDER BY datetime(ts) DESC
    LIMIT {int(limit)}
    """
    return _df(con, q, [marketId])

# 📍 TARGET: dashboard_data.py
# 🔎 SEARCH: def get_dropdown_markets(
# ✅ ADD: distinct dropdown list (one per marketId), ordered by off time:contentReference[oaicite:13]{index=13}

def get_dropdown_markets(con):
    """
    Returns list of label strings: 'marketId | HH:MM | course – market_name'
    (deduped one per marketId; today only).
    """
    q = """
    SELECT marketId, course, market_name, off_at_utc
    FROM (
      SELECT marketId, course, market_name, off_at_utc,
             ROW_NUMBER() OVER (PARTITION BY marketId ORDER BY datetime(off_at_utc)) AS rn
      FROM dashboard_markets
      WHERE day = date('now','utc')
    )
    WHERE rn = 1
    ORDER BY datetime(off_at_utc)
    """
    rows = _df(con, q)
    def _fmt(r):
        # Prefer concise HH:MM; off_at_utc is UTC text in DB
        hhmm = (r["off_at_utc"] or "")[11:16]
        return f"{r['marketId']} | {hhmm} | {r.get('course') or ''} – {r.get('market_name') or ''}".strip()
    return [_fmt(r) for r in rows]

# 📍 TARGET: dashboard_data.py
# 🔎 SEARCH: def get_market_liability(
# ✅ ADD: per‑market liability (matched‑open vs unmatched), formula mirrors view logic:contentReference[oaicite:14]{index=14}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH: (append at end of file, near mastery helpers)
# 📆 PATCHED: 2025-09-29T15:20Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, json, datetime
from engines.config_paths import auto_conn as _auto_conn, q_retry as _q_retry

def mastery_narrative_events(limit: int = 50) -> list[dict]:
    """
    Tail the AUTO DB 'events' table for Mastery-emitted rows.
    Looks for message starting with 'MASTERY ' and parses payload JSON.

    Returns list of dicts [{ts, exit_kind, result, detail}, …] newest first.
    """
    out: list[dict] = []
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row
        rows = _q_retry(con, """
            SELECT ts, message FROM events
             WHERE source='Mastery'
               AND message LIKE 'MASTERY %'
             ORDER BY datetime(ts) DESC
             LIMIT ?
        """, (int(limit),)).fetchall()
        con.close()
    except Exception:
        rows = []

    for r in rows:
        ts = r["ts"]
        msg = r["message"] or ""
        payload = {}
        try:
            j = msg.split(" ",1)[1] if " " in msg else msg
            payload = json.loads(j)
        except Exception:
            pass
        ek = payload.get("exit_kind") or ""
        res = payload.get("result") or payload.get("type") or ""
        # Pretty printable line
        detail = f"{res.upper()} {ek} " \
                 f"{payload.get('market','')} {payload.get('runner','')} " \
                 f"entry={payload.get('entry_odds')} exit={payload.get('exit_odds')} " \
                 f"realized={payload.get('realized')}"
        out.append({"ts": ts, "exit_kind": ek, "result": res, "detail": detail})
    return out


def get_market_liability(con, marketId):
    """
    Compute current liability for this market from orders (AUTO DB).
    - Unmatched: entry_status in ('QUEUED','PLACED')
    - Matched open: entry_status='MATCHED' and exit_status not in ('MATCHED','SETTLED')
    Risk formula:
      LAY → (odds-1)*stake
      BACK → stake
    """
    q = """
    WITH base AS (
      SELECT
        UPPER(COALESCE(entry_status,'')) AS es,
        UPPER(COALESCE(exit_status ,'')) AS xs,
        UPPER(COALESCE(side,''))        AS side,
        COALESCE(entry_matched_odds,  entry_odds,  0.0) AS odds,
        COALESCE(entry_matched_stake, entry_stake, 0.0) AS stake
      FROM orders
      WHERE marketId = ?
        AND UPPER(COALESCE(mode,'')) = 'LIVE'
        AND (role IS NULL OR role='PARENT')
    )
    SELECT
      ROUND(SUM(
        CASE WHEN es IN ('QUEUED','PLACED')
             THEN CASE WHEN side='LAY' THEN (odds-1.0)*stake ELSE stake END
             ELSE 0 END), 2) AS unmatched_liab,
      ROUND(SUM(
        CASE WHEN es='MATCHED' AND xs NOT IN ('MATCHED','SETTLED')
             THEN CASE WHEN side='LAY' THEN (odds-1.0)*stake ELSE stake END
             ELSE 0 END), 2) AS matched_open_liab
    FROM base
    """
    rows = _df(con, q, [marketId])
    return rows[0] if rows else {"unmatched_liab": 0.0, "matched_open_liab": 0.0}


def _iso_utc(s):
    """Parse 'YYYY-MM-DDTHH:MM:SS[.mmm][Z|+00:00]' into Python aware-UTC string (roundtrip-safe)."""
    if not s:
        return None
    s = s.strip()
    # Normalize trailing Z to +00:00 for sqlite datetime()
    return s.replace('Z', '+00:00')


def _bets(ro=True) -> sqlite3.Connection:
    con = connect_db(ro=not not ro)
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def _get_off_utc(mid: str) -> datetime | None:
    b = _bets(ro=True)
    try:
        row = _q(b, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        if row and row["off_at_utc"]:
            s = str(row["off_at_utc"]).strip()
            if s.endswith("Z"):
                return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
            try: return datetime.fromisoformat(s).astimezone(timezone.utc)
            except Exception: pass
        # fallback: bets table with marketStartTime
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

def minutes_to_off(mid: str, *, now_utc: datetime | None = None) -> float | None:
    """
    The ONLY minutes-to-off function the dashboard should call.
    Source of truth = Bets DB schedule (or bets.marketStartTime fallback).
    """
    now = now_utc or _now_utc()
    off = _get_off_utc(mid)
    if not off:
        return None
    return (off - now).total_seconds() / 60.0

def _resolve_days() -> list[str]:
    if DASH_DAYS:
        return list(dict.fromkeys(DASH_DAYS))
    out = []
    base = _now_utc().date()
    n = DASH_LAST if DASH_LAST and DASH_LAST>0 else 1
    for i in range(n):
        out.append((base - timedelta(days=i)).strftime("%Y-%m-%d"))
    return out

def mastery_running_narrative(day: str, marketId: str, limit: int = 50) -> list[dict]:
    """
    Returns newest-first events summarised for display.

    Each dict:
      {
        "ts": "2025-09-29T12:30:52Z",
        "kind": "hedge_matched" | "parent_placed" | "plan_shift" | "downsized" | "condition_met" | ...,
        "runner": "Swift Breeze",
        "selectionId": 12345,
        "detail": "A-HEDGE matched @ 2.84 (+£3.40) [streak +3]",
        "pnl_delta": 3.40,              # optional
        "streak": 3,                    # optional
        "meta": {...}                   # optional raw
      }
    """

def mastery_requirements(day: str, marketId: str, limit_runners: int = 8) -> list[dict]:
    """
    Returns per-runner next step + unmet conditions.

    Each dict:
      {
        "rank": 1,                         # display order (e.g., by plan_ledger priority)
        "runner": "Swift Breeze",
        "selectionId": 12345,
        "next_action": "LAY £3 @ ≤3.05",   # already sized/phrased for UI
        "progress_pct": 0.60,              # 0..1
        "eta_s": 75,                       # optional
        "needs": [
          {"label": "price",        "now": 3.18, "target": 3.05, "delta": -0.13},
          {"label": "liquidity",    "now": 420,  "target": 600,  "delta": +180, "unit": "£"},
          {"label": "volatility",   "now": "high","target": "med"},
          {"label": "CAP A slots",  "now": 0,    "target": 1},
        ],
        "global": False                     # True for market-level tasks (e.g., GREEN-UP)
      }
    """

# === PATCH START ===
# 📍 TARGET: gui/dashboard_data.py
# 📆 PATCHED: 2025-10-16Z — add missing update_mastery_replay_summary() for Replay Monitor
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def update_mastery_replay_summary(con=None):
    """
    Build or refresh the mastery_replay_summary table for the Replay Monitor.
    Populates one row per replay day (epoch_type='100d' or '365d').
    """
    import sqlite3, datetime, os

    # auto-connect if not passed in
    close_after = False
    if con is None:
        db_path = os.path.join(os.path.dirname(__file__), "..", "data", "autoscalp_gui.db")
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        close_after = True

    # ensure table exists
    con.executescript("""
    CREATE TABLE IF NOT EXISTS mastery_replay_summary (
        epoch_type TEXT NOT NULL,
        day TEXT NOT NULL,
        first_id INTEGER,
        last_id INTEGER,
        outcomes INTEGER,
        processed_id INTEGER,
        posterior_day TEXT,
        status TEXT,
        updated_at TEXT DEFAULT (datetime('now','utc')),
        PRIMARY KEY(epoch_type, day)
    );
    """)

    # pull core stats
    processed_id = 0
    try:
        r = con.execute("SELECT COALESCE(processed_outcome_id,0) FROM mastery_posteriors_progress LIMIT 1").fetchone()
        processed_id = int(r[0]) if r else 0
    except Exception:
        pass

    posterior_day = None
    try:
        r = con.execute("SELECT MAX(day) FROM mastery_posteriors").fetchone()
        posterior_day = r[0] if r else None
    except Exception:
        pass

    rows = con.execute("""
        SELECT day, MIN(id) AS first_id, MAX(id) AS last_id, COUNT(*) AS outcomes
        FROM mastery_outcomes_raw
        GROUP BY day
        ORDER BY day DESC
    """).fetchall()

    now = datetime.utcnow().isoformat()
    for r in rows:
        d, f, l, n = r["day"], r["first_id"], r["last_id"], r["outcomes"]
        epoch_type = "365d" if datetime.date.fromisoformat(d).weekday() == 6 else "100d"

        # --- guard against None or non-numeric comparisons ---
        try:
            processed_id = int(processed_id or 0)
            f = int(str(f).split("-")[-1].split(".")[0] or 0)
        except Exception:
            return  # safely skip update if values not comparable


        if processed_id < f:
            status = "⏳ Pending"
        elif f <= processed_id <= l:
            status = "🟢 Running"
        elif processed_id > l and (posterior_day is None or d > posterior_day):
            status = "⚙️ Waiting"
        else:
            status = "✅ Consolidated"

        con.execute("""
            INSERT INTO mastery_replay_summary(
                epoch_type, day, first_id, last_id, outcomes,
                processed_id, posterior_day, status, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(epoch_type, day) DO UPDATE SET
                outcomes=excluded.outcomes,
                processed_id=excluded.processed_id,
                posterior_day=excluded.posterior_day,
                status=excluded.status,
                updated_at=excluded.updated_at;
        """, (epoch_type, d, f, l, n,
              processed_id, posterior_day, status, now))

    con.commit()
    if close_after:
        con.close()
# === PATCH END ===

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Mastery integration for Dashboard panels
# 📆 PATCHED: 2025-09-29T12:00Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import datetime
from engines.mastery import event_sink, context_builder

def mastery_running_narrative(limit: int = 50) -> list[dict]:
    """
    Consume new events from mastery.event_sink and transform into narrative lines.
    Returns newest-first list of dicts: {ts, detail}.
    """
    out = []
    try:
        events = event_sink.drain_new_events()
    except Exception as e:
        return [{"ts": datetime.utcnow().isoformat(), "detail": f"[error] {e}"}]

    now_iso = datetime.utcnow().isoformat()
    for ev in events[-limit:]:
        try:
            ts = ev.get("ts") or now_iso
            kind = ev.get("kind") or ev.get("event") or "?"
            runner = ev.get("runner_name") or ev.get("runner") or ""
            detail = ev.get("detail") or json.dumps(ev, default=str)
            line = f"{ts}  {kind.upper()}  {runner}  {detail}"
            out.append({"ts": ts, "detail": line})
        except Exception:
            continue
    return out

def mastery_requirements() -> list[dict]:
    """
    Build context from mastery.context_builder and convert into 'needs' rows.
    Returns list of dicts: {runner, next_action, needs, progress, eta}.
    """
    try:
        ctx, meta = context_builder.build_context()
    except Exception as e:
        return [{"runner": "—", "next_action": f"[error] {e}", "needs": [], "progress": 0.0, "eta": None}]

    out = []
    if not meta:
        return out

    runner = meta.get("selectionId")
    course = meta.get("course")
    off = meta.get("off_at_utc")

    # For now: placeholder "needs" based directly on ctx fields.
    # Later we can refine using mastery_policy/priors.
    needs = []
    for k, v in ctx.items():
        if isinstance(v, (int, float, str)):
            needs.append({"label": k, "now": v})

    out.append({
        "runner": f"{runner} ({course})",
        "next_action": f"decide_from_policy",   # placeholder
        "needs": needs[:5],                     # limit display
        "progress": 0.5,
        "eta": off,
    })
    return out


import os, sqlite3
from datetime import datetime

from engines.config_paths import connect_db, autoscalp_db, get_mode
import sqlite3, datetime as _dt, json as _json
from engines.utils.time_utils import now_utc

# --- PATCH START: mode normaliser --------------------------------------
def _qmode(m: str | None) -> str | None:
    """
    Normalise dashboard mode:
      LIVE → 'LIVE'
      SIM  → 'TEST'
      ALL  → None (no filter)
    """
    mm = (m or "LIVE").upper()
    if mm == "SIM":
        return "TEST"
    if mm == "ALL":
        return None
    return mm
# --- PATCH END ---------------------------------------------------------


def _mode_upper() -> str:
    try:
        m = (get_mode() or os.environ.get("AUTOSCALP_MODE") or "learning").upper()
    except Exception:
        m = (os.environ.get("AUTOSCALP_MODE") or "learning").upper()
    return "LEARNING" if m not in ("TEST","LEARNING","LIVE") else m



def _autoscalp_conn(ro: bool = True) -> sqlite3.Connection:
    con = _auto_conn()
    con.row_factory = sqlite3.Row
    if ro:
        try: _q_retry(con, "PRAGMA query_only=ON")
        except Exception: pass
    return con

def _run_window(bdb: sqlite3.Connection) -> tuple[str|None, str|None]:
    """TEST: return (started_at, ended_at) of the latest run; else (None, None)."""
    try:
        row = _q_retry(bdb, "SELECT started_at, ended_at FROM sim_runs ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row and row[0]:
            return str(row[0]), (str(row[1]) if row[1] else None)
    except Exception:
        pass
    return None, None

def _hours_since(started_at: str|None) -> float:
    if not started_at: return 0.0
    try:
        start = datetime.strptime(str(started_at)[:19], "%Y-%m-%d %H:%M:%S")
        return max((now_utc() - start).total_seconds()/3600.0, 1e-9)
    except Exception:
        return 0.0

def _adb_ro():
    import os, time, sqlite3
    path = autoscalp_db()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    except Exception:
        pass

    # small retry loop to survive path flips / WAL checkpoints
    last_err = None
    for i in range(5):
        try:
            con = _auto_conn()
            con.row_factory = sqlite3.Row
            try:
                _q_retry(con, "PRAGMA journal_mode=WAL")
                _q_retry(con, "PRAGMA busy_timeout=5000")
                _q_retry(con, "PRAGMA synchronous=NORMAL")
                _q_retry(con, "PRAGMA read_uncommitted=1")
            except Exception:
                pass
            return con
        except sqlite3.OperationalError as e:
            # log once per runner every 30s — reuse GUI helper if available
            try:
                from gui.GUI import _oc_timeline_log as _oc_tl
            except Exception:
                _oc_tl = None

            if _oc_tl:
                try:
                    _oc_tl(mid, sid, f"fetch error: {e}")
                except Exception:
                    pass
            else:
                # last-resort single line
                try:
                    print(f"[OC_TIMELINE] {mid}/{sid} fetch error: {e}")
                except Exception:
                    pass

            last_err = e
            time.sleep(0.05 * (i + 1))
        except Exception as e:
            last_err = e
            break
    raise sqlite3.OperationalError(f"ADB_RO open failed: {last_err}")


# If not already defined elsewhere in this module:
try:
    ACTIVE_LETTERS  # type: ignore
except NameError:
    # ── Letter status helpers for dashboard (decisions → lights)
    ACTIVE_LETTERS = ['P', 'S', 'A', 'X', 'R', 'F', 'I']  # adjust later if needed

def _now_utc_sql(con):
    try:
        return _q_retry(con, "SELECT date('now','utc') AS d, datetime('now','utc') AS ts").fetchone()
    except Exception:
        return {"d": None, "ts": None}

def _top6_for_market(con, market_id: str, limit: int = 6):
    """
    Today’s top-6 snapshot for a market from dashboard_runners.
    Returns list of dicts with keys: rank, selectionId, name, odds, bar_color.
    """
    day_row = _q_retry(con, "SELECT date('now','utc') AS d").fetchone()
    day = day_row["d"] if day_row else None
    rows = _q_retry(con, """
        SELECT CAST(top6_rank AS INTEGER) AS rank,
               selectionId,
               runner_name,
               odd            AS odds,
               bar_color
          FROM dashboard_runners
         WHERE day = COALESCE(?, date('now','utc'))
           AND marketId = ?
           AND (COALESCE(in_top6,0)=1
                OR (top6_rank IS NOT NULL AND CAST(top6_rank AS INTEGER) BETWEEN 1 AND 6))
         ORDER BY CAST(top6_rank AS INTEGER) ASC
         LIMIT ?
    """, (day, market_id, int(limit))).fetchall()
    out = []
    for r in rows:
        out.append({
            "rank": int(r["rank"] or 0),
            "selectionId": str(r["selectionId"]),
            "name": r["runner_name"] or "—",
            "odds": (float(r["odds"]) if r["odds"] is not None else None),
            "hi": None,  # keep '—' in UI (can be wired to snapshots later)
            "lo": None,
            "bar_color": (r["bar_color"] or "")
        })
    return out

def _latest_letters_for_market(con, market_id: str):
    """
    Latest letter decision per (selectionId, letter) for THIS market for UTC today.
    Returns dict: { selectionId: { 'A': {'outcome':..,'reason':..,'t':..}, 'B': {...}, ... }, ... }
    """
    sql = """
    WITH ranked AS (
      SELECT marketId,
             selectionId,
             notes AS letter,
             COALESCE(json_extract(meta_json,'$.placement_outcome'),'') AS outcome,
             COALESCE(json_extract(meta_json,'$.why'), why)              AS reason,
             datetime(decided_at)                                        AS t,
             ROW_NUMBER() OVER (
               PARTITION BY marketId, selectionId, notes
               ORDER BY datetime(decided_at) DESC
             ) AS rn
        FROM decisions
       WHERE marketId = ?
         AND date(decided_at) = date('now','utc')
    )
    SELECT selectionId, letter, outcome, reason, t
      FROM ranked
     WHERE rn = 1
       AND letter IS NOT NULL AND TRIM(letter) <> ''
    """
    rows = _q_retry(con, sql, (market_id,)).fetchall()
    by_sid: dict[str, dict[str, dict]] = {}
    for r in rows:
        sid = str(r["selectionId"])
        L   = str(r["letter"]).upper()
        by_sid.setdefault(sid, {})[L] = {
            "outcome": (r["outcome"] or ""),
            "reason":  (r["reason"]  or ""),
            "t":       (r["t"]       or "")
        }
    return by_sid

def _dot_for(outcome: str, reason: str) -> str:
    """
    Map placement outcome/reason to a traffic-light dot.
    """
    try:
        o = (outcome or "").lower()
        r = (reason  or "").lower()
        if not o and not r:
            return "⚪"
        if "downsized" in r:
            return "🟡"  # softer restriction
        if o == "not_placed":
            return "🔴"
        if o in ("placed","matched","open_parent","open_child","queued","placed_parent","placed_child"):
            return "🟢"
        # fallbacks
        if "block" in r or "not_allowed" in r:
            return "🔴"
        return "⚪"
    except Exception:
        return "⚪"

def letter_matrix_for_market(market_id: str, *, limit: int = 6, window_sec: int = 3600) -> list[dict]:
    """
    Build the 'letter matrix' rows expected by the GUI for the primary table:
      • rank, name, odds, hi, lo
      • per-letter dot in ACTIVE_LETTERS
    We read today's top-6 from dashboard_runners and the latest decision per (sid, letter)
    from decisions (UTC day).
    """
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row  # type: ignore
    except Exception:
        # fail closed
        return []
    try:
        base = _top6_for_market(con, market_id, limit=limit)
        if not base:
            return []
        latest = _latest_letters_for_market(con, market_id)

        rows = []
        for r in base:
            sid = r["selectionId"]
            letters = {}
            for L in ACTIVE_LETTERS:
                info = (latest.get(sid, {}).get(L) or {})
                dot  = _dot_for(info.get("outcome",""), info.get("reason",""))
                letters[L] = {
                    "dot": dot,
                    "outcome": info.get("outcome",""),
                    "reason":  info.get("reason",""),
                    "t":       info.get("t","")
                }
            rows.append({
                "rank": r["rank"],
                "name": r["name"],
                "odds": r["odds"],
                "hi":   r["hi"],
                "lo":   r["lo"],
                "letters": letters,
                "selectionId": sid,
            })
        return rows
    except Exception:
        return []
    finally:
        try: con.close()
        except Exception: pass

def narratives_for_market(market_id: str, *, limit: int = 6, window_sec: int = 3600) -> list[tuple[str,str]]:
    """
    For the “Runner Gate Narratives” box: return [(runner_name, narrative_text), ...]
    Narrative chooses the 'strongest' (red > amber > green > none) letter for each runner.
    """
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row  # type: ignore
    except Exception:
        return []
    try:
        base   = _top6_for_market(con, market_id, limit=limit)
        latest = _latest_letters_for_market(con, market_id)

        def rank_status(outcome: str, reason: str) -> int:
            d = _dot_for(outcome, reason)
            return {"🔴":3, "🟡":2, "🟢":1}.get(d, 0)

        out: list[tuple[str,str]] = []
        for r in base:
            sid = r["selectionId"]
            best = ("", "", "", 0)  # (letter, outcome, reason, rank)
            for L in ACTIVE_LETTERS:
                info = latest.get(sid, {}).get(L) or {}
                o, y = (info.get("outcome","") or ""), (info.get("reason","") or "")
                rk = rank_status(o, y)
                if rk > best[3]:
                    best = (L, o, y, rk)
            if best[3] == 0:
                line = "—"
            else:
                L, o, y, _ = best
                line = f"{L}: {o or '-'} — {y or '-'}"
            out.append((r["name"], line))
        return out
    except Exception:
        return []
    finally:
        try: con.close()
        except Exception: pass

def global_gate_for_market(market_id: str, *, limit: int = 6, window_sec: int = 3600) -> str:
    """
    Overall traffic light for the primary header:
      • 'red'   if ANY latest letter in top-6 is a blocking not_placed
      • 'amber' if no red, but at least one 'downsized' reason
      • 'green' otherwise
    """
    status = "green"
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row  # type: ignore
    except Exception:
        return status
    try:
        base   = _top6_for_market(con, market_id, limit=limit)
        latest = _latest_letters_for_market(con, market_id)

        has_amber = False
        for r in base:
            sid = r["selectionId"]
            for L in ACTIVE_LETTERS:
                info = latest.get(sid, {}).get(L) or {}
                o, y = (info.get("outcome","") or "").lower(), (info.get("reason","") or "").lower()
                if "downsized" in y:
                    has_amber = True
                if o == "not_placed" or "block" in y or "not_allowed" in y:
                    return "red"
        return "amber" if has_amber else "green"
    except Exception:
        return status
    finally:
        try: con.close()
        except Exception: pass

def get_preview_rows(market_id: str, *, limit: int = 6) -> list[dict]:
    """
    Lightweight fallback for preview table when letter matrix isn’t ready.
    Shapes rows so the GUI can paint without errors.
    """
    try:
        prev = get_market_preview(market_id, limit=limit) or {}
        out = []
        for r in (prev.get("runners") or [])[:limit]:
            out.append({
                "rank": int(r.get("top6_rank") or r.get("rank") or 0),
                "runner_name": r.get("runner_name") or r.get("name") or "—",
                "odd": r.get("odd") if ("odd" in r) else r.get("odds"),
                "hi": None,
                "lo": None,
                "gate_label": "",
                "fired": 0,
                "pairs": 0,
            })
        return out
    except Exception:
        return []

def gate_widget_data(run_id: str | None = None, *, lookback_sec: int = 90) -> dict:
    """
    Compact status for the Risk card: scan decisions in the last N seconds.
    Returns {'status': 'GREEN'|'AMBER'|'RED', 'text': '...'}.
    """
    status = "GREEN"
    txt = "No recent blocks"
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row  # type: ignore
    except Exception:
        return {"status": status, "text": txt}
    try:
        rows = _q_retry(con, f"""
            SELECT COALESCE(json_extract(meta_json,'$.why'), why) AS reason,
                   COUNT(*) AS n
              FROM decisions
             WHERE datetime(decided_at) >= datetime('now','utc','-{int(lookback_sec)} seconds')
             GROUP BY reason
             ORDER BY n DESC
             LIMIT 6
        """).fetchall()
        if not rows:
            return {"status": status, "text": txt}

        red = amber = 0
        parts = []
        for r in rows:
            reason = (r["reason"] or "")
            n = int(r["n"] or 0)
            parts.append(f"{reason}:{n}")
            rl = reason.lower()
            if "downsized" in rl:
                amber += n
            if "block" in rl or "not_allowed" in rl:
                red += n

        if red > 0:
            status = "RED"
        elif amber > 0:
            status = "AMBER"
        else:
            status = "GREEN"

        return {"status": status, "text": f"Recent: {' | '.join(parts)}"}
    except Exception:
        return {"status": status, "text": txt}
    finally:
        try: con.close()
        except Exception: pass

def _shorten_why(why: str) -> str:
    w = (why or '').strip()
    lw = w.lower()
    # common normalizations
    w = w.replace('band_block:', 'band:')
    if 'b2l_not_allowed' in lw:
        return 'B2L not allowed'
    if 'band:' in lw:
        # try to surface band name
        # e.g. 'band: letter=A band=PASSIVE px=8.00 fav=False'
        import re
        m = re.search(r'band\s*=\s*([A-Z]+)', w)
        return f"band: {m.group(1).title()}" if m else "band"
    if 'inactive' in lw:
        return 'inactive odds'
    if 'rotation_hold' in lw:
        return 'rotation hold'
    if 'range_gate' in lw and 'ticks<' in lw:
        # e.g. 'range_gate:0.0ticks<3'
        import re
        m = re.search(r'ticks<\s*([0-9]+)', lw)
        need = m.group(1) if m else '?'
        return f"ticks<{need}"
    if 'too_early' in lw or 'tto>' in lw or 'minutes_to_off' in lw:
        return 'too early'
    if 'too_late' in lw:
        return 'too late'
    if 'steam' in lw and 'block' in lw:
        return 'steam block'
    if 'downsized' in lw:
        return 'downsized'
    # default: keep short
    return w[:80]

def classify_reason(why: str, letter: str) -> tuple[str, str]:
    """
    Map a `decisions.why` string to (status, short_label).
    status ∈ {'amber','red'}
    - Green is inferred from 'orders' (actual placement), not from 'decisions'.
    """
    lw = (why or '').lower()
    label = _shorten_why(why)

    # Hard blocks → red
    if ('b2l_not_allowed' in lw or
        'inactive' in lw or
        'too_late' in lw or
        'band_block' in lw or
        label in ('band', 'steam block')):
        return ('red', label)

    # Waiting / needs → amber
    if ('range_gate' in lw or 'ticks<' in lw or 'cooldown' in lw or 'rotation_hold' in lw):
        return ('amber', label)

    # Too early → amber for all EXCEPT A (A is allowed to fire anytime by policy)
    if ('too_early' in lw or 'tto>' in lw or 'minutes_to_off' in lw):
        if (letter or '').upper() == 'A':
            # treat as "ok" at global level — UI will show ⚪ unless placed
            return ('amber', 'ok')  # narrative says ok; dot stays ⚪ without an order
        return ('amber', 'too early')

    # Fallback: red (conservative)
    return ('red', label)

def _orders_open_by_sid_letter(con, mid: str) -> dict[tuple[str,str], bool]:
    """
    Map (sid, letter) -> True if an open/active parent exists in orders.
    """
    rows = _q_retry(con, """
        SELECT CAST(selectionId AS TEXT) AS sid,
               COALESCE(source, '') AS letter,
               COALESCE(status, entry_status, '') AS st
          FROM orders
         WHERE marketId = ?
           AND COALESCE(hedge_of,'') = ''
           AND COALESCE(role,'PARENT') = 'PARENT'
           AND COALESCE(st,'') NOT IN ('CANCELLED','VOIDED','LAPSED')
    """, (mid,)).fetchall() or []
    out = {}
    for r in rows:
        L = (r['letter'] or '').strip().upper()[:1]
        if L:
            out[(str(r['sid']), L)] = True
    return out

def _latest_decisions_by_sid_letter(con, mid: str, window_sec: int = 3600) -> dict[tuple[str,str], dict]:
    """
    Return latest decision row per (sid, letter) within window.
    """
    rows = _q_retry(con, f"""
        SELECT CAST(sid AS TEXT) AS sid,
               UPPER(COALESCE(letter,'')) AS letter,
               COALESCE(outcome,'') AS outcome,
               COALESCE(why,'') AS why,
               decided_at
          FROM decisions
         WHERE mid = ?
           AND datetime(decided_at) >= datetime('now','utc', ?)
         ORDER BY sid, letter, datetime(decided_at) DESC
    """, (mid, f'-{int(max(1, window_sec))} seconds')).fetchall() or []
    latest = {}
    for r in rows:
        key = (str(r['sid']), (r['letter'] or '')[:1])
        if key not in latest:
            latest[key] = dict(r)
    return latest

def _top6_for_market(con, mid: str, limit: int = 6) -> list[dict]:
    """
    Pull runner ids/names from feeder outputs; fallback to preview rows.
    """
    rows = _q_retry(con, """
        SELECT CAST(selectionId AS TEXT) AS sid,
               COALESCE(runner_name, name, '—') AS name,
               odd AS odds, hi, lo,
               CAST(top6_rank AS INTEGER) AS rank
          FROM dashboard_runners
         WHERE marketId = ?
           AND (COALESCE(in_top6,0)=1 OR (top6_rank IS NOT NULL AND CAST(top6_rank AS INTEGER) BETWEEN 1 AND 6))
         ORDER BY rank ASC
         LIMIT ?
    """, (mid, int(limit))).fetchall() or []
    if rows:
        return [dict(r) for r in rows]

    # Fallback to preview helper
    try:
        from gui.dashboard_data import get_market_preview
        prev = get_market_preview(mid, limit=limit) or {}
        out = []
        for r in (prev.get('runners') or [])[:limit]:
            out.append({
                'sid': str(r.get('selectionId') or ''),
                'name': r.get('runner_name') or r.get('name') or '—',
                'odds': r.get('odd') if ('odd' in r) else r.get('odds'),
                'hi': r.get('hi'),
                'lo': r.get('lo'),
                'rank': r.get('rank') or r.get('top6_rank') or None,
            })
        return out
    except Exception:
        return []

def dot_for(status: str) -> str:
    return {'green':'🟢','amber':'🟡','red':'🔴','unknown':'⚪'}.get(status, '⚪')

def letter_matrix_for_market(mid: str, *, limit: int = 6, window_sec: int = 3600) -> list[dict]:
    """
    Returns list of rows for the preview table:
        [{'sid': '123', 'name': 'Runner', 'odds': 3.5, 'hi':..., 'lo':..., 'letters': {'A':{'dot':'🟡','label':'ticks<3'}, ...}}, ...]
    """
    con = _auto_conn(); con.row_factory = sqlite3.Row
    try:
        runners = _top6_for_market(con, mid, limit=limit)
        if not runners:
            return []

        latest = _latest_decisions_by_sid_letter(con, mid, window_sec=window_sec)
        opened = _orders_open_by_sid_letter(con, mid)

        out = []
        for r in runners:
            sid = str(r['sid'])
            letters = {}
            for L in ACTIVE_LETTERS:
                key = (sid, L)
                if key in opened:
                    letters[L] = {'status': 'green', 'dot': dot_for('green'), 'label': 'placed'}
                elif key in latest:
                    row = latest[key]
                    # outcome is usually 'not_placed' for blocks
                    st, lab = classify_reason(row.get('why',''), L)
                    letters[L] = {'status': st, 'dot': dot_for(st), 'label': lab}
                else:
                    letters[L] = {'status': 'unknown', 'dot': dot_for('unknown'), 'label': ''}
            x = dict(r)
            x['letters'] = letters
            out.append(x)
        return out
    finally:
        try: con.close()
        except Exception: pass

def narratives_for_market(mid: str, *, limit: int = 6, window_sec: int = 3600) -> list[tuple[str,str]]:
    """
    Returns list of (runner_name, narrative_text)
    Narrative composes 'L: tag' per ACTIVE_LETTERS in A|X|R|F order.
    """
    matrix = letter_matrix_for_market(mid, limit=limit, window_sec=window_sec)
    lines: list[tuple[str,str]] = []
    for r in matrix:
        parts = []
        for L in ACTIVE_LETTERS:
            info = r['letters'].get(L, {'status':'unknown','label':''})
            tag = 'ready' if info['status'] == 'green' else (info.get('label') or '—')
            parts.append(f"{L}: {tag}")
        lines.append((r.get('name','—'), " | ".join(parts)))
    return lines

def global_gate_for_market(market_id: str, window_sec: int = 3600) -> str:
    """
    Return 'green' | 'amber' | 'red' based on recent decisions for a market.
    """
    since = f"-{int(max(1, window_sec))} seconds"
    rows = _ro("""
        SELECT COALESCE(json_extract(meta_json,'$.why'), why) AS reason
          FROM decisions
         WHERE marketId = ?
           AND datetime(decided_at) >= datetime('now', ?)
    """, (market_id, since))
    if not rows:
        return "green"
    got = set(_norm_reason(r["reason"]) for r in rows)
    if "range_gate" in got:
        return "red"
    if any(k in got for k in ("band_block","b2l_not_allowed","inactive-odds","too_late","too_early")):
        return "amber"
    return "green"



# --- Markets-left helpers -------------------------------------------------
def _auto_conn():
    # unified read-only connection to autoscalp_gui.db
    import sqlite3
    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    return con

def markets_left_today() -> int:
    """
    Count remaining races today based on settlements:
      total scheduled - markets already settled.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db
    from engines.live.settlements import settlements_db_path
    con_s = sqlite3.connect(settlements_db_path()); con_s.row_factory = sqlite3.Row
    con_a = sqlite3.connect(autoscalp_db()); con_a.row_factory = sqlite3.Row
    try:
        total = _q_retry(con_a, "SELECT COUNT(DISTINCT marketId) AS n FROM markets_schedule "
                                 "WHERE date(off_at_utc)=date('now','utc')").fetchone()["n"]
        settled = _q_retry(con_s, "SELECT COUNT(DISTINCT marketId) AS n FROM bf_cleared_orders "
                                   "WHERE date(settledDate)=date('now','utc')").fetchone()["n"]
        return max(total - settled, 0)
    except Exception:
        return 0
    finally:
        con_s.close(); con_a.close()



# --- PATCH START: mode-aware strategy view wrapper --------------------
def ensure_strategy_view_mode(qmode: str | None):
    """
    Recreate v_strategy_perf for the requested qmode.
    qmode=None → ALL modes; else 'LIVE' / 'TEST' / 'LEARNING'.
    Leaves the 'today' filter exactly as in your current view.
    """
    con = _adb_ro()
    try:
        _q_retry(con, "DROP VIEW IF EXISTS v_strategy_perf")
        mode_pred = "" if qmode is None else f" WHERE UPPER(mode)=UPPER('{qmode}') "
        con.executescript(f"""
        CREATE VIEW v_strategy_perf AS
        WITH base AS (
          SELECT
            id, role, hedge_of,
            date(COALESCE(opened_at,ts))                 AS d,
            UPPER(COALESCE(entry_status,''))             AS es,
            UPPER(COALESCE(exit_status ,''))             AS xs,
            UPPER(COALESCE(side,''))                     AS side,
            COALESCE(entry_matched_odds,  entry_odds,  0.0) AS odds,
            COALESCE(entry_matched_stake, entry_stake, 0.0) AS stake,
            COALESCE(mode,'')                            AS mode,
            CASE WHEN COALESCE(source,'')<>'' THEN UPPER(SUBSTR(source,1,1)) ELSE 'S' END AS letter,
            CASE
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='A' THEN 'ALWAYS_ON'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='B' THEN 'BTL_SCOUT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='G' THEN 'BTL_AGGR'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='X' THEN 'S4_CROSSOVER'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='R' THEN 'S5_BREAKOUT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='F' THEN 'S6_STEAM_FADE'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='M' THEN 'LADDER_STRATEGY'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='Z' THEN 'OG_BIAS'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='I' THEN 'IP1_SHOCK_DRIFT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='T' THEN 'IP2_TIRED_LEADER'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='C' THEN 'IP3_CLOSE_FINISH'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='E' THEN 'IP4_FENCE_ERROR'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='K' THEN 'IP5_COLLAPSE_FADE'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='S' THEN 'LEGACY_S'
              ELSE 'UNKNOWN'
            END AS strategy_name,
            COALESCE(net_pl, realized_pnl, 0.0)          AS pnl,
            COALESCE(opened_at,ts)                       AS opened_ts,
            COALESCE(closed_at,opened_at,ts)             AS closed_ts
          FROM orders
          WHERE date(COALESCE(opened_at,ts)) = date('now')  -- today-only (unchanged)
        ),
        mode_sel AS (SELECT * FROM base{mode_pred}),
        open_parents AS (
          SELECT id FROM mode_sel
           WHERE (role IS NULL OR role='PARENT')
             AND es='MATCHED' AND xs<>'MATCHED'
        ),
        liab_unmatched AS (
          SELECT letter,
                 SUM(CASE WHEN side='LAY' THEN (odds-1.0)*stake ELSE stake END) AS v
          FROM mode_sel
          WHERE (role IS NULL OR role='PARENT') AND es IN ('QUEUED','PLACED')
          GROUP BY letter
        ),
        liab_matched_open AS (
          SELECT letter,
                 SUM(CASE WHEN side='LAY' THEN (odds-1.0)*stake ELSE stake END) AS v
          FROM mode_sel
          WHERE (role IS NULL OR role='PARENT') AND es='MATCHED' AND xs NOT IN ('MATCHED','SETTLED')
          GROUP BY letter
        ),
        pnl_today AS (
          SELECT letter, ROUND(SUM(pnl),2) AS pnl
          FROM mode_sel
          WHERE (role IS NULL OR role='PARENT') AND xs='MATCHED'
          GROUP BY letter
        ),
        last_trade AS (
          SELECT letter, MAX(opened_ts) AS last_ts
          FROM mode_sel WHERE (role IS NULL OR role='PARENT')
          GROUP BY letter
        )
        SELECT
          COALESCE(bl.strategy_name, bl.letter) AS strategy,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='QUEUED')                                  AS p_q,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='PLACED')                                  AS p_p,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='MATCHED')                                 AS p_m,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND bl.es='QUEUED')                                    AS c_q,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND bl.es='PLACED')                                    AS c_p,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND (bl.es='MATCHED' OR bl.xs='MATCHED'))              AS c_m,
          (SELECT COUNT(*) FROM open_parents op JOIN mode_sel b2 ON b2.id=op.id AND b2.letter=bl.letter) AS open_parents,
          ROUND(COALESCE((SELECT v FROM liab_matched_open WHERE letter=bl.letter),0),2)                  AS matched_liab,
          ROUND(COALESCE((SELECT v FROM liab_unmatched   WHERE letter=bl.letter),0),2)                    AS unmatched_liab,
          ROUND(COALESCE((SELECT pnl FROM pnl_today      WHERE letter=bl.letter),0),2)                    AS pnl_today,
          COALESCE((SELECT last_ts FROM last_trade       WHERE letter=bl.letter),'')                      AS last_trade_ts
        FROM mode_sel bl
        GROUP BY bl.letter, bl.strategy_name
        ORDER BY strategy;
        """)
    finally:
        try: con.close()
        except Exception: pass
# --- PATCH END ---------------------------------------------------------


def ensure_strategy_view():
    """Create/refresh v_strategy_perf (local-day) so the dashboard rows are correct."""
    con = _adb_ro()
    try:
        # make sure matched-actual columns exist (no-op if already there)
        try:
            cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)")]
            if "entry_matched_odds" not in cols:
                _q_retry(con, "ALTER TABLE orders ADD COLUMN entry_matched_odds REAL")
            if "entry_matched_stake" not in cols:
                _q_retry(con, "ALTER TABLE orders ADD COLUMN entry_matched_stake REAL")
        except Exception:
            pass

        _q_retry(con, "DROP VIEW IF EXISTS v_strategy_perf")

        con.executescript("""
        -- Local 'today' (orders timestamps are naïve/local)
        CREATE VIEW v_strategy_perf AS
        WITH base AS (
          SELECT
            id, role, hedge_of,
            date(COALESCE(opened_at,ts))                 AS d,
            UPPER(COALESCE(entry_status,''))             AS es,
            UPPER(COALESCE(exit_status ,''))             AS xs,
            UPPER(COALESCE(side,''))                     AS side,
            COALESCE(entry_matched_odds,  entry_odds,  0.0) AS odds,
            COALESCE(entry_matched_stake, entry_stake, 0.0) AS stake,
            COALESCE(mode,'')                            AS mode,
            -- letter from source (e.g. X1/R3/…) with fallback S
            CASE WHEN COALESCE(source,'')<>'' THEN UPPER(SUBSTR(source,1,1)) ELSE 'S' END AS letter,
            -- stable name shown in the UI (keep in sync with STRAT_CODE)
            CASE
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='A' THEN 'ALWAYS_ON'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='B' THEN 'BTL_SCOUT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='G' THEN 'BTL_AGGR'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='X' THEN 'S4_CROSSOVER'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='R' THEN 'S5_BREAKOUT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='F' THEN 'S6_STEAM_FADE'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='M' THEN 'LADDER_STRATEGY'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='Z' THEN 'OG_BIAS'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='I' THEN 'IP1_SHOCK_DRIFT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='T' THEN 'IP2_TIRED_LEADER'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='C' THEN 'IP3_CLOSE_FINISH'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='E' THEN 'IP4_FENCE_ERROR'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='K' THEN 'IP5_COLLAPSE_FADE'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='S' THEN 'LEGACY_S'
              ELSE 'UNKNOWN'
            END AS strategy_name,
            COALESCE(net_pl, realized_pnl, 0.0)          AS pnl,
            COALESCE(opened_at,ts)                       AS opened_ts,
            COALESCE(closed_at,opened_at,ts)             AS closed_ts
          FROM orders
          WHERE date(COALESCE(opened_at,ts)) = date('now')
        ),
        mode_live AS (
          SELECT * FROM base WHERE UPPER(mode)=UPPER('LIVE')
        ),
        -- open parents: entry matched, exit NOT matched yet
        open_parents AS (
          SELECT id FROM mode_live
           WHERE (role IS NULL OR role='PARENT')
             AND es='MATCHED' AND xs<>'MATCHED'
        ),
        -- unmatched liab = QUEUED/PLACED parents (working)
        liab_unmatched AS (
          SELECT letter,
                 SUM(CASE WHEN side='LAY'
                          THEN (odds-1.0)*stake
                          ELSE stake END) AS v
          FROM mode_live
          WHERE (role IS NULL OR role='PARENT') AND es IN ('QUEUED','PLACED')
          GROUP BY letter
        ),
        -- matched liab = entry MATCHED & not yet closed (open risk)
        liab_matched_open AS (
          SELECT letter,
                 SUM(CASE WHEN side='LAY'
                          THEN (odds-1.0)*stake
                          ELSE stake END) AS v
          FROM mode_live
          WHERE (role IS NULL OR role='PARENT') AND es='MATCHED' AND xs NOT IN ('MATCHED','SETTLED')
          GROUP BY letter
        ),
        -- P&L today from closed parents
        pnl_today AS (
          SELECT letter, ROUND(SUM(pnl),2) AS pnl
          FROM mode_live
          WHERE (role IS NULL OR role='PARENT') AND xs='MATCHED'
          GROUP BY letter
        ),
        -- last trade time per letter
        last_trade AS (
          SELECT letter, MAX(opened_ts) AS last_ts
          FROM mode_live WHERE (role IS NULL OR role='PARENT')
          GROUP BY letter
        )
        SELECT
          COALESCE(bl.strategy_name, bl.letter) AS strategy,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='QUEUED')                                  AS p_q,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='PLACED')                                  AS p_p,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='MATCHED')                                 AS p_m,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND bl.es='QUEUED')                                    AS c_q,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND bl.es='PLACED')                                    AS c_p,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND (bl.es='MATCHED' OR bl.xs='MATCHED'))              AS c_m,
          (SELECT COUNT(*) FROM open_parents op JOIN mode_live b2 ON b2.id=op.id AND b2.letter=bl.letter) AS open_parents,
          ROUND(COALESCE((SELECT v FROM liab_matched_open WHERE letter=bl.letter),0),2)                  AS matched_liab,
          ROUND(COALESCE((SELECT v FROM liab_unmatched   WHERE letter=bl.letter),0),2)                    AS unmatched_liab,
          ROUND(COALESCE((SELECT pnl FROM pnl_today      WHERE letter=bl.letter),0),2)                    AS pnl_today,
          COALESCE((SELECT last_ts FROM last_trade       WHERE letter=bl.letter),'')                      AS last_trade_ts
        FROM mode_live bl
        GROUP BY bl.letter, bl.strategy_name
        ORDER BY strategy;
        """)
    finally:
        con.close()






# ── Worst-case loss calculators (matched/unmatched) ─────────────────────────
def _market_worst_loss(con: sqlite3.Connection, market_id: str, *, where_sql: str, args: tuple) -> float:
    """
    Return the worst-case loss for a single market over the subset of orders
    selected by `where_sql` (e.g., matched entries without exit).
    Formula:
      loss_if_wins(w) = lay_liab_on_w + (total_back_stake - back_stake_on_w)
    """
    # Aggregate BACK stakes and LAY liabilities by selection
    rows = _q_retry(con, f"""
        SELECT selectionId, UPPER(side) AS side, COALESCE(entry_odds,0.0) AS o, COALESCE(entry_stake,0.0) AS s
          FROM orders
         WHERE marketId=? {where_sql}
    """, (str(market_id), *args)).fetchall()
    if not rows:
        return 0.0

    back_by_sel: dict[str, float] = {}
    lay_by_sel:  dict[str, float] = {}
    total_back = 0.0
    for r in rows:
        sid = str(r["selectionId"])
        if r["side"] == "BACK":
            total_back += float(r["s"] or 0.0)
            back_by_sel[sid] = back_by_sel.get(sid, 0.0) + float(r["s"] or 0.0)
        else:  # LAY
            liab = float(r["s"] or 0.0) * max(0.0, float(r["o"] or 0.0) - 1.0)
            lay_by_sel[sid] = lay_by_sel.get(sid, 0.0) + liab

    if not back_by_sel and not lay_by_sel:
        return 0.0

    # Runner outcomes considered = union of seen selections
    outcomes = set(back_by_sel.keys()) | set(lay_by_sel.keys())
    worst = 0.0
    for w in outcomes:
        loss_w = lay_by_sel.get(w, 0.0) + (total_back - back_by_sel.get(w, 0.0))
        if loss_w > worst:
            worst = loss_w
    return float(worst)

def _live_matched_liab(con: sqlite3.Connection, *, mode: str = "LIVE") -> float:
    """
    Sum per-market worst-case loss for matched entries that are still open
    (parent matched, exit not matched).
    """
    cols = [c["name"] for c in _q_retry(con, "PRAGMA table_info(orders)")]
    has_role = "role" in cols
    where = "AND entry_status='matched' AND (exit_status IS NULL OR UPPER(exit_status) <> 'MATCHED')"
    if has_role:
        where += " AND role='PARENT'"
    if "mode" in cols and mode:
        where += " AND UPPER(COALESCE(mode,''))=UPPER(?)"
        args = (mode,)
    else:
        args = tuple()

    mids = [str(r[0]) for r in _q_retry(con, "SELECT DISTINCT marketId FROM orders WHERE 1=1 " + where.replace("AND ",""), args)]
    return sum(_market_worst_loss(con, mid, where_sql=where, args=args) for mid in mids)

def _live_unmatched_liab(con: sqlite3.Connection, *, mode: str = "LIVE") -> float:
    """
    Exposure for working (not matched) entries.
      BACK -> stake
      LAY  -> (odds-1)*stake
    """
    cols = [c["name"] for c in _q_retry(con, "PRAGMA table_info(orders)")]
    where = "WHERE COALESCE(entry_status,'') IN ('queued','placed')"
    args = []
    if "role" in cols:
        where += " AND role='PARENT'"
    if "mode" in cols and mode:
        where += " AND UPPER(COALESCE(mode,''))=UPPER(?)"
        args.append(mode)

    tot = 0.0
    for r in _q_retry(con, f"SELECT UPPER(side) AS side, COALESCE(entry_odds,0.0) o, COALESCE(entry_stake,0.0) s FROM orders {where}", tuple(args)):
        if r["side"] == "BACK":
            tot += float(r["s"] or 0.0)
        else:
            tot += float(r["s"] or 0.0) * max(0.0, float(r["o"] or 0.0) - 1.0)
    return float(tot)

# --- PATCH START: risk engine + worst-case helper ----------------------------
import math as _math

def _liability(side: str, odds: float, stake: float) -> float:
    """Per-order absolute liability (no sign)."""
    side = (side or "").upper()
    return stake * max(0.0, odds - 1.0) if side == "LAY" else stake

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH (regex): ^def loss_if_runner_wins\(con: sqlite3\.Connection, market_id: str, runner_id: str, \*, mode: str = "LIVE"\) -> float:
# Replace the entire function with the block below.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def portfolio_risk(mode: str = "LIVE") -> Dict[str, Any]:
    con = _adb_ro()
    try:
        preds = _predicates(con)
        has_mode = _has(con, "orders", "mode")
        pnl_col = "net_pl" if _has(con,"orders","net_pl") else ("realized_pnl" if _has(con,"orders","realized_pnl") else None)

        # matched & unmatched (today scope for liabs)
        matched_liab = 0.0
        unmatched_liab = 0.0
        if _has(con,"orders","entry_liability"):
            # unmatched = parents without matched child
            sql_un = (f"SELECT COALESCE(SUM(p.entry_liability),0.0) s FROM orders p "
                      f"WHERE date(p.opened_at)=date('now','utc') "
                      f"AND {('p.role=\'PARENT\'' if 'role' in preds['parent'] else f'(p.{preds['link']} IS NULL OR p.{preds['link']}=\'\')')} "
                      f"AND NOT EXISTS (SELECT 1 FROM orders c "
                      f"                 WHERE c.{preds['link']}=p.id AND UPPER(COALESCE(c.entry_status,''))='MATCHED')")
            args=()
            if has_mode: sql_un += " AND UPPER(COALESCE(p.mode,''))=UPPER(?)"; args=(mode.upper(),)
            unmatched_liab = float(_q_retry(con, sql_un, args).fetchone()[0] or 0.0)

            # matched liability ≈ liability that belonged to parents whose child matched today
            sql_ml = (f"SELECT COALESCE(SUM(p.entry_liability),0.0) s FROM orders p "
                      f"WHERE date(p.opened_at)=date('now','utc') "
                      f"AND {('p.role=\'PARENT\'' if 'role' in preds['parent'] else f'(p.{preds['link']} IS NULL OR p.{preds['link']}=\'\')')} "
                      f"AND EXISTS (SELECT 1 FROM orders c "
                      f"            WHERE c.{preds['link']}=p.id AND UPPER(COALESCE(c.entry_status,''))='MATCHED')")
            args=()
            if has_mode: sql_ml += " AND UPPER(COALESCE(p.mode,''))=UPPER(?)"; args=(mode.upper(),)
            matched_liab = float(_q_retry(con, sql_ml, args).fetchone()[0] or 0.0)

        # pnl / hour + wins/closed today
        pnl_per_hour = 0.0; wins_today=0; closed_today=0
        if pnl_col:
            row = _q_retry(con, f"SELECT COALESCE(SUM({pnl_col}),0.0) s, COUNT(*) n, SUM(CASE WHEN {pnl_col}>0 THEN 1 ELSE 0 END) w "
                f"FROM orders o WHERE {preds['parent']} AND o.exit_status='matched' AND date(o.closed_at)=date('now','utc')"
                + (" AND UPPER(COALESCE(o.mode,''))=UPPER(?)" if has_mode else ""),
                ((mode.upper(),) if has_mode else ())
            ).fetchone()
            today_sum = float(row["s"] or 0.0); closed_today = int(row["n"] or 0); wins_today = int(row["w"] or 0)
            # since local midnight UTC
            hrs = max( (_q_retry(con, "SELECT (strftime('%s','now')-strftime('%s',date('now')))/3600.0").fetchone()[0] or 1.0), 1e-9 )
            pnl_per_hour = today_sum / float(hrs)

        # avg win/loss per market (rolling 30d, optional)
        try:
            win_mkt = _q_retry(con, f"SELECT AVG(s) FROM (SELECT marketId, SUM({pnl_col}) s FROM orders "
                f" WHERE {preds['parent']} AND o.exit_status='matched' "
                f" AND datetime(closed_at)>=datetime('now','-30 days') "
                + (" AND UPPER(COALESCE(mode,''))=UPPER(?)" if has_mode else "")
                + " GROUP BY marketId HAVING s>0)",
                ((mode.upper(),) if has_mode else ())
            ).fetchone()[0]
            loss_mkt = _q_retry(con, f"SELECT AVG(s) FROM (SELECT marketId, SUM({pnl_col}) s FROM orders "
                f" WHERE {preds['parent']} AND o.exit_status='matched' "
                f" AND datetime(closed_at)>=datetime('now','-30 days') "
                + (" AND UPPER(COALESCE(mode,''))=UPPER(?)" if has_mode else "")
                + " GROUP BY marketId HAVING s<0)",
                ((mode.upper(),) if has_mode else ())
            ).fetchone()[0]
        except Exception:
            win_mkt = loss_mkt = 0.0

        return {
            "matched_liab": matched_liab,
            "unmatched_liab": unmatched_liab,
            "pnl_per_hour": pnl_per_hour,
            "wins_today": wins_today,
            "closed_today": closed_today,
            "avg_win_per_mkt": float(win_mkt or 0.0),
            "avg_loss_per_mkt": float(loss_mkt or 0.0),
        }
    finally:
        try: con.close()
        except Exception: pass
# --- PATCH END ---------------------------------------------------------------
# 📍 TARGET: gui/dashboard_data.py  (where your data helpers live)
# 🔎 SEARCH: add near other query helpers (new function)
def runner_book_pnl(day: str, market_id: str, *, mode: str = "LIVE") -> dict[str, float]:
    """
    Return per-runner book P&L (GBP) for one market, computed from matched legs
    in AUTO_DB.orders for the given mode (LIVE/TEST/LEARNING).
    Keys are selectionId (str). Value = profit if that runner WINS "right now".
    """
    import sqlite3
    from engines.config_paths import autoscalp_db

    con = _auto_conn()
    try:
        # Discover columns defensively
        cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)")]
        has  = lambda c: c in cols

        if not has("marketId") or not has("selectionId") or not has("side"):
            return {}

        # Use matched ENTRY legs for both parents & children (fires as fills arrive)
        legs = _q_retry(con, "SELECT selectionId, UPPER(side) AS side, entry_odds, entry_stake "
            "FROM orders "
            "WHERE marketId=? AND UPPER(COALESCE(mode,''))=UPPER(?) "
            "AND UPPER(COALESCE(entry_status,''))='MATCHED' "
            "AND entry_odds IS NOT NULL AND entry_stake IS NOT NULL",
            (str(market_id), str(mode))
        ).fetchall()

        if not legs:
            return {}

        # Build the outcome set from runners table if available (else from legs)
        sids = set()
        try:
            rws = _q_retry(con, "SELECT selectionId FROM dashboard_runners WHERE day=? AND marketId=? ORDER BY top6_rank ASC",
                (day, str(market_id))
            ).fetchall()
            sids = {str(r["selectionId"]) for r in rws}
        except Exception:
            pass
        if not sids:
            sids = {str(r["selectionId"]) for r in legs}

        # Compute book vector
        pnl = {sid: 0.0 for sid in sids}
        for r in legs:
            sid = str(r["selectionId"])
            side = (r["side"] or "").upper()
            try:
                o = float(r["entry_odds"] or 0.0)
                st = float(r["entry_stake"] or 0.0)
            except Exception:
                continue
            if o <= 0 or st <= 0:
                continue

            if side == "BACK":
                for j in pnl.keys():
                    pnl[j] += (o - 1.0) * st if j == sid else -st
            elif side == "LAY":
                liab = (o - 1.0) * st
                for j in pnl.keys():
                    pnl[j] += -liab if j == sid else +st
            else:
                # Unknown side – ignore
                pass

        return pnl
    finally:
        try: con.close()
        except Exception: pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH (regex): ^\s*#\s*public helpers\b
#    Insert this new helper below your public helpers section (or anywhere at module scope).
# 📆 PATCHED: 2025-09-21T09:05Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.config_paths import auto_conn as _auto_conn, q_retry as _q_retry
import sqlite3

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH (regex): ^\s*def get_preview_rows\(market_id:\s*str,\s*limit:\s*int\s*=\s*6\)\s*->\s*list\[dict\]:
#    Replace the entire function block below.
# 📆 PATCHED: 2025-09-21T09:45Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_preview_rows(market_id: str, limit: int = 6) -> list[dict]:
    """
    Return rows for the preview table of a WIN market:
      {rank, selectionId, runner_name, odd, hi, lo, gate_label, fired, pairs}
    Hi/Lo: oc_series (date(snapshot_ts)=UTC today). Gate: latest runner-level event when available.
    Fired/Pairs: orders (zeros are expected before the tool runs).
    """
    import sqlite3, re
    from engines.config_paths import auto_conn as _auto_conn, q_retry as _q_retry

    con = _auto_conn()
    con.row_factory = sqlite3.Row
    try:
        rows: list[dict] = []

        def _cols(tab: str) -> set:
            try:
                return {r["name"] for r in _q_retry(con, f"PRAGMA table_info({tab})")}
            except Exception:
                return set()

        has_series = bool(_q_retry(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oc_series'").fetchone())
        series_cols = _cols("oc_series") if has_series else set()
        price_col = "odd" if "odd" in series_cols else None
        has_snapshot_ts = "snapshot_ts" in series_cols

        has_events = bool(_q_retry(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone())
        events_cols = _cols("events") if has_events else set()
        ev_has_ts = "ts" in events_cols and "message" in events_cols

        orders_cols = _cols("orders")
        role_col   = "role" if "role" in orders_cols else None
        link_col   = "hedge_of" if "hedge_of" in orders_cols else ("parent_id" if "parent_id" in orders_cols else None)
        status_col = "entry_status" if "entry_status" in orders_cols else ("status" if "status" in orders_cols else None)
        ts_col     = "opened_at" if "opened_at" in orders_cols else ("ts" if "ts" in orders_cols else ("timestamp" if "timestamp" in orders_cols else None))
        id_col     = "id" if "id" in orders_cols else ("order_id" if "order_id" in orders_cols else None)

        # WIN market top-6
        top6 = _q_retry(con, """
            SELECT CAST(COALESCE(top6_rank, row_number() OVER()) AS INTEGER) AS rank,
                   selectionId, runner_name, odd
              FROM dashboard_runners
             WHERE marketId = ?
               AND (COALESCE(in_top6,0)=1 OR (top6_rank IS NOT NULL AND CAST(top6_rank AS INTEGER) BETWEEN 1 AND 6))
             ORDER BY CAST(top6_rank AS INTEGER) ASC
             LIMIT ?
        """, (market_id, int(limit))).fetchall()

        def _hi_lo(sel_id: str) -> tuple[float|None, float|None]:
            if not (has_series and has_snapshot_ts and price_col):
                return (None, None)
            try:
                r = _q_retry(con, f"""
                    SELECT MAX({price_col}) AS hi, MIN({price_col}) AS lo
                      FROM oc_series
                     WHERE marketId=? AND selectionId=?
                       AND date(snapshot_ts)=date('now','utc')
                """, (market_id, sel_id)).fetchone()
                hi = float(r["hi"]) if r and r["hi"] is not None else None
                lo = float(r["lo"]) if r and r["lo"] is not None else None
                return (hi, lo)
            except Exception:
                return (None, None)

        def _fired(sel_id: str) -> int:
            if not orders_cols:
                return 0
            try:
                if role_col:
                    r = _q_retry(con, f"""
                        SELECT COUNT(*) AS n
                          FROM orders o
                         WHERE date(COALESCE({ts_col},'now'),'utc')=date('now','utc')
                           AND o.marketId=? AND o.selectionId=? AND o.{role_col}='PARENT'
                    """, (market_id, sel_id)).fetchone()
                else:
                    if not link_col:
                        return 0
                    r = _q_retry(con, f"""
                        SELECT COUNT(*) AS n
                          FROM orders o
                         WHERE date(COALESCE({ts_col},'now'),'utc')=date('now','utc')
                           AND o.marketId=? AND o.selectionId=?
                           AND (o.{link_col} IS NULL OR o.{link_col}='')
                    """, (market_id, sel_id)).fetchone()
                return int(r["n"] if r else 0)
            except Exception:
                return 0

        def _pairs(sel_id: str) -> int:
            if not (orders_cols and link_col and status_col and id_col):
                return 0
            try:
                r = _q_retry(con, f"""
                    WITH parents AS (
                        SELECT p.{id_col} AS pid
                          FROM orders p
                          JOIN orders c ON c.{link_col}=p.{id_col}
                         WHERE date(COALESCE(p.{ts_col},'now'),'utc')=date('now','utc')
                           AND p.marketId=? AND p.selectionId=?
                           AND UPPER(COALESCE(p.{status_col},'')) IN ('MATCHED','EXECUTION_COMPLETE','FILLED')
                           AND UPPER(COALESCE(c.{status_col},'')) IN ('MATCHED','EXECUTION_COMPLETE','FILLED')
                         GROUP BY 1
                    )
                    SELECT COUNT(*) AS n FROM parents
                """, (market_id, sel_id)).fetchone()
                return int(r["n"] if r else 0)
            except Exception:
                return 0

        def _gate_label(sel_id: str) -> str:
            # Latest "gate block | ..." event for this runner today, if available
            if not (has_events and ev_has_ts):
                return "—"
            try:
                row = _q_retry(con, """
                    SELECT message
                      FROM events
                     WHERE date(ts)=date('now','utc')
                       AND message LIKE '%gate block |%'
                       AND message LIKE ?
                       AND message LIKE ?
                     ORDER BY datetime(ts) DESC
                     LIMIT 1
                """, (f"%marketId={market_id}%", f"%selectionId={sel_id}%")).fetchone()
                if not row or not row["message"]:
                    return "—"
                msg = str(row["message"])
                # Extract label after "gate block |"
                label = msg.split("gate block |", 1)[-1].strip()
                label = label.split("|", 1)[0].strip()
                # Normalise common keys to readable labels
                key = re.sub(r"[^a-z0-9\-_\s]", "", label.lower()).replace(" ", "_")
                mapping = {
                    "too_early": "Too early (TTO > 20m)",
                    "inactive_odds": "Inactive odds",
                    "inactive-odds": "Inactive odds",
                    "run_active": "Run (active)",
                    "run-active": "Run (active)",
                    "run_passive": "Run (passive)",
                    "run-passive": "Run (passive)",
                    "too_late": "Too late",
                    "fav_steam_block": "Fav steaming – LAY blocked",
                    "mild_steam_downsized": "Fav mildly shortening – downsized",
                }
                return mapping.get(key, label or "—")
            except Exception:
                return "—"

        for r in top6:
            sel = str(r["selectionId"])
            hi, lo = _hi_lo(sel)
            rows.append({
                "rank": int(r["rank"] or 0),
                "selectionId": sel,
                "runner_name": r["runner_name"] or "—",
                "odd": float(r["odd"]) if r["odd"] is not None else None,
                "hi": hi, "lo": lo,
                "gate_label": _gate_label(sel),
                "fired": _fired(sel),
                "pairs": _pairs(sel),
            })

        # Fallback from preview if feeder hasn't populated
        if not rows:
            try:
                prev = get_market_preview(market_id, limit=limit) or {}
                rr = prev.get("runners", []) or []
                for i, x in enumerate(rr[:limit], start=1):
                    sel = str(x.get("selectionId") or "")
                    hi, lo = _hi_lo(sel)
                    rows.append({
                        "rank": i,
                        "selectionId": sel,
                        "runner_name": x.get("runner_name") or x.get("name") or "—",
                        "odd": float(x.get("odd", x.get("odds"))) if (x.get("odd") or x.get("odds")) is not None else None,
                        "hi": hi, "lo": lo,
                        "gate_label": _gate_label(sel),
                        "fired": _fired(sel),
                        "pairs": _pairs(sel),
                    })
            except Exception:
                pass

        rows.sort(key=lambda z: int(z.get("rank", 9999)))
        return rows[:limit]
    finally:
        try: con.close()
        except Exception: pass

# --- DB helpers ---------------------------------------------------------------
def _adb() -> sqlite3.Connection:
    con = _auto_conn()
    con.row_factory = sqlite3.Row
    return con

def _bdb(ro: bool = True) -> sqlite3.Connection:
    con = connect_db(ro=ro)
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def _tbl_exists(con: sqlite3.Connection, name: str) -> bool:
    r = _q_retry(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(r)

def _has_col(con: sqlite3.Connection, t: str, c: str) -> bool:
    try:
        for r in _q_retry(con, f"PRAGMA table_info({t})"):
            nm = r["name"] if isinstance(r, sqlite3.Row) else r[1]
            if nm == c: return True
    except Exception:
        pass
    return False

def _predicates(con: sqlite3.Connection) -> Dict[str, str]:
    cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)")]
    link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
    role = "role" if "role" in cols else None
    if role:
        return {"parent": "o.role='PARENT'", "child": "o.role='CHILD'", "link": (link or "")}
    return {"parent": f"(o.{link} IS NULL OR o.{link}='')" if link else "1=1",
            "child": f"(o.{link} IS NOT NULL AND o.{link}<>'')" if link else "0=1",
            "link": (link or "")}

# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH (regex): ^def kpi_tiles\(
# (Insert the helpers just above kpi_tiles or anywhere at module top.)

# --- PATCH START: odds feed autodetect helpers -------------------------------
def _db_has_table(db: sqlite3.Connection, name: str) -> bool:
    return bool(_q_retry(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone())

def _inbound_latest_rows(db: sqlite3.Connection, market_id: str) -> int:
    """How many inbound rows exist for this market (rough proxy for 'is this the live feed?')."""
    if not _db_has_table(db, "inbound_oc_cache"):
        return 0
    try:
        return int(_q_retry(db, "SELECT COUNT(*) FROM inbound_oc_cache WHERE marketId=?",
            (str(market_id),)
        ).fetchone()[0] or 0)
    except Exception:
        return 0

def _choose_odds_db(bdb: sqlite3.Connection, adb: sqlite3.Connection, market_id: str) -> sqlite3.Connection | None:
    """Return the DB (BETS or AUTO) that currently has inbound odds for this market."""
    nb = _inbound_latest_rows(bdb, market_id)
    na = _inbound_latest_rows(adb, market_id)
    if na > 0 or nb > 0:
        return adb if na >= nb else bdb
    # no market-specific rows yet — fall back to any inbound presence
    for db in (adb, bdb):
        try:
            if _db_has_table(db, "inbound_oc_cache"):
                n = int(_q_retry(db, "SELECT COUNT(*) FROM inbound_oc_cache").fetchone()[0] or 0)
                if n > 0:
                    return db
        except Exception:
            pass
    return None

def _odds_tail(db: sqlite3.Connection, market_id: str) -> dict[str, dict]:
    """
    Latest oc1 + band tail per selectionId for a market from the chosen feed DB.
    Returns {sid: {'oc1': float|None, 'band': list|None}}
    """
    out = {}
    if not _db_has_table(db, "inbound_oc_cache"):
        return out
    cur = _q_retry(db, "SELECT selectionId, oc1, oc1_band_json FROM inbound_oc_cache "
        "WHERE marketId=? ORDER BY id DESC", (str(market_id),)
    )
    for r in cur:
        sid = str(r["selectionId"])
        if sid in out:
            continue
        oc1 = r["oc1"]
        band = None
        try:
            band = _json.loads(r["oc1_band_json"]) if r["oc1_band_json"] else None
        except Exception:
            band = None
        out[sid] = {"oc1": (float(oc1) if oc1 is not None else None), "band": band}
    return out

def _runner_name(bdb: sqlite3.Connection, market_id: str, selection_id: str) -> str:
    if not _db_has_table(bdb, "runners"):
        return selection_id
    cols = [c["name"] for c in _q_retry(bdb, "PRAGMA table_info(runners)")]
    name_col = next((c for c in ("runner","name","runner_name") if c in cols), None)
    if not name_col:
        return selection_id
    row = _q_retry(bdb, f"SELECT {name_col} FROM runners WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
        (str(market_id), str(selection_id))
    ).fetchone()
    return str(row[0]) if row and row[0] else selection_id

def _bar_from_odds(odds: float) -> float:
    """1.01 → 1.0 (full), 1000 → 0.0 (empty)."""
    lo, hi = 1.01, 1000.0
    v = (hi - float(odds)) / (hi - lo)
    return 0.0 if v < 0 else (1.0 if v > 1 else v)
# --- PATCH END ----------------------------------------------------------------
def _live_open_exposure() -> float:
    """
    Unrealized exposure (liability) for LIVE when parent+hedge are on ONE row.
    Count rows where entry_status='matched' AND exit_status!='matched'.
      LAY  -> (entry_odds - 1) * entry_stake
      BACK -> entry_stake
    """
    adb = _auto_conn()
    adb.row_factory = sqlite3.Row
    try:
        # orders has (mode, side, entry_* , exit_*)
        cols = [r["name"] for r in _q_retry(adb, "PRAGMA table_info(orders)")]
        if not all(c in cols for c in ("mode", "entry_status", "exit_status", "side", "entry_stake", "entry_odds")):
            return 0.0

        # LIVE only; parent matched; hedge not yet matched
        rows = _q_retry(adb, "SELECT side, entry_stake, entry_odds "
            "FROM orders "
            "WHERE mode='LIVE' AND entry_status='matched' "
            "AND (exit_status IS NULL OR exit_status<>'matched')"
        ).fetchall()

        exposure = 0.0
        for r in rows:
            side = (r["side"] or "").upper()
            stake = float(r["entry_stake"] or 0.0)
            odds  = float(r["entry_odds"]  or 0.0)
            if side.startswith("LAY"):
                exposure += stake * max(0.0, odds - 1.0)
            else:  # BACK
                exposure += stake
        return float(exposure)
    except Exception:
        return 0.0
    finally:
        try: adb.close()
        except Exception: pass


import re
from typing import Tuple, Dict, Any, List

_REASON_NEEDS_MAP = [
    # (pattern, blocker_label, need_label, severity)
    (r"\bbias_veto\b",                    "Bias contradicts plan",         "Align plan with bias / wait for flip",        "RED"),
    (r"\bb2l_not_allowed_for_letter\b",   "B2L not allowed for letter",    "Use L2B entry (or allow B2L)",                "RED"),
    (r"\bband_block:.*\bIGNORED\b",       "Price band IGNORED",            "Move into ACTIVE band",                        "RED"),
    (r"\bband_block:.*\bPASSIVE\b",       "Price band PASSIVE",            "Move into ACTIVE band",                        "AMBER"),
    (r"\brange_gate\b",                   "Range gate closed",             "Meet range/dir/time window",                   "AMBER"),
    (r"\brotation_block\b",               "Rotation hold (same selection)","Rotate or wait next runner",                   "AMBER"),
    (r"\bstale\b",                        "Stale tape",                    "Get fresh odds/trades",                        "AMBER"),
]

def _gate_rollup_from_decisions(run_id: str, lookback_sec: int = 90) -> Tuple[str, str]:
    """
    Returns (STATUS, TEXT) where STATUS in {RED, AMBER, GREEN}.
    TEXT is a compact 'Blocking: … | Needs: …' sentence.
    Pulls from today's decisions (not_placed) within lookback_sec.
    """
    con = _auto_conn()
    blockers: Dict[str, int] = {}
    needs: Dict[str, int] = {}
    worst = "GREEN"

    def worse(a: str, b: str) -> str:
        order = {"GREEN": 0, "AMBER": 1, "RED": 2}
        return a if order[a] >= order[b] else b

    try:
        rows = con.execute("""
            SELECT COALESCE(why,'') AS why, COALESCE(meta_json,'') AS meta_json
              FROM decisions
             WHERE date(decided_at)=date('now','utc')
               AND datetime(decided_at) >= datetime('now','utc', ?)
               AND LOWER(COALESCE(outcome,''))='not_placed'
             ORDER BY decided_at DESC
             LIMIT 400
        """, (f"-{int(max(15, lookback_sec))} seconds",)).fetchall() or []

        for r in rows:
            why = (r["why"] or "").lower()
            for pat, block_label, need_label, sev in _REASON_NEEDS_MAP:
                if re.search(pat, why):
                    blockers[block_label] = blockers.get(block_label, 0) + 1
                    needs[need_label]     = needs.get(need_label, 0) + 1
                    worst = worse(worst, sev)
                    break

            # If meta_json exists and has 'needs', surface them too
            try:
                import json
                mj = json.loads(r["meta_json"] or "{}")
                for n in (mj.get("needs") or []):
                    txt = str(n).strip()
                    if txt:
                        needs[txt] = needs.get(txt, 0) + 1
                        worst = worse(worst, "AMBER")
            except Exception:
                pass

    except Exception:
        # fail-open: no data means GREEN/no blockers
        pass
    finally:
        try: con.close()
        except Exception: pass

    if not blockers and not needs:
        return ("GREEN", "Ready: no active blocks in the last 90s")

    def _top(d: Dict[str,int], k=3) -> List[str]:
        return [f"{name}×{cnt}" for name, cnt in sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:k]]

    parts = []
    if blockers:
        parts.append("Blocking: " + "; ".join(_top(blockers)))
    if needs:
        parts.append("Needs: " + "; ".join(_top(needs)))

    return (worst, " | ".join(parts))

def gate_widget_data(run_id=None, lookback_sec: int = 90) -> dict:
    """
    Summarise recent gate reasons for the traffic-light widget.

    Status model:
      • RED   → any 'range_gate' (hard block) in the window
      • AMBER → otherwise, if any band_block / b2l_not_allowed / inactive-odds present
      • GREEN → otherwise
    Text: "Blocking: band_block=12, b2l_not_allowed=3" or a ready message.
    """
    counts = gate_reason_counts_recent(lookback_sec=lookback_sec)
    if not counts:
        return {"status": "GREEN", "text": "Ready: no active blocks in the last 90s"}

    totals = { (c["reason"] or "").lower(): int(c["count"] or 0) for c in counts }
    status = "GREEN"
    if totals.get("range_gate", 0) > 0:
        status = "RED"
    elif any(totals.get(k, 0) > 0 for k in ("band_block","b2l_not_allowed","inactive-odds","too_late","too_early")):
        status = "AMBER"

    # compact text
    top = sorted(counts, key=lambda x: -int(x.get("count", 0)))[:4]
    txt = "Blocking: " + ", ".join(f"{c['reason']}={c['count']}" for c in top)
    return {"status": status, "text": txt}



def _live_realized_today() -> float:
    """
    Realized LIVE P&L for today.
    Prefer pnl_trades(source='LIVE'); fallback to orders.net_pl (matched parents, today).
    """
    bdb = connect_db(ro=True)
    bdb.row_factory = sqlite3.Row
    try:
        # pnl_trades first
        if _q_retry(bdb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_trades'").fetchone():
            cols = [r["name"] for r in _q_retry(bdb, "PRAGMA table_info('pnl_trades')")]
            if "source" in cols:
                row = _q_retry(bdb, "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE source='LIVE' AND date(created_at)=date('now','utc')"
                ).fetchone()
                val = float(row[0] or 0.0)
                if abs(val) > 0.0:
                    return val

        # fallback to orders.net_pl
        adb = _auto_conn()
        adb.row_factory = sqlite3.Row
        cols = [r["name"] for r in _q_retry(adb, "PRAGMA table_info(orders)")]
        pnl_col = "net_pl" if "net_pl" in cols else ( "pnl_amount" if "pnl_amount" in cols else None )
        if pnl_col:
            mode_clause = " AND mode='LIVE'" if "mode" in cols else ""
            row = _q_retry(adb, f"""
                SELECT COALESCE(SUM({pnl_col}),0.0)
                FROM orders
                WHERE entry_status='matched'
                  AND date(COALESCE(closed_at, date('now','utc'))) = date('now','utc')
                  {mode_clause}
                """
            ).fetchone()
            return float(row[0] or 0.0)
        return 0.0
    except Exception:
        return 0.0
    finally:
        try: bdb.close()
        except Exception: pass
        try: adb.close()
        except Exception: pass

def loss_if_runner_wins(con: sqlite3.Connection, market_id: str, runner_id: str, *, mode: str = "LIVE") -> float:
    """Worst-case loss if `runner_id` wins for the current matched/open parents in this market."""
    cols = [c["name"] for c in _q_retry(con, "PRAGMA table_info(orders)")]
    where = (" AND entry_status='matched' AND (exit_status IS NULL OR UPPER(exit_status) <> 'MATCHED')"
             + (" AND role='PARENT'" if "role" in cols else "")
             + (" AND UPPER(COALESCE(mode,''))=UPPER(?)" if "mode" in cols else ""))
    args = (mode,) if "mode" in cols else tuple()

    # total back & lay per selection
    rows = _q_retry(con, f"""
        SELECT selectionId, UPPER(side) AS side, COALESCE(entry_odds,0.0) AS o, COALESCE(entry_stake,0.0) AS s
          FROM orders
         WHERE marketId=? {where}
    """, (str(market_id), *args)).fetchall()
    if not rows:
        return 0.0

    total_back = sum(float(r["s"] or 0.0) for r in rows if r["side"]=="BACK")
    back_w     = sum(float(r["s"] or 0.0) for r in rows if r["side"]=="BACK" and str(r["selectionId"])==str(runner_id))
    lay_liab_w = sum(float(r["s"] or 0.0) * max(0.0, float(r["o"] or 0.0)-1.0)
                     for r in rows if r["side"]=="LAY" and str(r["selectionId"])==str(runner_id))
    return float(lay_liab_w + (total_back - back_w))


def risk_exposure(*, source: str = "LIVE") -> dict:
    con = _auto_conn(); con.row_factory = sqlite3.Row
    out = dict(
        open_parents=0, open_child=0,
        matched_liab=0.0, unmatched_liab=0.0,
        trade_attempts=0, trade_success=0
    )
    try:
        rows = _q_retry(con, """
            SELECT role, entry_status, exit_status, side, entry_odds, entry_stake
              FROM orders
             WHERE UPPER(mode)=?
               AND date(COALESCE(opened_at, ts))=date('now','utc')
        """, (source.upper(),)).fetchall()
        for r in rows:
            side = (r["side"] or "").upper()
            odds = float(r["entry_odds"] or 0.0)
            stake = float(r["entry_stake"] or 0.0)
            liab = (odds - 1.0) * stake if side == "LAY" else stake
            role = (r["role"] or "PARENT").upper()
            es = (r["entry_status"] or "").upper()
            xs = (r["exit_status"] or "").upper()

            if role == "PARENT":
                out["trade_attempts"] += 1
                if es == "MATCHED" and xs != "MATCHED":
                    out["open_parents"] += 1
                    out["matched_liab"] += liab
                elif es in ("QUEUED", "PLACED"):
                    out["unmatched_liab"] += liab
            else:  # CHILD
                if es in ("QUEUED", "PLACED"):
                    out["open_child"] += 1
                if xs == "MATCHED":
                    out["trade_success"] += 1
        return out
    finally:
        con.close()


# -----------------------------------------------------------------------------#
#                        KPI TILES + BASIC COUNTS                              #
# -----------------------------------------------------------------------------#
def _tbl_exists(conn, name: str) -> bool:
    try:
        return bool(_q_retry(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())
    except Exception:
        return False


def races_today(*, source: str = "TEST") -> int:
    b = _bdb(ro=True)
    try:
        # Prefer markets_schedule (sim day)
        if _tbl_exists(b, "markets_schedule"):
            row = _q_retry(b, "SELECT COUNT(DISTINCT marketId) AS n FROM markets_schedule "
                            "WHERE date(off_at_utc)=date('now','utc')").fetchone()
            return int(row["n"] if row and "n" in row.keys() else (row[0] if row else 0))
        # Fallback: bets
        row = _q_retry(b, "SELECT COUNT(DISTINCT marketId) AS n FROM bets "
                        "WHERE date(marketStartTime)=date('now','utc')").fetchone()
        return int(row["n"] if row and "n" in row.keys() else (row[0] if row else 0))
    finally:
        try: b.close()
        except Exception: pass

def runners_today(*, source: str = "TEST") -> int:
    b = _bdb(ro=True)
    try:
        if _tbl_exists(b, "bets"):
            row = _q_retry(b, "SELECT COUNT(*) AS n FROM bets WHERE date(marketStartTime)=date('now','utc')").fetchone()
            return int(row["n"] if row and "n" in row.keys() else (row[0] if row else 0))
        return 0
    finally:
        try: b.close()
        except Exception: pass

def first_oc1_in_minutes(schedule: dict[int,int], *, source: str = "TEST"):
    """Return small 'Xm Ys' string until the first OC1 due; '—' if unknown."""
    b = _bdb(ro=True)
    try:
        if not _tbl_exists(b, "markets_schedule"):
            return "—"
        # sim speed
        sp = 0.5
        if _tbl_exists(b, "sim_params"):
            row = _q_retry(b, "SELECT v FROM sim_params WHERE k='speed_min_per_sec'").fetchone()
            try: sp = float(row["v"] if row and "v" in row.keys() else (row[0] if row else "0.5"))
            except Exception: pass
        now = _now_utc()
        row = _q_retry(b, "SELECT off_at_utc FROM markets_schedule ORDER BY off_at_utc ASC LIMIT 1").fetchone()
        if not row: return "—"
        try:
            off = datetime.strptime(row["off_at_utc"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return "—"
        # When OC1 is scheduled in real minutes
        # (we compress using speed_min_per_sec → virtual minutes elapse faster)
        minutes_to_off = (off - now).total_seconds() * sp
        oc1_cut = schedule.get(1, 80)  # default 80
        delta = minutes_to_off - oc1_cut
        if delta <= 0:
            return "0m"
        m = int(delta // 60); s = int(delta % 60)
        return f"{m}m {s}s" if m else f"{s}s"
    finally:
        try: b.close()
        except Exception: pass

# -----------------------------------------------------------------------------#
#                        HEALTH / TIMELINE / RISK                              #
# -----------------------------------------------------------------------------#
def oc_data_health(*, source: str = "TEST") -> dict:
    """
    coverage_pct  : % runners with any oc1 present (or band samples)
    band_depth_med: median band length for oc1 bands (if arrays are stored)
    stale_pct     : % inbound_oc_cache rows older than 2 min (sim)
    liq_ok/conf_ok/chap_ok : simple ‘exists’ flags
    """
    out = dict(coverage_pct=0.0, band_depth_med="—", stale_pct=0.0,
               liq_ok=False, conf_ok=False, chap_ok=False)
    b = _bdb(ro=True)
    try:
        if not _tbl_exists(b, "inbound_oc_cache"):
            return out
        rows = _q_retry(b, "SELECT oc1, oc1_band_json, last_sync_ts FROM inbound_oc_cache").fetchall()
        n = len(rows) or 1
        cov = 0
        depths = []
        stale = 0
        now = _now_utc()
        for r in rows:
            oc1 = r["oc1"] if "oc1" in r.keys() else r[0]
            bj  = r["oc1_band_json"] if "oc1_band_json" in r.keys() else r[1]
            ts  = r["last_sync_ts"] if "last_sync_ts" in r.keys() else r[2]
            if oc1 is not None or (bj and len(str(bj))>2):
                cov += 1
            if bj:
                try:
                    arr = json.loads(bj)
                    if isinstance(arr, list): depths.append(len(arr))
                except Exception:
                    pass
            try:
                if ts:
                    t = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(timezone.utc)
                    if (now - t).total_seconds() > 120:  # > 2 minutes = stale in sim
                        stale += 1
            except Exception:
                pass
        def _median(L):
            if not L: return "—"
            L = sorted(L); k = len(L)//2
            return (L[k] if len(L)%2 else (L[k-1]+L[k])/2)
        out["coverage_pct"]   = 100.0 * cov / n
        out["band_depth_med"] = _median(depths)
        out["stale_pct"]      = 100.0 * stale / n

        # simple green lights if related tables exist
        out["liq_ok"]  = _tbl_exists(b, "inbound_bets_min")
        out["conf_ok"] = _tbl_exists(b, "mastery_state")
        out["chap_ok"] = _tbl_exists(b, "chapters") or _tbl_exists(b, "stories")
        return out
    finally:
        try: b.close()
        except Exception: pass

def live_timeline(_: list) -> list[dict]:
    """
    TEST: build timeline from markets_schedule (seeded by the simulator),
    showing minutes-to-off for every simulated market.
    LEARNING/LIVE: keep showing upcoming markets from bets.
    """
    rows: list[dict] = []
    mode = _mode_upper()
    bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    try:
        if mode == "TEST":
            now = now_utc()
            rs = _q_retry(bdb, "SELECT marketId, off_at_utc FROM markets_schedule ORDER BY off_at_utc"
            ).fetchall()
            for r in rs:
                try:
                    off = datetime.strptime(r["off_at_utc"][:19], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    continue
                mto = (off - now).total_seconds()/60.0
                rows.append({"market": r["marketId"], "mto": round(mto, 1)})
        else:
            now = now_utc()
            rs = _q_retry(bdb, "SELECT marketId, marketStartTime FROM bets "
                "WHERE datetime(COALESCE(marketStartTime,'')) >= datetime('now','-6 hours','utc') "
                "GROUP BY marketId ORDER BY marketStartTime"
            ).fetchall()
            for r in rs:
                try:
                    off = datetime.fromisoformat(str(r["marketStartTime"]).replace("Z","+00:00"))
                except Exception:
                    continue
                mto = (off - now).total_seconds()/60.0
                rows.append({"market": r["marketId"], "mto": round(mto, 1)})
    finally:
        try: bdb.close()
        except Exception: pass
    return rows


def strategy_by_tto(*, source: str = "TEST") -> list[dict]:
    """
    Optional rollup. If there are decisions with ctx.tto_window and orders.net_pl,
    aggregate by bucket. If not present, return [] and the table will show placeholders.
    """
    a = _adb()
    try:
        if not _tbl_exists(a, "orders") or not _tbl_exists(a, "decisions"):
            return []
        if not _has_col(a, "orders", "net_pl"):
            return []

        # Join last few hundred decisions → order id → net_pl / status
        rows = _q_retry(a, """
          SELECT
            json_extract(d.meta_json, '$.ctx.tto_window') AS bucket,
            o.net_pl AS pl, o.entry_status AS st
          FROM decisions d
          JOIN orders o ON o.id = d.order_id
          WHERE d.meta_json IS NOT NULL
            AND o.closed_at IS NOT NULL
          LIMIT 1000
        """).fetchall()

        if not rows: return []
        agg = {}
        for r in rows:
            bkt = r["bucket"] if "bucket" in r.keys() else r[0] or "—"
            pl  = float(r["pl"] if "pl" in r.keys() else r[1] or 0.0)
            st  = r["st"] if "st" in r.keys() else r[2]
            hit = 1 if (st == "matched") else 0
            x = agg.setdefault(bkt, {"pnl":0.0,"hits":0,"n":0})
            x["pnl"] += pl; x["hits"] += hit; x["n"] += 1

        out = []
        for bkt, x in sorted(agg.items()):
            hit_pct = (x["hits"]/x["n"]*100.0) if x["n"]>0 else 0.0
            ev = (x["pnl"]/x["n"]) if x["n"]>0 else 0.0
            out.append({"bucket": bkt, "ev": f"{ev:+.2f}", "hit": f"{hit_pct:.0f}%", "pnl": f"£{x['pnl']:.2f}", "n": x["n"]})
        return out
    finally:
        try: a.close()
        except Exception: pass
def _mode_clause(con: sqlite3.Connection, table: str, source: str) -> Tuple[str, tuple]:
    if _has_col(con, table, "mode"):
        return " AND mode=? ", (source,)
    return "", tuple()

def _parent_pred(con: sqlite3.Connection) -> str:
    cols = [r[1] for r in _q_retry(con, "PRAGMA table_info(orders)")]
    if "hedge_of" in cols:
        return "(hedge_of IS NULL OR hedge_of='')"
    if "parent_id" in cols:
        return "(parent_id IS NULL OR parent_id='')"
    return "1=1"


def runner_watch(*, source: str = "ALL", limit: int = 12) -> list[dict]:
    """
    Return recent parent orders with PL and TTO:
      [{market, runner, cap, side, entry, odds, status, pl, tto}]
    """
    bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
    adb = _auto_conn() 
    adb.row_factory = sqlite3.Row
    try:
        cols = [r["name"] for r in _q_retry(adb, "PRAGMA table_info(orders)")]
        link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        parent_pred = f"({link} IS NULL OR {link}='')" if link else "1=1"
        has_mode = ("mode" in cols)
        has_pl   = any(c in cols for c in ("net_pl","pnl_amount","pnl"))
        pnl_col  = "net_pl" if "net_pl" in cols else ("pnl_amount" if "pnl_amount" in cols else ("pnl" if "pnl" in cols else None))

        where = f"{parent_pred}"
        args: list = []
        if has_mode and (source or "").upper() in ("TEST","LEARNING","LIVE"):
            where += " AND mode=?"; args.append((source or "").upper())

        rows = _q_retry(adb, f"""
            SELECT id, marketId, selectionId, side, entry_odds, entry_stake, entry_status,
                   opened_at, closed_at, {pnl_col if pnl_col else 'NULL'} AS pl
            FROM orders
            WHERE {where}
            ORDER BY datetime(COALESCE(opened_at,'')) DESC
            LIMIT ?
            """,
            (*args, int(limit))
        ).fetchall()

        # helper: TTO label from BETS_DB schedule
        def tto_label(mid: str) -> str:
            try:
                r = _q_retry(bdb, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? ORDER BY id DESC LIMIT 1", (mid,)).fetchone()
                if not r or not r[0]: return "—"
                off = _dt.datetime.strptime(str(r[0])[:19], "%Y-%m-%d %H:%M:%S")
                mins = int((off - _dt.datetime.utcnow()).total_seconds()/60)
                return "off" if mins <= 0 else f"{mins}m"
            except Exception:
                return "—"

        out = []
        for r in rows:
            mid, sid = str(r["marketId"]), str(r["selectionId"])
            out.append({
                "market": mid,
                "runner": _runner_name(bdb, mid, sid),
                "cap": "—",
                "side": ("L/B" if str(r["side"]).upper().startswith("LAY") else "B/L"),
                "entry": f"£{float(r['entry_stake'] or 0.0):.2f}",
                "odds":  f"{float(r['entry_odds'] or 0.0):.2f}",
                "status": r["entry_status"] or "",
                "pl":    (f"£{float(r['pl'] or 0.0):.2f}" if has_pl else "£0.00"),
                "tto":   tto_label(mid),
            })
        return out
    finally:
        try: adb.close()
        except Exception: pass
        try: bdb.close()
        except Exception: pass

# --- Optional feed (used by the log panel) -----------------------------------
def events_feed(*, source: str = "TEST", limit: int = 5) -> list[str]:
    b = _bdb(ro=True)
    msgs = []
    try:
        if _tbl_exists(b, "mastery_events"):
            rows = _q_retry(b, "SELECT event_type, details_json, datetime(created_at) AS ts "
                "FROM mastery_events ORDER BY rowid DESC LIMIT ?", (int(limit),)
            ).fetchall()
            for r in rows:
                typ = r["event_type"] if "event_type" in r.keys() else r[0]
                det = r["details_json"] if "details_json" in r.keys() else r[1]
                try:
                    d = json.loads(det or "{}")
                except Exception:
                    d = {}
                msgs.append(f"[{typ}] {d}")
        return msgs
    finally:
        try: b.close()
        except Exception: pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LIVE dashboard helpers: next race, timeline, activity, gate reasons
# 📆 PATCHED: 2025-08-25T12:05Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# Safe datetime parser
def _parse_dt_iso(s: str) -> datetime | None:
    try:
        s = str(s)
        if not s:
            return None
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00"))
        return datetime.fromisoformat(s)
    except Exception:
        return None

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)

def _get_sched_course(row: sqlite3.Row) -> str:
    for k in ("venue", "course", "event_name", "market_name", "marketName"):
        if k in row.keys() and row[k]:
            return str(row[k])
    return "-"

# ──────────────────────────────────────────────────────────────────────
# REPLACE next_race_preview in gui/dashboard_data.py
# ──────────────────────────────────────────────────────────────────────
from typing import Any, Dict, List
import sqlite3, json
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db, connect_db

def _dtparse(s: str) -> datetime | None:
    try:
        if not s: return None
        return datetime.fromisoformat(s.replace("Z","+00:00")) if s.endswith("Z") else datetime.fromisoformat(s)
    except Exception:
        return None

def _runner_name(bdb: sqlite3.Connection, marketId: str, selectionId: str) -> str:
    try:
        r = _q_retry(bdb, "SELECT horse_name FROM runners WHERE marketId=? AND selectionId=? LIMIT 1",
            (marketId, selectionId)
        ).fetchone()
        if r and r[0]: return str(r[0])
    except Exception:
        pass
    return str(selectionId)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH (regex): ^def next_race_preview\([^)]*\):[\s\S]*?return \{
# --- PATCH START: replace the whole function ---------------------------
def next_race_preview(*, limit: int = 6) -> Dict[str, Any]:
    """
    Top-6 runners for the chosen primary market.
    course/off: markets_schedule (by marketId)
    odds: inbound_oc_cache.oc1 (today) -> oc_series (today) -> anchor_odd
    flow/vol/trend: from last up-to-10 oc_series odds (today)
    """
    import sqlite3, json
    marketId = None; course = "-"; off_iso = None; runners: List[Dict[str, Any]] = []

    prim, _ = primary_secondary_markets(concurrent_window_min=6)
    marketId = prim.get("marketId")

    # resolve course/off
    if marketId:
        try:
            bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
            s = _q_retry(bdb, "SELECT COALESCE(venue,course,event_name,market_name) AS course, off_at_utc "
                "FROM markets_schedule WHERE marketId=? LIMIT 1", (marketId,)
            ).fetchone()
            if s:
                course = str(s["course"] or "-")
                off_iso = str(s["off_at_utc"]) if s["off_at_utc"] else None
            bdb.close()
        except Exception:
            pass

    latest: dict[str, Dict[str, Any]] = {}

    # inbound oc1 (today)
    if marketId:
        try:
            adb = _auto_conn()
            adb.row_factory = sqlite3.Row
            rows = _q_retry(adb, "SELECT c.selectionId, c.oc1, c.oc1_band_json, COALESCE(m.horse_name,c.selectionId) AS name "
                "FROM inbound_oc_cache c LEFT JOIN inbound_bets_min m "
                "ON m.marketId=c.marketId AND m.selectionId=c.selectionId "
                "WHERE c.marketId=? AND date(c.last_sync_ts)=date('now','utc') ORDER BY c.id DESC", (marketId,)
            ).fetchall()
            for r in rows:
                sid = str(r["selectionId"])
                if sid in latest: continue
                oc1 = r["oc1"]
                if oc1 is None and r["oc1_band_json"]:
                    try:
                        band = json.loads(r["oc1_band_json"]) or []
                        oc1 = float(band[-1]) if band else None
                    except Exception:
                        oc1 = None
                latest[sid] = {"name": r["name"] or sid, "odds": (float(oc1) if oc1 is not None else None)}
            adb.close()
        except Exception:
            pass

    # oc_series tail helper
    def _series_tail(bdb, mid: str, sid: str, n: int = 10) -> List[float]:
        rows = _q_retry(bdb, "SELECT odd FROM oc_series WHERE marketId=? AND selectionId=? "
            "AND date(snapshot_ts)=date('now','utc') ORDER BY datetime(snapshot_ts) DESC LIMIT ?",
            (mid, sid, n)
        ).fetchall()
        return [float(r["odd"]) for r in rows if r["odd"] is not None]

    # oc_series fallback for odds + flow/vol/trend
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        if marketId:
            # include any series-only selections
            sids = [str(r["selectionId"]) for r in _q_retry(bdb, "SELECT DISTINCT selectionId FROM oc_series WHERE marketId=? AND date(snapshot_ts)=date('now','utc')",
                (marketId,)
            ).fetchall()]
            # names best-effort
            names = {}
            try:
                adb = _auto_conn() 
                adb.row_factory = sqlite3.Row
                for r in _q_retry(adb, "SELECT selectionId, COALESCE(horse_name, selectionId) AS name FROM inbound_bets_min WHERE marketId=?",
                    (marketId,)
                ).fetchall():
                    names[str(r["selectionId"])] = r["name"]
                adb.close()
            except Exception:
                pass

            for sid in sids:
                latest.setdefault(sid, {"name": names.get(sid, sid), "odds": None})
                tail = _series_tail(bdb, marketId, sid, 10)
                if tail:
                    if latest[sid]["odds"] is None:
                        latest[sid]["odds"] = float(tail[0])
                    delta = (tail[0] - tail[1]) if len(tail) >= 2 else 0.0
                    rng = (max(tail) - min(tail)) if len(tail) >= 2 else abs(delta)
                    latest[sid]["flow"]  = "↑" if delta > 0 else ("↓" if delta < 0 else "—")
                    latest[sid]["vol"]   = f"{rng:.2f}"
                    latest[sid]["trend"] = "drift" if delta > 0 else ("steam" if delta < 0 else "flat")
                else:
                    latest[sid].setdefault("flow","—"); latest[sid].setdefault("vol","—"); latest[sid].setdefault("trend","—")
        bdb.close()
    except Exception:
        pass

    # anchor fallback if still nothing
    if marketId and not latest:
        try:
            adb = _auto_conn() 
            adb.row_factory = sqlite3.Row
            rows = _q_retry(adb, "SELECT selectionId, anchor_odd, COALESCE(horse_name, selectionId) AS name "
                "FROM inbound_bets_min WHERE marketId=? ORDER BY id DESC", (marketId,)
            ).fetchall()
            for r in rows:
                sid = str(r["selectionId"])
                latest[sid] = {"name": r["name"], "odds": (float(r["anchor_odd"]) if r["anchor_odd"] is not None else None),
                               "flow":"—","vol":"—","trend":"—"}
            adb.close()
        except Exception:
            pass

    # assemble top-6
    arr = [{"selectionId": sid,
            "name": v.get("name", sid),
            "odds": v.get("odds"),
            "flow": v.get("flow","—"),
            "vol": v.get("vol","—"),
            "trend": v.get("trend","—")} for sid, v in latest.items()]
    arr.sort(key=lambda x: (float("inf") if x["odds"] is None else x["odds"]))
    runners = arr[:max(1, int(limit))]

    # minutes to off
    t_to_off = 0
    if off_iso:
        off = _parse_iso(off_iso)
        if off:
            from datetime import datetime, timezone
            t_to_off = max(0, int((off - datetime.now(timezone.utc)).total_seconds()))

    return {
        "marketId": marketId,
        "course": course,
        "off_at_utc": off_iso,
        "t_to_off_sec": int(t_to_off),
        "runners": runners,
    }

# --- PATCH END ----------------------------------------------------------

def _fill_ratio_for_odds(odds: float | None, *, pre_off: bool) -> float:
    """Map odds to [0..1] fill. Pre-off scale uses 25% of the bar."""
    if odds is None:
        return 0.0
    try:
        o = float(odds)
        # 1000 (empty) → 0, 1.01 (full) → 1
        r = (1000.0 - o) / (1000.0 - 1.01)
        r = max(0.0, min(1.0, r))
        if pre_off:
            r *= 0.25
        return r
    except Exception:
        return 0.0

# ──────────────────────────────────────────────────────────────────────
# REPLACE live_timeline_data in gui/dashboard_data.py
# ──────────────────────────────────────────────────────────────────────
def _fill_ratio_for_odds(odds: float | None, *, pre_off: bool) -> float:
    if odds is None: return 0.0
    try:
        r = (1000.0 - float(odds)) / (1000.0 - 1.01)
        r = max(0.0, min(1.0, r))
        return r * (0.25 if pre_off else 1.0)
    except Exception:
        return 0.0

def live_timeline_data(marketId: str | None,
                       runner_ids: List[str],
                       off_at_utc: str | None,
                       *, force_pre_off: bool = False) -> List[Dict[str, Any]]:
    """
    Latest odds per requested runner for the given market:
      1) AUTOSCALP_DB.inbound_oc_cache.oc1 (most recent per runner)
      2) BETS_DB.oc_series.odd (latest snapshot per runner)
      3) AUTOSCALP_DB.inbound_bets_min.anchor_odd (fallback)
    Lock-tolerant: short-lived WAL readers + small retry on SQLITE_BUSY/locked.
    """
    import sqlite3, time
    from datetime import datetime, timezone
    from engines.config_paths import autoscalp_db, connect_db

    out: List[Dict[str, Any]] = []
    if not marketId or not runner_ids:
        return out

    # Pre-off flag (keeps your behaviour)
    pre_off = True
    if not force_pre_off and off_at_utc:
        try:
            off = _dtparse(off_at_utc)  # your helper in this module
        except Exception:
            off = None
        if off and datetime.now(timezone.utc) >= off:
            pre_off = False

    # Build IN (...) placeholders once
    rids = [str(s) for s in runner_ids]
    placeholders = ",".join("?" * len(rids))

    # Small retry loop to tolerate write locks while Step 4 is committing
    tries = 0
    while True:
        adb = None
        bdb = None
        try:
            # AUTOSCALP_DB reader
            adb = _auto_conn()
            try:
                adb.row_factory = sqlite3.Row
                _q_retry(adb, "PRAGMA journal_mode=WAL;")
                _q_retry(adb, "PRAGMA busy_timeout=5000;")
                _q_retry(adb, "PRAGMA read_uncommitted=1;")
            except Exception:
                pass

            # BETS_DB reader
            bdb = connect_db(ro=True)
            try:
                bdb.row_factory = sqlite3.Row
                # read pragmas are no-ops for some connections, harmless to set
                _q_retry(bdb, "PRAGMA journal_mode=WAL;")
                _q_retry(bdb, "PRAGMA busy_timeout=5000;")
                _q_retry(bdb, "PRAGMA read_uncommitted=1;")
            except Exception:
                pass

            latest: dict[str, float | None] = {sid: None for sid in rids}

            # 1) inbound_oc_cache.oc1 (most recent per runner)
            #    Limit scan to runners of interest
            rows = _q_retry(adb, f"""
                SELECT selectionId, oc1
                FROM inbound_oc_cache
                WHERE marketId=? AND selectionId IN ({placeholders})
                ORDER BY id DESC
                """,
                (marketId, *rids)
            ).fetchall()
            for r in rows:
                sid = str(r["selectionId"])
                if latest.get(sid) is None and r["oc1"] is not None:
                    latest[sid] = float(r["oc1"])

            # 2) fallback to oc_series.odd (latest snapshot per runner)
            missing = [sid for sid, v in latest.items() if v is None]
            if missing:
                ph2 = ",".join("?" * len(missing))
                rs = _q_retry(bdb, f"""
                    SELECT selectionId, odd
                    FROM oc_series
                    WHERE marketId=? AND selectionId IN ({ph2})
                    ORDER BY datetime(snapshot_ts) DESC
                    """,
                    (marketId, *missing)
                ).fetchall()
                for r in rs:
                    sid = str(r["selectionId"])
                    if latest.get(sid) is None and r["odd"] is not None:
                        latest[sid] = float(r["odd"])

            # 3) anchor_odd last
            if any(v is None for v in latest.values()):
                miss3 = [sid for sid, v in latest.items() if v is None]
                ph3 = ",".join("?" * len(miss3)) if miss3 else ""
                if ph3:
                    for r in _q_retry(adb, f"""
                        SELECT selectionId, anchor_odd
                        FROM inbound_bets_min
                        WHERE marketId=? AND selectionId IN ({ph3})
                        ORDER BY id DESC
                        """,
                        (marketId, *miss3)
                    ).fetchall():
                        sid = str(r["selectionId"])
                        if latest.get(sid) is None and r["anchor_odd"] is not None:
                            latest[sid] = float(r["anchor_odd"])

            # Build rows in the same order as runner_ids
            for sid in rids:
                o = latest.get(sid)
                out.append({
                    "selectionId": sid,
                    "odds": o,
                    "fill": _fill_ratio_for_odds(o, pre_off=pre_off or force_pre_off)
                })
            return out

        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if ("locked" in msg or "busy" in msg) and tries < 5:
                tries += 1
                time.sleep(0.1 * tries)  # 100ms → 500ms backoff
                continue
            # Bubble up so caller logs once (your [OC_TIMELINE] line)
            raise
        finally:
            try:
                if adb: adb.close()
            except Exception:
                pass
            try:
                if bdb: bdb.close()
            except Exception:
                pass


# ──────────────────────────────────────────────────────────────────────
# REPLACE engine_activity_today in gui/dashboard_data.py
# ──────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────────────
# 🧩 PATCH: gui/dashboard_data.py — engine_activity_today replay-aware window
# 📍 TARGET: replace the whole function block below
# 🔎 SEARCH: def engine_activity_today(*, source: str = "LIVE", window_hours: int = 12) -> Dict[str, Any]:
# ─────────────────────────────────────────────────────────────────────────────
def engine_activity_today(*, source: str = "LIVE", window_hours: int = 12) -> Dict[str, Any]:
    """
    Counters over a RECENT window (default last 12h), so they don't disappear at midnight
    or with UTC/local timestamp mismatches. If the window returns 0 for everything,
    we fall back to 'today' for that counter.

    REPLAY: window is centered on replay_now_utc(); 'today' fallbacks use replay day.
    """
    import sqlite3, datetime as _dt
    from engines.config_paths import autoscalp_db, is_replay_mode

    mode = (source or "LIVE").upper()
    out = {
        "runners_scanned": 0,
        "decisions": 0,
        "proposals": 0,
        "parents_placed": 0,
        "hedges_matched": 0,
        "cancels": 0,
        "timeouts": 0,
        "open_parents": 0,
        "recent_events": [],
    }

    is_rep = is_replay_mode()
    if is_rep:
        from engines.replay_clock import replay_now_utc, replay_day_iso
        now = replay_now_utc()
        replay_day = replay_day_iso()
        win_start_dt = now - _dt.timedelta(hours=int(window_hours))
    else:
        now = _dt.datetime.utcnow()
        replay_day = None
        win_start_dt = now - _dt.timedelta(hours=int(window_hours))

    try:
        adb = _auto_conn() 
        adb.row_factory = sqlite3.Row

        def _tbl_exists(conn, name: str) -> bool:
            return bool(_q_retry(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

        # decisions / runners scanned
        if _tbl_exists(adb, "decisions"):
            cols = [r["name"] for r in _q_retry(adb, "PRAGMA table_info(decisions)")]
            col_ts = "decided_at" if "decided_at" in cols else ("opened_at" if "opened_at" in cols else None)
            where_m = " AND mode=?" if "mode" in cols and mode in ("TEST","LEARNING","LIVE") else ""
            args_m = [mode] if where_m else []
            if col_ts:
                out["decisions"] = int(_q_retry(adb, f"SELECT COUNT(*) FROM decisions WHERE datetime({col_ts})>= ?{where_m}",
                    (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"), *args_m)
                ).fetchone()[0] or 0)
                out["runners_scanned"] = int(_q_retry(adb, f"SELECT COUNT(DISTINCT marketId||':'||selectionId) FROM decisions "
                    f"WHERE datetime({col_ts})>= ?{where_m}",
                    (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"), *args_m)
                ).fetchone()[0] or 0)
                # proposals fallback
                fallback_proposals = 0
                if "blueprint_match" in cols:
                    fallback_proposals = int(_q_retry(adb, f"SELECT COUNT(*) FROM decisions WHERE datetime({col_ts})>= ?{where_m} "
                        f"AND blueprint_match IS NOT NULL",
                        (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"), *args_m)
                    ).fetchone()[0] or 0)
                elif "confidence" in cols:
                    fallback_proposals = int(_q_retry(adb, f"SELECT COUNT(*) FROM decisions WHERE datetime({col_ts})>= ?{where_m} "
                        f"AND COALESCE(confidence,0)>0",
                        (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"), *args_m)
                    ).fetchone()[0] or 0)

        # orders counters
        if _tbl_exists(adb, "orders"):
            cols_o = [r["name"] for r in _q_retry(adb, "PRAGMA table_info(orders)")]
            have_opened = "opened_at" in cols_o
            have_closed = "closed_at" in cols_o
            where_mo = " AND mode=?" if "mode" in cols_o and mode in ("TEST","LEARNING","LIVE") else ""
            args_mo = [mode] if where_mo else []

            if have_opened:
                out["parents_placed"] = int(_q_retry(adb, f"SELECT COUNT(*) FROM orders WHERE datetime(opened_at)>= ?{where_mo}",
                    (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"), *args_mo)
                ).fetchone()[0] or 0)

            if have_closed:
                out["hedges_matched"] = int(_q_retry(adb, f"SELECT COUNT(*) FROM orders WHERE exit_status='matched' AND datetime(closed_at)>= ?{where_mo}",
                    (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"), *args_mo)
                ).fetchone()[0] or 0)

            out["cancels"] = int(_q_retry(adb, "SELECT COUNT(*) FROM orders WHERE (entry_status='cancelled' OR exit_status='cancelled')"
                + (f" AND (datetime(opened_at)>= ? " if have_opened else "")
                + (f" OR datetime(closed_at)>= ?)" if have_closed else (" )" if have_opened else ""))  # balanced
                + where_mo,
                tuple(
                    ([win_start_dt.strftime("%Y-%m-%d %H:%M:%S")] if have_opened else [])
                    + ([win_start_dt.strftime("%Y-%m-%d %H:%M:%S")] if have_closed else [])
                    + (args_mo)
                )
            ).fetchone()[0] or 0)

            out["open_parents"] = int(_q_retry(adb, f"SELECT COUNT(*) FROM orders WHERE entry_status='matched' "
                f"AND (exit_status IS NULL OR exit_status<>'matched'){where_mo}",
                tuple(args_mo)
            ).fetchone()[0] or 0)

        # proposals/events + last-10 events
        if _tbl_exists(adb, "events"):
            ev_props = int(_q_retry(adb, "SELECT COUNT(*) FROM events WHERE datetime(ts)>= ? AND message LIKE '%[DEC] ENTER%'",
                (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"),)
            ).fetchone()[0] or 0)
            out["proposals"] = ev_props if ev_props > 0 else out["proposals"]

            rows = _q_retry(adb, "SELECT ts, source, message FROM events WHERE datetime(ts)>= ? "
                "ORDER BY id DESC LIMIT 10",
                (win_start_dt.strftime("%Y-%m-%d %H:%M:%S"),)
            ).fetchall()
            out["recent_events"] = [(str(r["ts"]), str(r["source"]), str(r["message"])) for r in rows]

        # fallbacks to "today"
        def _today_count(sql: str, args: tuple = ()):
            try:
                return int(_q_retry(adb, sql, args).fetchone()[0] or 0)
            except Exception:
                return 0

        if is_rep:
            day_expr = "date(?)"
            day_arg = (replay_day,)
        else:
            day_expr = "date('now')"
            day_arg = tuple()

        if out["parents_placed"] == 0 and 'orders' in [t[0] for t in _q_retry(adb, "SELECT name FROM sqlite_master WHERE type='table'")]:
            if have_opened:
                out["parents_placed"] = _today_count(f"SELECT COUNT(*) FROM orders WHERE date(opened_at)={day_expr}", day_arg)
        if out["hedges_matched"] == 0 and 'orders' in [t[0] for t in _q_retry(adb, "SELECT name FROM sqlite_master WHERE type='table'")]:
            if have_closed:
                out["hedges_matched"] = _today_count(f"SELECT COUNT(*) FROM orders WHERE exit_status='matched' AND date(closed_at)={day_expr}", day_arg)
        if out["decisions"] == 0 and 'decisions' in [t[0] for t in _q_retry(adb, "SELECT name FROM sqlite_master WHERE type='table'")]:
            if 'decided_at' in (cols if 'cols' in locals() else []):
                out["decisions"] = _today_count(f"SELECT COUNT(*) FROM decisions WHERE date(decided_at)={day_expr}", day_arg)

        adb.close()
    except Exception:
        pass

    return out


def gate_reason_counts_today() -> List[Dict[str, Any]]:
    """
    Aggregate 'gate block | <reason>' messages from today's events.
    Never raises; returns [] if none.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db

    reasons: dict[str, int] = {}
    try:
        adb = _auto_conn() 
        adb.row_factory = sqlite3.Row
        if _q_retry(adb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='events'").fetchone():
            rows = _q_retry(adb, "SELECT message FROM events WHERE date(ts)=date('now','utc') AND message LIKE '%gate block | %'"
            ).fetchall()
            for r in rows:
                msg = str(r["message"])
                seg = msg.split("gate block |", 1)[1] if "gate block |" in msg else ""
                seg = seg.split(" mid=", 1)[0].strip() if seg else ""
                reason = seg or "unknown"
                reasons[reason] = reasons.get(reason, 0) + 1
        adb.close()
    except Exception:
        pass
    return [{"reason": k, "count": v} for k, v in sorted(reasons.items(), key=lambda x: (-x[1], x[0]))]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LIVE data helpers: today-only markets + phase + open-parents
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH: (append new impl at end of file)
# 📆 PATCHED: 2025-08-25T11:35Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _parse_iso(s: str | None) -> datetime | None:
    if not s: return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")) if s.endswith("Z") else datetime.fromisoformat(s)
    except Exception:
        return None

def today_market_ids(*, window_minutes: int = 0) -> set[str]:
    """
    Union of markets that are clearly 'today':
      - bets.db.markets_schedule with off_at_utc = today (UTC)
      - autoscalp_gui.db inbound_oc_cache (last_sync_ts = today)
      - bets.db.oc_series (snapshot_ts = today)
    window_minutes>0 keeps races within ±window around 'now' in schedule.
    """
    mids: set[str] = set()
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        # Schedule (today)
        q = "SELECT marketId, off_at_utc FROM markets_schedule WHERE date(off_at_utc)=date('now','utc')"
        for r in _q_retry(bdb, q).fetchall():
            mid = str(r["marketId"])
            if window_minutes > 0 and r["off_at_utc"]:
                off = _parse_iso(str(r["off_at_utc"]))
                if off is None: continue
                if abs((_utcnow() - off).total_seconds())/60.0 <= window_minutes:
                    mids.add(mid)
                else:
                    # still include – today-only means *at least* same-day schedule
                    mids.add(mid)
            else:
                mids.add(mid)
        # oc_series today
        if _q_retry(bdb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oc_series'").fetchone():
            for r in _q_retry(bdb, "SELECT DISTINCT marketId FROM oc_series WHERE date(snapshot_ts)=date('now','utc')"
            ).fetchall():
                mids.add(str(r["marketId"]))
        bdb.close()
    except Exception:
        pass
    try:
        adb = _auto_conn() 
        adb.row_factory = sqlite3.Row
        if _q_retry(adb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_oc_cache'").fetchone():
            for r in _q_retry(adb, "SELECT DISTINCT marketId FROM inbound_oc_cache WHERE date(last_sync_ts)=date('now','utc')"
            ).fetchall():
                mids.add(str(r["marketId"]))
        adb.close()
    except Exception:
        pass
    return mids

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH (regex): ^def primary_secondary_markets\([^)]*\):[\s\S]*?return primary, secondary
# --- PATCH START: replace the whole function ---------------------------
def primary_secondary_markets(*, concurrent_window_min: int = 6) -> Tuple[dict, dict | None]:
    """
    Pick the primary next race, and optionally a concurrent second race.
    Returns (primary_meta, secondary_meta_or_None)
    meta: {marketId, course, off_at_utc}
    Order of preference (today-only):
      1) markets_schedule (UTC, today, soonest)
      2) inbound_oc_cache (today, latest two markets seen)
      3) oc_series (today, most recent two markets with snapshots)
    """
    primary: dict = {"marketId": None, "course": "-", "off_at_utc": None}
    secondary: dict | None = None

    # 1) schedule (today)
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        rows = _q_retry(bdb, "SELECT marketId, off_at_utc, COALESCE(venue,course,event_name,market_name) AS course "
            "FROM markets_schedule "
            "WHERE date(off_at_utc)=date('now','utc') "
            "AND datetime(off_at_utc)>=datetime('now','utc') "
            "ORDER BY datetime(off_at_utc) ASC LIMIT 4"
        ).fetchall()
        if rows:
            p = rows[0]
            primary = {"marketId": str(p["marketId"]), "course": str(p["course"] or "-"), "off_at_utc": str(p["off_at_utc"] or "")}
            if len(rows) > 1:
                c = rows[1]
                off_p = _parse_iso(primary["off_at_utc"]); off_c = _parse_iso(str(c["off_at_utc"]))
                if off_p and off_c and 0 <= (off_c - off_p).total_seconds() / 60.0 <= concurrent_window_min:
                    secondary = {"marketId": str(c["marketId"]), "course": str(c["course"] or "-"), "off_at_utc": str(c["off_at_utc"] or "")}
        bdb.close()
    except Exception:
        pass

    # 2) inbound cache (today) fallback
    if not primary["marketId"]:
        try:
            adb = _auto_conn()
            adb.row_factory = sqlite3.Row
            mids = [str(r["marketId"]) for r in _q_retry(adb, "SELECT DISTINCT marketId FROM inbound_oc_cache "
                "WHERE date(last_sync_ts)=date('now','utc') ORDER BY id DESC LIMIT 2"
            ).fetchall()]
            adb.close()
            if mids:
                primary = {"marketId": mids[0], "course": "-", "off_at_utc": None}
                if len(mids) > 1:
                    secondary = {"marketId": mids[1], "course": "-", "off_at_utc": None}
        except Exception:
            pass

    # 3) oc_series (today) last resort
    if not primary["marketId"]:
        try:
            bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
            mids = [str(r["marketId"]) for r in _q_retry(bdb, "SELECT marketId FROM oc_series WHERE date(snapshot_ts)=date('now','utc') "
                "GROUP BY marketId ORDER BY max(datetime(snapshot_ts)) DESC LIMIT 2"
            ).fetchall()]
            bdb.close()
            if mids:
                primary = {"marketId": mids[0], "course": "-", "off_at_utc": None}
                if len(mids) > 1:
                    secondary = {"marketId": mids[1], "course": "-", "off_at_utc": None}
        except Exception:
            pass

    return primary, secondary
# --- PATCH END ----------------------------------------------------------

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH (regex): ^def infer_race_phase\(marketId:.*\)
# 📆 PATCHED: 2025-08-25T12:45Z
# --- replace the whole function ---------------------------------------
def infer_race_phase(marketId: str, off_at_utc: str | None) -> str:
    """
    Return 'PRE', 'DELAYED', or 'OFF'.
    Prefer Betfair (listMarketBook via engines.betfair_status); fallback to schedule time.
    """
    # 1) Betfair live status
    try:
        from engines.betfair_status import get_or_update_phase
        bf = get_or_update_phase(marketId)
        if bf == "OFF":
            return "OFF"
        # if Betfair says PRE, still allow DELAYED via schedule check below
    except Exception:
        pass

    # 2) No OFF from API -> compare schedule time
    off = _parse_iso(off_at_utc) if off_at_utc else None
    if off and _utcnow() >= off:
        return "DELAYED"
    return "PRE"


def open_parents_for_market(marketId: str) -> int:
    """LIVE open parents count for a specific market."""
    try:
        adb = _auto_conn() 
        adb.row_factory = sqlite3.Row
        row = _q_retry(adb, "SELECT COUNT(*) AS n FROM orders WHERE mode='LIVE' AND marketId=? "
            "AND entry_status='matched' AND (exit_status IS NULL OR exit_status<>'matched')",
            (str(marketId),)
        ).fetchone()
        adb.close()
        return int(row["n"] or 0)
    except Exception:
        return 0

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NEXT RACE + CONCURRENCY + PREVIEW (today-only, robust fallbacks)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from typing import List, Dict, Tuple

# ─────────────────────────────────────────────────────────────────────────────
# 🧩 PATCH: gui/dashboard_data.py — make get_next_markets replay-aware
# 📍 TARGET: replace the whole function block below
# 🔎 SEARCH: def get_next_markets(*, concurrent_window_min: int = 6, grace_sec: int = 60) -> Tuple[dict, List[dict]]:
# ─────────────────────────────────────────────────────────────────────────────
def get_next_markets(*, concurrent_window_min: int = 6, grace_sec: int = 60, now_utc=None) -> Tuple[dict, List[dict]]:
    """
    Return (primary, secondaries[]) for the next future market (off >= now+grace).
    Falls back to inbound_oc_cache if schedule is empty.

    REPLAY (mode='test'): time flows from engines.replay_clock.replay_now_utc()
    and queries are constrained to the replay day, so we never pick stale/other-day markets.
    """
    import sqlite3, datetime as _dt
    from engines.config_paths import connect_db, autoscalp_db, is_replay_mode
    if is_replay_mode():
        from engines.replay_clock import replay_now_utc, replay_day_iso
        now_utc = replay_now_utc()
        replay_day = replay_day_iso()
    else:
        now_utc = now_utc or _dt.datetime.utcnow()
        replay_day = None

    cutoff = now_utc + _dt.timedelta(seconds=grace_sec)

    def _row_to_meta(r):
        return {
            "marketId": str(r["marketId"]),
            "course": str(r.get("course") or r.get("venue") or r.get("event_name") or r.get("market_name") or "-"),
            "off_at_utc": str(r.get("off_at_utc") or ""),
        }

    primary = {"marketId": None, "course": "-", "off_at_utc": None}
    seconds: List[dict] = []

    # 1) schedule (strictly future, with grace) — constrain to replay day when REPLAY
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        if is_replay_mode() and replay_day:
            rows = _q_retry(bdb, "SELECT marketId, COALESCE(venue,course,event_name,market_name) AS course, off_at_utc "
                "FROM markets_schedule "
                "WHERE date(off_at_utc)=date(?) AND datetime(off_at_utc) >= datetime(?,'utc') "
                "ORDER BY datetime(off_at_utc) ASC LIMIT 6",
                (replay_day, cutoff.strftime("%Y-%m-%d %H:%M:%S"))
            ).fetchall()
        else:
            rows = _q_retry(bdb, "SELECT marketId, COALESCE(venue,course,event_name,market_name) AS course, off_at_utc "
                "FROM markets_schedule "
                "WHERE datetime(off_at_utc) >= datetime(?,'utc') "
                "ORDER BY datetime(off_at_utc) ASC LIMIT 6",
                (cutoff.strftime("%Y-%m-%d %H:%M:%S"),)
            ).fetchall()
        if rows:
            primary = _row_to_meta(rows[0])
            p_off = _dt.datetime.fromisoformat(primary["off_at_utc"].replace("Z","+00:00")) if primary["off_at_utc"] else None
            if p_off and len(rows) > 1:
                for r in rows[1:]:
                    off = r["off_at_utc"]
                    if not off:
                        continue
                    off_dt = _dt.datetime.fromisoformat(off.replace("Z","+00:00"))
                    dt_min = (off_dt - p_off).total_seconds()/60.0
                    if 0.0 <= dt_min <= float(concurrent_window_min):
                        seconds.append(_row_to_meta(r))
        bdb.close()
    except Exception:
        pass

    # 2) fallback to inbound_oc_cache (most recent markets for the appropriate day)
    if not primary["marketId"]:
        try:
            adb = _auto_conn() 
            adb.row_factory = sqlite3.Row
            if is_replay_mode() and replay_day:
                mids = [str(r["marketId"]) for r in _q_retry(adb, "SELECT DISTINCT marketId FROM inbound_oc_cache "
                    "WHERE date(last_sync_ts)=date(?) "
                    "ORDER BY id DESC LIMIT 2",
                    (replay_day,)
                ).fetchall()]
            else:
                mids = [str(r["marketId"]) for r in _q_retry(adb, "SELECT DISTINCT marketId FROM inbound_oc_cache "
                    "WHERE date(last_sync_ts)=date('now','utc') "
                    "ORDER BY id DESC LIMIT 2"
                ).fetchall()]
            adb.close()
            if mids:
                primary = {"marketId": mids[0], "course": "-", "off_at_utc": None}
                if len(mids) > 1:
                    seconds = [{"marketId": mids[1], "course": "-", "off_at_utc": None}]
        except Exception:
            pass

    return primary, seconds




def get_market_preview(marketId: str, *, limit: int = 6) -> Dict[str, Any]:
    """
    Build top-6 runners for a given market (today).
    odds: inbound_oc_cache.oc1 -> oc_series -> anchor_odd
    flow/vol/trend from last 10 oc_series points (today).
    Returns {course, off_at_utc, runners:[{selectionId,name,odds,flow,vol,trend}]}
    """
    import sqlite3, json
    out = {"course": "-", "off_at_utc": None, "runners": []}

    # course/off from schedule if available
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        s = _q_retry(bdb, "SELECT COALESCE(venue,course,event_name,market_name) AS course, off_at_utc "
            "FROM markets_schedule WHERE marketId=? LIMIT 1", (marketId,)
        ).fetchone()
        if s:
            out["course"] = str(s["course"] or "-")
            out["off_at_utc"] = str(s["off_at_utc"]) if s["off_at_utc"] else None
    except Exception:
        pass

    latest: dict[str, Dict[str, Any]] = {}

    # 1) inbound oc1 today
    try:
        adb = _auto_conn()
        adb.row_factory = sqlite3.Row
        rows = _q_retry(adb, "SELECT c.selectionId, c.oc1, c.oc1_band_json, COALESCE(m.horse_name,c.selectionId) AS name "
            "FROM inbound_oc_cache c LEFT JOIN inbound_bets_min m "
            "ON m.marketId=c.marketId AND m.selectionId=c.selectionId "
            "WHERE c.marketId=? AND date(c.last_sync_ts)=date('now','utc') ORDER BY c.id DESC", (marketId,)
        ).fetchall()
        for r in rows:
            sid = str(r["selectionId"])
            if sid in latest:
                continue
            oc1 = r["oc1"]
            if oc1 is None and r["oc1_band_json"]:
                try:
                    band = json.loads(r["oc1_band_json"]) or []
                    oc1 = float(band[-1]) if band else None
                except Exception:
                    oc1 = None
            latest[sid] = {"name": r["name"] or sid, "odds": (float(oc1) if oc1 is not None else None)}
        adb.close()
    except Exception:
        pass

    # helper: oc_series tail
    def _series_tail(bdb, sid: str, n: int = 10) -> List[float]:
        rows = _q_retry(bdb, "SELECT odd FROM oc_series WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date('now','utc') "
            "ORDER BY datetime(snapshot_ts) DESC LIMIT ?", (marketId, sid, n)
        ).fetchall()
        return [float(r["odd"]) for r in rows if r["odd"] is not None]

    # 2) oc_series odds + flow/vol/trend
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        sids = [str(r["selectionId"]) for r in _q_retry(bdb, "SELECT DISTINCT selectionId FROM oc_series WHERE marketId=? AND date(snapshot_ts)=date('now','utc')",
            (marketId,)
        ).fetchall()]
        # names best-effort
        names = {}
        try:
            adb = _auto_conn() 
            adb.row_factory = sqlite3.Row
            for r in _q_retry(adb, "SELECT selectionId, COALESCE(horse_name, selectionId) AS name FROM inbound_bets_min WHERE marketId=?",
                (marketId,)
            ).fetchall():
                names[str(r["selectionId"])] = r["name"]
            adb.close()
        except Exception:
            pass

        for sid in sids:
            latest.setdefault(sid, {"name": names.get(sid, sid), "odds": None})
            tail = _series_tail(bdb, sid, 10)
            if tail:
                if latest[sid]["odds"] is None:
                    latest[sid]["odds"] = float(tail[0])
                delta = (tail[0] - tail[1]) if len(tail) >= 2 else 0.0
                rng = (max(tail) - min(tail)) if len(tail) >= 2 else abs(delta)
                latest[sid]["flow"]  = "↑" if delta > 0 else ("↓" if delta < 0 else "—")
                latest[sid]["vol"]   = f"{rng:.2f}"
                latest[sid]["trend"] = "drift" if delta > 0 else ("steam" if delta < 0 else "flat")
            else:
                latest[sid].setdefault("flow","—"); latest[sid].setdefault("vol","—"); latest[sid].setdefault("trend","—")
        bdb.close()
    except Exception:
        pass

    

    # 3) anchor fallback if still missing
    if not latest:
        try:
            adb = _auto_conn() 
            adb.row_factory = sqlite3.Row
            for r in _q_retry(adb, "SELECT selectionId, anchor_odd, COALESCE(horse_name, selectionId) AS name "
                "FROM inbound_bets_min WHERE marketId=? ORDER BY id DESC", (marketId,)
            ).fetchall():
                sid = str(r["selectionId"])
                latest[sid] = {"name": r["name"], "odds": (float(r["anchor_odd"]) if r["anchor_odd"] is not None else None),
                               "flow":"—","vol":"—","trend":"—"}
            adb.close()
        except Exception:
            pass

    # FINAL FALLBACK: bets.db -> bets table anchors (if present)
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row

        # Detect plausible anchor column in bets table
        has_bets = bool(_q_retry(bdb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bets'").fetchone()
        )
        if has_bets:
            cols = {r["name"] if isinstance(r, sqlite3.Row) else r[1]
                    for r in _q_retry(bdb, "PRAGMA table_info(bets)").fetchall()}
            # Choose a sensible anchor column name
            anchor_col = None
            for c in ("anchor_odd","anchor","sp","starting_price","start_price","odd","odds"):
                if c in cols:
                    anchor_col = c; break

            if anchor_col:
                rs = _q_retry(bdb, f"SELECT selectionId, COALESCE({anchor_col}, NULL) AS anchor, "
                    "       COALESCE(horse_name, selectionId) AS name "
                    "FROM bets WHERE marketId=?",
                    (marketId,)
                ).fetchall()
                for r in rs:
                    sid = str(r["selectionId"])
                    if sid not in latest:
                        latest[sid] = {"name": r["name"], "odds": None}
                    if latest[sid].get("odds") is None and r["anchor"] is not None:
                        latest[sid]["odds"] = float(r["anchor"])
        bdb.close()
    except Exception:
        pass

    # shape into top-6 (by odds asc, unknown odds at end)
    arr = [{"selectionId": sid,
            "name": v.get("name", sid),
            "odds": v.get("odds"),
            "flow": v.get("flow","—"),
            "vol": v.get("vol","—"),
            "trend": v.get("trend","—")} for sid, v in latest.items()]
    arr.sort(key=lambda x: (float("inf") if x["odds"] is None else x["odds"]))
    out["runners"] = arr[:max(1, int(limit))]
    return out

from engines.config_paths import autoscalp_db

def _alias_mode(m: str) -> str | None:
    # SIM→TEST alias only applies when m == "SIM"
    if not m: return "LIVE"
    m = m.upper()
    if m == "SIM":  return "TEST"
    if m == "ALL":  return None
    return m          # LIVE/TEST/LEARNING stay as-is

# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH: ^def fetch_orders_tape\(\*, limit: int = 250, mode: str = "LIVE"\):
#    Replace the function body with the minimal block below (no other edits).

def fetch_orders_tape(*, limit: int = 250, mode: str = "LIVE") -> list[dict]:
    qmode = _qmode(mode)  # SIM→TEST, ALL→None

    con = None
    try:
        con = _auto_conn()
        con.row_factory = sqlite3.Row
        try:
            _q_retry(con, "PRAGMA journal_mode=WAL")
            _q_retry(con, "PRAGMA busy_timeout=5000")
            _q_retry(con, "PRAGMA synchronous=NORMAL")
            _q_retry(con, "PRAGMA read_uncommitted=1")
        except Exception:
            pass

        base = """
        SELECT
          COALESCE(ts, opened_at, closed_at, datetime('now','utc')) AS ts,
          marketId        AS market_id,
          selectionId     AS runner_id,
          UPPER(COALESCE(side,'')) AS side,
          COALESCE(role, CASE WHEN hedge_of IS NULL OR hedge_of='' THEN 'PARENT' ELSE 'CHILD' END) AS role,
          CASE
            WHEN UPPER(COALESCE(exit_status,''))='MATCHED' THEN 'exit_matched'
            WHEN UPPER(COALESCE(entry_status,''))='MATCHED' THEN 'entry_matched'
            ELSE LOWER(COALESCE(status, entry_status, 'queued'))
          END             AS event,
          COALESCE(entry_odds, 0.0)  AS price,
          COALESCE(entry_stake, 0.0) AS size,
          COALESCE(source,'')        AS source,
          NULL                       AS oco_group_id
        FROM orders
        {where}
        ORDER BY datetime(COALESCE(ts, opened_at, closed_at)) DESC, id DESC
        LIMIT ?
        """

        today_where = "date(COALESCE(ts, opened_at, closed_at)) = date('now','utc')"

        if qmode is None:
            # ALL modes, today-only
            where_sql = f"WHERE {today_where}"
            exec_args = (int(limit),)
        else:
            # Specific mode + today-only
            where_sql = f"WHERE UPPER(COALESCE(mode,''))=? AND {today_where}"
            exec_args = (qmode, int(limit))

        rows = _q_retry(con, base.format(where=where_sql), exec_args).fetchall()
        return [dict(r) for r in rows]

    except sqlite3.OperationalError:
        return []  # don’t bomb the UI; UI will retry
    finally:
        try:
            if con is not None:
                con.close()
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────────────────────
# KPI tiles provider (Plan §6B.1–6B.2)
# bank:     sum(cash_ledger.delta) [mode]
# used:     open lay liability (live/matched lays)
# realised: sum(pnl_trades.pnl) [mode]
# unreal.:  per-oco (lay_matched - back_matched)
# LIVE: subtract open exposure from bank
# ─────────────────────────────────────────────────────────────────────────────
from typing import Dict, Any
from engines.config_paths import connect_db

def _table_exists(conn, name: str) -> bool:
    try:
        r = _q_retry(conn, "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        return bool(r)
    except Exception:
        return False

def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r["name"] for r in _q_retry(con, f"PRAGMA table_info({table})")}
    except Exception:
        return set()

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH: ^def kpi_tiles\(source: str = "LIVE"\) -> dict:
# 📆 PATCHED: 2025-10-14T23:40Z
# ───────────────────────────────────────────────────────────────
def kpi_tiles(source: str = "LIVE") -> dict:
    """
    KPI tiles with canonical overlay.
    Keeps settlement-based today/yesterday/d7/d30 intact,
    and adds canonical_total from the new engine for cross-check.
    """
    out = {
        "total": 0.0, "today": 0.0, "yesterday": 0.0,
        "d7": 0.0, "d30": 0.0,
        "win_pct_mkt": 0.0,
        "pnl_per_hour": 0.0,
        "markets_left": 0,
        "avg_win_per_mkt": 0.0,
        "avg_loss_per_mkt": 0.0,
        "markets_total": 0,
    }

    # ── original settlement rollups (unchanged) ─────────────────────────
    try:
        from engines.live.settlements import settlements_db_path, ensure_kpi_views
        ensure_kpi_views()
        db_path = settlements_db_path()
    except Exception:
        import os
        from engines.config_paths import autoscalp_db
        db_path = os.path.join(os.path.dirname(autoscalp_db()), "settlements.db")

    import sqlite3
    con = sqlite3.connect(db_path, timeout=5.0)
    con.row_factory = sqlite3.Row

    def _num(sql: str) -> float:
        try:
            r = con.execute(sql).fetchone()
            return float((list(r)[0] if r else 0.0) or 0.0)
        except Exception:
            return 0.0

    out["total"]     = _num("SELECT COALESCE(SUM(net),0.0) FROM v_settle_day")
    out["today"]     = _num("SELECT COALESCE(SUM(net),0.0) FROM v_settle_day WHERE day=date('now','utc')")
    out["yesterday"] = _num("SELECT COALESCE(SUM(net),0.0) FROM v_settle_day WHERE day=date('now','utc','-1 day')")
    out["d7"]        = _num("SELECT COALESCE(SUM(net),0.0) FROM v_settle_day WHERE day BETWEEN date('now','utc','-6 day') AND date('now','utc')")
    out["d30"]       = _num("SELECT COALESCE(SUM(net),0.0) FROM v_settle_day WHERE day BETWEEN date('now','utc','-29 day') AND date('now','utc')")

    # ── live daily metrics (today-only) ─────────────────────────────────
    try:
# === PATCH START ===
# 📍 TARGET: gui/dashboard_data.py:kpi_tiles (inside live daily metrics try-block)
# 📆 PATCHED: 2025-10-16Z — replace only the four broken metrics with the verified working logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # --- FIXED METRICS (canonical verified replacements) ---
        # win % market — settlements canonical (today)
        import os, sqlite3

        b = connect_db(ro=True); b.row_factory = sqlite3.Row
        out["win_pct_mkt"] = _num("""
            SELECT ROUND(100.0 *
                   SUM(CASE WHEN profit_sum>0 THEN 1 ELSE 0 END)
                   / COUNT(*),1)
              FROM (
                SELECT marketId, SUM(COALESCE(profit,0.0)) AS profit_sum
                  FROM bf_cleared_orders
                 WHERE date(settledDate)=date('now','utc')
                 GROUP BY marketId
              );
        """)

        # pnl per hour — canonical from bf_cleared_orders (today)
        out["pnl_per_hour"] = _num("""
            WITH first_last AS (
              SELECT MIN(settledDate) AS first_s,
                     MAX(settledDate) AS last_s
                FROM bf_cleared_orders
               WHERE date(settledDate)=date('now','utc')
            ),
            hours AS (
              SELECT MAX((strftime('%s',last_s)-strftime('%s',first_s))/3600.0,1.0) AS h FROM first_last
            ),
            pnl AS (
              SELECT COALESCE(SUM(profit),0.0) AS p
                FROM bf_cleared_orders
               WHERE date(settledDate)=date('now','utc')
            )
            SELECT ROUND(p/h,2) FROM pnl,hours;
        """)

        # markets total — from bets (today)
        with sqlite3.connect(os.path.join(os.path.dirname(db_path), "bets.db")) as b:
            b.row_factory = sqlite3.Row
            try:
                r = b.execute("""
                    SELECT COUNT(DISTINCT marketId)
                      FROM bets
                     WHERE date(marketStartTime)=date('now','utc');
                """).fetchone()
                out["markets_total"] = float(r[0] if r else 0.0)
            except Exception:
                out["markets_total"] = 0.0

        # markets left — from bets (today, still upcoming)
        with sqlite3.connect(os.path.join(os.path.dirname(db_path), "bets.db")) as b:
            b.row_factory = sqlite3.Row
            try:
                r = b.execute("""
                    SELECT COUNT(DISTINCT marketId)
                      FROM bets
                     WHERE date(marketStartTime)=date('now','utc')
                       AND datetime(marketStartTime)>datetime('now','utc');
                """).fetchone()
                out["markets_left"] = float(r[0] if r else 0.0)
            except Exception:
                out["markets_left"] = 0.0



        # average win/loss
        out["avg_win_per_mkt"] = _num("""
            SELECT ROUND(AVG(net),2)
              FROM v_settle_mkt_day
             WHERE day=date('now','utc') AND net>0
        """)
        out["avg_loss_per_mkt"] = _num("""
            SELECT ROUND(ABS(AVG(net)),2)
              FROM v_settle_mkt_day
             WHERE day=date('now','utc') AND net<0
        """)
        b.close()
    except Exception as e:
        print(f"[dashboard] KPI extras warn: {e}")

    try:
        con.close()
    except Exception:
        pass

    return out
# ───────────────────────────────────────────────────────────────

#────────────────────────────────────────────────────────────────────────────
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
# Matched liability (market worst-case) per strategy family
# For each (source,strategy) and market:
#   loss_if_wins(sel) = lay_liab[sel] + (total_back_stake - back_stake[sel])
# matched_liab(strategy) = sum_over_markets( max_sel loss_if_wins(sel) )
def _matched_liability_by_strategy(con: sqlite3.Connection, mode: str) -> dict[str, float]:
    cols = ("strategy","parents","matched%","hedge%","pnl_today","pnl_7d","pnl_30d",
            "win%mkt30d","avg_ticks","hold_med_s","open","matched_liab","unmatched_liab","last_trade")
    has_mode = ("mode" in cols)
    # Parent rows: prefer role='PARENT', else hedge_of IS NULL
    parent_pred = ("COALESCE(role, CASE WHEN hedge_of IS NULL OR hedge_of='' "
                   "THEN 'PARENT' ELSE 'CHILD' END)='PARENT'")

    where = (f"{parent_pred} AND entry_status='matched' "
             "AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')")
    args: list = []
    if has_mode:
        where += " AND UPPER(COALESCE(mode,''))=UPPER(?)"
        args.append(mode)

    rows = _q_retry(con, f"""
        SELECT COALESCE(source,'') AS src,
               marketId,
               selectionId,
               UPPER(COALESCE(side,'')) AS side,
               COALESCE(entry_odds,0.0) AS odds,
               COALESCE(entry_stake,0.0) AS stake
          FROM orders
         WHERE {where}
    """, tuple(args)).fetchall()

    by_src_mkt: dict[tuple[str, str], dict] = {}
    for r in rows:
        key = (str(r["src"]), str(r["marketId"]))
        blk = by_src_mkt.setdefault(key, {"back": {}, "lay": {}, "total_back": 0.0})
        sid = str(r["selectionId"])
        if r["side"] == "LAY":
            liab = float(r["stake"]) * max(0.0, float(r["odds"]) - 1.0)
            blk["lay"][sid] = blk["lay"].get(sid, 0.0) + liab
        else:
            st = float(r["stake"])
            blk["back"][sid] = blk["back"].get(sid, 0.0) + st
            blk["total_back"] += st

    out: dict[str, float] = {}
    for (src, _mid), blk in by_src_mkt.items():
        all_sids = set(blk["back"]) | set(blk["lay"])
        worst = 0.0
        for sid in all_sids:
            loss = blk["lay"].get(sid, 0.0) + (blk["total_back"] - blk["back"].get(sid, 0.0))
            worst = max(worst, loss)
        out[src] = out.get(src, 0.0) + worst
    return out

# ─────────────────────────────────────────────────────────────────────────────
# Portfolio view for the Risk card
def portfolio_risk(*, mode: str = "LIVE") -> dict:
    """
    Portfolio snapshot from autoscalp_gui.db orders:
      matched_liab     : sum over markets of worst-case loss if one runner wins (matched legs)
      unmatched_liab   : sum of queued/placed liabilities (parents not matched)
      open_parents     : parents entry matched & no matched exit
      closed_today     : exits matched today OR (parent matched AND child matched today)
      wins_today       : closed parents with positive P&L today
      pnl_per_hour     : today's realized pnl / hours since UTC midnight
      avg_win_per_mkt  : average positive P&L per market today (optional UI)
      avg_loss_per_mkt : average negative P&L per market today (absolute, optional UI)
    """
    import sqlite3
    from datetime import datetime, timezone
    from engines.config_paths import autoscalp_db

    con = _auto_conn()
    try:
        cols = [r["name"] for r in _q_retry(con, "PRAGMA table_info(orders)")]
        has  = lambda c: c in cols
        where_mode = " AND UPPER(COALESCE(mode,''))=?" if has("mode") else ""
        args_mode  = (mode.upper(),) if has("mode") else ()

        # ---------- matched legs (for worst-case per market)
        rows = _q_retry(con, f"""SELECT marketId, selectionId, side, entry_odds, entry_stake
                  FROM orders
                 WHERE UPPER(COALESCE(entry_status,''))='MATCHED'{where_mode}""",
            args_mode
        ).fetchall()

        by_mkt = {}
        for r in rows:
            by_mkt.setdefault(str(r["marketId"]), []).append(r)

        def _worst_loss(mrows):
            if not mrows: return 0.0
            total_back = 0.0
            back_by = {}
            lay_liab_by = {}
            for r in mrows:
                st = float(r["entry_stake"] or 0.0)
                od = float(r["entry_odds"]  or 0.0)
                sid = str(r["selectionId"])
                if str(r["side"]).upper().startswith("BACK"):
                    total_back += st
                    back_by[sid] = back_by.get(sid, 0.0) + st
                else:
                    lay_liab_by[sid] = lay_liab_by.get(sid, 0.0) + st * max(0.0, od - 1.0)
            worst = 0.0
            for sid in set(back_by) | set(lay_liab_by):
                loss = lay_liab_by.get(sid, 0.0) + (total_back - back_by.get(sid, 0.0))
                if loss > worst: worst = loss
            return worst

        matched_liab = sum(_worst_loss(mrows) for mrows in by_mkt.values())

        # ---------- unmatched liability (queued/placed)
        unr = _q_retry(con, f"""SELECT side, entry_odds, entry_stake
                  FROM orders
                 WHERE UPPER(COALESCE(entry_status,'')) IN ('QUEUED','PLACED'){where_mode}""",
            args_mode
        ).fetchall()
        unmatched_liab = 0.0
        for r in unr:
            st = float(r["entry_stake"] or 0.0); od = float(r["entry_odds"] or 0.0)
            unmatched_liab += (st * max(0.0, od - 1.0)) if str(r["side"]).upper().startswith("LAY") else st

        # ---------- open parents (matched entry, exit not matched)
        open_parents = int(_q_retry(con, f"""SELECT COUNT(*) FROM orders
                 WHERE UPPER(COALESCE(entry_status,''))='MATCHED'
                   AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED'){where_mode}""",
            args_mode
        ).fetchone()[0] or 0)

        # ---------- closed / wins today
        # A. standard: parent exit_status='matched' today
        closed_std = int(_q_retry(con, f"""SELECT COUNT(*) FROM orders
                 WHERE UPPER(COALESCE(role,''))='PARENT'
                   AND UPPER(COALESCE(exit_status,''))='MATCHED'
                   AND date(COALESCE(closed_at, opened_at))=date('now','utc'){where_mode}""",
            args_mode
        ).fetchone()[0] or 0)

        # B. inferred: parent matched AND there exists a child with entry_status='matched' today
        if "role" in cols or "hedge_of" in cols:
            closed_inf = int(_q_retry(con, f"""SELECT COUNT(*) FROM orders p
                       JOIN orders c ON c.hedge_of=p.id
                     WHERE UPPER(COALESCE(p.role,''))='PARENT'
                       AND UPPER(COALESCE(p.entry_status,''))='MATCHED'
                       AND UPPER(COALESCE(c.entry_status,''))='MATCHED'
                       AND date(COALESCE(c.closed_at, c.opened_at))=date('now','utc'){where_mode}""",
                args_mode
            ).fetchone()[0] or 0)
        else:
            closed_inf = 0

        # avoid double-count if both conditions hit the same parent:
        closed_today = max(closed_std, closed_std + closed_inf) if closed_inf else closed_std

        # wins = positive pnl on truly closed rows (needs net_pl/realized_pnl)
        wins_today = int(_q_retry(con, f"""SELECT COUNT(*) FROM orders
                 WHERE UPPER(COALESCE(exit_status,''))='MATCHED'
                   AND date(COALESCE(closed_at, opened_at))=date('now','utc')
                   AND COALESCE(net_pl,realized_pnl,0)>0{where_mode}""",
            args_mode
        ).fetchone()[0] or 0)

        pnl_today = float(_q_retry(con, f"""SELECT COALESCE(SUM(COALESCE(net_pl, realized_pnl)),0.0)
                 FROM orders
                WHERE UPPER(COALESCE(exit_status,''))='MATCHED'
                  AND date(COALESCE(closed_at, opened_at))=date('now','utc'){where_mode}""",
            args_mode
        ).fetchone()[0] or 0.0)

        # average win/loss per market (today), optional
        by_mkt = _q_retry(con, f"""SELECT marketId, SUM(COALESCE(net_pl, realized_pnl)) AS s
                   FROM orders
                  WHERE UPPER(COALESCE(exit_status,''))='MATCHED'
                    AND date(COALESCE(closed_at, opened_at))=date('now','utc'){where_mode}
                  GROUP BY marketId""",
            args_mode
        ).fetchall()
        wins = [float(r["s"] or 0.0) for r in by_mkt if float(r["s"] or 0.0) > 0]
        losses = [abs(float(r["s"] or 0.0)) for r in by_mkt if float(r["s"] or 0.0) < 0]
        avg_win_per_mkt  = (sum(wins)/len(wins)) if wins else 0.0
        avg_loss_per_mkt = (sum(losses)/len(losses)) if losses else 0.0

        # pnl/hour since UTC midnight
        midnight = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc)
        hours = max((datetime.now(timezone.utc) - midnight).total_seconds()/3600.0, 1e-9)
        pnl_per_hour = pnl_today / hours

        return dict(
            matched_liab=float(matched_liab),
            unmatched_liab=float(unmatched_liab),
            open_parents=int(open_parents),
            closed_today=int(closed_today),
            wins_today=int(wins_today),
            pnl_per_hour=float(pnl_per_hour),
            avg_win_per_mkt=float(avg_win_per_mkt),
            avg_loss_per_mkt=float(avg_loss_per_mkt),
        )
    finally:
        try: con.close()
        except Exception: pass
# ─────────────────────────────────────────────────────────────────────────────
# Strategy P&L / performance table (LIVE defaults)
# ─────────────────────────────────────────────────────────────────────────────
# gui/dashboard_data.py
def _feeder_day(con: sqlite3.Connection) -> str:
    # Prefer feeder partition; fall back to UTC today
    r = _q_retry(con, "SELECT day FROM dashboard_runs ORDER BY datetime(last_heartbeat_ts) DESC LIMIT 1").fetchone()
    if r and r["day"]:
        return str(r["day"])
    return _q_retry(con, "SELECT date('now','utc')").fetchone()[0]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: gui/dashboard_data.py
# 🔎 SEARCH: ^def fetch_strategy_table\(
# Replace the entire function with the block below.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def fetch_strategy_table(*, mode: str = "LIVE") -> list[dict]:
    """
    Return rows for the Strategies — Performance table straight from v_strategy_perf.
    The view already:
      - filters to local 'today'
      - normalises letters -> strategy names
      - computes P(q)/P(p)/P(m), C(q)/C(p)/C(m), Open P
      - computes matched_liab / unmatched_liab (positive)
      - computes pnl_today
      - exposes last_trade_ts
    """
    from sqlite3 import Row
    try:
        # make sure the view exists / is up to date
        qmode = _qmode(mode)          # normalise (SIM→TEST, ALL→None)
        ensure_strategy_view_mode(qmode)
     
    except Exception:
        pass

    con = _adb_ro()
    con.row_factory = Row
    try:
        # view is LIVE-only inside its definition; mode kept for future-proofing
        rows = _q_retry(con, "SELECT strategy, p_q, p_p, p_m, c_q, c_p, c_m, "
            "       open_parents, matched_liab, unmatched_liab, pnl_today, last_trade_ts "
            "FROM v_strategy_perf "
            "ORDER BY strategy"
        ).fetchall()
        out = []
        for r in rows:
            out.append({
                "strategy":       r["strategy"],
                "p_q":            int(r["p_q"] or 0),
                "p_p":            int(r["p_p"] or 0),
                "p_m":            int(r["p_m"] or 0),
                "c_q":            int(r["c_q"] or 0),
                "c_p":            int(r["c_p"] or 0),
                "c_m":            int(r["c_m"] or 0),
                "open_parents":   int(r["open_parents"] or 0),
                "matched_liab":   float(r["matched_liab"] or 0.0),
                "unmatched_liab": float(r["unmatched_liab"] or 0.0),
                "pnl_today":      float(r["pnl_today"] or 0.0),
                "last_trade_ts":  (r["last_trade_ts"] or None),
            })
        return out
    finally:
        try: con.close()
        except Exception: pass

# === PATCH START: Date selector + minutes-to-off ===
# Active day (UTC). Default = today. Override with DASH_DATE=YYYY-MM-DD
_ACTIVE_DAY_UTC = os.environ.get("DASH_DATE", "").strip() or None

def set_active_day(day_utc: str | None) -> None:
    """Set the dashboard's working day (UTC 'YYYY-MM-DD'). None => today."""
    global _ACTIVE_DAY_UTC
    _ACTIVE_DAY_UTC = (day_utc or "").strip() or None

def _day_sql() -> str:
    """Return a SQLite date() expression respecting the active day (UTC)."""
    return f"date('{_ACTIVE_DAY_UTC}')" if _ACTIVE_DAY_UTC else "date('now','utc')"

def _bets_conn() -> sqlite3.Connection:
    con = connect_db(ro=True)
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def _parse_off_to_utc(s: str) -> datetime | None:
    if not s: return None
    s = s.strip()
    try:
        # Normalize ISO with Z
        if s.endswith("Z"):
            return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
        # ISO without tz
        dtp = datetime.fromisoformat(s[:26])
        return (dtp if dtp.tzinfo else dtp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    except Exception:
        return None

def minutes_to_off_from_bets(market_id: str, *, now_utc: datetime | None = None) -> float | None:
    """
    Canonical MTO: read OFF time from BETS_DB.markets_schedule.off_at_utc, fall back to local-date rows.
    Returns minutes to off (float; negative if in-play), or None if unknown.
    """
    now = now_utc or datetime.now(timezone.utc)
    con = _bets_conn()
    try:
        # try exact row first
        r = con.execute("SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1",
                        (str(market_id),)).fetchone()
        if not (r and r["off_at_utc"]):
            # fallback: any of today's rows for this market (rare)
            r = con.execute(
                "SELECT off_at_utc FROM markets_schedule "
                f"WHERE marketId=? AND substr(off_at_utc,1,10)={_day_sql()} "
                "ORDER BY off_at_utc DESC LIMIT 1",
                (str(market_id),)
            ).fetchone()
        if not (r and r["off_at_utc"]):
            return None
        off = _parse_off_to_utc(str(r["off_at_utc"]))
        if not off: return None
        return (off - now).total_seconds() / 60.0
    except Exception:
        return None
    finally:
        try: con.close()
        except Exception: pass

def resolve_minutes_to_off(market_id: str, *, default: float | None = None) -> float | None:
    """
    Single source used by the dashboard: always try BETS_DB first.
    """
    m = minutes_to_off_from_bets(market_id)
    return m if m is not None else default
# === PATCH END ===



