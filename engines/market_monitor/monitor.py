# engines/market_monitor/monitor.py
from __future__ import annotations
from typing import Dict, Tuple, Optional
import sqlite3, math, time

# --- config (overridable via engines.daily_config.MARKET_MONITOR) ---
DEFAULT_BANDS = {
    "ACTIVE_MIN": 1.50,
    "ACTIVE_MAX": 9.00,      # ACTIVE:   <= 9
    "PASSIVE_MAX": 12.00,    # PASSIVE:  9–12
    "EXTENDED_MAX": 15.00,   # EXTENDED: 12–15
}

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

# band / fav / price memory
_STATE.setdefault("runner_band", {})       # {mid: {sid: (band, ts)}}
_STATE.setdefault("high_seen", {})         # {mid: {sid: float}}
_STATE.setdefault("fav_sid", None)
_STATE.setdefault("fav_changed_ts", 0.0)

# rank / structure memory (USED BY ORCHESTRATOR BOOTSTRAP)
_STATE.setdefault("rank_prev", {})         # {mid: [sid1, sid2, ...]}
_STATE.setdefault("rank_now", {})          # {mid: [sid1, sid2, ...]}
_STATE.setdefault("crossovers", {})        # {mid: {sid: {...}}}



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



def refresh(mids: list[str], *, max_runners: int = 12) -> None:
    """Refresh market snapshots from odds_current; cheap and safe to call every tick."""
    if not mids:
        return
    con = _adb(); con.row_factory = sqlite3.Row
    try:
        now = time.time()
        for mid in mids:
            # latest ltp per sid
            rows = con.execute("""
                SELECT oc.selectionId AS sid, oc.ltp AS px, oc.updated_ts AS ts
                FROM odds_current oc
                WHERE oc.marketId = ?
                  AND oc.updated_ts = (
                      SELECT MAX(updated_ts) FROM odds_current
                      WHERE marketId = oc.marketId AND selectionId = oc.selectionId
                  )
                LIMIT ?
            """, (str(mid), int(max_runners))).fetchall() or []

# === PATCH START ============================================================
# 📍 TARGET: engines/market_monitor/monitor.py
# 🔎 ANCHOR: fallback to inbound_oc_cache latest snapshot
# 🎯 PURPOSE: REMOVE synthetic price fallback that forces runners ACTIVE
# 📆 PATCHED: 2025-12-18
# ===========================================================================

            if not rows:
                # fallback to inbound_oc_cache latest snapshot if any
                rows = con.execute("""
                    SELECT selectionId AS sid,
                           COALESCE(oc1, anchor_odd) AS px,
                           COALESCE(last_sync_ts, '') AS ts
                    FROM inbound_oc_cache
                    WHERE marketId = ?
                    ORDER BY datetime(COALESCE(last_sync_ts,'')) DESC
                    LIMIT ?
                """, (str(mid), int(max_runners))).fetchall() or []

                # ❌ REMOVED:
                # Any synthetic / hard-coded px fallback (e.g. px = 5.0)
                # Rationale:
                # - If px is missing or extreme, runner must be IGNORED
                # - Forcing px ACTIVE corrupts scope + execution integrity

# === PATCH END ==============================================================



            runners: Dict[str, dict] = {}
            fav_sid, fav_px = None, None
            for r in rows:
                sid = str(r["sid"])
                try:
                    px = float(r["px"]) if r["px"] is not None else None
                except Exception:
                    px = None
                prev = _STATE.get(mid, {}).get("runners", {}).get(sid, {}).get("px")
                if prev and px:
                    _record_move(mid, sid, prev, px)

                band = _classify_price(px)
                # 📍 TARGET: engines/market_monitor/monitor.py
# 🔎 SEARCH: runners[sid] = {"px": px, "band": band, "is_fav": False}
# 📆 PATCHED: 2025-10-12T09:45Z — persist ltp + fix fav assignment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                runners[sid] = {
                    "px": px,
                    "ltp": px,                     # ✅ ensure visible in external state
                    "band": band,
                    "is_fav": False,
                }
                # assign temporary px for fav check
                if px is not None and (fav_px is None or px < fav_px):
                    fav_px, fav_sid = px, sid

            # ✅ after loop: mark favourite properly
            if fav_sid and fav_sid in runners:
                runners[fav_sid]["is_fav"] = True
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


            _STATE[str(mid)] = {
                "updated_ts": now,
                "fav_sid": fav_sid,
                "runners": runners,
            }
            _update_rank_state(str(mid), runners)

            # NEW: update per-runner state *after* favourite is known
            for sid_key, info in runners.items():
                update_runner_state(str(mid), str(sid_key), info.get("band","UNKNOWN"), info.get("px"), bool(info.get("is_fav")))
    finally:
        try: con.close()
        except Exception: pass

def ensure_for_markets(mids: list[str], *, max_runners: int = 12) -> None:
    """Refresh only if we have no state for these mids yet."""
    miss = [m for m in mids if str(m) not in _STATE]
    if miss:
        refresh(miss, max_runners=max_runners)

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
