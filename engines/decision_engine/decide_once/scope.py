# engines/decision_engine/decide_once/scope.py
from __future__ import annotations
from typing import Dict, List, Tuple, Iterable
import time
from engines.fallbackscope import _next5_from_bets_with_active
from .helpers import (
    utc_now,
    build_scope as _project_build_scope,  # may not exist in some trees
    is_today_in_scope,
    status_once,
    open_bets_db,
    open_auto_db,
)

# Public globals (kept)
SCOPE_WINDOW_MAX: int = 5
SCOPE_WINDOW: List[str] = []
SCOPE_OVERRIDES: Dict[str, float] = {}
SCOPE_CURSOR: int = 0

# ---------- primary/variant classification -----------------------------------
_VARIANT_KEYS = (
    "EACH WAY", "TO BE PLAC", " TBP", " PLACE ", "PLACE ONLY",
    "WITHOUT", "BETTING WITHOUT", "MATCH BET", "INSURANCE",
    "FORECAST", "TRICAST", "EXACTA", "QUINELLA", "ANTEPOST"
)

def _is_primary_market(name: str | None) -> bool:
    n = (name or "").upper()
    if not n:
        return True  # fail-open: treat unknown as primary
    return not any(k in n for k in _VARIANT_KEYS)

# ---------- helpers to fetch and normalize rows ------------------------------
def _fetch_msched_rows(con, day_expr: str) -> List[Tuple[str, float, str, str]]:
    """
    Return rows as (marketId, tto_minutes (float), market_name, off_at_utc)
    for the given day (UTC), where `day_expr` is e.g. "julianday('now','utc')".
    """
    rows = con.execute(f"""
        SELECT
          marketId,
          CAST((julianday(off_at_utc) - {day_expr}) * 1440.0 AS INTEGER) AS tto,
          COALESCE(market_name, '') AS market_name,
          off_at_utc
        FROM markets_schedule
        WHERE date(off_at_utc) = date('now','utc')
        ORDER BY datetime(off_at_utc) ASC
        LIMIT 1000
    """).fetchall() or []
    out: List[Tuple[str, float, str, str]] = []
    for r in rows:
        try:
            off = datetime.fromisoformat(str(r["marketStartTime"]).replace("Z","+00:00"))
            mto = (off - now_utc).total_seconds() / 60.0

            # ✅ Skip markets that are already finished beyond in-play window
            if mto < -15.0:   # older than 15 min after off-time
                continue

            row = (str(r["marketId"]), mto)
            if str(off.date()) == today:
                today_rows.append(row)
            else:
                tomorrow_rows.append(row)
        except Exception:
            continue
  

def _merge_sources(day_expr: str) -> List[Tuple[str, float, str, str]]:
    """Union of bets.db then autoscalp_gui.db, dedup by marketId (prefer first seen)."""
    merged: List[Tuple[str, float, str, str]] = []
    seen = set()
    # bets.db first
    con_b = open_bets_db(ro=True)
    if con_b is not None:
        try:
            for row in _fetch_msched_rows(con_b, day_expr):
                if row[0] in seen:  # marketId
                    continue
                seen.add(row[0]); merged.append(row)
        except Exception:
            pass
        finally:
            try: con_b.close()
            except Exception: pass
    # autoscalp_gui.db next
    con_a = open_auto_db(ro=True)
    if con_a is not None:
        try:
            for row in _fetch_msched_rows(con_a, day_expr):
                if row[0] in seen:
                    continue
                seen.add(row[0]); merged.append(row)
        except Exception:
            pass
        finally:
            try: con_a.close()
            except Exception: pass
    return merged

def _normalize_future_rows(rows: Iterable, lookup: Dict[str, Tuple[str, str]]) -> List[Tuple[str, float, str, str]]:
    """
    Normalize any mixture of:
      - 4-tuples: (mid, tto, name, off) => pass through
      - 2-tuples: (mid, tto)            => fill (name, off) from lookup if possible
    """
    out: List[Tuple[str, float, str, str]] = []
    for it in rows or []:
        try:
            # fast-path
            mid, tto, name, off = it  # type: ignore[misc]
            out.append((str(mid), float(tto), str(name), str(off)))
            continue
        except Exception:
            pass
        # 2-tuple fallback
        try:
            mid, tto = it  # type: ignore[misc]
            mid = str(mid); tto = float(tto)
            name, off = lookup.get(mid, ("", ""))
            out.append((mid, tto, name, off))
        except Exception:
            # unrecognized item shape – skip
            continue
    # apply primary filter (again) in case the project scope included variants
    out = [r for r in out if _is_primary_market(r[2])]
    # sort by tto then off for stability
    out.sort(key=lambda r: (r[1], r[3], r[0]))
    return out

# ---------- scope builders ----------------------------------------------------
def _fallback_scope_union_today() -> Dict[str, list]:
    return _fallback_scope_union_for_day("julianday('now','utc')")

def _fallback_scope_union_tomorrow() -> Dict[str, list]:
    return _fallback_scope_union_for_day("julianday('now','utc','+1 day')")

def _fallback_scope_union_for_day(day_expr: str) -> Dict[str, list]:
    """
    Build scope dict using live DBs with the correct shapes:
      pre_near, pre_far -> list of (mid, tto, name, off)
      in_play           -> list of (mid, elapsed_minutes)  [2‑tuple by design]
    """
    rows = _merge_sources(day_expr)
    future = [(m, t, n, o) for (m, t, n, o) in rows if t >= 0.0]
    in_play_rows = [(m, -t) for (m, t, n, o) in rows if t < 0.0]  # elapsed positive

    pre_near = [(m, t, n, o) for (m, t, n, o) in future if 0.0 <= t <= 20.0]
    pre_far  = [(m, t, n, o) for (m, t, n, o) in future if t > 20.0]

    return {
        "pre_near": pre_near,
        "pre_far":  pre_far,
        "in_play":  in_play_rows,  # 2‑tuple list
    }

def scope_snapshot(inplay_window_min: int = 15, *, allow_tomorrow_fallback: bool = False) -> Dict[str, list]:
    """
    Try project scope first; if empty or shape-mismatched, build from DBs.
    Always return shapes:
      pre_* : (mid, tto, name, off)
      in_play: (mid, elapsed_min)
    """
    now = utc_now()
    sc: Dict[str, list] = {"pre_near": [], "pre_far": [], "in_play": []}

    # 1) try project build
    try:
        proj = _project_build_scope(now_utc=now, inplay_window_min=inplay_window_min) or {}
    except Exception:
        proj = {}

    # Prepare lookup to normalize 2‑tuples -> 4‑tuples
    today_lookup = {m: (n, o) for (m, _t, n, o) in _merge_sources("julianday('now','utc')")}

    # normalize if project scope gave us anything
    pre_near = _normalize_future_rows(proj.get("pre_near", []), today_lookup)
    pre_far  = _normalize_future_rows(proj.get("pre_far",  []), today_lookup)
    in_play  = []
    # in_play should be 2‑tuples (mid, elapsed)
    for it in proj.get("in_play", []):
        try:
            mid, elapsed = it  # type: ignore[misc]
            in_play.append((str(mid), float(elapsed)))
        except Exception:
            # if a project path returned 4‑tuples here, coerce to 2‑tuple
            try:
                mid, tto, _name, _off = it  # type: ignore[misc]
                in_play.append((str(mid), float(-tto) if float(tto) < 0 else 0.0))
            except Exception:
                continue

    if not (pre_near or pre_far or in_play):
        sc = _fallback_scope_union_today()
        # Final fallback: inject bets.db next-5 if even that fails
        try:
            mids = _next5_from_bets_with_active(max_items=5)
            if mids:
                sc["pre_far"] += [(m, 9999.0, "", "") for m in mids if m not in [x[0] for x in sc["pre_far"]]]
        except Exception:
            pass

    # Optional tomorrow-only fallback for CLI viewing
    if allow_tomorrow_fallback and not (sc["pre_near"] or sc["pre_far"] or sc["in_play"]):
        sc = _fallback_scope_union_tomorrow()

    ok = bool(sc["pre_near"] or sc["pre_far"] or sc["in_play"])
    status_once("scope:build", ok, "empty" if not ok else "")
    return sc

# ---------- window seeding / maintenance / ordering --------------------------
def seed_scope_window(scope: Dict[str, list]) -> None:
    """
    WIN5 = strictly the next five *primary Win* markets by tto (today).
    Input expects pre_* rows of 4‑tuple shape.
    """
    global SCOPE_WINDOW
    if SCOPE_WINDOW:
        return
    future: List[Tuple[str, float, str, str]] = list(scope.get("pre_near", [])) + list(scope.get("pre_far", []))
    # shape guard (raises in your logs when tuples were (mid,tto))
    future = _normalize_future_rows(future, {m: (n, o) for (m, _t, n, o) in _merge_sources("julianday('now','utc')")})
    dedup: List[str] = []
    seen = set()
    for (m, t, name, off) in future:
        if m in seen:
            continue
        seen.add(m)
        dedup.append(m)
        if len(dedup) >= SCOPE_WINDOW_MAX:
            break
    SCOPE_WINDOW = dedup

def prune_and_slide_window(scope: Dict[str, list]) -> None:
    """
    Keep WIN5 rolling forward:
      • Drop anything no longer today OR no longer in schedule nor in-play
      • Append next future markets until we have 5
    """
    global SCOPE_WINDOW
    # Accept both shapes for safety
    in_play_ids = []
    for it in scope.get("in_play", []):
        try:
            in_play_ids.append(str(it[0]))  # (mid, elapsed)
        except Exception:
            pass

    future_rows: List[Tuple[str, float, str, str]] = list(scope.get("pre_near", [])) + list(scope.get("pre_far", []))
    future_rows = _normalize_future_rows(future_rows, {m: (n, o) for (m, _t, n, o) in _merge_sources("julianday('now','utc')")})
    future_ids = [m for (m, _t, _n, _o) in future_rows]
    future_set = set(future_ids)

    # in prune_and_slide_window
    SCOPE_WINDOW = [
        m for m in SCOPE_WINDOW
        if is_today_in_scope(m)
        and (m in future_set or m in in_play_ids)
      
    ]

    # 2) Refill to 5 with next future markets
    for (m, _t, _n, _o) in future_rows:
        if len(SCOPE_WINDOW) >= SCOPE_WINDOW_MAX:
            break
        if m not in SCOPE_WINDOW and is_today_in_scope(m):
            SCOPE_WINDOW.append(m)

def advance_scope_cursor(step: int = 1, span: int | None = None) -> None:
    global SCOPE_CURSOR
    try:
        n = int(span or 0)
        if n <= 0:
            return
        SCOPE_CURSOR = (int(SCOPE_CURSOR) + int(step)) % n
    except Exception:
        SCOPE_CURSOR = 0

def promote_market_on_signal(market_id: str, ttl_s: int = 120) -> None:
    try:
        SCOPE_OVERRIDES[str(market_id)] = time.time() + max(30, int(ttl_s))
    except Exception:
        pass

def trim_expired_overrides() -> None:
    now = time.time()
    for m, exp in list(SCOPE_OVERRIDES.items()):
        if exp <= now:
            SCOPE_OVERRIDES.pop(m, None)




# --- add/replace in engines/decision_engine/decide_once/scope.py ---

import sqlite3, json
from datetime import datetime, timezone

def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone()
    return bool(row)

def _resolve_off_col(con: sqlite3.Connection) -> str:
    """
    Find an OFF time column on markets_schedule.
    Supported names include your build: off_at_utc (PRIMARY),
    plus other common aliases for compatibility.
    """
    cols = {r[1].lower(): r[1] for r in con.execute("PRAGMA table_info(markets_schedule)") or []}
    for cand in ("off_at_utc", "off_ts", "off_at", "scheduled_off", "openDate", "startTime"):
        if cand.lower() in cols:
            return cols[cand.lower()]
    return ""  # none found

from engines.market_monitor.monitor import get_market_state

# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 🔎 SEARCH: def _active_sids
# 📆 PATCHED: 2025-10-05T17:55Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _active_sids(mid: str) -> list[str]:
    """
    Return active runner IDs for a market, preferring MarketMonitor.
    Falls back to oc1/ltp [1.5–12.0] scan if monitor has no data yet.
    """
    try:
        from engines.market_monitor.monitor import get_market_state
        state = get_market_state(mid) or {}
        runners = state.get("runners", {})
        if runners:
            active = [
                sid for sid, info in runners.items()
                if info.get("band") == "ACTIVE" and not info.get("is_fav", False) is None
            ]
            if active:
                return [str(s) for s in active]
    except Exception as e:
        print(f"[active_sids] monitor miss: {e}")

    # Fallback: direct DB scan 1.5–12.0 odds
    try:
        from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
        con = _auto_conn(); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT DISTINCT selectionId
              FROM inbound_oc_cache
             WHERE marketId=?
               AND oc1 BETWEEN 1.5 AND 12.0
             ORDER BY CAST(oc1 AS REAL) ASC
             LIMIT 12
        """, (mid,)).fetchall() or []
        con.close()
        if not rows:
            con = _auto_conn(); con.row_factory = sqlite3.Row
            rows = _q(con, """
                SELECT DISTINCT selectionId
                  FROM odds_current
                 WHERE marketId=?
                   AND CAST(ltp AS REAL) BETWEEN 1.5 AND 12.0
                 ORDER BY CAST(ltp AS REAL) ASC
                 LIMIT 12
            """, (mid,)).fetchall() or []
            con.close()
        return [str(r["selectionId"]) for r in rows]
    except Exception as e:
        print(f"[active_sids] fallback warn: {e}")
        return []

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 🔎 SEARCH: def read_scope_window
# 📆 PATCHED: 2025-10-09T12:30Z — restore legacy structure (list[str] + map[mid→sids])
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def read_scope_window(ahead_min: int = 90) -> dict:
    """
    Rebuild today's live scope snapshot for DecideOnce + Mastery.
    Returns legacy structure:
      { "markets": ["1.2487...", "1.2488..."], "active_sids": {"1.2487...": ["123","124"], ...} }
    """
    import sqlite3, datetime
    from .helpers import open_bets_db
    from engines.config_paths import open_auto_db, q_retry as _q


    scope = {"markets": [], "active_sids": {}}
    # === PATCH START ===
    # 📍 TARGET: engines/decision_engine/decide_once/scope.py:read_scope_window
    # 🔎 SEARCH: bdb = connect_db(path=bets_db(), ro=True)
    # 📆 PATCHED: 2025-11-22 — replace unsafe connect_db() with DAL-safe open_bets_db()

    try:
        # use DAL-safe bets connector
        bdb = open_bets_db(ro=True)
        bdb.row_factory = sqlite3.Row
        rows = _q(bdb, """
            SELECT marketId, selectionId
              FROM bets
             WHERE datetime(marketStartTime) BETWEEN datetime('now','-1 hour','utc')
                                               AND datetime('now','+12 hour','utc')
             ORDER BY datetime(marketStartTime)
        """).fetchall()
        bdb.close()
# === PATCH END ===

    except Exception:
        rows = []

    for r in rows:
        mid, sid = str(r["marketId"]), str(r["selectionId"])
        scope["active_sids"].setdefault(mid, set()).add(sid)
    scope["markets"] = list(scope["active_sids"].keys())
    # convert sets to sorted lists
    scope["active_sids"] = {m: sorted(list(s)) for m, s in scope["active_sids"].items()}

    _SCOPE_STATE.clear()
    _SCOPE_STATE.update(scope)
    return scope
# === PATCH END ===



# ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 🔎 SEARCH: def seed_scope_window(scope:
# ⛏️ ACTION: ensure WIN5 only pulls markets with active_sids > 0
# ============================================================

def seed_scope_window(scope: Dict[str, list]) -> None:
    """
    WIN5 = strictly the next five *primary Win* markets by tto (today).
    Input expects pre_* rows of 4-tuple shape + active_sids check.
    """
    global SCOPE_WINDOW
    if SCOPE_WINDOW:
        return

    future: List[Tuple[str, float, str, str]] = list(scope.get("pre_near", [])) + list(scope.get("pre_far", []))
    future = _normalize_future_rows(future, {m: (n, o) for (m, _t, n, o) in _merge_sources("julianday('now','utc')")})

    dedup: List[str] = []
    seen = set()
    for (m, t, name, off) in future:
        # require active_sids in scope.markets
        m_entry = next((x for x in scope.get("markets", []) if x.get("marketId") == m), None)
        if not m_entry or not m_entry.get("active_sids"):
            continue
        if m in seen:
            continue
        seen.add(m)
        dedup.append(m)
        if len(dedup) >= SCOPE_WINDOW_MAX:
            break

    SCOPE_WINDOW = dedup




def ordered_markets_for_tick(scope_obj=None, *, ahead_min: int = 30, lookback_min: int = 5) -> list[str]:
    if isinstance(scope_obj, dict) and scope_obj.get("markets"):
        return [m["marketId"] for m in scope_obj["markets"]]
    snap = read_scope_window(ahead_min=ahead_min, lookback_min=lookback_min)
    return [m["marketId"] for m in snap["markets"]]



import sqlite3
from datetime import datetime, timezone, timedelta
from .helpers import open_auto_db as _adb, q_retry as _q

def _now_utc():
    return datetime.now(timezone.utc)

def _has_col(con, tbl, col) -> bool:
    con.row_factory = sqlite3.Row
    rows = con.execute(f"PRAGMA table_info({tbl})").fetchall() or []
    return any(r["name"].lower()==col.lower() for r in rows)



def print_scope_dashboard(scope: Dict[str, list], inplay_window_min: int = 15) -> None:
    try:
        now_s = int(time.time())
        # future maps expect 4‑tuples
        tto_map = {m: t for (m, t, _n, _o) in (scope.get("pre_near", []) + scope.get("pre_far", []))}
        ip_map  = {}
        for it in scope.get("in_play", []):
            try:
                mid, elapsed = it
                ip_map[str(mid)] = float(elapsed)
            except Exception:
                pass
        win5 = list(SCOPE_WINDOW)
        extras20 = [m for (m, t, _n, _o) in (scope.get("pre_near", []) or []) if float(t) <= 20.0 and m not in win5]
        in_play = list(ip_map.keys())
        signals = [m for (m, exp) in SCOPE_OVERRIDES.items() if exp > now_s]
        print(f"[SCOPES] WIN5={len(win5)}  ADD≤20={len(extras20)}  INPLAY={len(in_play)}  SIGNALS={len(signals)}")
        if win5:
            items = ", ".join(f"{m}({int(tto_map.get(m, 999))}m)" for m in win5[:12])
            tail  = "" if len(win5) <= 12 else f" …+{len(win5)-12}"
            print(f"  WIN5: {items}{tail}")
        if extras20:
            items = ", ".join(f"{m}({int(t)}m)" for m in extras20[:12])
            tail  = "" if len(extras20) <= 12 else f" …+{len(extras20)-12}"
            print(f"  ADD≤20: {items}{tail}")
        if in_play:
            items = ", ".join(f"{m}(+{int(ip_map.get(m, 0))}m)" for m in in_play[:12])
            tail  = "" if len(in_play) <= 12 else f" …+{len(in_play)-12}"
            print(f"  INPLAY: {items}{tail}")
        if signals:
            items = ", ".join(f"{m}(+{int(SCOPE_OVERRIDES[m]-now_s)}s)" for m in signals[:12])
            tail  = "" if len(signals) <= 12 else f" …+{len(signals)-12}"
            print(f"  SIGNALS: {items}{tail}")
    except Exception:
        pass

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 🔎 SEARCH: def build_and_maintain_scope(
# 📆 PATCHED: 2025-10-07T18:10Z — live-time aware, today-only, monitor-driven scope
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from datetime import datetime, timezone
import sqlite3
from engines.market_monitor import monitor

_SCOPE_STATE = {"markets": [], "active_sids": {}}

def build_and_maintain_scope(*, inplay_window_min: int = 15, show_dashboard: bool = True) -> dict:
    """
    Build live-time-aware scope for TODAY only.

    Includes markets:
      • In-play (off_at <= now, within inplay_window_min)
      • <=20 min to off
      • <=60 min to off
      • next 5 future
    Filters out past markets beyond in-play window.
    Attaches ACTIVE runners from MarketMonitor.
    """
    from .helpers import open_bets_db
    from engines.config_paths import open_auto_db, q_retry as _q

    now_utc = datetime.now(timezone.utc)
    con = open_bets_db(ro=True)     # FIXED
    con.row_factory = sqlite3.Row


    rows = _q(con, """
        SELECT DISTINCT marketId, marketStartTime
          FROM bets
         WHERE date(marketStartTime)=date('now','utc')
         ORDER BY datetime(marketStartTime) ASC
    """).fetchall() or []
    con.close()

    today_rows = []
    for r in rows:
        try:
            off = datetime.fromisoformat(str(r["marketStartTime"]).replace("Z","+00:00"))
            mto = (off - now_utc).total_seconds() / 60.0
            # Exclude markets long finished (beyond in-play window)
            if mto < -float(inplay_window_min):
                continue
            today_rows.append((str(r["marketId"]), mto))
        except Exception:
            continue

    # --- Prioritisation by TTO -----------------------------------------
    today_rows.sort(key=lambda x: x[1])
    in_play = [m for m,t in today_rows if t <= 0]
    near20  = [m for m,t in today_rows if 0 < t <= 20]
    near60  = [m for m,t in today_rows if 20 < t <= 60]
    next5   = [m for m,t in today_rows if t > 60][:5]
    mids = in_play + near20 + near60 + next5

    if not mids:
        print("[SCOPE] no live markets in scope (all finished today)")
        _SCOPE_STATE["markets"].clear()
        _SCOPE_STATE["active_sids"].clear()
        return {"markets": []}

# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 📆 PATCHED: 2025-10-12T10:00Z — use monitor state only (no backfills)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Ask MarketMonitor to refresh and read back runner state -------
    monitor.refresh(mids)
    snap = {"markets": []}

    for mid, mto in today_rows:
        if mid not in mids:
            continue
        state = monitor.get_market_state(mid) or {}
        runners = state.get("runners", {}) or {}

        # build active/passive sets directly from monitor
        active_sids  = [sid for sid, r in runners.items() if r.get("band") == "ACTIVE"]
        passive_sids = [sid for sid, r in runners.items() if r.get("band") == "PASSIVE"]
        fav_sid      = state.get("fav_sid")

        snap["markets"].append({
            "marketId": mid,
            "minutes_to_off": round(mto, 1),
            "active_sids": active_sids,
            "passive_sids": passive_sids,
            "fav_sid": fav_sid,
        })

    _SCOPE_STATE = {
        "markets": [m["marketId"] for m in snap["markets"]],
        "active_sids": {m["marketId"]: m["active_sids"] for m in snap["markets"]},
        "passive_sids": {m["marketId"]: m["passive_sids"] for m in snap["markets"]},
        "fav_sids": {m["marketId"]: m["fav_sid"] for m in snap["markets"] if m.get("fav_sid")},
    }

    print(f"[SCOPE] built {len(_SCOPE_STATE['markets'])} markets (monitor-driven)")
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


    # --- Bucket classification (priority order) -----------------------
    try:
        in_play = [m for m in snap["markets"] if m.get("minutes_to_off", 9999) <= 0]
        near20  = [m for m in snap["markets"] if 0 < m.get("minutes_to_off", 9999) <= 20]
        near60  = [m for m in snap["markets"] if 20 < m.get("minutes_to_off", 9999) <= 60]
        next5   = [m for m in snap["markets"] if m.get("minutes_to_off", 9999) > 60][:5]
    except Exception as e:
        print(f"[SCOPE] warn (bucket classify): {e}")
        in_play, near20, near60, next5 = [], [], [], []

    # --- Always include today's markets with open bets ----------------
    open_bets = []
    try:
        from engines.config_paths import connect_autoscalp_db as _auto_db, q_retry as _q
        con = _auto_db(ro=True)
        con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT
              marketId, selectionId,
              SUM(CASE WHEN UPPER(role)='PARENT' AND entry_status='MATCHED' THEN 1 ELSE 0 END) AS parent_entry_matched,
              SUM(CASE WHEN UPPER(role)='PARENT' AND entry_status<>'MATCHED' THEN 1 ELSE 0 END) AS parent_entry_unmatched,
              SUM(CASE WHEN UPPER(role)='CHILD'  AND entry_status='MATCHED' THEN 1 ELSE 0 END) AS child_entry_matched,
              SUM(CASE WHEN UPPER(role)='CHILD'  AND entry_status<>'MATCHED' THEN 1 ELSE 0 END) AS child_entry_unmatched
            FROM orders
            WHERE date(opened_at)=date('now','utc')
            GROUP BY marketId, selectionId
            HAVING (parent_entry_matched+parent_entry_unmatched+
                     child_entry_matched+child_entry_unmatched) > 0
        """).fetchall() or []
        con.close()
        for r in rows:
            open_bets.append({
                "marketId": str(r["marketId"]),
                "selectionId": str(r["selectionId"]),
                "parents_matched": int(r["parent_entry_matched"] or 0),
                "parents_unmatched": int(r["parent_entry_unmatched"] or 0),
                "children_matched": int(r["child_entry_matched"] or 0),
                "children_unmatched": int(r["child_entry_unmatched"] or 0),
            })
    except Exception as e:
        print(f"[SCOPE] warn (open_bets): {e}")


    # --- Capture any movement signals (if monitor supports it) --------
    signals = []
    try:
        moves = monitor.get_moved_signals()
        if moves:
            from engines.mastery import event_sink
            for mid, sid, delta in moves:
                payload = {
                    "type": "movement_signal",
                    "marketId": mid,
                    "selectionId": sid,
                    "delta": delta,
                    "ts": datetime.utcnow().isoformat(),
                }
                event_sink.on_decision(payload)
                signals.append(payload)
            print(f"[SCOPE] {len(signals)} movement signals sent to Mastery")
    except Exception as e:
        print(f"[SCOPE] warn (signals): {e}")

    # --- Update global scope state for downstream readers -------------
    try:
        _SCOPE_STATE.update({
            "in_play": in_play,
            "near20": near20,
            "near60": near60,
            "next5": next5,
            "open_bets": open_bets,
            "signals": signals,
            "markets": [m["marketId"] for m in snap["markets"]],
            "markets_dict": snap["markets"],  # ← full dicts version kept here
            "active_sids": {m["marketId"]: m.get("active_sids", [])
                            for m in snap["markets"] if "marketId" in m},
        })
        print(f"[SCOPE] buckets: in_play={len(in_play)}  near20={len(near20)}  "
              f"near60={len(near60)}  next5={len(next5)}  open={len(open_bets)}")
    except Exception as e:
        print(f"[SCOPE] warn (update state): {e}")

    # --- Normalize dual-access structure for DecideOnce.run_all() -----
    snap["active_sids"] = {m["marketId"]: m["active_sids"] for m in snap["markets"]}
    snap["passive_sids"] = {m["marketId"]: m["passive_sids"] for m in snap["markets"]}
    snap["fav_sids"] = {m["marketId"]: m["fav_sid"] for m in snap["markets"] if m.get("fav_sid")}

    # Dual-format: allow both dict + str access downstream
    snap["markets_dict"] = snap["markets"]
    snap["markets"] = [
        {"marketId": m["marketId"], "active_sids": m.get("active_sids", [])}
        if isinstance(m, dict)
        else {"marketId": str(m), "active_sids": _SCOPE_STATE.get("active_sids", {}).get(str(m), [])}
        for m in (_SCOPE_STATE.get("markets") or [])
    ]

    # keep a flat list of strings for older modules
    _SCOPE_STATE["market_ids"] = [m["marketId"] for m in snap["markets"]]

    return snap


# Optional CLI: python -m engines.decision_engine.decide_once.scope
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 📆 PATCHED: 2025-10-07T14:45Z — background refresher daemon (final layout)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import threading, time, traceback



# === PATCH START (disable refresher permanently) ===
# 📍 TARGET: engines/decision_engine/decide_once/scope.py
# 📆 PATCHED: 2025-10-07T16:05Z — disable runaway background refresher
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def start_scope_refresher(interval_s: int = 30) -> None:
    """Disabled — refresher caused stale market spam and empty bets."""
    print("[SCOPE] refresher disabled (use build_and_maintain_scope() manually)")
    return
# === PATCH END ===



# Optional CLI: python -m engines.decision_engine.decide_once.scope
if __name__ == "__main__":
    sc = scope_snapshot(15, allow_tomorrow_fallback=True)  # viewing only
    print_scope_dashboard(sc, 15)


