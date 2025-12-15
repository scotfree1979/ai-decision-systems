# engines/mastery/mastery_policy.py
from __future__ import annotations

import os, json, math, sqlite3, random
from dataclasses import dataclass
from typing import Any, Dict, Tuple, Optional, List

from engines.config_paths import connect_db
from engines.mastery.policy_lookup import get_bin_key, load_posteriors
from engines.mastery.microstructure import compute_p_fill, expected_slippage_ticks
from engines.mastery.risk import pretrade_ok, derive_stops
from collections import defaultdict
from typing import Dict, Set

# === PATCH A START (EPIC state) ===
from collections import defaultdict

_SCOPE_STATE = {
    "at": None,
    "mids": set(),                  # type: Set[str]
    "mid_to_sids": defaultdict(set),# type: Dict[str, Set[str]]
    "epics": {}                     # NEW: mid -> {"stories": int, "sids": set[str]}
}

import sqlite3
from datetime import datetime, timezone

# === PATCH START ===
# 📍 TARGET: engines/mastery/mastery_policy.py (global flags)
# 📆 PATCHED: 2025-11-26 — Legacy-only mode enable
ENABLE_LEGACY_ONLY = True
LEGACY_FAMILIES = {"S","B","G","R","X","F","P","L","I","T","C","E","K","A","M"}  # Legacy letters only
# === PATCH END ===


# === PATCH: fav/field tags, movement, blueprint conf =========================
import glob

def _auto_open():
    """Open autoscalp_gui.db using config_paths if available; else local path."""
    try:
        from engines.config_paths import auto_conn as _auto_conn
        con = _auto_conn(); con.row_factory = sqlite3.Row
        return con
    except Exception:
        # last-ditch local path
        con = sqlite3.connect(os.path.join("data", "autoscalp_gui.db"))
        con.row_factory = sqlite3.Row
        return con

def _view_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT name FROM sqlite_master WHERE type IN ('view','table') AND name=?", (name,)).fetchone()
    return bool(row)

def _fav_tags_for_market(mid: str, *, lo: float = 1.5, hi: float = 12.0) -> dict[str, dict]:
    """
    Returns { sid: {'rank':1.., 'tag':'fav'|'2nd'|'3rd'|'4th'|'field'|'ignore', 'ltp':float} }
    Uses v_odds_latest if available, else falls back to oc_series CTE.
    """
    out: dict[str, dict] = {}
    if not mid: return out
    con = None
    try:
        con = _auto_open()
        if _view_exists(con, "v_odds_latest"):
            rows = con.execute("""
              SELECT selectionId, CAST(ltp AS REAL) AS ltp
              FROM v_odds_latest
              WHERE marketId=? AND ltp BETWEEN ? AND ?
              ORDER BY ltp ASC
              LIMIT 6
            """, (mid, lo, hi)).fetchall() or []
        else:
            rows = con.execute("""
              WITH latest AS (
                SELECT selectionId, MAX(snapshot_ts) AS max_ts
                FROM oc_series
                WHERE marketId=?
                GROUP BY selectionId
              )
              SELECT o.selectionId, CAST(o.odd AS REAL) AS ltp
              FROM oc_series o JOIN latest l
                ON o.selectionId=l.selectionId AND o.snapshot_ts=l.max_ts
              WHERE CAST(o.odd AS REAL) BETWEEN ? AND ?
              ORDER BY ltp ASC
              LIMIT 6
            """, (mid, lo, hi)).fetchall() or []

        tags = ["fav","2nd","3rd","4th","field","field"]
        for i, r in enumerate(rows, start=1):
            sid = str(r["selectionId"]); ltp = float(r["ltp"])
            tag = tags[i-1] if i <= len(tags) else "ignore"
            out[sid] = {"rank": i, "tag": tag, "ltp": ltp}
        return out
    except Exception:
        return out
    finally:
        try:
            if con: con.close()
        except Exception:
            pass


# === PATCH: disable Overwatcher ingestion (A) ===
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def consume_overwatcher_events():
# ⛏️ ACTION: replace entire function body
def consume_overwatcher_events():
    """
    Legacy-only mode:
    Overwatcher-driven plans (stop-loss signals, emergency bailout,
    credit updates, MLM liability hits) are disabled.
    This function intentionally does nothing in legacy mode.
    """
    return None
# === PATCH END ===


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: SELECT ltp_first, ltp_last
# ⛏️ ACTION: remove any WHERE datetime(updated_ts) >= … filter; keep simple selection
# 📆 PATCHED: 2025-10-06T00:00Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _ensure_recent_net_ticks(ctx: Dict[str, Any]) -> None:
    """Derive 'recent_net_ticks' from first vs last today (oc_series)."""
    if ctx.get("recent_net_ticks") is not None:
        return
    mid = str(ctx.get("marketId") or ""); sid = str(ctx.get("selectionId") or "")
    if not mid or not sid:
        return
    try:
        from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
        con = _auto_conn(); con.row_factory = sqlite3.Row
        r = _q(con, """
            SELECT ltp_first, ltp_last
              FROM v_odds_first_last_today
             WHERE marketId=? AND selectionId=?
             LIMIT 1
        """, (mid, sid)).fetchone()   # ← removed any datetime('now', …) gating
        con.close()
        if not r or r["ltp_first"] is None or r["ltp_last"] is None:
            return
        dticks = _ticks_between(float(r["ltp_first"]), float(r["ltp_last"]))
        ctx["recent_net_ticks"] = int(dticks)
        if abs(dticks) >= 3 and ctx.get("slope_ppm") in (None, 0, 0.0):
            ctx["slope_ppm"] = 0.04 if dticks < 0 else -0.04
    except Exception:
        pass

from engines.mastery.goal_adapter import compute_live_goals, evaluate_progress

try:
    live_pnl, win_rate, matched_ratio = compute_live_goals()
    goal_score = evaluate_progress(live_pnl, win_rate, matched_ratio)
    reward *= (0.5 + goal_score)
except Exception:
    pass




def _movement_for_market(mid: str, *, lo: float = 1.5, hi: float = 12.0) -> dict[str, dict]:
    """
    Returns { sid: {'was_rank':int, 'now_rank':int, 'movement':'steaming'|'drifting'|'steady',
                    'ltp_first':float,'ltp_last':float} }
    Prefers v_odds_first_last_today; falls back to raw oc_series if view missing.
    """
    out: dict[str, dict] = {}
    if not mid: return out
    con = None
    try:
        con = _auto_open()
        if _view_exists(con, "v_odds_first_last_today"):
            fl = con.execute("""
              SELECT selectionId, ltp_first, ltp_last
              FROM v_odds_first_last_today
              WHERE marketId=?
            """, (mid,)).fetchall() or []
            # rank within band for first and last
            first = sorted([(r["selectionId"], float(r["ltp_first"])) for r in fl if r["ltp_first"] is not None and lo <= float(r["ltp_first"]) <= hi], key=lambda x: x[1])
            last  = sorted([(r["selectionId"], float(r["ltp_last"]))  for r in fl if r["ltp_last"]  is not None and lo <= float(r["ltp_last"])  <= hi], key=lambda x: x[1])
            franks = {str(s): i+1 for i, (s, _) in enumerate(first)}
            lranks = {str(s): i+1 for i, (s, _) in enumerate(last)}
            fpx    = {str(s): float(p) for s,p in first}
            lpx    = {str(s): float(p) for s,p in last}
        else:
            # on-the-fly emulate the view for today only
            fl = con.execute("""
              WITH raw AS (
                SELECT selectionId, snapshot_ts, odd, stage, id,
                       CASE WHEN stage GLOB 'OC[0-9]*' THEN CAST(SUBSTR(stage,3) AS INT) ELSE 0 END AS stage_n
                FROM oc_series WHERE marketId=? AND date(snapshot_ts)=date('now','utc')
              ),
              first_ts AS ( SELECT selectionId, MIN(snapshot_ts) AS first_ts FROM raw GROUP BY selectionId ),
              last_ts  AS ( SELECT selectionId, MAX(snapshot_ts) AS last_ts  FROM raw GROUP BY selectionId ),
              first_pick AS (
                SELECT r.selectionId, r.snapshot_ts AS ts, r.odd AS ltp,
                       ROW_NUMBER() OVER (PARTITION BY r.selectionId, r.snapshot_ts ORDER BY r.stage_n DESC, r.id DESC) AS rn
                FROM raw r JOIN first_ts f ON r.selectionId=f.selectionId AND r.snapshot_ts=f.first_ts
              ),
              last_pick AS (
                SELECT r.selectionId, r.snapshot_ts AS ts, r.odd AS ltp,
                       ROW_NUMBER() OVER (PARTITION BY r.selectionId, r.snapshot_ts ORDER BY r.stage_n DESC, r.id DESC) AS rn
                FROM raw r JOIN last_ts l ON r.selectionId=l.selectionId AND r.snapshot_ts=l.last_ts
              )
              SELECT f.selectionId,
                     CAST(f.ltp AS REAL) AS ltp_first,
                     CAST(l.ltp AS REAL) AS ltp_last
              FROM first_pick f JOIN last_pick l USING (selectionId)
              WHERE f.rn=1 AND l.rn=1
            """, (mid,)).fetchall() or []
            first = sorted([(r["selectionId"], float(r["ltp_first"])) for r in fl if r["ltp_first"] is not None and lo <= float(r["ltp_first"]) <= hi], key=lambda x: x[1])
            last  = sorted([(r["selectionId"], float(r["ltp_last"]))  for r in fl if r["ltp_last"]  is not None and lo <= float(r["ltp_last"])  <= hi], key=lambda x: x[1])
            franks = {str(s): i+1 for i, (s, _) in enumerate(first)}
            lranks = {str(s): i+1 for i, (s, _) in enumerate(last)}
            fpx    = {str(s): float(p) for s,p in first}
            lpx    = {str(s): float(p) for s,p in last}

        for sid in set(franks) | set(lranks):
            was = franks.get(sid); now = lranks.get(sid)
            if was is None or now is None: continue
            mov = "steady"
            if was > now:  mov = "steaming"
            elif was < now: mov = "drifting"
            out[sid] = {"was_rank": was, "now_rank": now, "movement": mov,
                        "ltp_first": fpx.get(sid), "ltp_last": lpx.get(sid)}
        return out
    except Exception:
        return out
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

def _blueprint_conf_for_sid(mid: str, sid: str) -> tuple[float|None, str|None]:
    """
    Return (score, key) for a runner, using in-memory blueprint cache if available.
    Falls back to DB or JSON only if cache is missing.
    """
    if not (mid and sid):
        return (None, None)

    # 1️⃣ Try in-memory cache first
    try:
        from engines.blueprints import blueprints as bp
        if hasattr(bp, "_BLUEPRINTS_CACHE") and "flat" in bp._BLUEPRINTS_CACHE:
            data = bp._BLUEPRINTS_CACHE["flat"]
            vals, best_key = [], None
            for pattern, entry in data.items():
                for t in (entry.get("trades") or []):
                    if str(t.get("market_id")) == str(mid) and str(t.get("selection_id")) == str(sid):
                        c = t.get("confidence")
                        if isinstance(c, (int, float)):
                            vals.append(float(c))
                            best_key = pattern
            if vals:
                return (sum(vals)/len(vals), best_key)
    except Exception:
        pass

    # 2️⃣ Fallback to DB and JSON as before
    try:
        con = _auto_open()
        row = con.execute("""
          SELECT blueprint_key, score
          FROM blueprint_state
          WHERE day = date('now','utc') AND marketId=? AND selectionId=?
          ORDER BY updated_at DESC
          LIMIT 1
        """, (mid, sid)).fetchone()
        if row:
            return (float(row["score"]), row["blueprint_key"])
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass

    try:
        import json, glob, os
        today = __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d")
        files = sorted(glob.glob(os.path.join("data","blueprints", f"blueprint_signals_{today}.json")))
        if files:
            with open(files[-1], "r") as f:
                data = json.load(f)
            vals, best_key = [], None
            for pattern, entry in data.items():
                for t in (entry.get("trades") or []):
                    if str(t.get("market_id")) == str(mid) and str(t.get("selection_id")) == str(sid):
                        c = t.get("confidence")
                        if isinstance(c, (int, float)):
                            vals.append(float(c))
                            best_key = pattern
            if vals:
                return (sum(vals)/len(vals), best_key)
    except Exception:
        pass

    return (None, None)


# --- day catalogue (bets.db) --------------------------------------------------
_DAY_PLAN = {
    "order": [],     # [mid1, mid2, ...] for today
    "mids": set(),   # set(mid)
    "primed": False,
}

_VARIANT_KEYS = (
    "EACH WAY", "TO BE PLAC", " TBP", " PLACE ", "PLACE ONLY",
    "WITHOUT", "BETTING WITHOUT", "MATCH BET", "INSURANCE",
    "FORECAST", "TRICAST", "EXACTA", "QUINELLA", "ANTEPOST"
)

def _is_primary_market_name(name: str | None) -> bool:
    n = (name or "").upper()
    if not n:
        return True
    return not any(k in n for k in _VARIANT_KEYS)

def _bets_full_day_markets() -> list[tuple[str, str]]:
    """
    Return today's primary Win markets from bets.db:
    [(marketId, off_ts_isoZ), ...] ordered by off time asc.
    """
    try:
        from engines.config_paths import bets_db
    except Exception:
        return []

    con = None
    try:
        con = sqlite3.connect(bets_db()); con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT DISTINCT marketId,
                   COALESCE(marketStartTime, '') AS off_ts,
                   COALESCE(marketName, '')      AS market_name
              FROM bets
             WHERE date(marketStartTime) = date('now','utc')
             ORDER BY datetime(marketStartTime) ASC
        """).fetchall() or []
        out: list[tuple[str,str]] = []
        for r in rows:
            mid  = str(r["marketId"])
            name = str(r["market_name"] or "")
            if not _is_primary_market_name(name):
                continue
            off_ts = str(r["off_ts"] or "")
            # Normalize to ISO-Z if possible
            if off_ts and "T" not in off_ts:
                off_ts = off_ts.replace(" ", "T") + "Z"
            out.append((mid, off_ts))
        return out
    except Exception:
        return []
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

def _maybe_filter_by_runner_count(mids: list[str]) -> list[str]:
    """
    Optional guard: drop markets with <=6 distinct selectionIds in autoscalp_gui.db.odds_current today.
    If AUTO DB unavailable, return the list as-is (fail-open).
    """
    try:
        from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
    except Exception:
        return mids

    con = None
    keep: list[str] = []
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row
        for mid in mids:
            r = _q(con, """
                SELECT COUNT(DISTINCT selectionId) AS c
                  FROM odds_current
                 WHERE marketId=? AND day IN (date('now'), date('now','utc'))
            """, (mid,)).fetchone()
            if r and int(r["c"] or 0) > 6:
                keep.append(mid)
        return keep or mids
    except Exception:
        return mids
    finally:
        try:
            if con: con.close()
        except Exception:
            pass

def _prime_day_plan_once() -> None:
    """
    Build today's catalogue once from bets.db (optionally filtered by runner count),
    store in _DAY_PLAN for Mastery's planning context.
    """
    if _DAY_PLAN["primed"]:
        return
    rows = _bets_full_day_markets()
    mids = [mid for (mid, _off) in rows]
    mids = _maybe_filter_by_runner_count(mids)
    _DAY_PLAN["order"]  = mids
    _DAY_PLAN["mids"]   = set(mids)
    _DAY_PLAN["primed"] = True

def day_plan_markets() -> list[str]:
    """
    Public helper for orchestrator/tests: full-day ordered marketIds known to Mastery.
    Always safe to call; primes on first call.
    """
    _prime_day_plan_once()
    return list(_DAY_PLAN["order"])

# ── hook the day-catalogue into existing scope ingestion (no behavior change to gating) ──
# find your existing ingest_scope(scope_snapshot: dict) and add the marked lines inside it:
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def ingest_scope(scope_snapshot: dict) -> None:
# 📆 PATCHED: 2025-10-06T18:30Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ingest_scope(scope_snapshot: dict) -> None:
    try:
        _SCOPE_STATE["at"] = scope_snapshot.get("at")
        mids = set()
        mapping = defaultdict(set)
        epics = {}

        timing_map = {}
        for m in (scope_snapshot.get("markets") or []):
            mid = str(m.get("marketId"))
            if not mid:
                continue
            mto = m.get("minutes_to_off")
            phase = "IN_PLAY" if (mto is not None and mto <= 0) else "PRE"
            timing_map[mid] = {"minutes_to_off": mto, "phase": phase}
        _SCOPE_STATE["timing"] = timing_map

        for m in (scope_snapshot.get("markets") or []):
            mid = str(m.get("marketId"))
            sids = {str(sid) for sid in (m.get("active_sids") or [])}
            mids.add(mid)
            mapping[mid].update(sids)
            epics[mid] = {"stories": len(sids), "sids": sids}

        _SCOPE_STATE["mids"] = mids
        _SCOPE_STATE["mid_to_sids"] = mapping
        _SCOPE_STATE["epics"] = epics
        _prime_day_plan_once()

        # === NEW: trigger plan board reseed so plan_ledger is refreshed ===
        try:
            from engines.mastery.mastery_policy import update_plan_board
            update_plan_board(scope_snapshot)
        except Exception as e:
            print(f"[ingest_scope] plan-board reseed warn: {e}")
        # =================================================================
    except Exception:
        pass
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def epic_info(mid: str) -> dict | None:
    """Return epic metadata for a marketId (stories count + sids set)."""
    return _SCOPE_STATE.get("epics", {}).get(str(mid))
# === PATCH A END ===

# === PATCH B START (story ranking helper) ===
def _rank_map_for_market(mid: str, sids: list[str]) -> dict[str, int]:
    """
    Return {sid -> rank} by latest LTP ascending (fav=1).
    If price missing, push to the end.
    """
    try:
        from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
        con = _auto_conn(); con.row_factory = sqlite3.Row
        rows = _q(con, """
            WITH latest AS (
              SELECT selectionId, MAX(updated_ts) AS mx
                FROM odds_current
               WHERE marketId=? AND date(day)=date('now','utc')
               GROUP BY selectionId
            )
            SELECT o.selectionId, CAST(o.ltp AS REAL) AS px
              FROM latest l
              JOIN odds_current o
                ON o.selectionId=l.selectionId
               AND o.marketId=?
               AND o.updated_ts=l.mx
             WHERE o.selectionId IN (%s)
        """ % (",".join("?"*len(sids))),
        [mid, mid, *list(map(str, sids))]).fetchall() or []
        con.close()
        # Build price map; missing = +inf
        px = {str(r["selectionId"]): (float(r["px"]) if r["px"] is not None else float("inf")) for r in rows}
        ranked = sorted(sids, key=lambda sid: px.get(str(sid), float("inf")))
        return {str(s): i+1 for i, s in enumerate(ranked)}
    except Exception:
        # fail-open: deterministic order as given
        return {str(s): i+1 for i, s in enumerate(sids)}
# === PATCH B END ===


def _in_scope(mid: str, sid: str) -> bool:
    try:
        return (mid in _SCOPE_STATE["mids"]) and (sid in _SCOPE_STATE["mid_to_sids"].get(mid, set()))
    except Exception:
        return False

# Optional guard at the top of plan_for_strategy (or in your internal dispatcher)
def _guard_scope(ctx: dict) -> tuple[bool, str]:
    mid = str(ctx.get("marketId") or "")
    sid = str(ctx.get("selectionId") or "")
    if not _in_scope(mid, sid):
        return False, "out_of_scope"
    return True, ""

# -----------------------------------------------------------------------------
# Row-factory safety (wrap connect_db in mastery submodules)
_PATCHED_DB_ROW = False
def _ensure_row_factory_monkeypatch() -> None:
    import engines.mastery.policy_lookup as _pl
    import engines.mastery.microstructure as _ms
    import engines.mastery.risk as _rk
    global _PATCHED_DB_ROW
    if _PATCHED_DB_ROW:
        return

    def _wrap_connect_db(fn):
        def _inner(*args, **kwargs):
            conn = fn(*args, **kwargs)
            try:
                conn.row_factory = sqlite3.Row
            except Exception:
                pass
            return conn
        return _inner

    try:
        if hasattr(_pl, "connect_db"):
            _pl.connect_db = _wrap_connect_db(_pl.connect_db)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        if hasattr(_ms, "connect_db"):
            _ms.connect_db = _wrap_connect_db(_ms.connect_db)  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        if hasattr(_rk, "connect_db"):
            _rk.connect_db = _wrap_connect_db(_rk.connect_db)  # type: ignore[attr-defined]
    except Exception:
        pass

    _PATCHED_DB_ROW = True

# ---- minimal imports at top of file (guarded) --------------------------------
try:
    import engines.daily_config as daily_config
except Exception:
    class daily_config:  # tiny defaults if module not loaded
        BASE_STAKE = 2.0
        BASE_STAKE_A = 2.0

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 🔎 SEARCH: from __future__ import annotations
# ⛏️ ACTION: append the following helpers near the top-level (after imports)

import sqlite3, json
from datetime import datetime, timezone, timedelta

def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _resolve_off_col(con: sqlite3.Connection) -> str:
    """
    Find an OFF time column on markets_schedule. Supported names:
    off_ts, off_at, scheduled_off, openDate (Betfair), startTime
    """
    cols = {r[1].lower(): r[1] for r in con.execute("PRAGMA table_info(markets_schedule)") or []}
    for cand in ("off_ts","off_at","scheduled_off","openDate","startTime"):
        if cand.lower() in cols:
            return cols[cand.lower()]
    # no column found -> we will fall back to inbound freshness only
    return ""

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
    return bool(row)

# === PATCH START ===
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def read_scope_window\(
# ⛏️ ACTION: add minutes_to_off calculation and attach it to each scoped market
# 📆 PATCHED: 2025-10-01T01:20Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def read_scope_window(*, ahead_min: int = 30, lookback_min: int = 5,
                      inbound_fresh_sec: int = 120, max_markets: int = 40):
    """
    Returns a scope snapshot dict:
      {
        'generated_at': ts, 'ahead_min': 30, 'lookback_min': 5,
        'markets': [
           {'marketId': '1.234', 'off_ts': '2025-09-25 15:10:00Z',
            'minutes_to_off': 12.4, 'fresh': True, 'active_sids': [...]}
        ]
      }
    """
    snap = {'generated_at': _now_utc_str(),
            'ahead_min': ahead_min, 'lookback_min': lookback_min,
            'markets': []}
    try:
        from .helpers import open_auto_db as _adb, q_retry as _q
    except Exception:
        from engines.config_paths import auto_conn as _adb, q_retry as _q

    con = _adb(ro=True); con.row_factory = sqlite3.Row
    try:
        off_col = _resolve_off_col(con) if _table_exists(con, "markets_schedule") else ""
        have_odds_current = _table_exists(con, "odds_current")

        lookback = f"-{int(max(0, lookback_min))} minutes"
        ahead    = f"+{int(max(1, ahead_min))} minutes"

        markets: list[sqlite3.Row] = []
        if off_col:
            markets = _q(con, f"""
                WITH windowed AS (
                  SELECT marketId, MIN({off_col}) AS off_ts
                    FROM markets_schedule
                   WHERE datetime({off_col})
                         BETWEEN datetime('now','utc','{lookback}')
                             AND datetime('now','utc','{ahead}')
                   GROUP BY marketId
                )
                SELECT w.marketId, w.off_ts
                  FROM windowed w
                  ORDER BY datetime(w.off_ts) ASC
                  LIMIT ?
            """, (max_markets,)).fetchall() or []

        mids = [r["marketId"] for r in markets] if markets else []
        fresh_cutoff = f"-{int(max(5, inbound_fresh_sec))} seconds"

        def _active_sids(mid: str) -> list[str]:
            # unchanged...
            sids: list[str] = []
            if have_odds_current:
                rows = _q(con, """
                    SELECT selectionId
                      FROM odds_current
                     WHERE marketId=? AND datetime(updated_ts) >= datetime('now','utc',?)
                     ORDER BY CAST(ltp AS REAL) ASC
                     LIMIT 12
                """, (mid, fresh_cutoff)).fetchall() or []
                sids = [str(r["selectionId"]) for r in rows]
            else:
                rows = _q(con, """
                    SELECT selectionId
                      FROM inbound_oc_cache
                     WHERE marketId=? AND date(last_sync_ts)=date('now','utc')
                     ORDER BY id DESC
                     LIMIT 24
                """, (mid,)).fetchall() or []
                seen = set()
                for r in rows:
                    sid = str(r["selectionId"])
                    if sid in seen: continue
                    seen.add(sid); sids.append(sid)
                sids = sids[:12]
            return sids

        scoped = []
        base = markets
        if not base and have_odds_current:
            base = _q(con, """
                SELECT marketId, MAX(updated_ts) AS off_ts
                  FROM odds_current
                 WHERE datetime(updated_ts) >= datetime('now','utc',?)
                 GROUP BY marketId
                 ORDER BY MAX(datetime(updated_ts)) DESC
                 LIMIT ?
            """, (fresh_cutoff, max_markets)).fetchall() or []

        now = datetime.now(timezone.utc)
        for r in base:
            mid = str(r["marketId"])
            off_ts = r["off_ts"] if "off_ts" in r.keys() else None
            mto = None
            try:
                if off_ts:
                    ts = str(off_ts)
                    if ts.endswith("Z"):
                        dt = datetime.fromisoformat(ts.replace("Z","+00:00")).astimezone(timezone.utc)
                    else:
                        dt = datetime.fromisoformat(ts).astimezone(timezone.utc)
                    mto = (dt - now).total_seconds() / 60.0
            except Exception:
                mto = None

            sids = _active_sids(mid)
            if not sids:
                continue
            scoped.append({
                "marketId": mid,
                "off_ts": off_ts,
                "minutes_to_off": mto,
                "fresh": True,
                "active_sids": sids,
            })

        snap["markets"] = scoped
        return snap
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===

# === keep existing build_and_maintain_scope/ordered_markets_for_tick, but add: ===

def ordered_markets_for_tick(scope_obj=None, *, ahead_min: int = 30, lookback_min: int = 5) -> list[str]:
    """
    Backward-compatible: returns just the marketId list, *but now* only markets in the active window.
    If a full scope snapshot was given, use that; else read a fresh snapshot.
    """
    if isinstance(scope_obj, dict) and scope_obj.get("markets"):
        return [m["marketId"] for m in scope_obj["markets"]]
    snap = read_scope_window(ahead_min=ahead_min, lookback_min=lookback_min)
    return [m["marketId"] for m in snap["markets"]]
# === PATCH END ===


# ---- minimal Always_On policy -------------------------------------------------
# === PATCH START ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def plan_for_always_on(
# 🎯 ACTION: Hard-disable ALWAYS_ON by delegating to OG_STRATEGY (letter S)
# 📆 PATCHED: 2025-12-05Z
# ============================================================================

def plan_for_always_on(ctx: dict) -> dict:
    """
    ALWAYS_ON (A) is deprecated.
    Replace all A plans with OG_STRATEGY (S) logic.
    """
    try:
        # Force strategy family to OG_STRATEGY
        ctx2 = dict(ctx)
        ctx2["letter"] = "S"
        return plan_for_strategy("OG_STRATEGY", ctx2)
    except Exception:
        # If OG_STRATEGY fails, return no-trade
        return {
            "enter": False,
            "letter": "A",
            "why": "A_disabled_redirect_to_S_failed",
            "px": float(ctx.get("px") or ctx.get("odds") or 0.0),
            "size": 0.0,
            "target_ticks": 0,
        }

# === PATCH END ================================================================


# -----------------------------------------------------------------------------
# Source helper
def _current_source_upper() -> str:
    try:
        from engines.upgrade_import_patch import get_mode  # optional
        m = (get_mode() or os.environ.get("AUTOSCALP_MODE") or "TEST").upper()
    except Exception:
        m = (os.environ.get("AUTOSCALP_MODE") or "TEST").upper()
    return "TEST" if m not in ("TEST","LEARNING","LIVE") else m


# -----------------------------------------------------------------------------
# Daily config passthroughs
def _cfg(name: str, default: float) -> float:
    try:
        import engines.daily_config as dc
        return float(getattr(dc, name))
    except Exception:
        return float(default)

def _dc_get(name: str, default: float | None = None) -> float | None:
    try:
        import engines.daily_config as dc
        val = getattr(dc, name, None)
        if val is None: return default
        return float(val)
    except Exception:
        return default

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: ^def _bank\(\) -> float:
# 📆 PATCHED: 2025-10-01T12:05Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _bank() -> float:
    """Return the current bank balance via BankState (fallback=400.0)."""
    try:
        from engines.live import bank_state
        return float(bank_state.get_balance())
    except Exception:
        return 400.0


def _calc_trade_pnl(entry_odds: float, stake: float, stop_ticks: int, hedge_ticks: int = 1) -> dict:
    """
    Calculate profit/loss envelope for a proposed trade.
    Returns {"profit": x, "stop_loss": y, "band": "2-3"}
    """
    from engines.daily_config import get_stop_ticks
    stop_ticks = stop_ticks or get_stop_ticks(entry_odds)

    # profit side (hedge at entry ± hedge_ticks)
    hedge_odds = entry_odds - 0.01 if hedge_ticks > 0 else entry_odds + 0.01  # simplified
    profit = round(stake * abs(1/hedge_odds - 1/entry_odds), 2)

    # stop side (stop_ticks away)
    stop_odds = entry_odds + 0.01 * stop_ticks
    loss = round(stake * abs(1/entry_odds - 1/stop_odds), 2)

    # band classification
    if entry_odds <= 2: band = "1.5-2"
    elif entry_odds <= 3: band = "2-3"
    elif entry_odds <= 4: band = "3-4"
    elif entry_odds <= 6: band = "4-6"
    elif entry_odds <= 8: band = "6-8"
    elif entry_odds <= 12: band = "8-12"
    else: band = ">12"

    return {"profit": profit, "stop_loss": -loss, "band": band}

def _apply_credit(letter: str, marketId: str, delta: float, band: str) -> None:
    """
    Update per-letter credits and emit events.
    """
    from engines.mastery import plan_ledger
    from engines.mastery import event_sink

    net = plan_ledger.record_credit_update(letter, marketId, delta, band)

    # emit event
    payload = {"type": "credit_update", "mid": marketId, "letter": letter,
               "delta": delta, "band": band, "net_credits": net}
    event_sink.on_decision(payload)

    # check bailout at EPIC level
    ep_net = plan_ledger.get_market_net(marketId)
    if ep_net <= -10:
        event_sink.on_decision({"type":"stop_loss_threshold_hit","mid":marketId,"net_credits":ep_net})


# -----------------------------------------------------------------------------
# Family → Letter lookup (used by plan_for_strategy)
# -----------------------------------------------------------------------------
_FAM_LETTER = {
    "MASTER": "S",             # fallback for direct Mastery plan calls
    "BLUEPRINTS": "P",
    "OG_STRATEGY": "S",
    "LADDER_STRATEGY": "L",
    "ALWAYS_ON": "A",
    "BTL_SCOUT": "B",
    "BTL_AGGR": "G",
    "S4_CROSSOVER": "X",
    "S5_BREAKOUT": "R",
    "S6_STEAM_FADE": "F",
    "MLM": "M",
    "IP1_SHOCK_DRIFT": "I",
    "IP2_TIRED_LEADER": "T",
    "IP3_CLOSE_FINISH": "C",
    "IP4_FENCE_ERROR": "E",
    "IP5_COLLAPSE_FADE": "K",
}

# -----------------------------------------------------------------------------
# Per-letter stake overrides and helpers (kept compatible with your daily_config)
LETTER_KEYS_BASE = {
    "P": "BASE_STAKE_P", "A": "BASE_STAKE_A", "S": "BASE_STAKE_S",
    "B": "BASE_STAKE_B", "G": "BASE_STAKE_G", "X": "BASE_STAKE_X",
    "R": "BASE_STAKE_R", "F": "BASE_STAKE_F", "L": "BASE_STAKE_L",
    "Z": "BASE_STAKE_Z", "I": "BASE_STAKE_I", "T": "BASE_STAKE_T",
    "C": "BASE_STAKE_C", "E": "BASE_STAKE_E", "K": "BASE_STAKE_K",
    "O": "BASE_STAKE_Z", "D": "BASE_STAKE_I", "M": "BASE_STAKE_L",
}
LETTER_KEYS_MAX = {
    "P": "STAKE_MAX_P", "A": "STAKE_MAX_A", "S": "STAKE_MAX_S",
    "B": "STAKE_MAX_B", "G": "STAKE_MAX_G", "X": "STAKE_MAX_X",
    "R": "STAKE_MAX_R", "F": "STAKE_MAX_F", "L": "STAKE_MAX_L",
    "Z": "STAKE_MAX_Z", "I": "STAKE_MAX_I", "T": "STAKE_MAX_T",
    "C": "STAKE_MAX_C", "E": "STAKE_MAX_E", "K": "STAKE_MAX_K",
    "O": "STAKE_MAX_Z", "D": "STAKE_MAX_I", "M": "STAKE_MAX_L",
}
LETTER_MULTIPLIER = {
    "P": 1.25, "A": 1.00, "S": 1.00, "B": 1.05, "G": 1.10,
    "X": 1.10, "R": 1.15, "F": 1.00, "L": 1.00, "Z": 0.90,
    "I": 0.80, "T": 0.80, "C": 0.80, "E": 0.80, "K": 0.80,
}
def _normalise_letter(ch: str) -> str:
    ch = (ch or "S").upper()
    if ch == "O": return "Z"
    if ch == "D": return "I"
    if ch == "M": return "L"
    return ch
def _bank_fraction_stake(bank: float, pct_default: float = 0.004) -> float:
    try:
        import engines.daily_config as dc
        pct = float(getattr(dc, "STAKE_PCT", pct_default))
    except Exception:
        pct = pct_default
    return max(2.0, round(float(bank) * pct, 2))
def base_stake_for_letter(letter: str, *, bank: float | None = None) -> float:
    L = _normalise_letter(letter)
    base = _bank_fraction_stake(float(bank)) if bank is not None else _dc_get("DEFAULT_UNIT_STAKE", 2.0) or 2.0
    key = LETTER_KEYS_BASE.get(L)
    if key:
        ov = _dc_get(key, None)
        if ov is not None:
            base = float(ov)
    mult = float(LETTER_MULTIPLIER.get(L, 1.0))
    return max(2.0, round(base * mult, 2))
def max_stake_for_letter(letter: str) -> float | None:
    L = _normalise_letter(letter)
    key = LETTER_KEYS_MAX.get(L)
    return _dc_get(key, None)

# ───────────────────────────────────────────────────────────────
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def _enrich_ctx\(ctx: Dict\[str, Any\]\) -> Dict\[str, Any\]:
# 📆 PATCHED: 2025-10-04T13:58Z
# ───────────────────────────────────────────────────────────────

def _extract_letter_from_meta(meta_json: str | None, notes: str | None = None) -> str | None:
    """
    Extract a strategy letter (A,P,S,...) from meta_json.cref or notes field.
    cref examples: "A-e6526d0ffb", "P-9a27d11efb".
    """
    import json, re
    # direct note like 'A'
    if notes and len(notes.strip()) == 1 and notes.isalpha():
        return notes.strip().upper()
    # inside meta_json
    try:
        if not meta_json:
            return None
        j = json.loads(meta_json)
        cref = j.get("cref") or ""
        if isinstance(cref, str):
            m = re.match(r"([A-Z])[-_]", cref.upper())
            if m:
                return m.group(1)
    except Exception:
        pass
    return None

# -----------------------------------------------------------------------------
# Context enrichment (odds + time/phase) for robust gating
def _enrich_ctx(ctx: Dict[str, Any]) -> Dict[str, Any]:
    c = dict(ctx or {})
    mid = str(c.get("marketId") or c.get("market_id") or "")
    sid = str(c.get("selectionId") or c.get("selection_id") or "")

    def _ensure_odds():
        if c.get("odds") not in (None, 0, 0.0, "0", "", "None"):
            v = float(c["odds"])
            c.setdefault("price", v)
            c.setdefault("price_now", v)
            c.setdefault("tape_px", v)
            return
        last = None; band = None
        # 1) helper.latest_price
        try:
            from engines.decision_engine.decide_once.helpers import latest_price as _lp
            last, band = _lp(mid, sid) or (None, None)
        except Exception:
            last, band = None, None
        if last is None and band:
            try:
                last = float(band[-1])
            except Exception:
                pass
        # 2) AUTO_DB.odds_current (day-aware → freshest)
        if last is None:
            try:
                from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
                con = _auto_conn(); con.row_factory = sqlite3.Row
                r = _q(con, """
                  SELECT ltp, mto_minutes
                    FROM odds_current
                   WHERE day IN (date('now'), date('now','utc'))
                     AND marketId=? AND selectionId=?
                   ORDER BY updated_ts DESC
                   LIMIT 1
                """, (mid, sid)).fetchone()
                if not (r and r["ltp"] is not None):
                    r = _q(con, """
                      SELECT ltp, mto_minutes
                        FROM odds_current
                       WHERE marketId=? AND selectionId=?
                       ORDER BY datetime(updated_ts) DESC
                       LIMIT 1
                    """, (mid, sid)).fetchone()
                if r and r["ltp"] is not None:
                    last = float(r["ltp"])
                    if c.get("tto_minutes") is None and r["mto_minutes"] is not None:
                        c["tto_minutes"] = float(r["mto_minutes"])
                        c["minutes_to_off"] = float(r["mto_minutes"])
                con.close()
            except Exception:
                pass
        # 3) BETS_DB oc_series
        if last is None:
            try:
                bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
                r = bdb.execute("""
                  SELECT odd FROM oc_series
                   WHERE marketId=? AND selectionId=?
                   ORDER BY datetime(snapshot_ts) DESC
                   LIMIT 1
                """, (mid, sid)).fetchone()
                if r and r["odd"] is not None:
                    last = float(r["odd"])
                bdb.close()
            except Exception:
                pass
        if last is not None:
            c["odds"] = float(last)
            c.setdefault("price", float(last))
            c.setdefault("px", float(last))

    def _ensure_time_phase():
        have_tto = (c.get("tto_minutes") is not None) or (c.get("minutes_to_off") is not None)
        if not have_tto and mid:
            try:
                from engines.decision_engine.orchestrator import _compute_minutes_to_off as _mto
                mto, _w = _mto(mid, source=str(c.get("source") or "LIVE"))
                if mto is not None:
                    c["tto_minutes"] = float(mto)
                    c["minutes_to_off"] = float(mto)
            except Exception:
                pass
        if not c.get("phase"):
            try:
                mto = c.get("minutes_to_off", c.get("tto_minutes"))
                mto = float(mto) if mto is not None else None
                c["phase"] = "IN_PLAY" if (mto is not None and mto <= 0.0) else "PRE"
            except Exception:
                c["phase"] = c.get("phase") or "PRE"

    _ensure_odds()
    _ensure_time_phase()
    _ensure_odds()
    _ensure_time_phase()
    _ensure_recent_net_ticks(ctx)

    # --- WOM enrichment (optional; safe no-op if probe not available) ---
    try:
        mid_s = str(mid); sid_s = str(sid)
        if mid_s and sid_s and c.get("wom_ratio") is None:
            try:
                from engines.indicators import wom_probe as _wom
                # Session token from env (same as your probe)
                _sess = os.environ.get("SESSION_TOKEN", "").strip()
                # Depth 6 → “5–6 ticks away” band included; adjust if you want
                if hasattr(_wom, "fetch_wom_for_runner"):
                    w = _wom.fetch_wom_for_runner(
                        marketId=mid_s, selectionId=int(sid_s),
                        depth=6, session_token=_sess
                    ) or {}
                else:
                    w = {}
            except Exception:
                w = {}

            back6 = float(w.get("back_sum6") or 0.0)
            lay6  = float(w.get("lay_sum6")  or 0.0)
            tot   = back6 + lay6
            if tot > 0.0:
                c["wom_back_6"] = back6
                c["wom_lay_6"]  = lay6
                c["wom_ratio"]  = lay6 / tot     # 0..1; >0.5 = drift pressure
                # Optional: keep the raw ladder levels for downstream tools
                if "levels" in w: c["wom_levels"] = w["levels"]
    except Exception:
        # Don’t let WOM issues break planning
        pass
    # ── Derive letter from meta_json.cref or notes (fallback) ─────────────────
    if not c.get("letter"):
        try:
            meta = c.get("meta_json") or ctx.get("meta_json")
            notes = c.get("notes") or ctx.get("notes")
            letter = _extract_letter_from_meta(meta, notes)
            if letter:
                c["letter"] = letter
        except Exception:
            pass

    # --- Fallback: minutes_to_off from bets.db if missing or absurdly high ---
    try:
        if not c.get("minutes_to_off") or float(c.get("minutes_to_off", 9999)) > 180:
            import sqlite3
            from engines.config_paths import connect_db
            con = connect_db(ro=True)
            con.row_factory = sqlite3.Row
            r = con.execute("""
                SELECT marketStartTime
                  FROM bets
                 WHERE marketId=?
                 ORDER BY marketStartTime DESC
                 LIMIT 1
            """, (mid,)).fetchone()
            con.close()
            if r and r["marketStartTime"]:
                from datetime import datetime, timezone
                off = datetime.fromisoformat(str(r["marketStartTime"]).replace("Z","+00:00"))
                mto = (off - datetime.now(timezone.utc)).total_seconds() / 60.0
                c["minutes_to_off"] = float(mto)
                c["tto_minutes"] = float(mto)
    except Exception:
        pass

    return c



# -----------------------------------------------------------------------------
# ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: ^# -----------------------------------------------------------------------------\n# Direction inference: slope first; fallback to OC-momentum$
# ⛏️ ACTION: replace this whole section through the next blank line with the new story-driven engine
# ============================================================
# Direction inference: story + plan + market aware (replaces slope-only)
def _tick_step(odds: float) -> float:
    """Betfair tick size for a given odds."""
    try:
        x = float(odds or 0.0)
    except Exception:
        return 0.01
    if x < 1.01:    return 0.01
    if x < 2.0:     return 0.01
    if x < 3.0:     return 0.02
    if x < 4.0:     return 0.05
    if x < 6.0:     return 0.10
    if x < 10.0:    return 0.20
    if x < 20.0:    return 0.50
    if x < 30.0:    return 1.00
    if x < 50.0:    return 2.00
    if x <= 100.0:  return 5.00
    return 5.00

def _ticks_between(a: float, b: float) -> int:
    """Count signed ticks from a -> b along the Betfair ladder."""
    try:
        aa = float(a); bb = float(b)
    except Exception:
        return 0
    if aa == bb or aa <= 0.0 or bb <= 0.0:
        return 0
    lo, hi = (aa, bb) if aa < bb else (bb, aa)
    x = lo; ticks = 0; guard = 0
    # walk upwards to hi using local step sizes
    while x < hi - 1e-9 and guard < 2000:
        s = _tick_step(x)
        x = round(x + s, 2 if s <= 0.1 else 3)
        ticks += 1
        guard += 1
    return ticks if bb >= aa else -ticks

def _series_last_minutes(mid: str, sid: str, minutes: int) -> List[float]:
    """Return LTP series for last N minutes from AUTO DB.odds_current (ascending)."""
    try:
        from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
        con = _auto_conn(); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT ltp
              FROM odds_current
             WHERE marketId=? AND selectionId=?
               AND updated_ts >= datetime('now', ?)
               AND ltp IS NOT NULL
             ORDER BY datetime(updated_ts) ASC
        """, (str(mid), str(sid), f"-{int(max(1, minutes))} minutes")).fetchall() or []
        try: con.close()
        except Exception: pass
        return [float(r["ltp"]) for r in rows if r and r["ltp"] is not None]
    except Exception:
        return []

def _anchor_from_sources(mid: str, sid: str) -> Optional[float]:
    """Try to obtain an anchor odds from inbound cache or earliest oc_series; None if unknown."""
    # 1) inbound_oc_cache.anchor_odd (AUTO DB)
    try:
        from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
        con = _auto_conn(); con.row_factory = sqlite3.Row
        r = _q(con, """
            SELECT anchor_odd
              FROM inbound_oc_cache
             WHERE marketId=? AND selectionId=?
             ORDER BY datetime(COALESCE(last_sync_ts,'')) DESC, id DESC
             LIMIT 1
        """, (str(mid), str(sid))).fetchone()
        try: con.close()
        except Exception: pass
        if r and r["anchor_odd"] is not None:
            return float(r["anchor_odd"])
    except Exception:
        pass
    # 2) earliest oc_series.odd (BETS DB)
    try:
        db = connect_db(ro=True); db.row_factory = sqlite3.Row
        r = db.execute("""
            SELECT odd
              FROM oc_series
             WHERE marketId=? AND selectionId=?
             ORDER BY datetime(snapshot_ts) ASC
             LIMIT 1
        """, (str(mid), str(sid))).fetchone()
        db.close()
        if r and r["odd"] is not None:
            return float(r["odd"])
    except Exception:
        pass
    return None

def _lifespan_delta_ticks(mid: str, sid: str, now_px: Optional[float], anchor_ctx: Optional[float]) -> int:
    """Signed ticks from anchor -> now across full lifespan (fallback to sources if ctx lacks anchor)."""
    try: nowv = float(now_px) if now_px is not None else None
    except Exception: nowv = None
    anc = anchor_ctx
    if anc is None:
        anc = _anchor_from_sources(mid, sid)
    if anc is None or nowv is None:
        return 0
    return _ticks_between(float(anc), float(nowv))

def _std(vals: List[float]) -> float:
    try:
        n = len(vals)
        if n < 2: return 0.0
        m = sum(vals)/n
        return math.sqrt(sum((v - m)*(v - m) for v in vals) / (n - 1))
    except Exception:
        return 0.0

def infer_direction_and_ticks(ctx: Dict[str, Any]) -> Tuple[str | None, int | None, str]:
    """
    New stack:
      1) Analyzer override (if confident)
      2) Lifespan story (anchor -> now in ticks)
      3) Mid-window slope & volatility (15m PRE / 2m IP)
      4) Micro momentum nudge (never blocks)
      5) Only flat if lifespan+mid+micro ALL quiet
    Returns: (direction 'LAY->BACK'|'BACK->LAY'|None, ticks|None, note)
    """
    # --- Tunables (read from daily_config if present)
    try:
        import engines.daily_config as dc
        MIN_ANALYZER_CONF = float(getattr(dc, "MIN_ANALYZER_CONF", 0.55))
        T_TOTAL           = int(getattr(dc, "T_TOTAL", 2))           # lifespan Δ ticks threshold
        W_MID_PRE         = int(getattr(dc, "W_MID_PRE", 15))
        W_MID_IP          = int(getattr(dc, "W_MID_IP", 2))
        S_MID             = float(getattr(dc, "S_MID", 0.10))        # ticks/min (approx)
        M_MICRO           = int(getattr(dc, "M_MICRO", 2))           # oc_momentum threshold
        V_STD             = float(getattr(dc, "V_STD", 0.60))        # flatness
    except Exception:
        MIN_ANALYZER_CONF = 0.55; T_TOTAL = 2; W_MID_PRE = 15; W_MID_IP = 2; S_MID = 0.10; M_MICRO = 2; V_STD = 0.60

    phase = str(ctx.get("phase","PRE")).upper()
    mid = str(ctx.get("marketId") or "")
    sid = str(ctx.get("selectionId") or "")
    # current price
    now_px = None
    for k in ("odds","px","ltp","price","price_now","tape_px","current_odds","last_price"):
        v = ctx.get(k)
        try:
            if v is not None:
                now_px = float(v); break
        except Exception:
            continue
    # analyzer override
    try:
        a_dir, a_conf, a_note = _direction_from_analyzer(ctx)
    except Exception:
        a_dir, a_conf, a_note = (None, 0.0, "analyzer_unavailable")
    if a_dir and float(a_conf) >= float(MIN_ANALYZER_CONF):
        # analyzer already returns 'BACK->LAY' or 'LAY->BACK'
        note = f"analyzer:{a_note}"
        # mild tick target; tick floor will still apply later
        return (a_dir, 2 if phase != "IN_PLAY" else 1, note)

    # lifespan story (anchor -> now, in ticks)
    delta_ticks = _lifespan_delta_ticks(mid, sid, now_px, ctx.get("anchor_odd"))
    # mid-window slope & volatility
    W = W_MID_IP if phase == "IN_PLAY" else W_MID_PRE
    series = _series_last_minutes(mid, sid, W)
    if series and len(series) >= 2:
        first, last = float(series[0]), float(series[-1])
        mid_slope = _ticks_between(first, last) / max(1.0, float(W))  # ticks per minute (approx)
        ticks_series = [_ticks_between(first, p) for p in series]
        vol_std = _std(ticks_series)
    else:
        mid_slope = 0.0; vol_std = 0.0

    micro = 0
    try: micro = int(ctx.get("oc_momentum_ticks") or 0)
    except Exception: micro = 0

    parts = [f"story:Δ={delta_ticks:+d}", f"mid:{mid_slope:+.2f}/std={vol_std:.2f}", f"micro:{micro:+d}"]

    # choose direction: story > mid > micro; only flat if ALL quiet
    chosen = None; ticks_rule = 1
    if abs(delta_ticks) >= T_TOTAL:
        chosen = "LAY->BACK" if delta_ticks > 0 else "BACK->LAY"
        ticks_rule = 2 if abs(delta_ticks) < 5 else 3
    elif abs(mid_slope) >= S_MID:
        chosen = "LAY->BACK" if mid_slope > 0 else "BACK->LAY"
        ticks_rule = 2
    elif abs(micro) >= M_MICRO:
        chosen = "LAY->BACK" if micro > 0 else "BACK->LAY"
        ticks_rule = 1
    else:
        # truly flat only if lifespan + mid + micro AND low volatility are all quiet
        if abs(delta_ticks) < T_TOTAL and abs(mid_slope) < S_MID and abs(micro) < M_MICRO and vol_std < V_STD:
            return (None, None, "flat_lifespan")
        # otherwise nudge with story bias even if small
        chosen = "LAY->BACK" if delta_ticks >= 0 else "BACK->LAY"
        ticks_rule = 1

    note = " | ".join(parts)
    return (chosen, int(max(1, ticks_rule)), note)


# ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def gate(ctx: dict, letter: str)
# 📆 PATCHED: 2025-12-12
# 🧠 WHY:
#   Time- and phase-based gating is deprecated in v7.
#   Scope + CTX determine eligibility, not clock position.
#   This patch preserves odds-band safety only.
# ============================================================

def gate(ctx: dict, letter: str) -> tuple[bool, str]:
    L = (letter or "").upper()

    def _f(key: str, default: float = 0.0):
        try: return float(ctx.get(key, default) or default)
        except Exception: return default

    odds = _f("odds", _f("px", _f("ltp", 0.0)))

    # --- PRE / LEGACY families ---
    if L in ("S","B","G","F","X","R","L","Z"):
        if odds and not (1.5 <= odds <= 12.0):
            return (False, "odds_band")
        return (True, "ok")

    # --- IN-PLAY families (phase gate removed) ---
    if L in ("I","T","C","E","K"):
        if odds and not (1.5 <= odds <= 12.0):
            return (False, "odds_band")
        return (True, "ok")

    # --- Always-on / Blueprint ---
    if L in ("P","A"):
        if odds and not (1.5 <= odds <= 12.0):
            return (False, "odds_band")
        return (True, "ok")

    if odds and not (1.5 <= odds <= 12.0):
        return (False, "odds_band")

    return (True, "ok")

# -----------------------------------------------------------------------------
# Confidence & simple tick floor
def _trend_sign(ctx: dict) -> int:
    try: sppm = float(ctx.get("slope_ppm", 0.0) or 0.0)
    except Exception: sppm = 0.0
    up = int(ctx.get("up_ticks_10s",0) or 0); dn = int(ctx.get("down_ticks_10s",0) or 0)
    tot = up + dn; dv = (dn - up)/tot if tot>0 else 0.0
    if (sppm <= -0.03) or (sppm <= -0.015 and dv <= -0.20): return -1
    if (sppm >= +0.03) or (sppm >= +0.015 and dv >= +0.20): return +1
    return 0

def _near_edge(ctx: dict) -> bool:
    try: tte = int(ctx.get("ticks_to_extreme", 999))
    except Exception: tte = 999
    return tte <= 4

def compute_confidence_and_ticks(ctx: dict) -> tuple[float, int, str]:
    base = 0.52
    trend = _trend_sign(ctx)
    if _near_edge(ctx) and trend != 0:
        base += 0.04
    brk = str(ctx.get("range_breakout","none")).lower()
    brk_conf = bool(ctx.get("range_breakout_confirmed", False))
    if brk != "none" and brk_conf:
        base += 0.06
    base = max(0.0, min(1.0, base))
    if brk_conf:
        ticks = 2; tag = "breakout"
    elif trend != 0:
        ticks = 2 if abs(float(ctx.get("slope_ppm",0.0))) >= 0.08 else 1
        tag = "trend"
    else:
        ticks = 1; tag = "flat"
    return (base, ticks, tag)

# --- WOM: derive book-imbalance direction & confidence -----------------------
def _wom_from_ctx(ctx: dict) -> tuple[Optional[str], float, str]:
    """
    Return (dir, conf, note):
      dir ∈ {'L2B','B2L',None}, conf∈[0..1]
    Expect either:
      - ctx['wom_ratio'] ∈ [0..1]  (lay_share), or
      - ctx['wom_back_sum'], ctx['wom_lay_sum'] (cash at top levels)
    """
    try:
        # 1) direct ratio if provided (lay share)
        if ctx.get("wom_ratio") is not None:
            r = float(ctx["wom_ratio"])
            dirn = "L2B" if r >= 0.55 else "B2L" if r <= 0.45 else None
            conf = max(0.0, min(1.0, abs(r - 0.5) * 2.0))
            return (dirn, conf, f"wom_ratio={r:.2f}")
        # 2) sums → ratio
        b = float(ctx.get("wom_back_sum", 0.0) or 0.0)
        l = float(ctx.get("wom_lay_sum",  0.0) or 0.0)
        tot = b + l
        if tot <= 0.0:
            return (None, 0.0, "wom:none")
        r = l / tot
        dirn = "L2B" if r >= 0.55 else "B2L" if r <= 0.45 else None
        conf = max(0.0, min(1.0, abs(r - 0.5) * 2.0))
        return (dirn, conf, f"wom={b:.0f}/{l:.0f} ({r:.2f})")
    except Exception:
        return (None, 0.0, "wom:error")

def _ticks_for_conf(conf: float) -> int:
    # your rule: <0.70→1, 0.70..0.90→2, ≥0.90→3
    if conf >= 0.90: return 3
    if conf >= 0.70: return 2
    return 1


# -----------------------------------------------------------------------------
# Final stake (dynamic sizing consistent with your daily_config)
# ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: ^def size_for_letter\(ctx: dict, letter: str, \*, confidence: float, direction: str\) -> float:
# ⛏️ ACTION: inject passive-band scaling before return
# ============================================================
def size_for_letter(ctx: dict, letter: str, *, confidence: float, direction: str) -> float:
    try:
        from engines.daily_config import (
            BANK_PCT_PER_ENTRY, MAX_BANK_PCT_PER_TRADE, MIN_STAKE,
            L1_FRACTION_CAP, LETTER_MULT, HARD_CAP_PRE, HARD_CAP_IP
        )
    except Exception:
        BANK_PCT_PER_ENTRY = 0.004
        MAX_BANK_PCT_PER_TRADE = 0.02
        MIN_STAKE = 2.0
        L1_FRACTION_CAP = 0.35
        LETTER_MULT = {letter:1.0}
        HARD_CAP_PRE, HARD_CAP_IP = 5.0, 3.0

    # bank & scaling
    try: bank = float(ctx.get("bank", 0.0) or 0.0)
    except Exception: bank = 0.0
    base_cash = max(0.0, bank) * float(BANK_PCT_PER_ENTRY)
    mult = float(LETTER_MULT.get(letter, 1.0))
    conf_scale = 1.0 + max(0.0, confidence - 0.50) * 2.0
    stake = base_cash * mult * conf_scale
    budget_cap = max(1.0, bank * float(MAX_BANK_PCT_PER_TRADE))
    stake = min(stake, budget_cap)

    # liquidity clamp
    try:
        l1 = float(ctx.get("l1_available_lay" if str(direction).upper().startswith("LAY") else "l1_available_back",
                           ctx.get("l1_available", 0.0)) or 0.0)
    except Exception:
        l1 = 0.0
    liq_cap = max(MIN_STAKE, float(L1_FRACTION_CAP) * l1)
    stake = min(stake, liq_cap)

    # hard cap by phase
    phase = str(ctx.get("phase","PRE")).upper()
    hard_cap = HARD_CAP_IP if phase == "IN_PLAY" else HARD_CAP_PRE
    stake = min(stake, hard_cap)

# ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: ^\s*# 📆 PATCHED: reduced stake in PASSIVE band \(8\.0 < odds <= 12\.0\)\n\s*try:\n\s*odds = .*?\n\s*except Exception:\n\s*.*?\n\s*if phase == "PRE" and 8\.0 < odds <= 12\.0:\n\s*.*?stake \*= .*?\n\n\s*return round\(max\(MIN_STAKE, stake\), 2\)
# ⛏️ ACTION: replace the passive-scaling block so it uses daily_config.PASSIVE_MULT with a safe default
# ============================================================
    # 📆 PASSIVE band scaling (8.0 < odds <= 12.0) — configurable via daily_config.PASSIVE_MULT
    try:
        odds = float(ctx.get("odds") or ctx.get("px") or ctx.get("ltp") or 0.0)
    except Exception:
        odds = 0.0
    try:
        from engines import daily_config as dc
        passive_mult = float(getattr(dc, "PASSIVE_MULT", 0.5))
    except Exception:
        passive_mult = 0.5
    if phase == "PRE" and 8.0 < odds <= 12.0:
        stake *= passive_mult

    return round(max(MIN_STAKE, stake), 2)



# -----------------------------------------------------------------------------
# Public planner (used by orchestrator): returns a plan with plan_why
def propose_trade(context: Dict[str, Any]) -> Dict[str, Any]:
    # === FIX: create working ctx immediately ===
    ctx = dict(context or {})

    if ENABLE_LEGACY_ONLY:
        ctx = _enrich_ctx(ctx)

    _ensure_row_factory_monkeypatch()

    # --- BUS DIRECTION OVERRIDE ---
    dir_override = ctx.get("direction_override")
    if dir_override:
        ctx["direction"] = dir_override

    # --- MSC DIRECTION-FIRST INJECTION ---
    if ctx.get("direction") is None:
        try:
            from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
            _msc = ExploratoryEngine()
            d = _msc.direction_only(ctx)
            if d:
                ctx["direction"] = d
        except Exception:
            pass

    # === FIX: enrich ctx, not context ===
    ctx = _enrich_ctx(ctx)

    # --- Scope filter -----------------------------------------------------
    try:
        mid = str(ctx.get("marketId") or "")
        # skip if market not in Mastery scope
        from engines.mastery.mastery_policy import _SCOPE_STATE
        if mid and _SCOPE_STATE.get("mids") and mid not in _SCOPE_STATE["mids"]:
            return {"enter": False, "letter": ctx.get("letter", "S"), "why": "scope:out"}
    except Exception:
        pass

    # --- Attach fav/field tags, movement, blueprint to context (safe, optional) ---
    try:
        mid = str(ctx.get("marketId") or "")
        sid = str(ctx.get("selectionId") or "")
        if mid and sid:
            favmap = _fav_tags_for_market(mid)            # {sid:{rank,tag,ltp}}
            movmap = _movement_for_market(mid)            # {sid:{was_rank,now_rank,movement,...}}
            bp_score, bp_key = _get_blueprint_conf_cached(mid, sid)

            fm = favmap.get(sid, {})
            mv = movmap.get(sid, {})

            if fm:
                ctx["fav_rank"] = fm.get("rank")
                ctx["fav_tag"]  = fm.get("tag")
                ctx.setdefault("px", fm.get("ltp", ctx.get("px")))
            if mv:
                ctx["rank_was"] = mv.get("was_rank")
                ctx["rank_now"] = mv.get("now_rank")
                ctx["movement"] = mv.get("movement")
            if bp_score is not None:
                ctx["blueprint_conf"] = float(bp_score)
            if bp_key:
                ctx["blueprint_key"]  = str(bp_key)
    except Exception:
        pass



    # ---- letter & family/defaults -------------------------------------
    L = str(ctx.get("letter") or ctx.get("family_code") or "S").upper()
    ctx["letter"] = L

    # ---- gates (time/phase/odds band) ---------------------------------
    ok, gwhy = gate(ctx, L)
    if not ok:
        # best-effort ledger write (NO-TRADE)
        try:
            from engines.mastery.plan_ledger import record_plan
            base = {
                "day": ctx.get("day") or __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d"),
                "run_id": ctx.get("run_id"),
                "marketId": ctx.get("marketId"),
                "selectionId": ctx.get("selectionId"),
                "family": str(ctx.get("strategy_name") or "MASTERY"),
                "letter": L,
                "direction": "",
                "target_ticks": 0,
                "px": float(ctx.get("px") or ctx.get("odds") or 0.0),
                "size": 0.0,
                "confidence": 0.0,
                "plan_why": f"gate:{gwhy}",
                "enter": False,
            }
            record_plan(base)
        except Exception:
            pass
        return {"enter": False, "letter": L, "why": f"gate:{gwhy}"}

    # ---- main direction inference -------------------------------------
    dir_rule, ticks_rule, note = infer_direction_and_ticks(ctx)

    # ---- last-resort fallback for early-day/sparse data ----------------
    if dir_rule is None:
        try:
            px = float(ctx.get("odds") or ctx.get("px") or ctx.get("ltp") or 0.0)
        except Exception:
            px = 0.0
        # conservative px-band fallback; keeps Mastery letter, not 'A'
        if 1.50 <= px <= 12.0:
            dir_rule = "LAY->BACK" if px >= 4.0 else "BACK->LAY"
            ticks_rule = 1
            note = (note + " | px_fallback") if note else "px_fallback"
        else:
            # record no-trade (quiet + price out of band)
            try:
                from engines.mastery.plan_ledger import record_plan
                base = {
                    "day": ctx.get("day") or __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d"),
                    "run_id": ctx.get("run_id"),
                    "marketId": ctx.get("marketId"),
                    "selectionId": ctx.get("selectionId"),
                    "family": str(ctx.get("strategy_name") or "MASTERY"),
                    "letter": L,
                    "direction": "",
                    "target_ticks": 0,
                    "px": px,
                    "size": 0.0,
                    "confidence": 0.0,
                    "plan_why": "flat_no_signal",
                    "enter": False,
                }
                record_plan(base)
            except Exception:
                pass
            return {"enter": False, "letter": L, "why": "flat_no_signal"}

    # ---- optional bias: be stricter on B2L unless fav+strong -----------
    # allow if fav OR strong (safer fills, fewer misses on fav that are edging)
    if dir_rule == "BACK->LAY" and L not in ("A","P"):
        fav_rank = int(ctx.get("fav_rank") or 99)
        strong = (abs(float(ctx.get("slope_ppm") or 0.0)) >= 0.03) or (abs(int(ctx.get("recent_net_ticks") or 0)) >= 3)
        if not (fav_rank == 1 or strong):
            return {"enter": False, "letter": L, "why": f"b2l_denied_bias fav_rank={fav_rank} strong_steam={strong}"}


    # ---- confidence & tick floor --------------------------------------
    conf, t_floor, tag2 = compute_confidence_and_ticks(ctx)
    ticks = max(int(ticks_rule or 1), int(t_floor))

    # --- Tiny, bounded confidence nudges from movement + blueprint (never block) ---
    try:
        mv = str(ctx.get("movement") or "steady").lower()
        bp = ctx.get("blueprint_conf")
        if isinstance(bp, (int, float)):
            # gentle nudge only if blueprint_conf ≥ 0.60
            if float(bp) >= 0.60:
                conf = min(1.0, conf + 0.02)
        # movement coherence with chosen direction (optional micro-nudge)
        if dir_rule:
            if mv == "steaming" and dir_rule == "BACK->LAY":
                conf = min(1.0, conf + 0.02)
            elif mv == "drifting" and dir_rule == "LAY->BACK":
                conf = min(1.0, conf + 0.02)
    except Exception:
        pass


    # ---- WOM nudge (never blocks) -------------------------------------
    wom_tag = ""
    try:
        wom = ctx.get("wom_ratio")
        LO, HI = 0.42, 0.58
        if isinstance(wom, (int, float)):
            if wom <= LO and dir_rule == "BACK->LAY":
                conf = min(1.0, conf + 0.03); wom_tag = f"wom={wom:.2f} steam+"
            elif wom >= HI and dir_rule == "LAY->BACK":
                conf = min(1.0, conf + 0.03); wom_tag = f"wom={wom:.2f} drift+"
            elif wom <= LO and dir_rule == "LAY->BACK":
                conf = max(0.0, conf - 0.02); wom_tag = f"wom={wom:.2f} steam-"
            elif wom >= HI and dir_rule == "BACK->LAY":
                conf = max(0.0, conf - 0.02); wom_tag = f"wom={wom:.2f} drift-"
            else:
                wom_tag = f"wom={wom:.2f}"
    except Exception:
        pass

    # === PATCH E START (attach EPIC in live planner) ===
    # ---- sizing --------------------------------------------------------
    base_from_dc = base_stake_for_letter(L, bank=_bank())
    sized = size_for_letter(ctx, L, confidence=conf, direction=dir_rule)

    plan = {
        "enter": True,
        "letter": L,
        "direction": dir_rule,
        "target_ticks": int(ticks),
        "hedge_ticks":  int(ticks),
        "size": float(sized),
        "base_stake": float(base_from_dc),
        "px": float(ctx.get("px") or ctx.get("odds") or 0.0),
        "plan_why": (f"{L}-plan {note}/{tag2}" + (f" | {wom_tag}" if wom_tag else "")),
        # NEW: surface metadata
        "fav_rank": int(ctx.get("fav_rank") or 0),
        "fav_tag":  str(ctx.get("fav_tag")  or ""),
        "movement": str(ctx.get("movement") or ""),
        "blueprint_key":  str(ctx.get("blueprint_key") or ""),
        "blueprint_conf": float(ctx.get("blueprint_conf") or 0.0),
    }


    # EPIC metadata (from in-memory scope; fail-open if unavailable)
    try:
        mid = str(ctx.get("marketId") or "")
        sid = str(ctx.get("selectionId") or "")
        epi = epic_info(mid)
        if epi:
            plan["epic_id"] = mid
            plan["epic_stories"] = int(epi.get("stories") or 0)
            # derive rank if we have sids; otherwise skip
            if sid and epi.get("sids"):
                # quick rank: use rank map helper (DB probe) or position in set order fallback
                rank_map = _rank_map_for_market(mid, list(epi["sids"]))
                plan["epic_rank"] = int(rank_map.get(sid, 0))
    except Exception:
        pass
    # === PATCH E END ===
  

    # ---- ledger write + attach plan_id (best-effort) -------------------
    try:
        from engines.mastery.plan_ledger import record_plan
        base = {
            "day": ctx.get("day") or __import__("datetime").datetime.utcnow().strftime("%Y-%m-%d"),
            "run_id": ctx.get("run_id"),
            "marketId": ctx.get("marketId"),
            "selectionId": ctx.get("selectionId"),
            "family": str(ctx.get("strategy_name") or "MASTERY"),
            "letter": plan.get("letter") or L,
            "direction": plan.get("direction") or "",
            "target_ticks": int(plan.get("target_ticks") or 1),
            "px": float(plan.get("px") or ctx.get("odds") or 0.0),
            "size": float(plan.get("size") or 0.0),
            "confidence": float(conf or 0.0),
            "plan_why": plan.get("plan_why") or "",
            "enter": True,
            # NEW metadata columns we already modeled in schema
            "blueprint_match": plan.get("blueprint_key") or "",
            "window_tag": plan.get("movement") or "",
            "why": plan.get("plan_why") or "",
        }

        res = record_plan(base)
        if isinstance(res, dict) and res.get("plan_id"):
            plan["plan_id"] = res["plan_id"]
    except Exception:
        pass
    # --- NEW: ensure all non-entry plans are persisted for visibility ---
    try:
        if not plan.get("enter"):
            from engines.mastery.plan_ledger import record_plan
            record_plan({
                "enter": False,
                "marketId": ctx.get("marketId"),
                "selectionId": ctx.get("selectionId"),
                "family": str(ctx.get("strategy_name") or "MASTERY"),
                "letter": plan.get("letter") or ctx.get("letter") or "S",
                "direction": "",
                "target_ticks": 0,
                "px": float(ctx.get("odds") or ctx.get("px") or 0.0),
                "size": 0.0,
                "confidence": 0.0,
                "plan_why": plan.get("why") or plan.get("plan_why") or "no_trade",
            })
    except Exception as e:
        print(f"[propose_trade] no-trade record warn: {e}")


    return plan

# ─────────────────────────────────────────────────────────────────────────────
# Global plan normalizer (used by DecideOnce + Mastery)
# ─────────────────────────────────────────────────────────────────────────────
# === PATCH START ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def _ensure_plan(
# 📆 PATCHED: 2025-12-12 — Enforce non-None Legacy plan contract
# 🧠 PURPOSE:
#   • Legacy must NEVER return None to BUS
#   • Surface no-signal as BLOCKED with reason
#   • Preserve all existing legacy behaviour
# ============================================================================

def _ensure_plan(fam: str, ctx: dict, raw):
    """
    Canonical Legacy plan normaliser.

    Contract:
      • NEVER return None
      • If raw is None → return explicit NO-SIGNAL plan
      • Do NOT force trades
      • Preserve existing plan content unchanged
    """

    # ------------------------------------------------------------
    # 1) HARD NORMALISATION — None is not allowed past this point
    # ------------------------------------------------------------
    if raw is None:
        plan = {
            "enter": False,
            "engine": "LEGACY",
            "strategy": fam,
            "letter": str(ctx.get("letter") or _FAM_LETTER.get(fam, fam[:1])),
            "reason": "no_signal",
            "why": "no_signal",
            "re_eval": True,   # allow future learning / widening
        }
        # --- Training / telemetry hook (non-blocking) ---
        try:
            from engines.mastery.event_sink import emit_event
            emit_event("legacy.no_signal", {
                "engine": "LEGACY",
                "strategy": fam,
                "marketId": ctx.get("marketId"),
                "selectionId": ctx.get("selectionId"),
                "letter": plan["letter"],
                "ts": ctx.get("ts"),
            })
        except Exception:
            pass

        return plan

    # ------------------------------------------------------------
    # 2) EXISTING NORMAL PATH (unchanged)
    # ------------------------------------------------------------
    if not isinstance(raw, dict):
        # Defensive: unexpected return type
        return {
            "enter": False,
            "engine": "LEGACY",
            "strategy": fam,
            "letter": str(ctx.get("letter") or _FAM_LETTER.get(fam, fam[:1])),
            "reason": "invalid_plan_type",
            "why": "invalid_plan_type",
            "re_eval": False,
        }

    # Ensure required fields exist (fail-open)
    raw.setdefault("engine", "LEGACY")
    raw.setdefault("strategy", fam)
    raw.setdefault("enter", False)

    return raw

# === PATCH END ==============================================================


# === PATCH: future stubs for microscalp/MLM/stoploss (E) ===
# 📍 TARGET: bottom of mastery_policy.py (before plan_for_strategy)
# ⛏️ ACTION: add stub functions

def _microscalp_plan(ctx: dict) -> dict | None:
    """
    FUTURE FEATURE:
    Microscalping engine should produce ultra-low-latency scalp signatures.
    Legacy-only mode disables this – return None.
    """
    return None

def _mlm_live_plan(ctx: dict) -> dict | None:
    """
    FUTURE FEATURE:
    Live MLM (liability manager) would create protection hedges.
    Disabled in legacy mode – return None.
    """
    return None

def _stoploss_exit_plan(ctx: dict) -> dict | None:
    """
    FUTURE FEATURE:
    Stop-loss nodes (Overwatcher → Mastery) historically generated exits.
    Legacy-only mode disables this – return None.
    """
    return None
# === PATCH END ===


# === PATCH START ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py:plan_for_strategy
# 🔎 SEARCH: def plan_for_strategy(fam: str, ctx: dict) -> dict:
# 🎯 ACTION: Disable ALWAYS_ON (A) strategy completely
# 📆 PATCHED: 2025-12-05Z
# ============================================================================

def plan_for_strategy(fam: str, ctx: dict) -> dict:
    # --- HARD DISABLE ALWAYS_ON (A) STRATEGY ------------------------------
    if str(fam).upper() == "ALWAYS_ON":
        return {
            "enter": False,
            "letter": "A",
            "why": "disabled_A_msc_replacement",
            "px": float(ctx.get("odds") or ctx.get("px") or 0.0),
            "target_ticks": 0,
            "size": 0.0,
        }
    # === PATCH END ========================================================



    mid = str(ctx.get("marketId") or "")
    sid = str(ctx.get("selectionId") or "")
    letter = _FAM_LETTER.get(fam, fam[:1])
    entry = PLAN_BOARD.get((mid, sid, letter))
    if entry and entry["valid_from"] <= time.time() <= entry["valid_until"]:
        return dict(entry["plan"])
    # fallback — compute fresh plan
    fn = globals().get(f"plan_for_{fam.lower()}")
    if callable(fn):
        raw = fn(ctx)
    else:
        ctx = dict(ctx or {})
        ctx["letter"] = letter              # ← inject correct letter
        raw = propose_trade(ctx)
    return _ensure_plan(fam, ctx, raw)



# -----------------------------------------------------------------------------
# Optional strategy-name entry point (kept compatible with your router)
def _family_code(name: str) -> str:
    s = (name or "").upper()
    if s.startswith("S4") or "CROSSOVER" in s: return "X"
    if s.startswith("S5") or "BREAKOUT"  in s: return "R"
    if s.startswith("S6") or "STEAM_FADE" in s: return "F"
    if "LADDER" in s:                        return "L"
    if s.startswith("BTL"):                  return "B"
    if "OG_BIAS" in s:                       return "Z"
    if "OG_STRATEGY" in s:                   return "S"
    if "ALWAYS_ON" in s:                     return "A"
    if "BLUEPRINTS" in s:                    return "P"
    if s.startswith("IP1") or "SHOCK_DRIFT" in s: return "I"
    if s.startswith("IP2") or "TIRED_LEADER" in s: return "T"
    if s.startswith("IP3") or "CLOSE_FINISH" in s: return "C"
    if s.startswith("IP4") or "FENCE_ERROR"  in s: return "E"
    if s.startswith("IP5") or "COLLAPSE_FADE" in s: return "K"
    return "S"


from typing import Dict, Any

# ---- Scope awareness ---------------------------------------------------------
_CURRENT_SCOPE: dict | None = None
_SCOPE_MIDS: set[str] = set()

def _in_scope(mid: str) -> bool:
    # If we have no scope yet (early boot), fail-open.
    return not _SCOPE_MIDS or str(mid) in _SCOPE_MIDS

# ---- existing code (…keep your functions…) ----

# === PATCH START ===
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: top of file / imports
# ⛏️ ACTION: add PlanBoard and an update_scope entrypoint


import time, threading
from collections import defaultdict

_PLAN_HORIZON_TICKS = 30        # ~30 ticks ahead
_COOL_OFF_MINUTES   = 3.0

class _PlanBoard:
    """
    Keeps a rolling N-tick plan per (mid,sid,letter).
    Inputs: scope snapshot + latest contexts from DecideOnce.
    Output: per-letter plan dicts (or explicit no-trade).
    """
    def __init__(self):
        self._lock = threading.RLock()
        self._scope = {"markets": []}
        self._plans: dict[tuple[str,str,str], dict] = {}  # (mid,sid,letter)->plan
        self._last_tick = 0

    def update_scope(self, scope_snap: dict):
        with self._lock:
            self._scope = scope_snap or {"markets": []}
            # prune plans for markets no longer in scope
            active = {(m["marketId"], sid)
                      for m in self._scope.get("markets", [])
                      for sid in (m.get("active_sids") or [])}
            stale = [key for key in self._plans.keys()
                     if (key[0], key[1]) not in active]
            for k in stale: self._plans.pop(k, None)

    def _letters_for(self, mto_min: float | None) -> list[str]:
        # mirror your lanes window policy (pre-day → in-play)
        if mto_min is None or mto_min > 60:   return ["A","P"]
        if 60 >= mto_min > 30:                return ["A","P","X","R","F"]
        if 30 >= mto_min > 20:                return ["A","P","X","R","F","L"]
        if 20 >= mto_min > 5:                 return ["A","P","X","R","F","L","B","G"]
        if 5 >= mto_min >= 3:                 return ["A","P","X","R","F","L","G"]
        if 3 > mto_min > 0:                   return ["P"]   # COOL_OFF: Blueprint hedges only
        return ["P","I","T","C","E","K"]                   # in-play letters

    def _default_direction(self, odds: float) -> str:
        return "LAY->BACK" if odds >= 4.0 else "BACK->LAY"

    def _cool_off_block(self, mto_min: float | None) -> bool:
        try:
            return (mto_min is not None) and (mto_min <= _COOL_OFF_MINUTES) and (mto_min > 0)
        except Exception:
            return False

    def propose(self, fam: str, ctx: dict) -> dict:
        """
        Return a plan dict (enter or no-trade) for fam given the current ctx.
        Never raises; never returns None.
        """
        mid = str(ctx.get("marketId","")); sid = str(ctx.get("selectionId",""))
        odds = float(ctx.get("odds") or 0.0)
        mto  = ctx.get("minutes_to_off", None)
        try: mto = float(mto) if mto is not None else None
        except Exception: mto = None

        # out-of-scope? explicit no-trade
        in_scope = False
        for m in self._scope.get("markets", []):
            if m.get("marketId") == mid and sid in (m.get("active_sids") or []):
                in_scope = True; break
        if not in_scope:
            return {"enter": False, "letter": fam[:1], "why": "scope:no", "px": odds}

        # cool-off: permit only hedge/blueprint; all others no-trade
        if self._cool_off_block(mto) and fam not in ("BLUEPRINTS", "ALWAYS_ON"):
            return {"enter": False, "letter": fam[:1], "why": "cool_off", "px": odds}

        # allowed letters in window
        letters = self._letters_for(mto)
        if (fam[:1] not in letters) and (fam not in ("BLUEPRINTS","ALWAYS_ON")):
            return {"enter": False, "letter": fam[:1], "why": "window", "px": odds}

        # Base 1-tick liquidity seeking plan
        plan = {
            "enter": True,
            "letter": (fam[:1] or "A").upper(),
            "direction": self._default_direction(odds),
            "target_ticks": 1,
            "hedge_ticks": 1,
            "size": float(ctx.get("size_cap", 2.0) or 2.0),
            "px": float(odds),
            "plan_why": f"{plan_letter(fam)}-plan horizon:{_PLAN_HORIZON_TICKS} | px_live",
        }
        return plan

# letter helper for consistent plan_why
def plan_letter(fam: str) -> str:
    return {
        "BLUEPRINTS":"P","OG_STRATEGY":"Z","LADDER_STRATEGY":"L","ALWAYS_ON":"A",
        "BTL_SCOUT":"B","BTL_AGGR":"G","S4_CROSSOVER":"X","S5_BREAKOUT":"R","S6_STEAM_FADE":"F",
        "IP1_SHOCK_DRIFT":"I","IP2_TIRED_LEADER":"T","IP3_CLOSE_FINISH":"C","IP4_FENCE_ERROR":"E","IP5_COLLAPSE_FADE":"K",
    }.get(fam, (fam[:1] or "A").upper())

# --- Simple in-memory plan board (persist optional via plan_ledger) ----------
PLAN_BOARD: dict[tuple[str,str,str], dict] = {}  # key=(mid,sid,letter)

_PLANBOARD = PLAN_BOARD

def update_scope(scope_snapshot: dict) -> None:
    """Legacy shim — delegate to update_plan_board on PLAN_BOARD."""
    try:
        update_plan_board(scope_snapshot or {})
    except Exception:
        pass

# ============================================================
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: def _letters_by_window(mto_min: float | None)
# 📆 PATCHED: 2025-12-12
# 🧠 WHY:
#   Time-window family suppression breaks engine firing.
#   Families must always be allowed to evaluate.
# ============================================================

def _letters_by_window(mto_min: float | None) -> list[str]:
    return [
 
        "BLUEPRINTS",
        "OG_STRATEGY",
 
        "S4_CROSSOVER",
        "S5_BREAKOUT",
        "S6_STEAM_FADE",
        "BTL_SCOUT",
        "BTL_AGGR",

    ]


# ─────────────────────────────────────────────────────────────
# 📍 TARGET: engines/mastery/mastery_policy.py
# 🔎 SEARCH: ^def update_plan_board\(scope_snapshot
# 📆 PATCHED: 2025-10-07T10:35Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NEW: cache blueprint lookups during each reseed to speed up plan generation
_bp_cache: dict[str, tuple[float|None, str|None]] = {}

def _get_blueprint_conf_cached(mid: str, sid: str) -> tuple[float|None, str|None]:
    key = f"{mid}:{sid}"
    if key not in _bp_cache:
        _bp_cache[key] = _blueprint_conf_for_sid(mid, sid)
    return _bp_cache[key]
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


# 📆 PATCHED: 2025-10-07T09:45Z
# Rolling 30-tick Plan Board — silent except one confirmation line

def update_plan_board(scope_snapshot: dict, *, now_ts=None, lookahead_ticks: int = 30) -> None:
    """
    Maintains a continuous 30-tick planning horizon.
    For each market/runner/strategy → generate or refresh plan.
    Records into plan_ledger silently (one print per full horizon).
    """
    families = [
        "BLUEPRINTS",         # P
        "OG_STRATEGY",        # S
        "LADDER_STRATEGY",    # L
        "S4_CROSSOVER",       # X
        "S5_BREAKOUT",        # R
        "S6_STEAM_FADE",      # F
        "ALWAYS_ON",          # A
        "BTL_SCOUT",          # B
        "BTL_AGGR",           # G
        "IP1_SHOCK_DRIFT",    # I
        "IP2_TIRED_LEADER",   # T
        "IP3_CLOSE_FINISH",   # C
        "IP4_FENCE_ERROR",    # E
        "IP5_COLLAPSE_FADE",  # K
        "MLM",                # M — cool-off / liability manager
    ]

    global PLAN_BOARD
    now_ts = now_ts or time.time()

    for m in (scope_snapshot.get("markets") or []):
        mid = str(m.get("marketId") or "")
        if not mid:
            continue

        sids = list(m.get("active_sids") or [])
        mto  = m.get("minutes_to_off")
        epic_stories = len(sids)
        rank_map = _rank_map_for_market(mid, sids) if epic_stories else {}

        for sid in sids:
            ctx_base = {
                "marketId": mid,
                "selectionId": str(sid),
                "minutes_to_off": mto,
            }

            for fam in families:


                try:
                    plan = plan_for_strategy(fam, dict(ctx_base)) or {}
                except Exception:
                    plan = {}

                plan = _ensure_plan(fam, ctx_base, plan)

                # attach EPIC metadata
                plan["epic_id"] = mid
                plan["epic_stories"] = epic_stories
                plan["epic_rank"] = int(rank_map.get(str(sid), 0)) if epic_stories else 0

                letter = plan.get("letter") or fam[:1]
                key = (mid, str(sid), str(letter))
                PLAN_BOARD[key] = {
                    "valid_from": now_ts,
                    "valid_until": now_ts + lookahead_ticks,
                    "plan": plan,
                }

                # ledger persistence (silent)
                try:
                    from engines.mastery.plan_ledger import record_plan
                    record_plan({
                        "marketId": mid,
                        "selectionId": sid,
                        "letter": letter,
                        "enter": bool(plan.get("enter")),
                        "why": plan.get("plan_why", plan.get("why", "no_reason")),
                        "run_id": None,
                        "epic_id": mid,
                        "epic_stories": epic_stories,
                        "epic_rank": plan.get("epic_rank", 0),
                    })
                except Exception:
                    pass

    # ✅ Confirmation (only once per full horizon)
    print("[PLAN] 30-tick horizon refreshed")

# Breakout/posterior tuning persistence (unchanged semantics)
@dataclass
class TradePlan:
    enter: bool
    direction: str | None
    target_ticks: int | None
    size: float | None
    stop_ticks: int | None
    timeout_sec: int | None
    pyramid_add_at: int | None
    why: str = ""

def record_outcome(trade_id: str, context: Dict[str, Any], outcome: Dict[str, Any]) -> None:
    used_breakout = bool(context.get("used_breakout"))
    src = str(context.get("source") or _current_source_upper()).upper()

    # Persist event
    try:
        payload = {"trade_id": trade_id, "ctx": context, "outcome": outcome}
        with connect_db(ro=False) as conn:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(mastery_events)")]
            if "source" not in cols:
                try: conn.execute("ALTER TABLE mastery_events ADD COLUMN source TEXT")
                except Exception: pass
            if "source" in [r[1] for r in conn.execute("PRAGMA table_info(mastery_events)")]:
                conn.execute(
                    "INSERT INTO mastery_events(event_type, details_json, delta_progress, source) VALUES (?,?,0,?)",
                    ("trade_outcome", json.dumps(payload), src),
                )
            else:
                conn.execute(
                    "INSERT INTO mastery_events(event_type, details_json, delta_progress) VALUES (?,?,0)",
                    ("trade_outcome", json.dumps(payload)),
                )
            conn.commit()
    except Exception:
        pass

    # Posteriors + breakout knobs
    try:
        from engines.mastery.posteriors import update_after_outcome
        bin_key = get_bin_key(
            context.get("distance_band", "1m-1m2"),
            context.get("code", "FLAT"),
            context.get("tto_window", "30-10"),
            context.get("class_band", "mid"),
            context.get("fav_rank_bin", "fav"),
        )
        tgt = int(outcome.get("target_ticks", context.get("target_ticks", 1)))
        success = bool(outcome.get("success", False))
        fill_observed = outcome.get("fill_observed")
        if fill_observed is None:
            fill_observed = True
        mae = outcome.get("mae_ticks")
        if mae is None and not success:
            mae = abs(int(outcome.get("realized_ticks", 0)))

        # (Optional) tune breakout params if used_breakout
        if used_breakout:
            def _ensure_mastery_params(db: sqlite3.Connection):
                db.execute("""
                  CREATE TABLE IF NOT EXISTS mastery_params(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT, key TEXT, value REAL, source TEXT, updated_at TEXT
                  )
                """); db.execute("CREATE INDEX IF NOT EXISTS idx_mparams_scope_key ON mastery_params(scope, key)"); db.commit()
            def _mp_get(db: sqlite3.Connection, scope: str, key: str, default: float, *, source: str) -> float:
                _ensure_mastery_params(db)
                row = db.execute(
                    "SELECT value FROM mastery_params WHERE scope=? AND key=? AND (source=? OR source IS NULL) "
                    "ORDER BY id DESC LIMIT 1", (scope, key, source)
                ).fetchone()
                return float(row[0]) if row and row[0] is not None else float(default)
            def _mp_set(db: sqlite3.Connection, scope: str, key: str, value: float, *, source: str):
                _ensure_mastery_params(db)
                db.execute(
                    "INSERT INTO mastery_params(scope, key, value, source, updated_at) VALUES (?,?,?,?,datetime('now'))",
                    (scope, key, float(value), source)
                ); db.commit()

            with connect_db(ro=False) as dbp:
                _ensure_mastery_params(dbp)
                brk_ticks = _mp_get(dbp, bin_key, "brk_ticks",      1.0, source=src)
                brk_flow  = _mp_get(dbp, bin_key, "brk_flow_min",  0.08, source=src)
                brk_vol   = _mp_get(dbp, bin_key, "brk_vol1m_min", 3.0,  source=src)

                a = _mp_get(dbp, bin_key, "brk_beta_a", 1.0, source=src)
                b = _mp_get(dbp, bin_key, "brk_beta_b", 1.0, source=src)
                if success: a += 1.0
                else:       b += 1.0
                _mp_set(dbp, bin_key, "brk_beta_a", a, source=src)
                _mp_set(dbp, bin_key, "brk_beta_b", b, source=src)

                step = 0.15
                if success:
                    ticks_t = max(0.8,  brk_ticks - 0.2)
                    flow_t  = max(0.02, brk_flow  - 0.01)
                    vol_t   = max(1.0,  brk_vol   - 0.5)
                else:
                    ticks_t = min(3.0,  brk_ticks + 0.3)
                    flow_t  = min(0.25, brk_flow  + 0.02)
                    vol_t   = min(8.0,  brk_vol   + 1.0)
                def clamp(v, lo, hi): return max(lo, min(hi, v))
                _mp_set(dbp, bin_key, "brk_ticks",     clamp((1-step)*brk_ticks + step*ticks_t, 0.5, 4.0),  source=src)
                _mp_set(dbp, bin_key, "brk_flow_min",  clamp((1-step)*brk_flow  + step*flow_t,  0.01, 0.40), source=src)
                _mp_set(dbp, bin_key, "brk_vol1m_min", clamp((1-step)*brk_vol   + step*vol_t,   1.0,  12.0), source=src)
        # Posterior update
        with connect_db(ro=False) as _c:
            update_after_outcome(_c, bin_key, target_ticks=tgt, success=success,
                                 fill_observed=fill_observed, mae_ticks=mae, source=src)
    except Exception:
        pass


# -----------------------------------------------------------------------------
# Minimal analyzer hook (optional).
def _oc_index_from_mto(mto: float | None) -> int:
    if mto is None: return 0
    try: m = float(mto)
    except Exception: return 0
    if m >= 80: return 1
    if m >= 60: return 2
    if m >= 40: return 3
    if m >= 20: return 4
    if m >= 10: return 5
    if m >= 5:  return 6
    if m >= 0:  return 7
    return 8 + max(0, int(abs(m)))

def _direction_from_analyzer(ctx: Dict[str, Any]) -> tuple[Optional[str], float, str]:
    try:
        from time import time as _now
        from engines.odds.trend_analyzer import direction_for as _direction_for
        from engines.odds.trend_analyzer.types import MarketTick as _Tick
    except Exception:
        return None, 0.0, "analyzer_unavailable"

    mid = str(ctx.get("marketId") or "")
    sid = ctx.get("selectionId")
    try: sid_int = int(sid) if sid is not None else None
    except Exception: sid_int = None
    if not mid or sid_int is None:
        return None, 0.0, "missing_ids"

    mto = ctx.get("minutes_to_off", ctx.get("tto_minutes"))
    oc_idx = _oc_index_from_mto(mto if isinstance(mto, (int,float)) else None)
    best_back = None
    for k in ("current_odds","odds","px","tape_px","ltp","price","last_price"):
        v = ctx.get(k)
        try:
            if v is not None:
                best_back = float(v); break
        except Exception:
            pass
    anchor = ctx.get("anchor_odd")
    try: anchor = float(anchor) if anchor is not None else None
    except Exception: anchor = None

    from time import time as now
    tick = _Tick(
        ts=float(now()),
        market_id=mid,
        selection_id=sid_int,
        oc_index=int(oc_idx),
        best_back_odds=best_back,
        anchor_odds=anchor,
        liquidity_flag=str(ctx.get("liquidity_flag","NORMAL")) or "NORMAL",
    )
    try:
        dec = _direction_for(tick, view_now=None)
    except Exception:
        return None, 0.0, "analyzer_error"

    side = str(dec.direction or "ABSTAIN").upper()
    conf = float(dec.confidence or 0.0)
    if side == "BACK": return "BACK->LAY", conf, f"analyzer BACK ({conf:.2f})"
    if side == "LAY":  return "LAY->BACK", conf, f"analyzer LAY ({conf:.2f})"
    return None, conf, f"analyzer abstain ({conf:.2f})"
