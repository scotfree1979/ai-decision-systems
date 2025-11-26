# === PATCH START ===
# 📍 TARGET: engines/odds/odds_service.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create file
from __future__ import annotations
from typing import Dict, Tuple
from collections import defaultdict, deque
from datetime import datetime, timezone
import time

from engines.decision_engine.decide_once.scope import build_and_maintain_scope, SCOPE_WINDOW, SCOPE_OVERRIDES
from engines.decision_engine.decide_once.helpers import status_once
from engines.odds.providers.db_fallback_provider import fetch_market_book
from engines.odds.features import slope_ppm, tick_velocity, ranks_by_ltp, market_breadth
from engines.odds.writers import upsert_odds_current, append_snapshot
from engines.blueprint.engine import update_for_market

# in-memory ring buffers (seconds,ltp)
# BUFFERS[(mid,sid)] = deque[(ts, ltp)]
BUFFERS: Dict[Tuple[str,str], deque] = defaultdict(lambda: deque(maxlen=90))
# per-market rank history to compute fav_rank_30s/breadth
RANK_HISTORY: Dict[str, deque] = defaultdict(lambda: deque(maxlen=180))

def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

def _today() -> str:
    return _utcnow().strftime("%Y-%m-%d")

def _mto_minutes_for(mid: str) -> float:
    try:
        from engines.decision_engine.orchestrator import _compute_minutes_to_off
        mto, _ = _compute_minutes_to_off(mid, source="LIVE")
        return float(mto) if mto is not None else 9999.0
    except Exception:
        return 9999.0

def _window_tag(mto: float) -> str:
    if mto <= 0.0: return "IP"
    if mto <= 20.0: return "T20"
    return "PRE"

import sqlite3, time
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db
from engines.decision_engine.decide_once.helpers import _q_retry as _q

def _utc_day() -> str:
    return datetime.now(timezone.utc).date().isoformat()

# === PATCH START ===
# 📍 TARGET: engines/odds/odds_service.py:_open_auto_rw
# 🔎 SEARCH: def _open_auto_rw(
# 📆 PATCHED: 2025-11-21 — replace raw sqlite3 with DAL writer

from engines.config_paths import auto_conn as _auto_conn
from engines.config_paths import q_retry as _q

def _open_auto_rw() -> sqlite3.Connection:
    """
    DAL-safe writer connection to autoscalp_gui.db.
    Replaces raw sqlite3.connect(autoscalp_db()).
    """
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row

    # keep original PRAGMAs (DAL ignores unsupported ones)
    _q(con, "PRAGMA journal_mode=WAL")
    _q(con, "PRAGMA busy_timeout=8000")
    _q(con, "PRAGMA synchronous=NORMAL")

    return con
# === PATCH END ===


def _ensure_odds_current(con: sqlite3.Connection) -> None:
    _q(con, """
    CREATE TABLE IF NOT EXISTS odds_current(
      day TEXT NOT NULL,
      marketId TEXT NOT NULL,
      selectionId TEXT NOT NULL,
      ltp REAL,
      ts TEXT,
      PRIMARY KEY(day, marketId, selectionId)
    )""")
    _q(con, "CREATE INDEX IF NOT EXISTS idx_odds_current_day_mid ON odds_current(day,marketId)")

def _latest_oc1_for_market(con: sqlite3.Connection, mid: str) -> dict[str, float]:
    """
    Return {selectionId: oc1} using the most recent inbound_oc_cache rows.
    """
    rows = _q(con, """
        SELECT selectionId, oc1
          FROM inbound_oc_cache
         WHERE marketId=?
         ORDER BY id DESC
    """, (mid,)).fetchall()
    out = {}
    for r in rows:
        sid = str(r["selectionId"])
        if sid not in out and r["oc1"] is not None:
            out[sid] = float(r["oc1"])
    return out

def tick_update_for_scope(inplay_window_min: int = 15) -> int:
    """
    For all in-scope markets (WIN5 + ≤20m + INPLAY + SIGNALS), upsert LTP into AUTO_DB.odds_current.
    Returns number of rows upserted this tick.
    """
    # pull current scope from decide_once helpers (same source runner uses)
    from engines.decision_engine.decide_once.helpers import build_scope, _utcnow
    scope = build_scope(now_utc=_utcnow(), inplay_window_min=inplay_window_min)
    mids = set(m for m,_ in scope.get("pre_near", [])) \
         | set(m for m,_ in scope.get("pre_far",  [])) \
         | set(m for m,_ in scope.get("in_play", []))
    if not mids:
        return 0

    con = _open_auto_rw()
    try:
        _ensure_odds_current(con)
        day = _utc_day()
        nowts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        upserts = 0
        for mid in mids:
            # get freshest oc1 per selection in this market
            oc1 = _latest_oc1_for_market(con, str(mid))
            if not oc1:
                continue
            for sid, ltp in oc1.items():
                _q(con, """
                    INSERT INTO odds_current(day, marketId, selectionId, ltp, ts)
                    VALUES(?,?,?,?,?)
                    ON CONFLICT(day, marketId, selectionId)
                    DO UPDATE SET ltp=excluded.ltp, ts=excluded.ts
                """, (day, str(mid), str(sid), float(ltp), nowts))
                upserts += 1

        con.commit()
        return upserts
    finally:
        try: con.close()
        except Exception: pass
