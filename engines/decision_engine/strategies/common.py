from __future__ import annotations
import os, sqlite3, time, math
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db

def _adb() -> sqlite3.Connection:
    con = sqlite3.connect(autoscalp_db(), timeout=12, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con

def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def _ticks_between(a: float, b: float) -> int:
    return int(round((b - a) / 0.01))

def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# 📍 TARGET: engines/decision_engine/runner.py
# 🔎 SEARCH: from __future__ import annotations
# Insert just below imports

def safe_price(source: dict | None) -> float | None:
    """
    Extract a usable odds float from a runner/plan/ctx dict.
    Returns None if no price is available.
    """
    if not source:
        return None
    for key in ("px", "price", "ltp", "last_price_traded", "anchor_odd", "entry_odds", "price_now"):
        try:
            v = source.get(key)
            if v is not None:
                return float(v)
        except Exception:
            continue
    return None


# === bias plan filter (lay-first) ===
from typing import List, Dict, Any
from engines.odds.trend_analyzer.direction_service import BiasDecision

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/common.py
# 🔎 SEARCH: ^def apply_bias_to_plan\(plan: List\[Dict\[str, Any\]\], bias: BiasDecision, \*, exposure: float\) -> List\[Dict\[str, Any\]\]:
# ⛏️ ACTION: replace the entire function to repair the missing indented block and harden exposure
def apply_bias_to_plan(plan: List[Dict[str, Any]], bias: BiasDecision, *, exposure: float) -> List[Dict[str, Any]]:
    """
    Filters a plan according to bias rules.

    Each plan item looks like:
      {
        "kind": "PARENT" | "CHILD",
        "edge": "L2B" | "B2L",
        "side": "LAY" | "BACK",
        "price": float,
        "size": float,
        "meta": {"letter": "A", "strategy": "range_breakout", ...}
      }
    """
    # --- harden inputs (fixes NoneType <= int) ---
    try:
        exposure = float(exposure) if exposure is not None else 0.0
    except Exception:
        exposure = 0.0

    shaped: List[Dict[str, Any]] = []

    # Extract bias booleans once (present on BiasDecision)
    allow_l2b      = bool(getattr(bias, "allow_l2b", False))
    allow_b2l      = bool(getattr(bias, "allow_b2l", False))
    b2l_hedge_only = bool(getattr(bias, "b2l_hedge_only", False))

    for item in plan:
        if item.get("kind") != "PARENT":
            shaped.append(item)
            continue

        edge = (item.get("edge") or "").upper()

        if edge == "L2B":
            if allow_l2b:
                shaped.append(item)

        elif edge == "B2L":
            if not allow_b2l:
                continue

            # ✅ FIX: if hedge-only and no exposure, skip cleanly (this was the missing block)
            if b2l_hedge_only and exposure <= 0.0:
                continue

            # Tag hedge-only when applicable (non-breaking)
            if b2l_hedge_only:
                item["meta"] = {**item.get("meta", {}), "hedge_only": True}

            shaped.append(item)

        else:
            shaped.append(item)

    return shaped
# === PATCH END ===



# ─────────────────────────────────────────────────────────────────────────────
# Safe price provider for strategies (MIRROR → oc_series → optional DIRECT)
# ─────────────────────────────────────────────────────────────────────────────
import os, sqlite3, json
from typing import Optional, Tuple

def _adb_path() -> str:
    from engines.config_paths import autoscalp_db
    return autoscalp_db()

def _bdb_conn(ro: bool = True) -> sqlite3.Connection:
    from engines.config_paths import connect_db
    return connect_db(ro=ro)

def _adb_conn(timeout: float = 6.0) -> sqlite3.Connection:
    con = sqlite3.connect(_adb_path(), timeout=timeout)
    con.row_factory = sqlite3.Row
    return con

def get_price_from_sources(market_id: str, selection_id: str) -> Optional[float]:
    """
    Best-effort current price for (market_id, selection_id):
      1) inbound_oc_cache.oc1 (AUTO_DB, latest)
      2) oc_series.odd (BETS_DB, latest)
      3) DIRECT (Betfair) only if AUTOSCALP_ENABLE_DIRECT_ODDS=1
    Returns a float or None.
    """
    mid, sid = str(market_id), str(selection_id)

    # 1) inbound_oc_cache.oc1
    try:
        adb = _adb_conn()
        row = adb.execute(
            "SELECT oc1 FROM inbound_oc_cache WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
            (mid, sid)
        ).fetchone()
        adb.close()
        if row and row["oc1"] is not None:
            return float(row["oc1"])
    except Exception:
        pass

    # 2) oc_series.odd
    try:
        bdb = _bdb_conn(ro=True); bdb.row_factory = sqlite3.Row
        row = bdb.execute(
            "SELECT odd FROM oc_series WHERE marketId=? AND selectionId=? "
            "ORDER BY datetime(snapshot_ts) DESC LIMIT 1",
            (mid, sid)
        ).fetchone()
        bdb.close()
        if row and row["odd"] is not None:
            return float(row["odd"])
    except Exception:
        pass

    # 3) optional DIRECT odds (strict opt-in; silent on failure)
    if os.getenv("AUTOSCALP_ENABLE_DIRECT_ODDS", "0") != "1":
        return None
    try:
        import requests
        API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"
        from engines.daily_config import APP_KEY as _AK, get_session_token as _TOK
        headers = {
            "X-Application": _AK,
            "X-Authentication": _TOK(),
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = [{
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketBook",
            "params": {
                "marketIds": [mid],
                "priceProjection": {"priceData": ["EX_BEST_OFFERS"], "virtualise": True}
            },
            "id": 1
        }]
        r = requests.post(API_URL, headers=headers, data=json.dumps(payload), timeout=3)
        r.raise_for_status()
        resp = r.json()[0]
        if "error" in resp:
            return None
        books = resp.get("result") or []
        if not books:
            return None
        for ru in (books[0].get("runners") or []):
            if str(ru.get("selectionId")) == sid:
                ex = ru.get("ex") or {}
                lays = ex.get("availableToLay") or []
                backs = ex.get("availableToBack") or []
                price = (lays[0].get("price") if lays else None) or (backs[0].get("price") if backs else None)
                return float(price) if isinstance(price, (int, float)) else None
        return None
    except Exception:
        return None



# ---------- Context ----------
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/runner.py
# 📆 PATCHED: 2025-10-28Z — restore extended Instruction/StrategyCtx (no build_ctx change)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from dataclasses import dataclass, field
from typing import Optional, Callable, Dict, Any

@dataclass
class Instruction:
    side: str            # 'LAY' or 'BACK'
    hedge_ticks: int     # +ve ticks
    stake: float
    source: str
    meta: Dict[str, Any] = field(default_factory=dict)

@dataclass
class StrategyCtx:
    run_id: str
    market_id: str
    selection_id: str

    # --- Time / phase --------------------------------------------------
    phase: str                      # PRE | WAIT_INPLAY | IN_PLAY | ENDED | UNKNOWN
    minutes_to_off: Optional[float] # unified MTO (may be None)
    tto_s: Optional[int] = None     # seconds to off (sign encodes direction)
    tto_window: str = "UNK"         # e.g. "S3","S2","S1","60","120","120+"

    # --- Price / odds dynamics ----------------------------------------
    price: float = 0.0
    slope_per_min: float = 0.0
    up_ticks_10s: int = 0
    down_ticks_10s: int = 0
    tick_vel_1s_up: int = 0
    tick_vel_3s_up: int = 0
    range_pos: float = 0.0
    range_span_ticks: int = 0
    depth10s: int = 0
    liq_score: float = 0.0

    # --- Favourites & movement ----------------------------------------
    fav_rank_now: int = 0
    fav_rank_30s: int = 0
    price_30s_ago: float = 0.0
    price_5s_ago: float = 0.0
    movement: str = "steady"
    fav_tag: str = ""

    # --- Blueprint / story features -----------------------------------
    blueprint_key: Optional[str] = None
    blueprint_conf: Optional[float] = None
    rank_was: Optional[int] = None
    rank_now: Optional[int] = None
    anchor_odd: Optional[float] = None

    # --- WOM / market pressure ----------------------------------------
    wom_ratio: Optional[float] = None
    wom_back_6: Optional[float] = None
    wom_lay_6: Optional[float] = None

    # --- Liquidity & exposure -----------------------------------------
    size_cap: float = 0.0
    traded_recent_amt: float = 0.0
    traded_recent_sec: float = 9999.0
    used_exposure: float = 0.0
    bank: float = 0.0

    # --- Gating / control callbacks -----------------------------------
    cooldown_ok: Optional[Callable] = None
    parent_open_for_runner: Optional[Callable] = None
    parent_info: Optional[Callable] = None

    # --- Meta extensions -----------------------------------------------
    meta: Dict[str, Any] = field(default_factory=dict)
# === PATCH END ===

# Strategy family → letter (S reserved for legacy)
STRAT_CODE = {
    # Always-on and base
    "ALWAYS_ON": "A",
    "BLUEPRINTS": "P",
    "OG_STRATEGY": "S",
    "LADDER_STRATEGY": "L",

    # Breakout / Crossover / Steam families
    "S4_CROSSOVER": "X",
    "S5_BREAKOUT": "R",
    "S6_STEAM_FADE": "F",
    "BTL_SCOUT": "B",
    "BTL_AGGR": "G",

    # Market Liability Manager
    "MLM": "M",

    # In-Play strategies (IP1–IP5)
    "IP1_SHOCK_DRIFT": "I",
    "IP2_TIRED_LEADER": "T",
    "IP3_CLOSE_FINISH": "C",
    "IP4_FENCE_ERROR": "E",
    "IP5_COLLAPSE_FADE": "K",

    # Extra or deprecated
    "LEGACY": "Z",   # older OG bias
    "MASTER": "S",   # default fallback
}



# ── SourceTagger: per-day, per-letter counters persisted in app_kv ───────────
class SourceTagger:
    @staticmethod
    def _today_utc():
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _key(letter: str, day: str) -> str:
        return f"src_counter:{day}:{(letter or '').upper()[:1]}"

    @staticmethod
    def next_tag(letter: str) -> str:
        """Return letter+N (e.g., 'X7'), incrementing a per-day counter in app_kv."""
        from engines.config_paths import connect_db
        day = SourceTagger._today_utc()
        code = (letter or "S").upper()[:1]
        key = SourceTagger._key(code, day)

        with connect_db(ro=False) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
            row = conn.execute("SELECT value FROM app_kv WHERE key=?", (key,)).fetchone()
            n = int(row["value"]) + 1 if row and str(row["value"]).isdigit() else 1
            from datetime import datetime, timezone
            conn.execute(
                "INSERT INTO app_kv(key, value, updated_at) VALUES(?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, str(n), datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
            )
        return f"{code}{n}"


def _phase_tto(con: sqlite3.Connection, mid: str) -> tuple[str, int]:
    r = con.execute("""SELECT t0_phase, t0_sec FROM dashboard_markets
                       WHERE day=date('now','utc') AND marketId=? LIMIT 1""", (mid,)).fetchone()
    if not r: return "UNKNOWN", 0
    phase = (r["t0_phase"] or "UNKNOWN").upper()
    sec = int(r["t0_sec"] or 0)
    tto = sec if phase == "PRE" else (-sec if phase in ("IN_PLAY","WAIT_INPLAY") else 0)
    return phase, tto

def _rank_at(con: sqlite3.Connection, mid: str, when_sql: str) -> Dict[str,int]:
    # Rank by lowest odds; snapshot from oc_series closest to 'when_sql' window
    rows = con.execute(f"""
      SELECT selectionId, odd
        FROM oc_series
       WHERE marketId=? AND datetime(snapshot_ts) >= datetime({when_sql})
         AND date(snapshot_ts)=date('now','utc')
       ORDER BY snapshot_ts DESC
       LIMIT 200
    """, (mid,)).fetchall()
    # build last seen price per selection
    last: Dict[str,float] = {}
    for r in rows:
        sid = str(r["selectionId"]); last[sid] = float(r["odd"])
    # rank
    sorted_s = sorted(last.items(), key=lambda kv: kv[1])
    return {sid: i+1 for i, (sid, _) in enumerate(sorted_s)}

def _market_type_jumps(con: sqlite3.Connection, mid: str) -> bool:
    r = con.execute("""SELECT market_name FROM markets_schedule WHERE marketId=?""",(mid,)).fetchone()
    name = (r["market_name"] if r else "") or ""
    return any(k in name.upper() for k in ("HURDLE","CHASE","HUNTER","NATIONAL","JUMP"))

import math as _math

def _nz(v, default=0.0):
    """float(v) with NaN/None -> default."""
    try:
        f = float(v)
        return default if _math.isnan(f) else f
    except Exception:
        return default

def _metrics(con: sqlite3.Connection, mid: str, sid: str) -> Dict[str, Any]:
    day = _today()

    # last ~180 samples for basic features
    rows = con.execute("""
        SELECT odd, snapshot_ts FROM oc_series
         WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
         ORDER BY id DESC LIMIT 180
    """, (mid, sid, day)).fetchall()
    odds = [float(r["odd"]) for r in rows][::-1]
    price_now = odds[-1] if odds else float("nan")

    # slope ~ last minute (approx by sample count)
    if len(odds) > 1:
        dt_min = max(len(odds), 1) / 60.0
        slope = (odds[-1] - odds[0]) / dt_min
    else:
        slope = 0.0

    last10 = odds[-10:] if len(odds) >= 10 else odds
    downs = sum(1 for i in range(1, len(last10)) if last10[i] < last10[i-1])
    ups   = sum(1 for i in range(1, len(last10)) if last10[i] > last10[i-1])

    # 1s/3s tick velocity up (approx by last 1/3 samples)
    last1 = odds[-1:] if len(odds) >= 1 else odds
    last3 = odds[-3:] if len(odds) >= 3 else odds
    vel1 = sum(1 for i in range(1, len(last1)) if last1[i] > last1[i-1])
    vel3 = sum(1 for i in range(1, len(last3)) if last3[i] > last3[i-1])

    # intraday range
    rng = con.execute("""
        SELECT MIN(odd) AS lo, MAX(odd) AS hi FROM oc_series
         WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
    """, (mid, sid, day)).fetchone()
    lo = float(rng["lo"] if rng and rng["lo"] is not None else (price_now if price_now == price_now else 0.0))
    hi = float(rng["hi"] if rng and rng["hi"] is not None else (price_now if price_now == price_now else 0.0))
    span = abs(_ticks_between(lo, hi))
    range_pos = 0.5 if hi <= lo else (price_now - lo) / (hi - lo)

    # depth proxy: number of updates in last 10s
    depth10s = int(con.execute("""
        SELECT COUNT(*) FROM oc_series
         WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
           AND datetime(snapshot_ts) >= datetime('now','-10 seconds')
    """, (mid, sid, day)).fetchone()[0] or 0)
    liq_score = max(0.0, min(1.0, depth10s / 10.0))

    # earlier prices for pattern checks
    p30 = con.execute("""
        SELECT odd FROM oc_series
         WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
           AND datetime(snapshot_ts) >= datetime('now','-30 seconds')
         ORDER BY id ASC LIMIT 1
    """, (mid, sid, day)).fetchone()
    p5 = con.execute("""
        SELECT odd FROM oc_series
         WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
           AND datetime(snapshot_ts) >= datetime('now','-5 seconds')
         ORDER BY id ASC LIMIT 1
    """, (mid, sid, day)).fetchone()

    # updates in last 60s (proxy for traded amount)
    updates_60 = int(con.execute("""
        SELECT COUNT(*) FROM oc_series
         WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
           AND datetime(snapshot_ts) >= datetime('now','-60 seconds')
    """, (mid, sid, day)).fetchone()[0] or 0)

    # seconds since last tick
    last_row = con.execute("""
        SELECT snapshot_ts FROM oc_series
         WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=date(?)
         ORDER BY datetime(snapshot_ts) DESC LIMIT 1
    """, (mid, sid, day)).fetchone()
    age_sec = 9999
    if last_row and last_row["snapshot_ts"]:
        try:
            from datetime import datetime
            age_sec = max(0, int((
                datetime.utcnow() - datetime.strptime(last_row["snapshot_ts"][:19], "%Y-%m-%d %H:%M:%S")
            ).total_seconds()))
        except Exception:
            age_sec = 9999

    # sanitize NaNs / None
    price_now_s = _nz(price_now, 0.0)
    price_30 = _nz((p30["odd"] if p30 and p30["odd"] is not None else price_now_s), price_now_s)
    price_5  = _nz((p5["odd"]  if p5  and p5["odd"]  is not None else price_now_s), price_now_s)

    return {
        "price_now":         price_now_s,
        "slope_per_min":     float(slope),
        "down_ticks_10s":    int(downs),
        "up_ticks_10s":      int(ups),
        "tick_vel_1s_up":    int(vel1),
        "tick_vel_3s_up":    int(vel3),
        "range_pos":         float(range_pos),
        "range_span_ticks":  int(span),
        "depth10s":          int(depth10s),
        "liq_score":         float(liq_score),
        "price_30s_ago":     price_30,
        "price_5s_ago":      price_5,
        "traded_recent_amt": float(updates_60),
        "traded_recent_sec": float(age_sec),
    }

def _rank_now_and_30s(con: sqlite3.Connection, mid: str, sid: str) -> tuple[int,int]:
    now_ranks = _rank_at(con, mid, "'now','-2 seconds'")  # last couple seconds
    was_ranks = _rank_at(con, mid, "'now','-32 seconds'")
    return int(now_ranks.get(sid, 99)), int(was_ranks.get(sid, 99))

def _size_cap() -> float:
    try:
        return float(os.environ.get("AUTOSCALP_LIVE_STAKE_CAP", "4.00"))
    except Exception:
        return 4.0

def _cooldown_factory():
    last: Dict[str,float] = {}
    def ok(key: str, cd: int = 60) -> bool:
        now = time.time()
        t = last.get(key, 0.0)
        if now - t >= cd:
            last[key] = now
            return True
        return False
    return ok

def _parent_open(con: sqlite3.Connection, mid: str, sid: str) -> bool:
    r = con.execute("""SELECT 1 FROM orders
                        WHERE mode='LIVE' AND marketId=? AND selectionId=?
                          AND entry_status='matched'
                          AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED') LIMIT 1""",
                    (mid, sid)).fetchone()
    return bool(r)

def _parent_info(con: sqlite3.Connection, mid: str, sid: str) -> Dict[str,Any]:
    r = con.execute("""SELECT entry_stake, entry_odds FROM orders
                        WHERE mode='LIVE' AND marketId=? AND selectionId=?
                          AND entry_status='matched'
                          AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
                        ORDER BY opened_at DESC LIMIT 1""", (mid, sid)).fetchone()
    return {"stake": float(r["entry_stake"] or 0.0) if r else 0.0,
            "odds":  float(r["entry_odds"]  or 0.0) if r else 0.0}

def _orchestrator_mto(mid: str) -> tuple[Optional[float], str]:
    """
    Use the orchestrator's _compute_minutes_to_off() so strategies share
    the exact same MTO as legacy. Returns (minutes, window_label).
    """
    try:
        from engines.decision_engine.orchestrator import _compute_minutes_to_off, _current_source
        mto, win = _compute_minutes_to_off(str(mid), source=_current_source())
        try:
            return (float(mto), str(win))
        except Exception:
            return (None, "UNK")
    except Exception:
        return (None, "UNK")

# --- MTO helpers (AUTO schedule -> BETS schedule -> BETS.bets.marketStartTime) ---
import sqlite3
from datetime import datetime, timezone

def _mto_from_gui_schedule(mid: str) -> tuple[float | None, str]:
    """AUTO_DB (autoscalp_gui.db) markets_schedule.off_at_utc → (mto, window)."""
    try:
        from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
        con = _auto_conn(); con.row_factory = sqlite3.Row
        r = _q(con, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        con.close()
        if not (r and r["off_at_utc"]):  # none or empty
            return (None, "")
        off = datetime.fromisoformat(str(r["off_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        mto = (off - datetime.now(timezone.utc)).total_seconds() / 60.0
        w = ("120-80" if mto >= 120 else "80-60" if mto >= 80 else
             "60-40"  if mto >= 60  else "40-20" if mto >= 40 else
             "20-10"  if mto >= 20  else "10-5"  if mto >= 10 else
             "5-2"    if mto >= 5   else "2-0")
        return (float(mto), w)
    except Exception:
        return (None, "")

def _mto_from_bets_schedule(mid: str) -> tuple[float | None, str]:
    """BETS_DB markets_schedule.off_at_utc → (mto, window)."""
    try:
        from engines.config_paths import connect_db, q_retry as _q
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        r = _q(bdb, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        bdb.close()
        if not (r and r["off_at_utc"]):
            return (None, "")
        off = datetime.fromisoformat(str(r["off_at_utc"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        mto = (off - datetime.now(timezone.utc)).total_seconds() / 60.0
        w = ("120-80" if mto >= 120 else "80-60" if mto >= 80 else
             "60-40"  if mto >= 60  else "40-20" if mto >= 40 else
             "20-10"  if mto >= 20  else "10-5"  if mto >= 10 else
             "5-2"    if mto >= 5   else "2-0")
        return (float(mto), w)
    except Exception:
        return (None, "")

# ─────────────────────────────────────────────────────────────────────────────
# Minutes-to-off helper (simple: bets.marketStartTime only)
# ─────────────────────────────────────────────────────────────────────────────
def _mto_from_bets_table(mid: str) -> tuple[float | None, str]:
    """
    Compute minutes-to-off directly from bets.marketStartTime.
    Returns (mto_minutes, window_label) or (None, "UNK") if not found.
    """
    try:
        from engines.config_paths import connect_db
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        row = bdb.execute(
            "SELECT marketStartTime FROM bets "
            "WHERE marketId=? AND marketStartTime IS NOT NULL "
            "ORDER BY datetime(marketStartTime) ASC LIMIT 1",
            (str(mid),)
        ).fetchone()
        bdb.close()
        if not row or not row["marketStartTime"]:
            return (None, "UNK")

        off = datetime.fromisoformat(str(row["marketStartTime"]).replace("Z", "+00:00")).astimezone(timezone.utc)
        mto = (off - datetime.now(timezone.utc)).total_seconds() / 60.0

        # simple bucketing
        if   mto <= 0:  w = "INP"
        elif mto <= 5:  w = "S3"
        elif mto <= 10: w = "S2"
        elif mto <= 20: w = "S1"
        elif mto <= 60: w = "60"
        elif mto <= 120:w = "120"
        else:           w = "120+"

        return (float(mto), w)
    except Exception:
        return (None, "UNK")


# ─────────────────────────────────────────────────────────────────────────────
# Build strategy context
# ─────────────────────────────────────────────────────────────────────────────
def build_ctx(run_id: str, market_id: str, selection_id: str) -> StrategyCtx:
    con = _adb()
    try:
        phase, tto = _phase_tto(con, market_id)  # feeder binding (phase + raw seconds)

        # minutes-to-off from bets table only
        mto, win = _mto_from_bets_table(market_id)
        if mto is None:
            minutes_to_off, tto_window = 1e9, "UNK"
        else:
            minutes_to_off, tto_window = float(mto), str(win)

        # feature metrics
        m = _metrics(con, market_id, selection_id)
        rank_now, rank_30s = _rank_now_and_30s(con, market_id, selection_id)

        # build the context object
        ctx = StrategyCtx(
            run_id=run_id, market_id=market_id, selection_id=selection_id,
            phase=phase, tto_s=int(tto),
            minutes_to_off=minutes_to_off, tto_window=tto_window,
            price=m["price_now"], slope_per_min=m["slope_per_min"],
            down_ticks_10s=m["down_ticks_10s"], up_ticks_10s=m["up_ticks_10s"],
            tick_vel_1s_up=m["tick_vel_1s_up"], tick_vel_3s_up=m["tick_vel_3s_up"],
            range_pos=m["range_pos"], range_span_ticks=m["range_span_ticks"],
            depth10s=m["depth10s"], liq_score=m["liq_score"],
            fav_rank_now=rank_now, fav_rank_30s=rank_30s,
            price_30s_ago=m["price_30s_ago"], price_5s_ago=m["price_5s_ago"],
            is_jumps=_market_type_jumps(con, market_id),
            size_cap=_size_cap(),
            cooldown_ok=lambda *_: True,  # runner overwrites
            parent_open_for_runner=lambda: _parent_open(con, market_id, selection_id),
            parent_info=lambda: _parent_info(con, market_id, selection_id),
        )

        # enrich ctx with tape metrics
        try:
            setattr(ctx, "traded_recent_amt", float(m.get("traded_recent_amt", 0.0)))
            setattr(ctx, "traded_recent_sec", float(m.get("traded_recent_sec", 9999)))
        except Exception:
            pass

        return ctx
    finally:
        try:
            con.close()
        except Exception:
            pass

def _bets_mto(mid: str) -> tuple[float | None, str]:
    """
    Fallback minutes-to-off from BETS_DB.markets_schedule.
    Returns (mto_minutes, window_label) or (None, "") if not found.
    """
    try:
        import sqlite3
        from datetime import datetime, timezone
        from engines.config_paths import connect_db
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        r = bdb.execute("SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1",
                        (str(mid),)).fetchone()
        bdb.close()
        if not (r and r["off_at_utc"]):
            return (None, "")
        off = datetime.fromisoformat(str(r["off_at_utc"]).replace("Z","+00:00")).astimezone(timezone.utc)
        mto = (off - datetime.now(timezone.utc)).total_seconds() / 60.0
        # same bucketing you already use elsewhere
        w = ("120-80" if mto >= 120 else "80-60" if mto >= 80 else
             "60-40"  if mto >= 60  else "40-20" if mto >= 40 else
             "20-10"  if mto >= 20  else "10-5"  if mto >= 10 else
             "5-2"    if mto >= 5   else "2-0")
        return (float(mto), w)
    except Exception:
        return (None, "")


# ─────────────────────────────────────────────────────────────────────────────
# Per-strategy gates: spec + checker
# ─────────────────────────────────────────────────────────────────────────────
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any

@dataclass(frozen=True)
class GateSpec:
    code_letter: str                 # 'S', 'X', 'B', 'F', 'L', 'D', 'T', ...
    phase: str = "ANY"               # "PRE", "IN_PLAY", "ANY"
    tto_min_max: Optional[Tuple[float, float]] = None  # (lo, hi) in minutes
    odds_band: Optional[Tuple[float, float]] = None    # (lo, hi)
    min_liq_back: float = 0.0
    min_liq_lay: float = 0.0
    min_traded_amt_60s: float = 0.0
    max_tape_age_s: float = 9999.0
    cadence_sec: float = 0.0         # call at most once per cadence per runner
    cooldown_sec: float = 0.0        # per-runner strategy cooldown after place

def _ctx_float(ctx: Dict[str, Any], *keys, default: float = 0.0) -> float:
    for k in keys:
        try:
            v = ctx.get(k)
            if v is not None:
                return float(v)
        except Exception:
            pass
    return float(default)

# engines/decision_engine/strategies/common.py
import os
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any

# … keep your existing GateSpec dataclass …

# Light-gate toggle (default ON)
_LIGHT_GATES = (os.getenv("AUTOSCALP_LIGHT_GATES", "1") == "1")

def _runner_phase(ctx: Dict[str, Any]) -> str:
    p = str(ctx.get("phase") or "").upper()
    if p in ("PRE", "IN_PLAY", "WAIT_INPLAY"):
        return "IN_PLAY" if p in ("IN_PLAY", "WAIT_INPLAY") else "PRE"
    # derive from minutes_to_off when phase missing
    try:
        mto = float(ctx.get("minutes_to_off", ctx.get("tto_minutes")))
        return "IN_PLAY" if mto <= 0.0 else "PRE"
    except Exception:
        return "PRE"

def gate_check(ctx: Dict[str, Any], spec: "GateSpec") -> Tuple[bool, str]:
    """
    LIGHT mode (default): enforce only phase (and optional time window).
    STRICT mode (AUTOSCALP_LIGHT_GATES=0): enforce full set.
    """
    if _LIGHT_GATES:
        ph = _runner_phase(ctx)
        if spec.phase and spec.phase != "ANY" and ph != spec.phase:
            return (False, f"phase={ph}!={spec.phase}")
        if spec.tto_min_max:
            lo, hi = spec.tto_min_max
            try:
                mto = float(ctx.get("minutes_to_off", ctx.get("tto_minutes")))
            except Exception:
                return (False, f"tto=?∉[{lo},{hi}]")
            if not (lo <= mto <= hi):
                return (False, f"tto={mto:.1f}m∉[{lo},{hi}]")
        return (True, "ok")

    # STRICT (legacy) path
    ph = _runner_phase(ctx)
    if spec.phase and spec.phase != "ANY" and ph != spec.phase:
        return (False, f"phase={ph}!={spec.phase}")

    if spec.tto_min_max:
        lo, hi = spec.tto_min_max
        try:
            mto = float(ctx.get("minutes_to_off", ctx.get("tto_minutes")))
        except Exception:
            return (False, f"tto=?∉[{lo},{hi}]")
        if not (lo <= mto <= hi):
            return (False, f"tto={mto:.1f}m∉[{lo},{hi}]")

    if spec.odds_band:
        from engines.decision_engine.strategies.runner import safe_price
        px = safe_price(ctx)
        if px is None:
            return (False, "odds=?∉[{:.1f},{:.1f}]".format(*spec.odds_band))
        lo, hi = spec.odds_band
        if not (lo <= px <= hi):
            return (False, f"odds={px:.2f}∉[{lo},{hi}]")

    return (True, "ok")



