# engines/market_monitor/monitor.py
from __future__ import annotations
from typing import Dict, Tuple, Optional
import sqlite3, math, time

# --- config (overridable via engines.daily_config.MARKET_MONITOR) ---
DEFAULT_BANDS = {
    "ACTIVE_MIN": 1.50,
    "ACTIVE_MAX": 9.00,      # ACTIVE:   <= 9
    "PASSIVE_MAX": 12.00,    # PASSIVE:  9–12
    "EXTENDED_MAX": 20.00,   # EXTENDED: 12–20
}


DEFAULT_POLICY = {
    # Which bands are allowed per letter; '*' is the fallback for unspecified letters.
    "A": ("ACTIVE",),           # A only on ACTIVE
    "P": ("ACTIVE",),           # P only on ACTIVE
    "*": ("ACTIVE", "PASSIVE")  # others default to ACTIVE or PASSIVE; IGNORED never allowed
}

# ------------------------------------------------------------------
# GLOBAL MARKET MONITOR STATE (AUTHORITATIVE, IMPORT-TIME)
# ------------------------------------------------------------------
_STATE: Dict[str, dict] = {}

# === PATCH START ==============================================================
# 📍 TARGET: engines/market_monitor/monitor.py
# 🔎 SEARCH: _STATE: Dict[str, dict] = {}
# 📆 PATCHED: 2026-03-06
# PURPOSE:
# Persist runner monitoring snapshot for dashboard
# ==============================================================================

def _ensure_monitor_snapshot_table():
    con = _adb()
    try:
        con.execute("""
        CREATE TABLE IF NOT EXISTS market_monitor_snapshot (
            ts REAL,
            marketId TEXT,
            selectionId TEXT,
            horse_name TEXT,
            px REAL,
            band TEXT,
            rank INTEGER,
            is_fav INTEGER,
            PRIMARY KEY (marketId, selectionId)
        )
        """)
    finally:
        con.close()

# === PATCH END ================================================================

# band / fav / price memory
_STATE.setdefault("runner_band", {})       # {mid: {sid: (band, ts)}}
_STATE.setdefault("high_seen", {})         # {mid: {sid: float}}
_STATE.setdefault("fav_sid", None)
_STATE.setdefault("fav_changed_ts", 0.0)

# rank / structure memory (USED BY ORCHESTRATOR BOOTSTRAP)
_STATE.setdefault("rank_prev", {})         # {mid: [sid1, sid2, ...]}
_STATE.setdefault("rank_now", {})          # {mid: [sid1, sid2, ...]}
_STATE.setdefault("crossovers", {})        # {mid: {sid: {...}}}
_STATE.setdefault("initial_px", {})
_STATE.setdefault("last_px", {})


try:
    # Optional override
    from engines.daily_config import MARKET_MONITOR as _CFG  # type: ignore
    BANDS = dict(DEFAULT_BANDS, **(_CFG.get("bands") or {}))
    POLICY = dict(DEFAULT_POLICY, **(_CFG.get("letter_policy") or {}))
except Exception:
    BANDS = DEFAULT_BANDS
    POLICY = DEFAULT_POLICY

# ──────────────────────────────────────────────────────────
# Add near your existing _STATE definition / helpers
# ──────────────────────────────────────────────────────────


def _now_ts() -> float:
    import time
    return float(time.time())

def _set_high_seen(mid: str, sid: str, px: float) -> None:
    m = _STATE["high_seen"].setdefault(mid, {})
    hs = m.get(sid, px)
    if px > float(hs or 0.0):
        m[sid] = float(px)

def _get_high_seen(mid: str, sid: str) -> float | None:
    return (_STATE["high_seen"].get(mid, {}) or {}).get(sid)

def update_runner_state(mid: str, sid: str, band: str, px: float | None, is_fav: bool) -> None:
    mid = str(mid); sid = str(sid)
    now = _now_ts()

    # highest price seen (for bias inputs)
    if px is not None:
        m = _STATE["high_seen"].setdefault(mid, {})
        hs = m.get(sid, px)
        if px > float(hs or 0.0):
            m[sid] = float(px)

    # band change
    rb = _STATE["runner_band"].setdefault(mid, {})
# === PATCH START ===
# 📍 TARGET: engines/market_monitor/monitor.py:update_runner_state
# 🔎 SEARCH: rb = _STATE["runner_band"].setdefault(mid, {})
# 🎯 ADD band-change event after prev=... and before favourite logic
# 📆 PATCHED: 2025-11-20
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    prev = rb.get(sid)  # tuple (band, ts) or None
    prev_band = prev[0] if prev else None
    rb[sid] = (band, now)

    # NEW: emit band-change event
    if prev_band and prev_band != band:
        try:
            from engines.mastery import event_sink
            payload = {
                "type": "band_change",
                "marketId": mid,
                "selectionId": sid,
                "prev_band": prev_band,
                "new_band": band,
                "ts": now,
            }
            event_sink.on_decision(payload)
            print(f"[MONITOR] band_change mid={mid} sid={sid} {prev_band}→{band}")
        except Exception:
            pass
# === PATCH END ===

    # store initial price once
    init_map = _STATE["initial_px"].setdefault(mid, {})
    if sid not in init_map and px is not None:
        init_map[sid] = float(px)

    # store last price
    if px is not None:
        _STATE["last_px"].setdefault(mid, {})[sid] = float(px)
  
    # favourite change
    prev_fav = _STATE.get("fav_sid")
    if is_fav and prev_fav != sid:
        _STATE["fav_sid"] = sid
        _STATE["fav_changed_ts"] = now
        # promote market on *new favourite*
        try:
            from engines.decision_engine.decide_once.scope import promote_market_on_signal
            promote_market_on_signal(mid, ttl_s=180)
        except Exception:
            pass

    # promote market on fresh PASSIVE->ACTIVE
    if prev and (prev[0] != band) and band == "ACTIVE":
        try:
            from engines.decision_engine.decide_once.scope import promote_market_on_signal
            promote_market_on_signal(mid, ttl_s=180)
        except Exception:
            pass

# ─────────────────────────────────────────────────────────────
# MARKET SNAPSHOT DATABASE WRITER (HIGH PERFORMANCE)
# ─────────────────────────────────────────────────────────────

from engines.config_paths import LOCAL_AUTO
import sqlite3
import threading
import time

_SNAPSHOT_CONN = None
_SNAPSHOT_LOCK = threading.Lock()


# === PATCH START ==============================================================
# 📍 TARGET: engines/market_monitor/monitor.py:_snapshot_db
# 🔎 SEARCH: def _snapshot_db():
# 📆 PATCHED: 2026-03-06
# PURPOSE:
# Route MarketMonitor snapshot writes through the DAL writer
# instead of opening a direct sqlite3 connection.
# ==============================================================================

from engines.config_paths import open_auto_db

def _snapshot_db():
    """
    DAL-safe snapshot connection.

    Returns the DAL-controlled writer connection for autoscalp_gui.db.
    Ensures the snapshot table exists before returning the connection.
    """

    con = open_auto_db(rw=True)

    con.execute("""
    CREATE TABLE IF NOT EXISTS market_runner_snapshot (
        ts REAL,
        marketId TEXT,
        selectionId TEXT,
        horse_name TEXT,
        px REAL,
        ltp REAL,
        band TEXT,
        is_fav INTEGER,
        PRIMARY KEY (marketId, selectionId)
    )
    """)

    return con

# === PATCH END ================================================================

def _write_snapshot_batch(rows):
    """
    Ultra-fast batch UPSERT of runner snapshots.
    rows = [(ts, mid, sid, horse, px, ltp, band, fav), ...]
    """

    if not rows:
        return

# === PATCH START ==============================================================
# 📍 TARGET: engines/market_monitor/monitor.py:_write_snapshot_batch
# 🔎 SEARCH: con = _snapshot_db()
# 📆 PATCHED: 2026-03-06
# PURPOSE:
# Route snapshot writes through DAL instead of raw sqlite
# ==============================================================================

    con = _snapshot_db()

    with _SNAPSHOT_LOCK:

        for row in rows:
            con.execute("""
            INSERT INTO market_runner_snapshot
            (ts, marketId, selectionId, horse_name, px, ltp, band, is_fav)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(marketId, selectionId)
            DO UPDATE SET
                ts = excluded.ts,
                px = excluded.px,
                ltp = excluded.ltp,
                band = excluded.band,
                is_fav = excluded.is_fav
            """, row)

# === PATCH END ================================================================

def signals_for_runner(mid: str, sid: str, px: float | None, *, recent_s: int = 10) -> dict:
    """Compute flip signals; *does not* promote (promotion is done in update_runner_state)."""
    now = _now_ts()
    mid = str(mid); sid = str(sid)

    band_now, band_ts = (_STATE["runner_band"].get(mid, {}).get(sid) or ("UNKNOWN", 0.0))
    p2a_recent = (band_now == "ACTIVE"  and (now - band_ts) <= recent_s)
    a2p_recent = (band_now == "PASSIVE" and (now - band_ts) <= recent_s)

    fav_sid = _STATE.get("fav_sid")
    fav_changed_ts = float(_STATE.get("fav_changed_ts") or 0.0)
    is_fav_now = (fav_sid == sid)
    new_fav_recent  = is_fav_now and (now - fav_changed_ts) <= recent_s
    lost_fav_recent = (not is_fav_now) and (now - fav_changed_ts) <= recent_s and fav_sid is not None

    high_seen = (_STATE["high_seen"].get(mid, {}) or {}).get(sid)

    return {
        "band_now": band_now,
        "p2a_recent": bool(p2a_recent),
        "a2p_recent": bool(a2p_recent),
        "is_fav_now": bool(is_fav_now),
        "new_fav_recent": bool(new_fav_recent),
        "lost_fav_recent": bool(lost_fav_recent),
        "high_seen": high_seen,
    }


# --- local DB connector (mirrors placement._adb pattern) ---
# === PATCH START ===
# 📍 TARGET: engines/market_monitor/monitor.py:_adb
# 📆 PATCHED: 2025-11-17Z — use raw LOCAL reader instead of DAL

# === PATCH START ===
# 📍 TARGET: engines/market_monitor/monitor.py:_adb
# 📆 PATCHED: 2025-11-20 — guaranteed REAL sqlite3 bypass (no hijack)

# === PATCH START ===
# 📍 TARGET: engines/market_monitor/monitor.py:_adb
# 📆 PATCHED: 2025-11-20 — guaranteed REAL sqlite3 (no hijack, no DAL)



def _update_rank_state(mid: str, runners: dict) -> None:
    """
    Compute and store runner rank order by price (ascending).
    Detect structural crossovers (any rank change).
    """
    mid = str(mid)

    # rank runners by px (ignore None)
    ranked = [
        sid for sid, r in
        sorted(
            runners.items(),
            key=lambda x: (x[1].get("px") is None, x[1].get("px", float("inf")))
        )
    ]

    prev = _STATE["rank_prev"].get(mid)
    _STATE["rank_now"][mid] = ranked
    _STATE["crossovers"].setdefault(mid, {})

    if prev:
        for sid in ranked:
            if sid in prev:
                prev_i = prev.index(sid)
                now_i  = ranked.index(sid)
                if prev_i != now_i:
                    _STATE["crossovers"][mid][sid] = {
                        "crossed_over_recent": True,
                        "rank_prev": prev_i,
                        "rank_now": now_i,
                        "rank_delta": prev_i - now_i,
                        "ts": _now_ts(),
                    }

    # advance snapshot
    _STATE["rank_prev"][mid] = ranked


def get_crossover_signal(mid: str, sid: str, *, recent_s: int = 10) -> dict:
    """
    Return crossover signal for this runner if recent.
    """
    mid = str(mid); sid = str(sid)
    sig = (_STATE.get("crossovers", {}).get(mid, {}) or {}).get(sid)
    if not sig:
        return {"crossed_over_recent": False}

    if (_now_ts() - sig["ts"]) > recent_s:
        return {"crossed_over_recent": False}

    return dict(sig)
# === PATCH END ==============================================================


def _adb():
    """
    Guaranteed REAL local sqlite3 connection to autoscalp_gui.db.
    Bypasses:
      • sqlite3.connect hijack
      • AlphaX scheduler
      • DAL routing
    Always uses the original C-extension _sqlite3 driver.
    """
    import _sqlite3 as _raw
    from engines.config_paths import LOCAL_AUTO

    path = LOCAL_AUTO  # always the real local autoscalp_gui.db path

    con = _raw.connect(
        f"file:{path}?mode=ro",
        uri=True,
        timeout=8,
        isolation_level=None,
        check_same_thread=False
    )
    con.row_factory = sqlite3.Row
    return con
# === PATCH END ===


# engines/market_monitor/monitor.py
from engines.strategy_config import CONFIG
BANDS = {
    "ACTIVE_MIN": float(CONFIG["bands"]["active_min"]),
    "ACTIVE_MAX": float(CONFIG["bands"]["active_max"]),
    "PASSIVE_MAX": float(CONFIG["bands"]["passive_max"]),
    "EXTENDED_MAX": float(CONFIG["bands"].get("extended_max", 15.00)),
}


def _classify_price(px):
    import math
    if px is None or not (px == px) or math.isinf(px):
        return "UNKNOWN"
    lo  = float(BANDS.get("ACTIVE_MIN", 1.50))
    hi  = float(BANDS.get("ACTIVE_MAX", 8.00))
    hi2 = float(BANDS.get("PASSIVE_MAX", 12.00))
    hi3 = float(BANDS.get("EXTENDED_MAX", 15.00))
    if px < lo:
        return "ACTIVE"
    if px <= hi:            # 8.00 is ACTIVE
        return "ACTIVE"
    if px <= hi2:
        return "PASSIVE"
    if px <= hi3:
        return "EXTENDED"
    return "IGNORED"

# === PATCH BLOCK: movement detection + signal accessor
# 📍 FILE: engines/market_monitor/monitor.py
# 🔎 ANCHOR: def refresh(mids
# 🧩 TYPE: ADDITION (insert BEFORE this definition)
# 📆 DATE: 2025-10-10T11:45Z
# ---------------------------------------------------------------------

_MOVES: list[tuple[str, str, float]] = []   # (marketId, selectionId, delta_pct)

def _record_move(mid: str, sid: str, prev_px: float, new_px: float) -> None:
    """Record a movement when price changes ≥ 2 % between refresh cycles."""
    try:
        if not prev_px or not new_px:
            return
        delta = abs(new_px - prev_px) / prev_px
        if delta >= 0.02:           # movement threshold (2 %)
            _MOVES.append((mid, sid, round(delta, 4)))
    except Exception:
        pass


def get_moved_signals() -> list[tuple[str, str, float]]:
    """
    Return and clear the list of movement signals detected since the last refresh.
    Each element: (marketId, selectionId, delta_fraction)
    """
    global _MOVES
    moves = list(_MOVES)
    _MOVES.clear()
    return moves
# ---------------------------------------------------------------------



def refresh(mids: list[str] | None = None, *, max_runners: int = 50) -> None:
    """
    FULL-DAY authoritative monitor.

    • Universe derived from bets table (today only)
    • Exchange best price is single px authority
    • No scope dependency
    • No inbound fallback
    • No odds_current dependency
    """

    from engines.config_paths import open_bets_db
    from engines.bus_route import get_runner_odds_map
    from datetime import datetime, timezone
    import os

    session_token = (
        os.getenv("SESSION_TOKEN")
        or os.getenv("BETFAIR_SESSION_TOKEN")
    )

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # --------------------------------------------------
    # 1️⃣ Get all (mid, sid) for today
    # --------------------------------------------------
    con = open_bets_db(rw=False)
    try:
        rows = con.execute("""
            SELECT DISTINCT marketId, selectionId
            FROM bets
            WHERE substr(marketStartTime,1,10) = ?
        """, (today,)).fetchall()
    finally:
        con.close()

    if not rows:
        return

    pairs = [(str(r[0]), str(r[1])) for r in rows if r[0] and r[1]]

    # --------------------------------------------------
    # 2️⃣ Fetch exchange px for all runners
    # --------------------------------------------------
    odds_map = get_runner_odds_map(pairs, session_token=session_token)

    now = time.time()

    # --------------------------------------------------
    # 3️⃣ Build state per market
    # --------------------------------------------------
    markets: Dict[str, dict] = {}

    for mid, sid in pairs:
        odds = odds_map.get((mid, sid))
        px = float(odds["px"]) if odds and odds.get("px") is not None else None

        band = _classify_price(px)

        m = markets.setdefault(mid, {
            "updated_ts": now,
            "runners": {},
            "fav_sid": None,
        })

        m["runners"][sid] = {
            "px": px,
            "ltp": px,
            "band": band,
            "is_fav": False,
        }

    # --------------------------------------------------
    # 4️⃣ Assign favourite per market
    # --------------------------------------------------
    for mid, data in markets.items():
        fav_sid = None
        fav_px = None

        for sid, info in data["runners"].items():
            px = info["px"]
            if px is not None and (fav_px is None or px < fav_px):
                fav_px = px
                fav_sid = sid

        if fav_sid:
            data["fav_sid"] = fav_sid
            data["runners"][fav_sid]["is_fav"] = True

    # --------------------------------------------------
    # 5️⃣ Commit to _STATE
    # --------------------------------------------------
    snapshot_rows = []

    for mid, data in markets.items():

        _STATE[mid] = data
        _update_rank_state(mid, data["runners"])

        for sid, info in data["runners"].items():

            band = info.get("band", "UNKNOWN")
            px = info.get("px")
            fav = bool(info.get("is_fav"))

            update_runner_state(
                mid,
                sid,
                band,
                px,
                fav,
            )

            snapshot_rows.append((
                time.time(),
                mid,
                sid,
                info.get("horse_name") or sid,
                px,
                px,
                band,
                1 if fav else 0
            ))

    # write snapshots once per refresh
    _write_snapshot_batch(snapshot_rows)

# === PATCH START ==============================================================
# 📍 TARGET: engines/market_monitor/monitor.py:refresh
# 🔎 SEARCH: for mid, data in markets.items():
# 📆 PATCHED: 2026-03-06
# PURPOSE:
# Persist snapshot used by dashboard panels
# ==============================================================================

    _ensure_monitor_snapshot_table()

# === PATCH START ==============================================================
# 📍 TARGET: engines/market_monitor/monitor.py:refresh
# 🔎 SEARCH: con = _adb()
# 📆 PATCHED: 2026-03-06
# PURPOSE:
# Route dashboard snapshot writes through DAL writer
# ==============================================================================

    from engines.config_paths import open_auto_db

    con = open_auto_db(rw=True)

# === PATCH END ================================================================
    try:

        for mid, data in markets.items():

            runners = data["runners"]

            ranked = sorted(
                runners.items(),
                key=lambda x: (x[1]["px"] is None, x[1]["px"])
            )

            for rank, (sid, r) in enumerate(ranked, start=1):

                con.execute("""
                INSERT OR REPLACE INTO market_monitor_snapshot
                (ts, marketId, selectionId, horse_name, px, band, rank, is_fav)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now,
                    mid,
                    sid,
                    sid,
                    r["px"],
                    r["band"],
                    rank,
                    1 if r["is_fav"] else 0
                ))

        con.commit()

    finally:
        con.close()

# === PATCH END ================================================================

# === PATCH START ============================================================
# 📍 TARGET: engines/market_monitor/monitor.py:ensure_for_markets
# 🔎 SEARCH: def ensure_for_markets(
# 📆 PATCHED: 2026-02-16 — Derive full-day universe from bets table
# 🎯 PURPOSE:
#   • MarketMonitor must refresh all live markets for today
#   • Universe comes from bets table (not schedule, not scope)
#   • Guarantees alignment with live trading universe
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def ensure_for_markets(mids: list[str], *, max_runners: int = 12) -> None:
    """
    Canonical behaviour:

    MarketMonitor derives its universe from today's bets table,
    not from caller-provided mids and not from markets_schedule.

    This guarantees:
        • Only live trading markets are refreshed
        • All SIDs for those markets get band classification
        • Scope cannot suppress classification
    """

    from engines.config_paths import open_bets_db
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    con = open_bets_db(rw=False)
    try:
        rows = con.execute("""
            SELECT DISTINCT marketId
            FROM bets
            WHERE substr(marketStartTime,1,10) = ?
        """, (today,)).fetchall()
    finally:
        con.close()

    day_mids = [str(r[0]) for r in rows]

    if day_mids:
        refresh(day_mids, max_runners=max_runners)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# === PATCH END ==============================================================


def get_market_state(mid: str) -> dict:
    return _STATE.get(str(mid), {})

def classify(mid: str, sid: str, *, odds: Optional[float] = None) -> dict:
    """
    Return {"band": str, "px": float|None, "is_fav": bool}.
    If `odds` provided, band is derived from that; otherwise we use the last snapshot or a DB query.
    """
    mid = str(mid); sid = str(sid)
    if odds is not None:
        band = _classify_price(float(odds))
        st = _STATE.get(mid, {})
        is_fav = bool(st and st.get("fav_sid") == sid)
        return {"band": band, "px": float(odds), "is_fav": is_fav}

    # snapshot first
    st = _STATE.get(mid, {})
    rn = st.get("runners", {}).get(sid) if st else None
    if rn:
        return {"band": rn.get("band", "UNKNOWN"),
                "px": rn.get("px"), "is_fav": bool(rn.get("is_fav", False))}

    # on-demand DB lookup
    con = _adb(); con.row_factory = sqlite3.Row
    try:
        r = con.execute("""
            SELECT ltp AS px
            FROM odds_current
            WHERE marketId=? AND selectionId=?
            ORDER BY datetime(updated_ts) DESC
            LIMIT 1
        """, (mid, sid)).fetchone()
        px = float(r["px"]) if r and r["px"] is not None else None
    finally:
        try: con.close()
        except Exception: pass
    band = _classify_price(px)
    st = _STATE.get(mid, {})
    is_fav = bool(st and st.get("fav_sid") == sid)
    return {"band": band, "px": px, "is_fav": is_fav}

def allowed_for_letter(letter: str, band: str) -> bool:
    L = (letter or "").upper()[:1] or "*"
    allowed = POLICY.get(L, POLICY.get("*", ("ACTIVE","PASSIVE")))
    # IGNORED is always disallowed by construction, but keep explicit:
    if band == "IGNORED":
        return False
    return band in allowed
