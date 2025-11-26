# engines/decision_engine/decide_once/candidates.py
from __future__ import annotations

import json, sqlite3, time
from typing import Dict, List, Tuple, Optional

# we only use the helpers api that already exists in decide_once.helpers
from .helpers import (
    status_once,               # health line
    open_auto_db as _adb,
    _q_retry as _q,
    _runner_activity,          # 'active'|'passive'|'ignored'
)

# engines/decision_engine/decide_once/candidates.py


import sqlite3, json, time
from typing import List, Tuple
from engines.config_paths import auto_conn as _auto_conn, q_retry as _q

# Optional: reuse the runner activity classifier from orchestrator if available
try:
    from engines.decision_engine.orchestrator import _runner_activity
except Exception:
    def _runner_activity(mid: str, sid: str) -> str:
        # fail-open: consider within-band prices active
        return "active"

def _latest_prices_from_inbound(mid: str) -> dict[str, float]:
    """Return latest usable price per runner from inbound cache with safe fallbacks."""
    import sqlite3, json
    from .helpers import open_auto_db as _adb, _q_retry as _q, status_once

    out: dict[str, float] = {}

    # 1) AUTO.inbound_oc_cache (latest-first per runner)
    try:
        con = _adb(ro=True); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT selectionId, oc1, anchor_odd, oc1_band_json
            FROM inbound_oc_cache
            WHERE marketId=?
            ORDER BY id DESC
        """, (str(mid),)).fetchall() or []
        seen: set[str] = set()
        for r in rows:
            sid = str(r["selectionId"])
            if sid in seen:
                continue
            px = r["oc1"]
            if px is None:
                px = r["anchor_odd"]
            if px is None:
                bj = r["oc1_band_json"]
                if bj:
                    try:
                        arr = json.loads(bj)
                        if isinstance(arr, list) and arr:
                            px = float(arr[-1])
                    except Exception:
                        pass
            if px is not None:
                out[sid] = float(px)
            seen.add(sid)
        try: con.close()
        except Exception: pass
    except Exception:
        pass

    if out:
        status_once("candidates:odds", True, "inbound — ok")
        return out

    # 2) BETS.oc_series (today’s latest per runner)
    try:
        try:
            from engines.decision_engine.decide_once.helpers import open_bets_db as _bdb
            bdb = _bdb(ro=True)
        except Exception:
            from engines.config_paths import connect_db as _connect
            bdb = _connect(ro=True)
        bdb.row_factory = sqlite3.Row
        rows = _q(bdb, """
            WITH latest AS (
                SELECT selectionId, MAX(chapter) AS mx
                FROM oc_series
                WHERE marketId=? AND date(day)=date('now','utc')
                GROUP BY selectionId
            )
            SELECT s.selectionId, s.px
            FROM oc_series s
            JOIN latest l ON l.selectionId=s.selectionId AND l.mx=s.chapter
            WHERE s.marketId=?
        """, (str(mid), str(mid))).fetchall() or []
        for r in rows:
            out[str(r["selectionId"])] = float(r["px"])
        try: bdb.close()
        except Exception: pass
    except Exception:
        pass

    if out:
        status_once("candidates:odds", True, "oc_series — ok")
        return out

    # 3) BETS.bets (anchor or OC0)
    try:
        if 'bdb' not in locals() or bdb is None:
            try:
                from engines.decision_engine.decide_once.helpers import open_bets_db as _bdb
                bdb = _bdb(ro=True); bdb.row_factory = sqlite3.Row
            except Exception:
                from engines.config_paths import connect_db as _connect
                bdb = _connect(ro=True); bdb.row_factory = sqlite3.Row
        rows = _q(bdb, """
            SELECT selectionId, COALESCE(anchor_odd, OC0) AS px
            FROM bets
            WHERE marketId=?
            GROUP BY selectionId
        """, (str(mid),)).fetchall() or []
        for r in rows:
            px = r["px"]
            if px is not None:
                out[str(r["selectionId"])] = float(px)
        try: bdb.close()
        except Exception: pass
    except Exception:
        pass

    if out:
        status_once("candidates:odds", True, "bets — ok")
        return out

    # 4) Last-ditch LIVE API (optional; network can time out)
    source = "none"
    try:
        from engines.utils.api_tools import fetch_live_odds as _fetch_live_odds  # optional
        if callable(_fetch_live_odds):
            prices = _fetch_live_odds(market_id=str(mid)) or {}
            for sid, book in (prices.get("back", {}) or {}).items():
                try:
                    out[str(sid)] = float(book.get("price") or book.get("px") or 0) or out.get(str(sid), 0)
                except Exception:
                    pass
            source = "api" if out else "none"
    except Exception:
        source = "none"

    status_once("candidates:odds", bool(out), f"{source} — {'ok' if out else 'no data'}")
    return out

# engines/decision_engine/decide_once/candidates.py

import sqlite3
from .helpers import open_auto_db as _adb, q_retry as _q

from engines.mastery.mastery_policy import _SCOPE_STATE

def cands_pairs_for_market(mid: str, max_runners: int = 12):
    """
    Return candidate (sid, px) pairs for DecideOnce — now driven purely by Scope.

    - Uses Scope’s active_sids for each market.
    - Falls back to odds_current only if Scope is empty.
    """
    sids = list(_SCOPE_STATE.get("mid_to_sids", {}).get(str(mid), set()))

    if sids:
        # Price optional — Scope is already authoritative
        return [(str(sid), 0.0) for sid in sids[:max_runners]]

    # Fallback: odds_current 1.5–12.0 (for tests / synthetic seeds)
    try:
        from engines.config_paths import auto_conn, q_retry as _q
        con = auto_conn(); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT DISTINCT selectionId, ltp
              FROM odds_current
             WHERE marketId=? AND ltp BETWEEN 1.5 AND 12.0
             ORDER BY CAST(ltp AS REAL) ASC
             LIMIT ?
        """, (mid, max_runners)).fetchall()
        con.close()
        return [(str(r["selectionId"]), float(r["ltp"] or 0.0)) for r in rows]
    except Exception:
        return []


from engines.mastery.mastery_policy import _SCOPE_STATE
def active_candidates_for_market(market_id: str, max_runners: int = 12):
    """
    Exported name used by lanes/orchestrator.
    Filters to 'active' runners (fail-open if the classifier errs) and sorts by price asc.
    """
    from .helpers import status_once
    odds = _latest_prices_from_inbound(str(market_id))
    if not odds:
        return []
    pool = []
    for sid, px in odds.items():
        try:
            from engines.decision_engine.orchestrator import _runner_activity as _ra  # optional
            if _ra(str(market_id), str(sid)) == "active":
                pool.append((str(sid), float(px)))
        except Exception:
            # fail-open if classifier is unavailable
            try:
                pool.append((str(sid), float(px)))
            except Exception:
                pass
    if not pool:
        pool = sorted([(str(sid), float(px)) for sid, px in odds.items()], key=lambda t: (t[1], t[0]))
    return sorted(pool, key=lambda t: (t[1], t[0]))[:max_runners]

# keep underscored alias for legacy imports
_active_candidates_for_market = active_candidates_for_market
# === PATCH END ===
# 📆 PATCHED: 2025-10-04 – dict-based candidates for Mastery & decide_once

def active_candidates_dicts_for_market(mid: str) -> list[dict]:
    """
    Returns list of candidate dicts for this market:
      [{'marketId':..., 'selectionId':..., 'odds':..., 'minutes_to_off':..., 'phase':'PRE', 'letter':'S'}]
    Used by Mastery/decide_once to build ctx correctly.
    """
    from datetime import datetime, timezone
    from engines.config_paths import auto_conn, q_retry as _q
    from engines.decision_engine.orchestrator import _compute_minutes_to_off as _mto

    con = auto_conn(); con.row_factory = sqlite3.Row
    rows = _q(con, """
        SELECT selectionId, oc1 AS ltp
          FROM inbound_oc_cache
         WHERE marketId=? AND oc1 BETWEEN 1.5 AND 12.0
         ORDER BY CAST(oc1 AS REAL) ASC
         LIMIT 12
    """, (mid,)).fetchall()
    con.close()

    # Compute minutes_to_off directly from bets.db schedule
    from engines.config_paths import connect_db
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        r = bdb.execute("""
            SELECT ROUND((julianday(marketStartTime) - julianday('now','utc'))*1440.0,1) AS mto
              FROM bets
             WHERE marketId=? LIMIT 1
        """, (mid,)).fetchone()
        bdb.close()
        mto = float(r["mto"]) if r and r["mto"] is not None else None
    except Exception:
        mto = None


    out = []
    for r in rows or []:
        sid = str(r["selectionId"])
        odds = float(r["ltp"] or 0.0)
        if odds <= 0:
            continue
        out.append({
            "marketId": mid,
            "selectionId": sid,
            "odds": odds,
            "minutes_to_off": mto if mto is not None else 10.0,
            "phase": "PRE" if (mto is None or mto > 0) else "IN_PLAY",
            "letter": "S"
        })
    return out


# optional live api fallback (works in LIVE/LEARNING, returns {'back','lay'})
try:
    from engines.utils.api_tools import fetch_live_odds as _fetch_live_odds
except Exception:
    _fetch_live_odds = None


def _prices_from_inbound(mid: str) -> Dict[str, float]:
    """
    Primary source: AUTO_DB.inbound_oc_cache
      COALESCE(oc1, anchor_odd, oc1_band tail) per runner (latest-first).
    """
    out: Dict[str, float] = {}
    try:
        con = _adb(ro=True); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT selectionId, oc1, anchor_odd, oc1_band_json
            FROM inbound_oc_cache
            WHERE marketId=?
            ORDER BY id DESC
        """, (str(mid),)).fetchall()
        seen = set()
        for r in rows or []:
            sid = str(r["selectionId"]); 
            if sid in seen: 
                continue
            px = r["oc1"]
            if px is None: px = r["anchor_odd"]
            if px is None and r["oc1_band_json"]:
                try:
                    band = json.loads(r["oc1_band_json"]) or []
                    px = band[-1] if band else None
                except Exception:
                    px = None
            if px is not None:
                out[sid] = float(px)
            seen.add(sid)
        con.close()
    except Exception:
        pass
    return out


def _prices_from_bets(mid: str) -> Dict[str, float]:
    """
    Fallback: BETS_DB.bets — COALESCE(anchor_odd, OC0) grouped by selectionId.
    """
    out: Dict[str, float] = {}
    try:
        # prefer helper if present, else config_paths.connect_db
        try:
            from engines.decision_engine.decide_once.helpers import open_bets_db as _bdb
            bdb = _bdb(ro=True)
        except Exception:
            from engines.config_paths import connect_db as _connect
            bdb = _connect(ro=True)

        bdb.row_factory = sqlite3.Row
        rows = _q(bdb, """
            SELECT selectionId, COALESCE(anchor_odd, OC0) AS px
            FROM bets
            WHERE marketId=?
            GROUP BY selectionId
        """, (str(mid),)).fetchall()
        for r in rows or []:
            if r["px"] is not None:
                out[str(r["selectionId"])] = float(r["px"])
        bdb.close()
    except Exception:
        pass
    return out


def _prices_from_api(mid: str, sids: List[str]) -> Dict[str, float]:
    """
    Last-ditch: live API best offers for runners we know the ids of.
    """
    out: Dict[str, float] = {}
    if not _fetch_live_odds or not sids:
        return out
    for sid in sids:
        try:
            od = _fetch_live_odds(None, mid, sid) or {}
            # prefer the side we’d enter **against** (lay for LAY->BACK, back for BACK->LAY),
            # but as a neutral current-price proxy, pick either that is present.
            px = od.get("lay") or od.get("back")
            if px:
                out[str(sid)] = float(px)
        except Exception:
            continue
    return out

# 📍 TARGET: engines/decision_engine/decide_once/candidates.py
# 🔎 SEARCH: def cands_for_market
# 📆 PATCHED: 2025-10-06T20:45Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# === PATCH 2 START (defer to Scope) ===
from engines.decision_engine.decide_once.scope import _SCOPE_STATE

def cands_for_market(mid: str, max_runners: int = 8) -> list[tuple[str, float]]:
    """
    Returns candidates (selectionId, price) purely from Scope.
    Falls back to odds_current only if Scope is empty.
    """
    sids = _SCOPE_STATE["active_sids"].get(str(mid), [])
    if sids:
        return [(sid, 0.0) for sid in sids[:max_runners]]

    # Fallback: odds_current snapshot 1.5-12.0 range (synthetic/test)
    try:
        from engines.config_paths import auto_conn, q_retry as _q
        con = auto_conn(); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT DISTINCT selectionId, ltp
              FROM odds_current
             WHERE marketId=? AND ltp BETWEEN 1.5 AND 12.0
             ORDER BY CAST(ltp AS REAL) ASC
             LIMIT ?
        """, (mid, max_runners)).fetchall()
        con.close()
        pool = [(str(r["selectionId"]), float(r["ltp"])) for r in rows if r["ltp"] is not None]
        if pool:
            print(f"[PATCH] odds_current fallback used for {mid} → {len(pool)} runners")
        return pool
    except Exception as e:
        print(f"[PATCH WARN] odds_current fallback failed: {e}")
        return []
# === PATCH 2 END (defer to Scope) ===

