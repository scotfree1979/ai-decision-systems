from __future__ import annotations
import json, sqlite3, time, os
from typing import Dict, Tuple, Optional
from engines.config_paths import auto_conn as _auto_conn, auto_ro as _auto_ro, q_retry as _q
# top of file with the other config_paths imports:
from engines.config_paths import connect_db
from datetime import datetime, timezone, timedelta
# internal schedule clock (decoupled from dashboard)
try:
    from engines.decision_engine.schedule_clock import boot_bet_schedule as _sched_boot, minutes_to_off as _sched_mto
except Exception:
    _sched_boot = lambda *a, **k: 0
    def _sched_mto(mid: str):  # type: ignore
        return (None, "UNK", "CLOCK")

from typing import Optional, Dict, Any
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: ^from engines.config_paths import connect_db$
# ⛏️ ACTION: insert a thin shim that re-exports the helpers’ blueprint runner (both names)

# Re-export the helpers blueprint daily runner so GUI/other modules can find it here too.
try:
    from engines.decision_engine.decide_once.helpers import run_blueprints_if_needed as run_blueprints_if_needed  # noqa: F401
    from engines.decision_engine.decide_once.helpers import _run_blueprints_if_needed as _run_blueprints_if_needed  # noqa: F401
except Exception:
    # final fallback to local no-op to avoid NameError spam
    def run_blueprints_if_needed(force: bool = False) -> bool:
        import os
        print("[blueprints] helper warn: helpers.run_blueprints_if_needed unavailable")
        return False
    _run_blueprints_if_needed = run_blueprints_if_needed
# === PATCH END ===

import engines.mastery.mastery_policy as mp

# ---- Scope window state (module-level) ---------------------------------
_SCOPE_WINDOW_MAX = 5
_SCOPE_WINDOW: list[str] = []        # current in-scope marketIds (ordered)
_SCOPE_OVERRIDES: dict[str, float] = {}  # mid -> expiry_epoch (signals)
_SCOPE_CURSOR: int = 0               # ← rotate which market we start with each tick

from datetime import datetime, timezone
import datetime as dt  # only if you still reference dt.datetime elsewhere

from engines.mastery.context_builder import build_context_from_test_db
from engines.mastery.policy_lookup import get_bin_key
from engines.mastery.priors import upsert_prior

# NEW (same place)
from engines.decision_engine.decide_once.rules import rulebook_allow, apply_rulebook
from engines.decision_engine.decide_once.helpers import (
    # keep the rest from helpers as-is
    can_open_scalp, next_pair_tag, pass_tag, already_open_pass,
    # time / health
    utc_now, minute_bucket, status_once,
    # scope/schedule
    is_today_in_scope, build_scope, in_pre_window, in_ip_window,
    # db
    open_auto_db, auto_conn, tbl_exists,
    # prices/odds
    latest_price, latest_prices_for_market, tick_size, odds_plus_ticks, _runner_activity,
    # rulebook / caps / tags
    rulebook_allow, apply_rulebook, can_open_scalp,
    next_pair_tag, pass_tag, already_open_pass,
    # orders
    queue_order, place_decision, place_companion_hedge, set_order_status, mark_last_child_placed,
    # candidates (both names are available)
    active_candidates_for_market, _active_candidates_for_market,
    # telemetry/gates (no-ops unless wired)
    gate_bump, gate_snapshot,
    # misc compat
    _q_retry, log_event_once,
    # add to the big helpers import block:
    ensure_orders_link_col as _ensure_orders_link_col,
 
)
# --- BEGIN DB SHIM (no call-site changes) ---
import sqlite3
from engines.config_paths import auto_conn as __cp_auto_conn, q_retry as __cp_q_retry

# === PATCH START (EARLY LOAD) ===
# Canonical mode resolver - DAL is source of truth.
try:
    from engines.config_paths import DAL_MODE
except Exception:
    DAL_MODE = "TEST"

def _current_source() -> str:
    """
    EARLY-LOADED mode resolver.
    DAL is authoritative:
        LIVE DAL      -> LIVE
        TEST DAL      -> TEST
        SETUP/LEARNING -> LEARNING
    """
    try:
        dm = str(DAL_MODE).upper()
        if dm in ("LIVE", "TEST", "LEARNING", "SETUP"):
            # SETUP means LIVE about to start, but we treat SETUP as LIVE
            return "LIVE" if dm == "SETUP" else dm
    except Exception:
        pass

    # Fallback to upgrade_import_patch shim
    try:
        from engines.upgrade_import_patch import get_mode
        return str(get_mode()).upper()
    except Exception:
        return "TEST"
# === PATCH END (EARLY LOAD) ===


def auto_conn(*_args, **_kwargs):
    con = __cp_auto_conn()
    try: con.row_factory = sqlite3.Row
    except Exception: pass
    return con

def q_retry(obj, sql, params=(), *_a, **_k):
    con = getattr(obj, "connection", None) or obj
    return __cp_q_retry(con, sql, params)
# --- END DB SHIM ---

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# ⛏️ ACTION: target engines.blueprint_build.main(force)

def _blueprints_daily(force: bool = False) -> bool:
    """Run Blueprints once per UTC day with a simple AUTO.app_kv gate."""
    # daily gate
    import sqlite3
    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db(), timeout=6)
    con.execute("""
        CREATE TABLE IF NOT EXISTS app_kv(
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    day = con.execute("SELECT date('now')").fetchone()[0]
    key = f"blueprints_ran_{day}"

    if not force:
        if con.execute("SELECT 1 FROM app_kv WHERE k=?", (key,)).fetchone():
            print(f"[blueprints] already-ran {day}")
            con.close()
            return False

    # entrypoint we confirmed exists
    try:
        from engines.blueprint_build import main as bp_main
    except Exception as e:
        print(f"[blueprints] no entrypoint found: {e!r}")
        con.close()
        return False

    try:
        bp_main(force=bool(force))
        con.execute("""
            INSERT INTO app_kv(key, value, updated_at) VALUES(?, '1', datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value='1', updated_at=datetime('now')
        """, (key,))
        con.commit()
        print(f"[blueprints] ran {day}")
        con.close()
        return True
    except Exception as e:
        print(f"[blueprints] build-error: {e!r}")
        con.close()
        return False

# export both names for legacy callers
run_blueprints_if_needed = _blueprints_daily
_run_blueprints_if_needed = _blueprints_daily
# === PATCH END ===

# expose build_scope for legacy imports/tests
try:
    from engines.decision_engine.decide_once.helpers import build_scope as _build_scope  # noqa: F401
except Exception:
    def _build_scope(*_a, **_k):  # fail-open
        return {"pre_far": [], "pre_near": [], "in_play": [], "signals": []}

# --- RESILIENT SCOPE WRAP (fail-open to inbound) -----------------------------
# If the folderized build_scope returns nothing (no PRE/IP), use fresh inbound
# markets seen in the last few minutes so the lanes can try placements.
try:
    import engines.decision_engine.decide_once.helpers as _h
    _orig_build_scope = getattr(_h, "build_scope", None)

    def _rescue_scope(now_utc, inplay_window_min: int = 15):
        sc = _orig_build_scope(now_utc=now_utc, inplay_window_min=inplay_window_min)
        # Empty if both PRE and IP are missing (signals don’t count)
        empty = not (sc.get("pre_near") or sc.get("pre_far") or sc.get("in_play"))
        if not empty:
            return sc

        # Fallback: harvest recent tape markets and treat as PRE-near (TTO≈10m)
        try:
            mids = _fallback_markets_from_tape(max_mkts=8, within_sec=300)
        except Exception:
            mids = []
        return {
            "pre_near": [(m, 10.0) for m in mids],
            "pre_far":  [],
            "in_play":  [],
            "signals":  []
        }

    if callable(_orig_build_scope):
        def _build_scope_resilient(*, now_utc, inplay_window_min: int = 15):
            sc = None
            try:
                sc = _orig_build_scope(now_utc=now_utc, inplay_window_min=inplay_window_min)
            except Exception:
                sc = None
            if not (sc and (sc.get("pre_near") or sc.get("pre_far") or sc.get("in_play"))):
                sc = _rescue_scope(now_utc, inplay_window_min=inplay_window_min)
            return sc

        # Monkey-patch the helpers module so folderized decide_once uses our wrap
        _h.build_scope = _build_scope_resilient
except Exception as _e:
    # If anything goes wrong, just keep the original and let health logs tell us
    pass

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def step2_fetch_markets
# (inside step2_fetch_markets, after you have the daily plan list)
from engines.config_paths import connect_db as _bets_db, q_retry as _q

def _upsert_schedule_from_plan(plan_markets: list[dict]) -> None:
    """
    Hotfix: push daily plan markets into bets.db → markets_schedule.
    """
    try:
        bdb = _bets_db(ro=False); bdb.row_factory = sqlite3.Row
        cur = bdb.cursor()
        _q(cur, """
          CREATE TABLE IF NOT EXISTS markets_schedule(
            marketId TEXT PRIMARY KEY,
            market_name TEXT,
            event_name TEXT,
            off_at_utc TEXT,
            day TEXT
          )
        """)
        for m in plan_markets:
            mid  = str(m.get("marketId") or "")
            off  = str(m.get("off_at") or m.get("off_at_utc") or "")
            mname= str(m.get("market_name") or "")
            ename= str(m.get("event_name") or "")
            day  = off.split("T",1)[0] if "T" in off else (m.get("day") or "")
            if not mid or not off: 
                continue
            _q(cur, """
              INSERT INTO markets_schedule(marketId,market_name,event_name,off_at_utc,day)
              VALUES(?,?,?,?,?)
              ON CONFLICT(marketId) DO UPDATE SET
                market_name=excluded.market_name,
                event_name=excluded.event_name,
                off_at_utc=excluded.off_at_utc,
                day=excluded.day
            """, (mid, mname, ename, off, day))
        bdb.commit(); bdb.close()
    except Exception as e:
        print(f"[SCHEDULE][ERR] upsert failed: {e}")
# === PATCH END ===


def _can_open_scalp(market_id: str, selection_id: str, *,
                    max_per_runner: int,
                    run_id: str,
                    mode: str = "LIVE",
                    family_letter: str | None = None) -> tuple[bool, str]:
    """
    Router-facing CAP gate. Delegates to engines.mastery.risk.can_open_scalp.
    Returns (ok, reason). Conservative fallback = allow (with reason) if import fails.
    """
    try:
        from engines.mastery.risk import can_open_scalp as _risk_cap
        return _risk_cap(
            market_id, selection_id,
            max_per_runner=max_per_runner,
            run_id=run_id,
            family_letter=(family_letter or "")
        )
    except Exception as e:
        return True, f"fallback:{e}"


# ── Source / Mode resolver (TEST | LEARNING | LIVE) ──────────────────────────
_SOURCE_OVERRIDE: str | None = None


def _set_current_source_override(mode: str | None) -> None:
    """
    Force the current source for this process (mainly for tests).
    Pass None to clear. Stored UPPERCASE. Safe to call anytime.
    """
    global _SOURCE_OVERRIDE
    _SOURCE_OVERRIDE = (mode.upper() if mode else None)

# Optional public alias for modules that expect a non-underscored name
try:
    current_source  # type: ignore[name-defined]
except NameError:
    current_source = _current_source


# ---- UTC helpers (safe drop-in) --------------------------------------------
from datetime import datetime, timezone

def _utcnow():
    """tz-aware now in UTC; back-compat for older callsites."""
    return datetime.now(timezone.utc)

def _utcnow_str():
    return _utcnow().strftime("%Y-%m-%d %H:%M:%S")

# optional aliases if older code expects these names:
try: utcnow
except NameError: utcnow = _utcnow
try: UTCNOW
except NameError: UTCNOW = _utcnow
try: datetime_utcnow
except NameError: datetime_utcnow = _utcnow


# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: from __future__ import annotations
# ⛏️ ACTION: insert a small compat block after imports
# -- compat: is_today_in_scope (new name) + alias for old name used in this file
try:
    from engines.decision_engine.decide_once.helpers import is_today_in_scope  # new unified helper
except Exception:
    # fail-open (resolver/health will catch missing wiring)
    def is_today_in_scope(*_a, **_k) -> bool:
        return True

# back-compat alias for older code paths in this file
_is_today_in_scope = is_today_in_scope

# -- compat: _log_once_per_minute (needed by GUI import)
try:
    # if orchestrator already defines it later, this import is harmless; otherwise it provides the symbol
    _log_once_per_minute  # type: ignore[name-defined]
except NameError:
    def _log_once_per_minute(tag: str, now_fn=None, _state: dict | None = None):
        """Log suppressor: True only once per calendar minute per tag."""
        if _state is None:
            _state = {}
        import time
        now = int((now_fn or time.time)() // 60)
        last = _state.get(tag)
        if last != now:
            _state[tag] = now
            return True
        return False
# === PATCH END ===



# Letter map per family (cap key). Adjust to taste, just keep them distinct.
STRAT_CODE = {
    "ALWAYS_ON":         "A",
    "LEGACY_S":          "S",   # legacy scout
    "BTL_SCOUT":         "B",
    "BTL_AGGR":          "G",
    "S4_CROSSOVER":      "X",
    "S5_BREAKOUT":       "R",   # 'R' for breakout to avoid 'B' clash
    "S6_STEAM_FADE":     "F",
    "IP1_SHOCK_DRIFT":   "I",   # IP family
    "LADDER_STRATEGY":   "L",
    "OG_BIAS":           "Z",
    "IP2_TIRED_LEADER":  "T",
    "IP3_CLOSE_FINISH":  "C",
    "IP4_FENCE_ERROR":   "E",
    "IP5_COLLAPSE_FADE": "K",
}



# put near the other helpers
_OFF_SQL = "datetime(replace(replace(off_at_utc,'T',' '),'Z',''))"

# New: schedule connection (markets_schedule lives in AUTO_DB for LIVE)
def _schedule_conn() -> sqlite3.Connection:
    """
    Prefer AUTO_DB if it has markets_schedule rows for today; otherwise fall back to BETS_DB.
    """
    import sqlite3
    a = _auto_conn()
    a.row_factory = sqlite3.Row
    try:
        cnt = _q_retry(a, "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='markets_schedule'"
        ).fetchone()[0]
        if cnt:
            today = _q_retry(a, "SELECT COUNT(*) FROM markets_schedule WHERE substr(off_at_utc,1,10) IN (date('now'), date('now'))"
            ).fetchone()[0]
            if int(today or 0) > 0:
                return a
    except Exception:
        pass
    try:
        a.close()
    except Exception:
        pass
    # fallback → BETS_DB
    b = connect_db(ro=True)
    try:
        b.row_factory = sqlite3.Row
    except Exception:
        pass
    return b


def _dbg_sched_counts():
    a = _q_retry(_auto_conn(), "SELECT COUNT(*) FROM markets_schedule WHERE substr(off_at_utc,1,10)=date('now')").fetchone()[0]
    b = _q_retry(_bets_conn(), "SELECT COUNT(*) FROM markets_schedule WHERE substr(off_at_utc,1,10)=date('now')").fetchone()[0]
    print(f"[SCHEDULE] today: AUTO_DB={a} BETS_DB={b}")



import math as _math

# safe-int: robust cast with NaN/None handling
def _si(x, default=1):
    try:
        v = float(x)
        if _math.isnan(v):
            return int(default)
        return int(v)
    except Exception:
        try:
            return int(x)
        except Exception:
            return int(default)

# Backward compatibility: some paths still call _safe_int
_safe_int = _si

def _load_where_today_utc(bdb) -> list:
    return _q_retry(bdb, f"""
        WITH s AS (
          SELECT marketId, off_at_utc,
                 {_OFF_SQL} AS off_dt
          FROM markets_schedule
        )
        SELECT marketId, off_at_utc
        FROM s
        WHERE substr(off_at_utc,1,10) = date('now')
          AND (
                off_dt >= datetime('now')
             OR ((julianday('now'') - julianday(off_dt)) * 1440.0) <= 15.0
          )
        ORDER BY off_dt ASC
        """
    ).fetchall()


def _load_where_today_local(bdb) -> list:
    return _q_retry(bdb, f"""
        WITH s AS (
          SELECT marketId, off_at_utc,
                 {_OFF_SQL} AS off_dt
          FROM markets_schedule
        )
        SELECT marketId, off_at_utc
        FROM s
        WHERE substr(off_at_utc,1,10) = date('now')
          AND (
                off_dt >= datetime('now')   -- local now
             OR ((julianday('now') - julianday(off_dt)) * 1440.0) <= 15.0
          )
        ORDER BY off_dt ASC
        """
    ).fetchall()



def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        v = float(x)
        return default if (_math.isnan(v)) else v
    except Exception:
        return default

def _is_today_market(mid: str) -> bool:
    try:
        db = _bets_conn(); db.row_factory = sqlite3.Row
        row = _q_retry(db, """
            SELECT 1
            FROM markets_schedule
            WHERE marketId=? AND (
                   date(off_at_utc)=date('now')
                OR date(off_at_utc)=date('now')
            )
            """,
            (str(mid),)
        ).fetchone()
        return bool(row)
    except Exception:
        return False

# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def _is_today_market\(mid: str\) -> bool:
# REPLACE the whole function with the stronger in-scope predicate, and keep
# a back-compat alias pointing to it.
# ─────────────────────────────────────────────────────────────────────────────
def _sched_row_off(mid: str) -> Optional[datetime]:
    """
    OFF datetime (UTC) for marketId.
    Prefer _schedule_conn() (AUTO_DB) and fall back to BETS_DB.
    Returns a tz-aware UTC datetime, or None.
    """
    def _parse_off(s: str) -> Optional[datetime]:
        s = (s or "").strip()
        try:
            if s.endswith("Z"):
                return datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
            dtp = datetime.fromisoformat(s[:26])
            return (dtp if dtp.tzinfo else dtp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except Exception:
            pass
        try:
            return datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc)
        except Exception:
            return None

    # 1) schedule conn (AUTO_DB preference)
    try:
        db = _schedule_conn(); db.row_factory = sqlite3.Row
        r = _q_retry(db, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        if r and r["off_at_utc"]:
            off = _parse_off(str(r["off_at_utc"]))
            if off: return off
    except Exception:
        pass

    # 2) fallback: BETS_DB
    try:
        bdb = _bets_conn(); bdb.row_factory = sqlite3.Row
        r = _q_retry(bdb, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        if r and r["off_at_utc"]:
            return _parse_off(str(r["off_at_utc"]))
    except Exception:
        pass

    return None

def _in_scope_status(mid: str, *, inplay_window_min: int = 15) -> tuple[bool, str]:
    off = _sched_row_off(mid)
    if not off:
        return (False, "no_off")

    now = _utcnow()  # <— was _utcnow_str()

    is_today = (
        off.date() == now.date()
        or off.astimezone().date() == datetime.now().date()
    )
    if not is_today:
        return (False, "not_today")

    if off >= now:
        return (True, "ok")

    elapsed_min = (now - off).total_seconds() / 60.0
    return (True, "ok") if elapsed_min <= float(inplay_window_min) else (False, "elapsed_gt_window")



# Back-compat alias used elsewhere in the module
_is_today_market = is_today_in_scope

# ─────────────────────────────────────────────────────────────────────────────
# Scope dashboard + completed-market report
# ─────────────────────────────────────────────────────────────────────────────

def _today_schedule_all() -> list[tuple[str, str]]:
    db = _schedule_conn()  # << was _bets_conn()
    rows = _q_retry(db, "SELECT marketId, off_at_utc FROM markets_schedule "
        "WHERE substr(off_at_utc,1,10)=date('now')"
    ).fetchall()
    if not rows:
        rows = _q_retry(db, "SELECT marketId, off_at_utc FROM markets_schedule "
            "WHERE substr(off_at_utc,1,10)=date('now')"
        ).fetchall()
    return [(str(r['marketId']), str(r['off_at_utc'])) for r in rows or []]

def _completed_markets_today(inplay_window_min: int = 15) -> list[str]:
    """Markets from today's schedule whose OFF was > inplay_window_min minutes ago."""
    now = _utcnow()
    out: list[str] = []
    for mid, off_txt in _today_schedule_all():
        try:
            # parse off (reuse robust logic)
            s = off_txt.strip()
            if s.endswith("Z"):
                off = datetime.fromisoformat(s.replace("Z","+00:00")).astimezone(timezone.utc)
            else:
                dtp = datetime.fromisoformat(s[:26])
                off = (dtp if dtp.tzinfo else dtp.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
            if (now - off).total_seconds() / 60.0 > float(inplay_window_min):
                out.append(mid)
        except Exception:
            continue
    return out

# once-only status logger keyed by <module>:<state>
_STATUS_LAST = {}
def status_once(key: str, ok: bool, detail: str = ""):
    """
    Print a single line when a module flips state (FAIL->OK or OK->FAIL).
    'key' is 'module:thing'; ok=True means healthy.
    """
    prev = _STATUS_LAST.get(key)
    cur  = ("OK" if ok else "FALLBACK", detail or "")
    if prev != cur:
        print(f"[HEALTH] {key} => {cur[0]} {('— ' + cur[1]) if cur[1] else ''}")
        _STATUS_LAST[key] = cur


def _market_summary(mid: str) -> dict:
    """Return small report {parents, parents_closed, children, children_matched, pnl} for today."""
    con = _auto_conn(); con.row_factory = sqlite3.Row
    link = _ensure_orders_link_col(con)
    cols = cols = {r["name"] for r in _rows(con, "PRAGMA table_info(orders)")}
    role_col = "role" if "role" in cols else None
    mode_col = "mode" if "mode" in cols else None
    src = _current_source().upper()

    # predicates
    if role_col:
        parent_pred = "p.role='PARENT'"
        child_pred  = "p.role='CHILD'"
    else:
        parent_pred = f"(p.{link} IS NULL OR p.{link}='')"
        child_pred  = f"(p.{link} IS NOT NULL AND p.{link}<>'')"

    where_base = "p.marketId=? AND date(p.opened_at)=date('now')"
    args_base = [str(mid)]

    if mode_col:
        where_base += " AND UPPER(COALESCE(p.mode,''))=UPPER(?)"
        args_base.append(src)

    def _int(sql, args): return int(_q_retry(con, sql, args).fetchone()[0] or 0)
    def _flt(sql, args): 
        v = _q_retry(con, sql, args).fetchone()[0]; 
        return float(v if v is not None else 0.0)

    parents_total = _int(f"SELECT COUNT(*) FROM orders p WHERE {parent_pred} AND {where_base}", args_base)
    parents_closed= _int(f"SELECT COUNT(*) FROM orders p WHERE {parent_pred} AND {where_base} AND (p.closed_at IS NOT NULL AND p.closed_at<>'')", args_base)
    children_total= _int(f"SELECT COUNT(*) FROM orders p WHERE {child_pred}  AND {where_base}", args_base)
    children_match= _int(f"SELECT COUNT(*) FROM orders p WHERE {child_pred}  AND {where_base} AND UPPER(COALESCE(p.entry_status,''))='MATCHED'", args_base)
    pnl_parents   = _flt(f"SELECT COALESCE(SUM(net_pl),0.0) FROM orders p WHERE {parent_pred} AND {where_base}", args_base)

    return {
        "parents": parents_total,
        "parents_closed": parents_closed,
        "children": children_total,
        "children_matched": children_match,
        "pnl": pnl_parents,
    }

def _print_scope_dashboard(scope: dict, inplay_window_min: int = 15) -> None:
    try:
        now_s = int(_time.time())

        # TTO + elapsed maps from the current scope build
        tto_map = {m: t for (m, t) in (scope.get("pre_near", []) + scope.get("pre_far", []))}
        ip_map  = {m: e for (m, e) in scope.get("in_play", [])}

        # 5-market rolling window (the “All” you asked for)
        win5 = list(_SCOPE_WINDOW)

        # Extras inside T-20 that are NOT already in the window
        extras20 = [m for (m, t) in (scope.get("pre_near", []) or [])
                    if float(t) <= 20.0 and m not in win5]

        # In-play now
        in_play = [m for (m, _) in scope.get("in_play", [])]

        # Signal add-ons (may overlap others; that’s fine)
        signals = [m for (m, exp) in _SCOPE_OVERRIDES.items() if exp > now_s]

        # Completed (today, no longer in-play window)
        completed = _completed_markets_today(inplay_window_min)
        active_now = set(win5) | set(extras20) | set(in_play) | set(signals)
        completed  = [m for m in completed if m not in active_now]

        # Header
        print(
            f"[SCOPES] WIN5={len(win5)}  ADD≤20={len(extras20)}  "
            f"INPLAY={len(in_play)}  SIGNALS={len(signals)}  COMPLETED={len(completed)}"
        )

        # Lists (trim for readability)
        if win5:
            items = ", ".join(f"{m}({int(tto_map.get(m,999))}m)" for m in win5[:12])
            tail  = "" if len(win5) <= 12 else f" …+{len(win5)-12}"
            print(f"  WIN5: {items}{tail}")

        if extras20:
            items = ", ".join(f"{m}({int(tto_map.get(m,0))}m)" for m in extras20[:12])
            tail  = "" if len(extras20) <= 12 else f" …+{len(extras20)-12}"
            print(f"  ADD≤20: {items}{tail}")

        if in_play:
            items = ", ".join(f"{m}(+{int(ip_map.get(m,0))}m)" for m in in_play[:12])
            tail  = "" if len(in_play) <= 12 else f" …+{len(in_play)-12}"
            print(f"  INPLAY: {items}{tail}")

        if signals:
            # show TTL ~ seconds remaining for visibility
            items = ", ".join(f"{m}(+{int(_SCOPE_OVERRIDES[m]-now_s)}s)" for m in signals[:12])
            tail  = "" if len(signals) <= 12 else f" …+{len(signals)-12}"
            print(f"  SIGNALS: {items}{tail}")

        if completed:
            print("  COMPLETED:")
            for m in completed[:6]:
                rep = _market_summary(m)
                print(f"    {m}: parents={rep['parents']}/{rep['parents_closed']}  "
                      f"children={rep['children']}/{rep['children_matched']}  pnl=£{rep['pnl']:.2f}")
            if len(completed) > 6:
                print(f"    …+{len(completed)-6} more")

    except Exception:
        pass

def _fallback_markets_from_tape(max_mkts: int = 5, within_sec: int = 300) -> list[str]:
    """
    Recent marketIds seen in odds_current (updated_ts within the last N seconds).
    Ordered by freshness desc.
    """
    con = _auto_conn(); con.row_factory = sqlite3.Row
    try:
        rows = _q_retry(con, """
            SELECT DISTINCT marketId
              FROM odds_current
             WHERE updated_ts >= datetime('now','utc', ?)
             ORDER BY updated_ts DESC
             LIMIT ?
        """, (f'-{int(within_sec)} seconds', int(max_mkts))).fetchall() or []
        return [str(r['marketId']) for r in rows]
    finally:
        try: con.close()
        except Exception: pass


def refresh_scope_tape_once(inplay_window_min: int = 15) -> int:
    """
    Build scope; if empty, fall back to recent tape markets.
    Refresh odds_current for those markets and return upsert count.
    """
    try:
        sc = _build_scope(now_utc=_utcnow(), inplay_window_min=inplay_window_min) or {}
    except Exception:
        sc = {"pre_near": [], "pre_far": [], "in_play": [], "signals": []}

    # normal path
    n = _refresh_odds_cache_for_scope(sc)
    if n > 0:
        return n

    # fallback: use tape to seed the market list for this tick
    mids = _fallback_markets_from_tape(max_mkts=5, within_sec=300)
    if not mids:
        status_once("decide_once:tick", True, "scope empty — deferring to Mastery")
        mids = []  # still run Mastery on its own plan


    # prefer the folder helper if present
    try:
        from engines.decision_engine.decide_once.helpers import refresh_odds_current_for_markets as _refmkts
        return int(_refmkts(mids, max_runners=8))
    except Exception:
        pass

    # last resort: synthesize a temporary scope and reuse the local refresher
    fake_scope = {"pre_near": [(m, 999.0) for m in mids], "pre_far": [], "in_play": [], "signals": []}
    return int(_refresh_odds_cache_for_scope(fake_scope))


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: ^_SCOPE_CURSOR: int = 0
# (Place helper nearby the scope globals)
# ─────────────────────────────────────────────────────────────────────────────
def _advance_scope_cursor(step: int = 1, span: int | None = None) -> None:
    """Rotate the scope start index every tick, even if no placement occurs."""
    try:
        n = int(span or 0)
        if n <= 0:
            return
        cur = int(globals().get("_SCOPE_CURSOR", 0))
        globals()["_SCOPE_CURSOR"] = (cur + int(step)) % n
    except Exception:
        globals()["_SCOPE_CURSOR"] = 0


import time as _time

def _seed_scope_window(scope: dict) -> None:
    global _SCOPE_WINDOW
    if _SCOPE_WINDOW:
        return
    # prefer PRE-near first, then earliest PRE-far
    near = [m for (m, _t) in scope.get("pre_near", [])]
    far  = [m for (m, _t) in scope.get("pre_far",  [])]
    ordered = near + far
    _SCOPE_WINDOW = ordered[:_SCOPE_WINDOW_MAX]

def _prune_and_slide_window(scope: dict) -> None:
    global _SCOPE_WINDOW
    in_play = {m for (m, _e) in scope.get("in_play", [])}
    near    = [m for (m, _t) in scope.get("pre_near", [])]
    far     = [m for (m, _t) in scope.get("pre_far",  [])]
    sched   = near + far

    # 🔧 NEW: also require canonical in-scope
    # in _prune_and_slide_window(scope)
    _SCOPE_WINDOW = [
        m for m in _SCOPE_WINDOW
        if (m in sched or m in in_play) and _is_today_in_scope(m)
    ]
    for m in sched:
        if len(_SCOPE_WINDOW) >= _SCOPE_WINDOW_MAX: break
        if m not in _SCOPE_WINDOW and _is_today_in_scope(m):
            _SCOPE_WINDOW.append(m)


def promote_market_on_signal(market_id: str, ttl_s: int = 120) -> None:
    """Temporarily allow placements in an out-of-window market that raised a strong signal."""
    try:
        market_id = str(market_id)
    except Exception:
        return
    _SCOPE_OVERRIDES[market_id] = _time.time() + max(30, int(ttl_s))

def _trim_expired_overrides() -> None:
    now = _time.time()
    expired = [m for (m, texp) in _SCOPE_OVERRIDES.items() if texp <= now]
    for m in expired:
        _SCOPE_OVERRIDES.pop(m, None)


# --- PATCH 1/6: tape direction signal ---------------------------------------
# 🔎 SEARCH: def _safe_float(
def _steam_drift_signal(c: dict) -> str:
    """'steam' | 'drift' | 'flat' from slope, reinforced by recent tick balance."""
    try:
        s = float(c.get("slope_per_min", c.get("slope_ppm", 0.0)) or 0.0)
    except Exception:
        s = 0.0
    try:
        up10 = int(c.get("up_ticks_10s", 0)); dn10 = int(c.get("down_ticks_10s", 0))
        dv = (dn10 - up10) / max(1, (dn10 + up10))
    except Exception:
        dv = 0.0
    if (s < -0.03) or (s <= -0.015 and dv < -0.20): return "steam"
    if (s > +0.03) or (s >= +0.015 and dv > +0.20): return "drift"
    return "flat"

# --- PATCH 3/6: global _pf shim ---------------------------------------------
# 🔎 SEARCH: # --- GLOBAL DB ROW WRAPPER
def _pf_compat(name, plan_like, maybe_ctx=None):
    c = maybe_ctx if maybe_ctx is not None else globals().get("_LAST_CTX", {}) or {}
    try:
        self_obj = globals().get("_THIS_ORCHESTRATOR_SELF", None)
        if self_obj is not None and hasattr(self_obj, "_place_from_plan"):
            return self_obj._place_from_plan(name, plan_like, c)
    except Exception:
        pass
    try:
        return globals().get("_PLACE_FROM_PLAN_LOCAL", lambda *_a, **_k: None)(name, plan_like, c)
    except TypeError as e:
        try:
            return globals().get("_PLACE_FROM_PLAN_LOCAL", lambda *_a, **_k: None)(name, plan_like, c or {})
        except Exception:
            raise e

_pf = _pf_compat  # back-compat alias

def _log_gate_snapshot(mid: str, sid: str, ctx: dict) -> None:
    """
    Print one compact gate line: strat:OK or strat:<reason>.
    Only logs once per minute per (mid/sid).
    """
    try:
        from engines.decision_engine.strategies.registry import ORDER as ORDER_LIST
        from engines.decision_engine.strategies.registry import STRAT_GATES
        from engines.decision_engine.strategies.common import gate_check
    except Exception:
        return

    # minute throttle
    wall_min = int(_utcnow().timestamp() // 60)
    key = (str(mid), str(sid), wall_min)
    seen = getattr(_log_gate_snapshot, "_SEEN", set())
    if key in seen:
        return
    seen.add(key)
    setattr(_log_gate_snapshot, "_SEEN", seen)

    # ensure dict ctx + minutes_to_off is a float (never None)
    ctx_map = ctx if isinstance(ctx, dict) else (ctx.__dict__ if hasattr(ctx, "__dict__") else dict(ctx or {}))
    try:
        mto_raw = ctx_map.get("minutes_to_off", ctx_map.get("tto_minutes"))
        ctx_map["minutes_to_off"] = float(mto_raw) if mto_raw is not None else 1e9
    except Exception:
        ctx_map["minutes_to_off"] = 1e9

    bits = []
    for (name, _fn) in (ORDER_LIST or []):
        spec = STRAT_GATES.get(name)
        if spec is None:
            continue
        try:
            ok, why = gate_check(ctx_map, spec)
            tag = "OK" if ok else str(why)
            bits.append(f"{name}:{tag}")
        except Exception:
            bits.append(f"{name}:err")

    if bits:
        mto = ctx_map.get("minutes_to_off")
        print(f"[GATE] {mid}/{sid} mto={mto if mto is not None else '?'} | " + "  ".join(bits), flush=True)

# --- PATCH 5/6: scope-first driver ------------------------------------------
# 🔎 SEARCH: def _build_scope(
def _scope_pass(run_id: str, ctx: dict, logger=None) -> Optional[int]:
    """
    Scope-first driver.
    NOW: scans all in-scope markets/runners *this tick* (priority order) and
    places up to MAX_PLACEMENTS_PER_TICK parents instead of returning after the
    first placement. Returns the first placed id (for legacy callers) or None.
    """
    log = (logger or (lambda *a, **k: None))

    # ── tunables
    MAX_PLACEMENTS_PER_TICK = int(os.getenv("AUTOSCALP_MAX_PER_TICK", "99"))
    MAX_MARKETS = _SCOPE_WINDOW_MAX + 2
    MAX_RUNNERS = 3

    # Use DecideOnce scope (decoupled from dashboard)
    try:
        from engines.decision_engine.decide_once.scope import (
            build_and_maintain_scope as _do_scope,
            ordered_markets_for_tick as _ordered_markets_for_tick,
        )
        scope = _do_scope(inplay_window_min=15, show_dashboard=True)
        # Build maps for minutes-to-off directly from scope
        tto_map = {m: float(t) for (m, t) in (scope.get("pre_near", []) + scope.get("pre_far", []))}
        ip_map  = {m: float(e) for (m, e) in scope.get("in_play", [])}  # elapsed minutes
        ordered = _ordered_markets_for_tick(scope)
    except Exception:
        # Fallback: keep old local scope path if import fails (rare)
        scope = _build_scope(now_utc=_utcnow(), inplay_window_min=15) or {}
        _trim_expired_overrides()
        _seed_scope_window(scope)
        _prune_and_slide_window(scope)
        _print_scope_dashboard(scope, inplay_window_min=15)
        tto_map = {m: float(t) for (m, t) in (scope.get("pre_near", []) + scope.get("pre_far", []))}
        ip_map  = {m: float(e) for (m, e) in scope.get("in_play", [])}
        def _prio_key(m):
            t = float(tto_map.get(m, 1e9))
            return (0 if t <= 20.0 else 1, t)
        window    = list(_SCOPE_WINDOW)
        overrides = [m for m in _SCOPE_OVERRIDES.keys() if m not in window]
        ip_list   = [m for (m, _e) in scope.get("in_play", [])]
        ordered = sorted(window, key=_prio_key) + \
                  [m for (m, t) in (scope.get("pre_near", []) or []) if float(t) <= 20.0 and m not in window] + \
                  overrides + ip_list

    if not ordered:
        return None

    # round-robin start; we still rotate each tick, but we can place many per tick now
    n = len(ordered)
    start_idx = int(globals().get("_SCOPE_CURSOR", 0)) % n
    ordered = ordered[start_idx:] + ordered[:start_idx]

    # throttle (once per minute)
    try:
        wall_min = int(_utcnow().timestamp() // 60)
    except Exception:
        import time as _t
        wall_min = int(_t.time() // 60)

    _ATTEMPTED = getattr(_scope_pass, "_ATTEMPTED_MINUTE", set())
    last_wall = getattr(_scope_pass, "_ATTEMPTED_WALL", None)
    if last_wall != wall_min:
        _ATTEMPTED = set()
        setattr(_scope_pass, "_ATTEMPTED_MINUTE", _ATTEMPTED)
        setattr(_scope_pass, "_ATTEMPTED_WALL", wall_min)

    # registry
    try:
        from engines.decision_engine.strategies.registry import ORDER as ORDER_LIST, ENABLED as ORDER_ENABLED
    except Exception:
        ORDER_LIST, ORDER_ENABLED = [], {}
    ORDER_MAP = {nm: fn for (nm, fn) in (ORDER_LIST or [])}

    placed_first: Optional[int] = None
    placed_count: int = 0

    def _is_enabled(name: str) -> bool:
        try:
            return ORDER_ENABLED.get(name, True)
        except Exception:
            return True

    def _win_for_mto(v: float) -> str:
        f = float(v)
        if f <= 0.0: return "INP"
        if f <= 5.0: return "S3"
        if f <= 10.0: return "S2"
        if f <= 20.0: return "S1"
        if f <= 60.0: return "60"
        if f <= 120.0: return "120"
        return "120+"

    # iterate markets, then their active runners
    for mid in ordered[:MAX_MARKETS]:
        # minutes-to-off direct from DecideOnce scope maps
        if mid in tto_map:
            mto_fix = float(tto_map[mid])
            win_fix = _win_for_mto(mto_fix)
        elif mid in ip_map:
            mto_fix = -float(ip_map[mid])  # in-play elapsed → negative mto
            win_fix = "INP"
        else:
            mto_fix, win_fix = (1e9, "UNK")

        phase = "IN_PLAY" if mto_fix <= 0.0 else "PRE"

        # candidates (ACTIVE only — enforced inside helper)
        cands = _cands_from_inbound(mid, max_runners=12)
        if not cands:
            continue

        # normalize any row shape -> (sid, odds:float)
        # just before: for sid, odds in cands:
        pairs = []
        for row in cands:
            try:
                sid, px = row[0], row[1]       # tuple-ish
            except Exception:
                # dict-ish
                sid = row.get("selectionId") or row.get("sid") or row.get("runnerId")
                px  = row.get("px") or row.get("odd") or row.get("ltp") or row.get("price")
            if sid is None or px is None: 
                continue
            try:
                pairs.append((str(sid), float(px)))
            except Exception:
                continue
        if not pairs: 
            continue
        cands = pairs

        # runner RR
        global _RUNNER_CURSOR

        if "_RUNNER_CURSOR" not in globals():
            _RUNNER_CURSOR = {}
        r_n = len(cands)
        r_start = int(_RUNNER_CURSOR.get(mid, 0)) % r_n
        cands = cands[r_start:] + cands[:r_start]
        _RUNNER_CURSOR[mid] = (r_start + 1) % r_n

        picked = 0
        for sid, odds in cands:
            # working ctx (always dict fields)
            ctx["marketId"]       = mid
            ctx["selectionId"]    = sid
            ctx["odds"]           = float(odds)
            ctx["ltp"]            = float(odds)
            ctx["minutes_to_off"] = float(mto_fix)
            ctx["tto_minutes"]    = float(mto_fix)
            ctx["tto_window"]     = win_fix
            ctx["phase"]          = phase
            # 🔒 HARD STRATEGY ISOLATION (L included)
            # Blueprint data is ONLY valid for P
            if "blueprint_key" in ctx or "blueprint_conf" in ctx or "blueprint_match" in ctx:
                # We do not know the family yet, so default to stripping
                # P will reattach via overlay later
                ctx.pop("blueprint_key", None)
                ctx.pop("blueprint_conf", None)
                ctx.pop("blueprint_match", None)


            # one-line snapshot
            try:
                _log_gate_snapshot(mid, sid, dict(ctx))
         
            except Exception:
                pass

            # BEFORE A-lane plan:
            # (place this right after ctx[...] is built, before "A-lane tiny scalp")
            if phase == "PRE" and float(odds) > 8.0:
                picked += 1
                if picked >= MAX_RUNNERS:
                    break
                continue


            # ---------- A-lane tiny scalp (unchanged) ----------
            letter = "A"
            keyA = (mid, sid, letter, wall_min)
            if keyA in _ATTEMPTED:
                picked += 1
                if picked >= MAX_RUNNERS:
                    break
                continue
            _ATTEMPTED.add(keyA)
            setattr(_scope_pass, "_ATTEMPTED_MINUTE", _ATTEMPTED)

            direction = _direction_from_signal(ctx, fallback_odds=float(odds))
            ticks     = 2 if abs(float(ctx.get("slope_ppm") or 0.0)) >= 0.08 else 1

            size_cap  = 2.0
            try:
                from engines.blueprint.runtime import oc_presence
                t = int(oc_presence(mid, sid) or 0)
                base_cap = float(ctx.get("size_cap", 2.0) or 2.0)
                size_cap = (3.0 if t < 2 else 4.0 if t < 4 else 5.0)
                size_cap = min(size_cap, base_cap)
            except Exception:
                pass

            planA = {
                "enter": True, "direction": direction,
                "target_ticks": max(1, int(ticks)),
                "size": max(2.0, float(size_cap)),
                "why": f"A-lane dir={direction} sppm={float(ctx.get('slope_ppm') or 0.0):.3f}"
            }
            try:
                from engines.mastery import mastery_policy as mp
                planA = mp.plan_for_strategy("ALWAYS_ON", dict(ctx))  # pass dict into policy
            except Exception:
                pass

            if planA and planA.get("enter"):
                try:
                    from engines.blueprint.overlay import overlay_plan as _bp_overlay
                    
                    planA = _bp_overlay(mid, sid, ctx, planA)
                except Exception:
                    pass
                pid = _pf("ALWAYS_ON", planA, ctx)
                if pid:
                    placed_first = placed_first or pid
                    placed_count += 1
                    if placed_count >= MAX_PLACEMENTS_PER_TICK:
                        _advance_scope_cursor(1, n)
                        return placed_first

            # ---------- Registry families ----------
            ip_set     = {m for (m, _e) in scope.get("in_play", [])}
            elapsed_by = {m: e for (m, e) in scope.get("in_play", [])}

            for (fname, ffn) in (ORDER_LIST or []):
                letter = _strat_letter_for(fname)
                if not _is_enabled(fname):
                    logger(f"[{fname}] disabled by registry")
                    continue

                if letter == "A":
                    continue  # A handled above

                # PRE families — enforce ACTIVE band here too
                if letter in {"S","B","F","L","X","G"}:  # include BTL + crossover
                    if phase != "PRE":
                        continue
                    if float(odds) > 8.0:
                        continue
                    try:
                        if not (3.0 < float(mto_fix) <= 60.0):
                            continue
                    except Exception:
                        continue
                    pass_tag = _scout_pass_for_tto(float(mto_fix))
                    if not pass_tag:
                        continue
                # IN-PLAY families
                elif letter in {"I","D"}:
                    if mid not in ip_set:
                        continue
                    try:
                        elapsed = float(elapsed_by.get(mid, 0.0))
                    except Exception:
                        elapsed = 0.0
                    pass_tag = _pass_tag(letter, -max(1.0, elapsed))
                else:
                    pass_tag = _pass_tag(letter, None)

                keyF = (mid, sid, letter, wall_min)
                if keyF in _ATTEMPTED:
                    continue
                _ATTEMPTED.add(keyF)
                setattr(_scope_pass, "_ATTEMPTED_MINUTE", _ATTEMPTED)

                if _already_open_pass(mid, sid, pass_tag, mode=ctx.get("source","LIVE")):
                    continue
                ctx["pass_tag"] = pass_tag

                # === MP plan (one call) — pass dict into policy ===
                ctx_dict = dict(ctx)
                try:
                    plan2 = _mp_plan_for(fname, ctx_dict)
                except Exception:
                    plan2 = None

                if not plan2:
                    # alternates through MP
                    for alt in _alts_for(fname, ctx_dict, mid, sid, ORDER_MAP):
                        try:
                            plan2 = _mp_plan_for(alt, ctx_dict)
                        except Exception:
                            plan2 = None
                        if plan2:
                            fname = alt
                            break

                if not (plan2 and plan2.get("enter")):
                    continue

                # Blueprint overlay (best-effort)
                try:
                    from engines.blueprint.overlay import overlay_plan as _bp_overlay
                 
                    plan2 = _bp_overlay(mid, sid, ctx, plan2)
                except Exception:
                    pass

                # Rulebook final gate
                preview_code = _next_pair_tag(letter, mid)
                skip_this, plan2 = _apply_rulebook(letter, preview_code, ctx, plan2)
                if skip_this:
                    continue

                placed_here = _pf(fname, plan2, ctx)
                if placed_here:
                    placed_first = placed_first or placed_here
                    placed_count += 1
                    if placed_count >= MAX_PLACEMENTS_PER_TICK:
                        _advance_scope_cursor(1, n)
                        return placed_first

            # ---------- Legacy S-scout ----------
            if phase == "PRE" and 0.0 < float(mto_fix) <= 60.0 and 1.5 <= float(odds) <= 8.0:
                letter = "S"
                keyS = (mid, sid, letter, wall_min)
                if keyS in _ATTEMPTED:
                    picked += 1
                    if picked >= MAX_RUNNERS:
                        break
                    continue
                _ATTEMPTED.add(keyS)
                setattr(_scope_pass, "_ATTEMPTED_MINUTE", _ATTEMPTED)

                s_dir = _direction_from_signal(ctx, fallback_odds=float(odds))
                s_ticks = 2 if abs(float(ctx.get("slope_ppm") or 0.0)) >= 0.08 else 1
                s_plan = {"enter": True, "direction": s_dir, "target_ticks": s_ticks,
                          "size": max(2.0, min(size_cap, 5.0)), "why":"S-scout scope"}
                pid2 = _pf("OG_STRATEGY", s_plan, ctx)
                if pid2:
                    placed_first = placed_first or pid2
                    placed_count += 1
                    if placed_count >= MAX_PLACEMENTS_PER_TICK:
                        _advance_scope_cursor(1, n)
                        return placed_first

            picked += 1
            if picked >= MAX_RUNNERS:
                break

    _advance_scope_cursor(1, n)
    return placed_first

# ── Feature flags (defaults: Legacy=ON, NewStrats=OFF, Mastery=OFF)
import os
F_ENABLE_LEGACY   = (os.getenv("AUTOSCALP_ENABLE_LEGACY", "1") == "1")
F_ENABLE_STRATS   = (os.getenv("AUTOSCALP_ENABLE_NEW_STRATS", "1") == "1")
F_ENABLE_MASTERY  = (os.getenv("AUTOSCALP_ENABLE_MASTERY", "1") == "1")


# ── Persistent DB connections to avoid FD exhaustion ─────────────────────────
_AUTO_CON: sqlite3.Connection | None = None
_BETS_CON: sqlite3.Connection | None = None

# --- Safe SQLite open/exec used by decision path ---------------------
import sqlite3 as _sqlite, time as _time
from engines.config_paths import autoscalp_db

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: # --- Safe SQLite open/exec used by decision path ---------------------
# ⛏️ ACTION: insert just below that header (after _exec_retry/_commit_retry)
def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    """
    Resilient row getter:
      - executes via _q_retry (primary)
      - falls back to config_paths.q_retry if available
      - always returns a list (never a Statement/Cursor/None)
    """
    try:
        cur = _q_retry(con, sql, params)
        return cur.fetchall() or []
    except Exception:
        try:
            # fallback to canonical q_retry in config_paths if present
            from engines.config_paths import q_retry as _cp_q
            cur = _cp_q(con, sql, params)
            return cur.fetchall() or []
        except Exception:
            return []
# === PATCH END ===

def _exec_retry(con: _sqlite.Connection, sql: str, params=(), tries: int = 6, delay_s: float = 0.08):
    last = None
    for i in range(max(1, tries)):
        try:
            return _q_retry(con, sql, params)
        except _sqlite.OperationalError as e:
            last = e
            msg = str(e).lower()
            if (("locked" in msg) or ("unable to open database file" in msg)) and i < tries - 1:
                _time.sleep(delay_s * (i + 1)); continue
            raise
    raise last  # pragma: no cover

def _commit_retry(con: _sqlite.Connection, tries: int = 6, delay_s: float = 0.08):
    last = None
    for i in range(max(1, tries)):
        try:
            return con.commit()
        except _sqlite.OperationalError as e:
            last = e
            msg = str(e).lower()
            if (("locked" in msg) or ("unable to open database file" in msg)) and i < tries - 1:
                _time.sleep(delay_s * (i + 1)); continue
            raise
    raise last  # pragma: no cover

_SCHEMA_DONE = False
def _ensure_auto_schema_once() -> None:
    global _SCHEMA_DONE
    if _SCHEMA_DONE:
        return
    con = _auto_conn()
    try:
        # Ensure link column
        _ensure_orders_link_col(con)
        # Ensure P&L + seed (from legacy)
        _ensure_orders_pnl_col(con)
        # Ensure Betfair ids (bf_bet_id, customer_ref)
        _ensure_orders_betfair_cols(con)
        _ensure_orders_trade_cols(con)   # ← add this line
        _ensure_odds_current_ts(con)
    except Exception:
        pass
    _SCHEMA_DONE = True

# helper: best-effort 'next' marketId from dashboard_markets
def _next_market_id_today() -> Optional[str]:
    try:
        import sqlite3
        from engines.config_paths import autoscalp_db
        con = _auto_conn()
        row = _q_retry(con, "SELECT marketId FROM dashboard_markets "
            "WHERE day=date('now') AND is_next=1 "
            "ORDER BY datetime(last_refreshed_ts) DESC LIMIT 1"
        ).fetchone()
        con.close()
        return str(row["marketId"]) if row and row["marketId"] else None
    except Exception:
        return None

from engines.decision_engine.decide_once.decision_logger import log_decision as _log_decision
try:
    from engines.mastery.event_sink import on_decision as _mastery_on_decision
except Exception:
    def _mastery_on_decision(_payload):  # safe no-op if mastery sink not importable
        return

from datetime import datetime, timezone
def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def place_strategy_instruction(run_id: str, marketId: str, selectionId: str,
                               side: str, price: float, size: float,
                               hedge_ticks: int, source: str) -> bool:
    """
    Decisive per-letter CAP=3 gate + decision logging.
    """
    try:
        letter = (source or "")[:1].upper() if source else ""

        # --- HARD CAP (per runner, per letter) — log and RETURN IMMEDIATELY ---
        n_letter = _open_parents_count_letter(marketId, selectionId, letter, run_id=str(run_id))
        if n_letter >= 3:
            # correct, deterministic decision row
            try:
                _log_decision(
                    run_id=int(run_id),
                    marketId=str(marketId),
                    selectionId=str(selectionId),
                    decided_at_iso=_now_utc_iso(),
                    scalp_direction=None,
                    placement_outcome="not_placed",
                    why="cap_block_letter",
                    cap_state={"open_before": int(n_letter), "cap_limit": 3},
                    direction_bases=["cap_gate"],
                    order_id=None,
                    notes=f"CAP letter={letter}",
                )
                _mastery_on_decision({
                    "run_id": int(run_id),
                    "marketId": str(marketId),
                    "selectionId": str(selectionId),
                    "letter": letter,
                    "decided_at": _now_utc_iso(),
                    "scalp_direction": None,
                    "placement_outcome": "not_placed",
                    "why": "cap_block_letter",
                    "direction_bases": ["cap_gate"],
                })
            except Exception:
                pass
            return False
# === PATCH END ===

        # 2) Policy gate (kept) — may add other reasons; also decisive
        can_open, why = _can_open_scalp(
            marketId, selectionId, max_per_runner=3,
            run_id=run_id, family_letter=letter or None
        )
        if not can_open:
            try:
                _log_decision(
                    run_id=int(run_id),
                    marketId=str(marketId),
                    selectionId=str(selectionId),
                    decided_at_iso=_now_utc_iso(),
                    scalp_direction=None,
                    placement_outcome="not_placed",
                    why=str(why or "cap_block"),
                    cap_state={"open_before": _open_parents_count(marketId, selectionId, run_id=str(run_id)), "cap_limit": 3},
                    direction_bases=[],
                    order_id=None,
                    notes="policy cap",
                )
                _mastery_on_decision({
                    "run_id": int(run_id),
                    "marketId": str(marketId),
                    "selectionId": str(selectionId),
                    "letter": letter,
                    "decided_at": _now_utc_iso(),
                    "scalp_direction": None,
                    "placement_outcome": "not_placed",
                    "why": str(why or "cap_block"),
                    "direction_bases": [],
                })
            except Exception:
                pass
            return False

        # Hard CAP guard (global, per (mid,sid,letter,mode))
        n_letter = _open_parents_count_letter(marketId, selectionId, letter or "", run_id=None)
        if n_letter >= 3:
             try:
                 _log_decision(
                     run_id=int(run_id) if str(run_id).isdigit() else 0,
                     marketId=str(marketId), selectionId=str(selectionId),
                     decided_at_iso=_now_utc_iso(),
                     scalp_direction=None,
                     placement_outcome="not_placed",
                     why="cap_block_letter",
                     cap_state={"open_before": int(n_letter), "cap_limit": 3},
                     direction_bases=["cap_gate"],
                     order_id=None,
                     notes=f"CAP letter={letter}",
                     letter=letter,
                 )
             except Exception:
                 pass
             return False

        # 3) Queue parent (normal path)
        parent_id = queue_order(
            run_id=run_id,
            side=side.upper(),
            odds=float(price),
            stake=float(size),
            marketId=str(marketId),
            selectionId=str(selectionId),
            mode_override="LIVE",
        )
        # Persist the letter onto orders so future CAP checks can filter reliably
        try:
            con = _auto_conn()
            _q_retry(con, "UPDATE orders SET source=? WHERE id=?", (letter, int(parent_id)))
            con.commit()
        except Exception:
            pass

        direction = "LAY->BACK" if side.upper() == "LAY" else "BACK->LAY"
        place_companion_hedge(
            parent_id=parent_id,
            direction=direction,
            entry_odds=float(price),
            parent_stake=float(size),
            target_ticks=int(hedge_ticks),
            marketId=str(marketId),
            selectionId=str(selectionId),
            run_id=run_id,
        )
        try:
            set_order_status(parent_id, "live")
        except NameError:
            pass

        # 4) Log PLACED decision (+ Mastery echo)
        try:
            n_before = _open_parents_count_letter(marketId, selectionId, letter, run_id=str(run_id))
            _log_decision(
                run_id=int(run_id),
                marketId=str(marketId),
                selectionId=str(selectionId),
                decided_at_iso=_now_utc_iso(),
                scalp_direction=("lay_to_back" if side.upper()=="LAY" else "back_to_lay"),
                placement_outcome="placed",
                why="ok",
                cap_state={"open_before": n_before, "cap_limit": 3},
                direction_bases=[],
                order_id=int(parent_id),
                proposed_odds=float(price),
                proposed_stake=float(size),
                notes=source,
                letter=letter,           # keep normalization context
            )
            _mastery_on_decision({
                "run_id": int(run_id),
                "marketId": str(marketId),
                "selectionId": str(selectionId),
                "letter": letter,
                "decided_at": _now_utc_iso(),
                "scalp_direction": ("lay_to_back" if side.upper()=="LAY" else "back_to_lay"),
                "placement_outcome": "placed",
                "why": "ok",
                "direction_bases": [],
                "order_id": int(parent_id),
            })
        except Exception:
            pass

        return True
    except Exception:
        # keep loop resilient
        return False 
# put near other imports at top of file
import subprocess, sys, os, time
from engines.config_paths import repo_root


def _bets_conn() -> sqlite3.Connection:
    """Singleton connection to BETS_DB (pnl_trades, pnl_daily, mastery_*)."""
    global _BETS_CON
    if _BETS_CON is None:
        _BETS_CON = connect_db(ro=False)          # <— DO NOT call _bets_conn() here
        try: _q_retry(_BETS_CON, "PRAGMA journal_mode=WAL")
        except Exception: pass
        try: _BETS_CON.row_factory = sqlite3.Row
        except Exception: pass
    return _BETS_CON

def _close_conns():
    """Close both DBs at end-of-day (releases file descriptors)."""
    global _AUTO_CON, _BETS_CON
    for c in (_AUTO_CON, _BETS_CON):
        try:
            if c: c.close()
        except Exception:
            pass
    _AUTO_CON = None
    _BETS_CON = None

import threading, subprocess, sys, os, time

# Place near other imports at top of file:
from engines.config_paths import repo_root  # must return repo root path

def _auto_reconcile_worker(interval_s: int = 60):
    """
    Periodically reconcile orders -> pnl_trades/pnl_daily for LIVE.
    Runs in a daemon thread for the duration of the LIVE session.
    """
    script = os.path.join(repo_root(), "scripts", "reconcile_from_orders.py")
    env = os.environ.copy()
    while True:
        try:
            # call the same script you use in terminal
            subprocess.run([sys.executable, script, "LIVE"], check=False)
        except Exception:
            pass
        time.sleep(max(15, int(interval_s)))

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH (regex): ^from engines\.config_paths import
# 📆 PATCHED: 2025-08-25T10:03Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.config_paths import _ROOT, autoscalp_db  # <- ensure both are imported


def _latest_prices_from_inbound(mid: str) -> dict[str, float]:
    out: dict[str, float] = {}
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row
        rows = _q_retry(con, """
            SELECT selectionId, oc1, anchor_odd, oc1_band_json
            FROM inbound_oc_cache
            WHERE marketId=?
            ORDER BY id DESC
        """, (str(mid),)).fetchall()
        seen = set()
        for r in rows or []:
            sid = str(r["selectionId"])
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
    except Exception:
        pass

    # optional last-ditch: odds service
    if not out:
        try:
            from engines.odds.odds_service import current_market_prices as _os
            data = _os(str(mid)) or {}
            for k, v in (data.items() if isinstance(data, dict) else []):
                try: out[str(k)] = float(v)
                except Exception: pass
        except Exception:
            pass
    return out

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def _cands_from_inbound
# ⛏️ ACTION: replace function body

def _cands_from_inbound(mid: str, max_runners: int = 8) -> list[tuple[str, float]]:
    """
    Return all runners (sid, odds) for a market from inbound_oc_cache.
    No odds-based activity filter. Scope decides if market is in play.
    Falls back to bets DB anchors if inbound is empty.
    """
    con = _auto_conn(); con.row_factory = sqlite3.Row
    seen = set()
    pairs: list[tuple[str, float]] = []
    try:
        rows = _q_retry(con, """
            SELECT selectionId, oc1, anchor_odd
            FROM inbound_oc_cache
            WHERE marketId=?
            ORDER BY id DESC
        """, (str(mid),)).fetchall() or []
        for r in rows:
            sid = str(r["selectionId"])
            if sid in seen:
                continue
            seen.add(sid)
            px = r["oc1"] if r["oc1"] is not None else r["anchor_odd"]
            if px is not None:
                pairs.append((sid, float(px)))
        if pairs:
            return sorted(pairs, key=lambda t: (t[1], t[0]))[:max_runners]
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass

    # Fallback → bets DB anchors
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        rows = _q_retry(bdb, """
            SELECT selectionId, COALESCE(OC0, anchor_odd) AS px
            FROM bets WHERE marketId=?
        """, (str(mid),)).fetchall() or []
        pairs = [(str(r["selectionId"]), float(r["px"])) for r in rows if r["px"] is not None]
        bdb.close()
        return sorted(pairs, key=lambda t: (t[1], t[0]))[:max_runners]
    except Exception:
        return []
# === PATCH END ===



import sqlite3 as _sqlite3



# ---------------------------------------------------------------------------
# UTC helpers (do not monkey-patch datetime.datetime; just provide aliases)
def aware_utcnow():
    """Timezone-aware UTC now (datetime with tzinfo=UTC)."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

# Canonical aliases used across this module
try:
    utcnow
except NameError:
    utcnow = aware_utcnow

try:
    UTCNOW
except NameError:
    UTCNOW = aware_utcnow

# Optional convenience name for grep-replacing old call sites
try:
    datetime_utcnow
except NameError:
    datetime_utcnow = aware_utcnow




def _today_local_sql():
    # use local date because opened_at/closed_at are naive in this DB
    return "date('now')"

def _scout_pass_for_tto(tto_min: float | None) -> str | None:
    """
    PRE minute passes for legacy scouts:
      T-60..T-5 → 'S60'..'S05'
      else      → None
    """
    if not _in_pre_window(tto_min):
        return None
    return _pass_tag("S", tto_min)

def _already_placed_scout(market_id: str, selection_id: str, scout_pass: str, *, mode: str = "LIVE") -> bool:
    """
    True if an order with notes=scout_pass already exists today for this runner (by mode).
    """
    try:
        from engines.config_paths import autoscalp_db as _adb_path
        con = _auto_conn()
        row = _q_retry(con, f"SELECT COUNT(*) AS n FROM orders "
            f"WHERE marketId=? AND selectionId=? AND mode=? AND notes=? AND date(opened_at)={_today_local_sql()}",
            (str(market_id), str(selection_id), str(mode).upper(), str(scout_pass))
        ).fetchone()
        con.close()
        return int(row["n"] or 0) > 0
    except Exception:
        return False

def _mark_order_notes(order_id: int, scout_pass: str) -> None:
    """
    Save the scout tag into orders.notes for visibility/analytics.
    """
    try:
        from engines.config_paths import autoscalp_db as _adb_path
        con = _auto_conn()
        _q_retry(con, "UPDATE orders SET notes=? WHERE id=?", (str(scout_pass), int(order_id)))
        con.commit(); con.close()
    except Exception:
        pass

# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def _is_active_runner(
def _is_active_runner(market_id: str, selection_id: str, ctx: dict,
                      odds_min: float = 1.50, odds_max: float = 8.00) -> tuple[bool, str]:
    """
    Fast front-door ActiveGate:
      - odds band: odds_min ≤ current_odds ≤ odds_max
      - time: allow from ≤20m; >20m => too_early
      - liquidity: if ctx has l1/top3 fields, enforce; otherwise skip
    Returns (ok, reason_str_for_log).
    """
    # current odds (never None)
    try:
        current_odds = None
        if "entry_odds" in ctx and ctx["entry_odds"]:
            current_odds = float(ctx["entry_odds"])
        elif "current_odds" in ctx and ctx["current_odds"]:
            current_odds = float(ctx["current_odds"])
        else:
            px = latest_price(str(market_id), str(selection_id))
            last = px[0] if isinstance(px, tuple) else None
            current_odds = float(last) if last else None
    except Exception:
        current_odds = None

    if current_odds is None:
        return (False, "no_price")
    try:
        if not (float(odds_min) <= current_odds <= float(odds_max)):
            return (False, "inactive-odds")
    except Exception:
        return (False, "no_price")

    # time gate (minutes to off) — coerce and guard
    mto = None
    try:
        mto = ctx.get("minutes_to_off", ctx.get("tto_minutes"))
        if mto is None:  # last resort
            mto_win = _compute_minutes_to_off(str(market_id), source=_current_source())
            mto = float(mto if mto is not None else 1e9)
    except Exception:
        mto = 1e9

    if mto > 60.0:
        return (False, "too_early")

# --- REPLACE the minimal-liquidity block with:
    # minimal liquidity (block only if we know it's effectively zero)
    try:
        l1 = ctx.get("l1_available")
        t3 = ctx.get("top3_available")
        tr = ctx.get("traded_recent_amt")
        tr_age = ctx.get("traded_recent_sec")  # our normalizer set this to 9999.0 when unknown

        # If tape/book freshness is unknown, do NOT block here
        unknown_liq = (tr_age is None) or (float(tr_age) >= 9999.0)
        if not unknown_liq:
            # With fresh-ish tape: block only if both book and recent trade are truly zero
            if (l1 is not None and float(l1) <= 0.0) and \
               ((t3 is not None and float(t3) <= 0.0) or (tr is not None and float(tr) <= 0.0)):
                return (False, "no_liquidity")
    except Exception:
        pass

    return (True, "ok")

# --- PATCH END ----------------------------------------------------------

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def _log_event(level: str, source: str, message: str)
# ⛏️ ACTION: adjust INSERT target table to dashboard_events

def _log_event(level: str, source: str, message: str) -> None:
    """Lightweight event logger to autoscalp_gui.db.dashboard_events."""
    try:
        con = _auto_conn()
        _q_retry(con, "CREATE TABLE IF NOT EXISTS dashboard_events("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                      "ts TEXT, level TEXT, source TEXT, message TEXT)")
        _q_retry(con, "INSERT INTO dashboard_events(ts, level, source, message) VALUES(?,?,?,?)",
            (_utcnow_str(), level, source, message)
        )
        con.commit()
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


def _reconcile_ledger_on_launch() -> None:
    """
    Run scripts/reconcile_from_orders.py LIVE once at startup so daily ledger
    is fresh. Tiles still compute from orders during the session.
    """
    try:
        script = os.path.join(_ROOT, "scripts", "reconcile_from_orders.py")
        if not os.path.exists(script):
            _log_event("WARN", "orchestrator", f"reconcile script missing: {script}")
            return

        # Optional: skip if pnl_daily already has a LIVE bucket for today
        try:
            bdb_path = os.path.join(_ROOT, "Data", "bets.db")
            con = _auto_conn(); con.row_factory = sqlite3.Row
            row = _q_retry(con, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_daily'"
            ).fetchone()
            if row:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                exists = _q_retry(con, "SELECT COUNT(*) AS n FROM pnl_daily "
                    "WHERE source='LIVE' AND date(created_at)=date(?)",
                    (today + "T00:00:00Z",)
                ).fetchone()
                if exists and int(exists[0] or 0) > 0:
                    _log_event("INFO", "orchestrator", "reconcile skipped (LIVE pnl_daily exists for today)")
                    con.close()
                    return
            con.close()
        except Exception:
            pass

        # Run reconcile LIVE
        cmd = ["python3", script, "LIVE"]
        res = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True, timeout=120)
        if res.returncode == 0:
            _log_event("INFO", "orchestrator", f"reconcile LIVE ok: {res.stdout.strip()[:240]}")
        else:
            _log_event("ERROR", "orchestrator", f"reconcile LIVE failed rc={res.returncode}: {res.stderr.strip()[:240]}")
    except Exception as e:
        _log_event("ERROR", "orchestrator", f"reconcile launcher error: {e}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/decision_engine/orchestrator.py
# --- PATCH START: add startup diagnostics helper -----------------------
def _startup_diagnostics(run_id: str, *, logger=None) -> None:
    """
    Print + event-log a one-shot launch summary so we can see exactly
    what this run will read/write and which gates/policies are active.
    Safe: never raises.
    """
    import os, sqlite3
    from engines.config_paths import autoscalp_db, connect_db

    log = logger or (lambda msg: print(msg, flush=True))
    def _safe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    # Resolve mode
    try:
        from engines.upgrade_import_patch import get_mode  # type: ignore
        src = (get_mode() or os.environ.get("AUTOSCALP_MODE") or "TEST").upper()
    except Exception:
        src = (os.environ.get("AUTOSCALP_MODE") or "TEST").upper()
    if src not in ("TEST", "LEARNING", "LIVE"):
        src = "TEST"

    # Paths + counts
    auto_path = autoscalp_db()
    bets_path = None
    sched_cnt = series_markets = inbound_markets = 0
    next1 = next2 = None

    # Bets DB info (via PRAGMA to get the real file path)
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        row = _safe(lambda: _q_retry(bdb, "PRAGMA database_list;").fetchone())
        bets_path = row[2] if row and len(row) >= 3 else None
        # today schedule (future)
        sched_cnt = int(_safe(lambda: _q_retry(bdb, "SELECT COUNT(*) FROM markets_schedule "
            "WHERE date(off_at_utc)=date('now') "
            "AND datetime(off_at_utc)>=datetime('now')"
        ).fetchone()[0], 0) or 0)
        # today oc_series markets
        series_markets = int(_safe(lambda: _q_retry(bdb, "SELECT COUNT(DISTINCT marketId) FROM oc_series "
            "WHERE date(snapshot_ts)=date('now')"
        ).fetchone()[0], 0) or 0)
        # next two from schedule
        rows = _safe(lambda: _q_retry(bdb, "SELECT COALESCE(venue,course,event_name,market_name) AS course, off_at_utc, marketId "
            "FROM markets_schedule "
            "WHERE date(off_at_utc)=date('now') "
            "AND datetime(off_at_utc)>=datetime('now') "
            "ORDER BY datetime(off_at_utc) ASC LIMIT 2"
        ).fetchall(), []) or []
        if rows:
            r0 = rows[0]
            next1 = (str(r0["course"] or "-"), str(r0["off_at_utc"] or "-"), str(r0["marketId"]))
            if len(rows) > 1:
                r1 = rows[1]
                next2 = (str(r1["course"] or "-"), str(r1["off_at_utc"] or "-"), str(r1["marketId"]))
        bdb.close()
    except Exception:
        pass

    # Auto DB info
    orders_live_today = decisions_today = inbound_markets = 0
    adb = None
    try:
        adb = _auto_conn()
        adb.row_factory = sqlite3.Row

        # LIVE orders today
        orders_live_today = int(_safe(
            lambda: _q_retry(
                adb,
                "SELECT COUNT(*) FROM orders WHERE mode='LIVE' AND date(opened_at)=date('now')"
            ).fetchone()[0],
            0
        ) or 0)

        # decisions today
        if _safe(lambda: _q_retry(adb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'").fetchone()):
            cols = [c[1] for c in _q_retry(adb, "PRAGMA table_info(decisions)")]
            col = "decided_at" if "decided_at" in cols else ("opened_at" if "opened_at" in cols else None)
            if col:
                decisions_today = int(_safe(
                    lambda: _q_retry(adb, f"SELECT COUNT(*) FROM decisions WHERE date({col})=date('now')").fetchone()[0],
                    0
                ) or 0)

        # inbound markets today
        if _safe(lambda: _q_retry(adb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_oc_cache'").fetchone()):
            inbound_markets = int(_safe(
                lambda: _q_retry(
                    adb,
                    "SELECT COUNT(DISTINCT marketId) FROM inbound_oc_cache WHERE date(last_sync_ts)=date('now')"
                ).fetchone()[0],
                0
            ) or 0)
    except Exception:
        pass
    finally:
        try:
            if adb:
                adb.close()
        except Exception:
            pass

    # Print banner
    log(f"[RUNCFG] mode={src} run_id={run_id}")
    log(f"[RUNCFG] AUTO_DB={auto_path}")
    if bets_path:
        log(f"[RUNCFG] BETS_DB={bets_path}")
    log(f"[RUNCFG] today: schedule_future={sched_cnt} oc_series_markets={series_markets} inbound_markets={inbound_markets}")
    if next1:
        log(f"[RUNCFG] next1: course={next1[0]} off={next1[1]} mid={next1[2]}")
    if next2:
        log(f"[RUNCFG] next2: course={next2[0]} off={next2[1]} mid={next2[2]}")
    log(f"[RUNCFG] orders_live_today={orders_live_today} decisions_today={decisions_today}")

    # ActiveGate and scout policy
    log("[RUNCFG] ActiveGate: odds_band=[1.50,8.00] liq: L1>=50 top3>=200 time<=20m")
    log("[RUNCFG] Scouts: S1<=20m, S2<=10m, S3<=5m; max 3 per runner (cap enforced)")
    log("[RUNCFG] Hedge: PERSIST (we do not cancel), auto-rehedge sweep enabled if scheduled")

    # Router DB path (logs into Events)
    try:
        from engines.live.live_router import _log_db_path_once  # type: ignore
        _log_db_path_once()
    except Exception:
        pass

    # Also write a single event so it’s in the dashboard log
    try:
        _log_event(
            "INFO",
            "DecisionEngine",
            f"RUNCFG | mode={src} run_id={run_id} AUTO_DB={auto_path} BETS_DB={bets_path} "
            f"sched_future={sched_cnt} series_markets={series_markets} inbound_markets={inbound_markets}",
        )
    except Exception:
        pass

    # Table presence + naive-time hint (safe)
    try:
        bdb2 = connect_db(ro=True)
        bdb2.row_factory = sqlite3.Row
        sch_has = bool(_safe(
            lambda: _q_retry(bdb2, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='markets_schedule'").fetchone(),
            None
        ))
        ser_has = bool(_safe(
            lambda: _q_retry(bdb2, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oc_series'").fetchone(),
            None
        ))
        bdb2.close()
        log(f"[RUNCFG] tables: schedule={'yes' if sch_has else 'no'} oc_series={'yes' if ser_has else 'no'}")
        naive_hint = bool(sch_has and next1 and 'Z' not in (next1[1] or ''))
        log(f"[RUNCFG] note: schedule_times_naive={'yes' if naive_hint else 'no'} (naive→use local date/time)")
    except Exception:
        pass


# --- PATCH END ----------------------------------------------------------



# --- GLOBAL DB ROW WRAPPER ----------------------------------------------------
# Guarantee every connect_db() across the process returns Row-backed connections
# so code that uses row["col"] never fails with tuple indexing errors.
import sqlite3 as _sqlite3
try:
    import engines.config_paths as _cp
    if not getattr(_cp, "_ROW_WRAPPED", False):
        _orig_connect_db = _cp.connect_db
        def _row_connect_db(*args, **kwargs):
            conn = _orig_connect_db(*args, **kwargs)
            try:
                conn.row_factory = _sqlite3.Row
            except Exception:
                pass
            return conn
        _cp.connect_db = _row_connect_db  # monkey-patch once
        _cp._ROW_WRAPPED = True
except Exception:
    pass
# ------------------------------------------------------------------------------

# ┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
# ┃ 📍 TARGET: engines/decision_engine/orchestrator.py                   ┃
# ┃ 🔎 SEARCH: def _place_from_plan(_name: str, _plan_like, _ctx: dict)  ┃
# ┃         -> Optional[int]:                                            ┃
# ┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
def _place_from_plan(_name: str, _plan_like, _ctx: dict) -> Optional[int]:
    """
    Thin delegator to the canonical decide_once/placement.place_from_plan.
    Keeps this function's name/signature for orchestrator callers.
    """
    from engines.decision_engine.decide_once.placement import place_from_plan as _do_place_from_plan

    # Coerce plan-like to dict if needed (preserves current tolerance)
    if isinstance(_plan_like, dict):
        plan = _plan_like
    elif hasattr(_plan_like, "__dict__"):
        plan = dict(_plan_like.__dict__)
    else:
        plan = {"_raw": _plan_like}  # last-resort wrapper

    ctx = dict(_ctx or {})
    return _do_place_from_plan(_name, plan, ctx)

# ⬇️ ADD THIS LINE RIGHT HERE (so _pf uses the canonical placement)
_PLACE_FROM_PLAN_LOCAL = _place_from_plan

# Compatibility alias for modules that import from orchestrator
def place_from_plan(name: str, plan: dict, ctx: dict) -> Optional[int]:
    """
    Public alias so callers like decide_once/lanes.py can
    `from engines.decision_engine.decide_once.placement import place_from_plan
    Delegates to the underscore version above (which already delegates to canonical).
    """
    bias      = plan.get("bias", 0.0)
    bias_dir  = plan.get("bias_dir", "FLAT")
    bias_conf = plan.get("bias_conf", 0.0)
    return _place_from_plan(name, plan, ctx)
# ─────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Priors bootstrap
# ─────────────────────────────────────────────────────────────────────────────
# _bootstrap_priors_for_ctx
def _bootstrap_priors_for_ctx(ctx: dict) -> None:
    bin_key = get_bin_key(ctx["distance_band"], ctx["code"], ctx["tto_window"],
                          ctx["class_band"], ctx["fav_rank_bin"])
    db = _bets_conn()
    row = _q_retry(db, "SELECT prior_p1_alpha, prior_p1_beta FROM mastery_priors WHERE bin_key=?",
        (bin_key,)
    ).fetchone()
    if row and float(row[0] or 0) + float(row[1] or 0) >= 20:
        return
    upsert_prior(db, bin_key, (55.0,20.0), (45.0,25.0), (35.0,25.0), (40.0,10.0), 1.2, 0.5)
    db.commit()

def _bootstrap_priors_if_empty() -> None:
    bin_key = "5-7f|FLAT|30-10|mid|fav"
    db = _bets_conn()
    row = _q_retry(db, "SELECT prior_p1_alpha, prior_p1_beta FROM mastery_priors WHERE bin_key=?",
        (bin_key,)
    ).fetchone()
    if row and (float(row[0] or 0) + float(row[1] or 0) >= 20):
        return
    upsert_prior(db, bin_key, (55.0,20.0), (45.0,25.0), (35.0,25.0), (40.0,10.0), 1.2, 0.5)
    db.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Run/day ledger (BETS_DB)
# ─────────────────────────────────────────────────────────────────────────────
def _ensure_ledger_tables():
    db = _bets_conn()
    db.row_factory = sqlite3.Row

    # Create daily ledger + sim runs (idempotent)
    _q_retry(db, """
    CREATE TABLE IF NOT EXISTS pnl_daily (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_day INTEGER NOT NULL,
      month_index INTEGER NOT NULL,
      amount REAL NOT NULL,
      created_at TEXT NOT NULL,
      run_id TEXT,
      source TEXT
    )""")
    _q_retry(db, """
    CREATE TABLE IF NOT EXISTS sim_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_day INTEGER NOT NULL,
      month_index INTEGER NOT NULL,
      run_id TEXT NOT NULL UNIQUE,
      started_at TEXT NOT NULL,
      ended_at TEXT
    )""")

    # Ensure mastery tables (idempotent)
    _q_retry(db, """
    CREATE TABLE IF NOT EXISTS mastery_state (
      id INTEGER PRIMARY KEY CHECK (id=1),
      progress INTEGER DEFAULT 0,
      thresholds_json TEXT DEFAULT '{}',
      volatility_json TEXT DEFAULT '{}',
      liquidity_json  TEXT DEFAULT '{}',
      time_windows_json TEXT DEFAULT '{}',
      exit_policy_json  TEXT DEFAULT '{}',
      confidence_json   TEXT DEFAULT '{}',
      updated_at TEXT
    )""")
    _q_retry(db, """
    CREATE TABLE IF NOT EXISTS mastery_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_type TEXT NOT NULL,
      details_json TEXT,
      delta_progress INTEGER DEFAULT 0,
      source TEXT,
      created_at TEXT DEFAULT (datetime('now'))
    )""")
    if not _q_retry(db, "SELECT 1 FROM mastery_state WHERE id=1").fetchone():
        _q_retry(db, "INSERT INTO mastery_state(id,progress,updated_at) VALUES (1,0,datetime('now'))")

    # Create pnl_trades with source (idempotent)
    _q_retry(db, """
    CREATE TABLE IF NOT EXISTS pnl_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      amount REAL,
      created_at TEXT,
      settled_at TEXT,
      source TEXT
    )""")

    # --- Schema normalisation: add missing columns & backfill 'source' ---
    def _cols(tab: str) -> set[str]:
        return {r["name"] for r in _rows(db, f"PRAGMA table_info({tab})")}

    src = _current_source()

    # pnl_trades: ensure 'source'
    c = _cols("pnl_trades")
    if "source" not in c:
        _q_retry(db, "ALTER TABLE pnl_trades ADD COLUMN source TEXT")
    _q_retry(db, "UPDATE pnl_trades SET source=? WHERE source IS NULL OR source=''", (src,))

    # pnl_daily: ensure 'source'
    c = _cols("pnl_daily")
    if "source" not in c:
        _q_retry(db, "ALTER TABLE pnl_daily ADD COLUMN source TEXT")
    _q_retry(db, "UPDATE pnl_daily SET source=? WHERE source IS NULL OR source=''", (src,))

    db.commit()

def _next_run_day() -> int:
    db = _bets_conn()
    row = _q_retry(db, "SELECT COALESCE(MAX(run_day),0) FROM pnl_daily").fetchone()
    return int((row[0] if isinstance(row, tuple) else row[0]) or 0) + 1

def _start_run_ledger(run_id: str) -> tuple[int, int]:
    _ensure_ledger_tables()
    db = _auto_conn()
    db.row_factory = sqlite3.Row
    row = _q_retry(db, "SELECT run_day, month_index FROM sim_runs WHERE run_id=?", (run_id,)).fetchone()
    if row:
        return int(row["run_day"]), int(row["month_index"])
    run_day = _next_run_day()
    month_index = 1 + (run_day - 1) // 30
    _q_retry(db, "INSERT INTO sim_runs (run_day, month_index, run_id, started_at) "
        "VALUES (?,?,?, datetime('now'))",
        (run_day, month_index, run_id)
    )
    db.commit()
    return run_day, month_index

def _end_run_ledger(run_id: str, amount: float, run_day: int, month_index: int):
    db = _bets_conn()
    src = _current_source()
    _q_retry(db, "INSERT INTO pnl_daily (run_day, month_index, amount, created_at, run_id, source) "
        "VALUES (?,?,?, datetime('now'), ?, ?)",
        (run_day, month_index, float(amount), run_id, src)
    )
    _q_retry(db, "UPDATE sim_runs SET ended_at=datetime('now') WHERE run_id=?", (run_id,))

    # Mastery: record a run_complete event + bounded progress delta
    try:
        delta = 0
        if amount > 0.0:
            # +1 to +5 depending on P&L size (tunable, safe)
            if   amount >= 50: delta = 5
            elif amount >= 20: delta = 3
            else:              delta = 1
        elif amount < 0.0:
            # small negative progression on losing day (optional)
            if   amount <= -50: delta = -3
            elif amount <= -20: delta = -2
            else:               delta = -1
        _q_retry(db, "INSERT INTO mastery_events(event_type, details_json, delta_progress, source) "
            "VALUES ('run_complete', ?, ?, ?)",
            (json.dumps({"run_id": run_id, "pnl": float(amount), "run_day": run_day}), int(delta), src)
        )
        if delta != 0:
            _q_retry(db, "UPDATE mastery_state "
                "SET progress = MIN(100, MAX(0, progress + ?)), updated_at=datetime('now') "
                "WHERE id=1",
                (int(delta),)
            )
    except Exception:
        pass

    db.commit()

def _sum_pnl_trades_test() -> float:
    db = _bets_conn()
    exists = _q_retry(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_trades'").fetchone()
    if not exists:
        return 0.0
    has_src = any((r[1] if isinstance(r, tuple) else r["name"]) == 'source' for r in _rows(db, "PRAGMA table_info('pnl_trades')"))
    sql = ("SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE source='TEST'"
           if has_src else "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades")
    return float(_q_retry(db, sql).fetchone()[0] or 0.0)

# ─────────────────────────────────────────────────────────────────────────────
# Reset helpers
# ─────────────────────────────────────────────────────────────────────────────
_TTO_BUCKETS: list[tuple[int, str]] = [
    (120, "120-80"),
    (80,  "80-60"),
    (60,  "60-40"),
    (40,  "40-20"),
    (20,  "20-10"),
    (10,  "10-5"),
    (5,   "5-2"),
    (2,   "2-0"),
]

def _window_from_minutes(mto_minutes: float) -> str:
    for thr, label in _TTO_BUCKETS:
        if mto_minutes >= thr:
            return label
    return "2-0"

def _compute_minutes_to_off(market_id: str, source: Optional[str] = None) -> tuple[Optional[float], str]:
    """
    Return (minutes_to_off, window), preferring our internal schedule clock (BETS DB).
    Always returns a float or None for minutes; window is one of INP/S3/S2/S1/60/120/120+/UNK.
    """
    try:
        _sched_boot(False)
        m, win, _src = _sched_mto(str(market_id))
        if m is not None:
            return (float(m), str(win))
    except Exception:
        pass

    # Final safe fallback (no dashboard dependency): treat as unknown long-dated
    # so comparisons are safe floats and gates will block by window where needed.
    return (1e9, "UNK")



def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    """
    True if 'col' exists on 'table'. Works with tuple rows or sqlite3.Row.
    """
    try:
        for r in _rows(conn, f"PRAGMA table_info({table})"):
            # PRAGMA table_info returns: (cid, name, type, notnull, dflt_value, pk)
            name = r[1] if isinstance(r, tuple) else r["name"]
            if name == col:
                return True
        return False
    except Exception:
        return False

def reset_pnl_ledger(logger=None) -> None:
    db = _bets_conn()
    # pnl_daily
    if _q_retry(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_daily'").fetchone():
        has_src = any((r[1] if isinstance(r, tuple) else r["name"])=='source' for r in _rows(db, "PRAGMA table_info('pnl_daily')"))
        _q_retry(db, "DELETE FROM pnl_daily" + (" WHERE source='TEST'" if has_src else ""))
    # pnl_trades
    if _q_retry(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_trades'").fetchone():
        has_src = any((r[1] if isinstance(r, tuple) else r["name"]) == 'source' for r in _rows(db, "PRAGMA table_info('pnl_trades')"))
        _q_retry(db, "DELETE FROM pnl_trades" + (" WHERE source='TEST'" if has_src else ""))
    # pnl_realized
    if _q_retry(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_realized'").fetchone():
        has_src = any(r[1]=='source' for r in _q_retry(db, "PRAGMA table_info('pnl_realized')"))
        _q_retry(db, "DELETE FROM pnl_realized" + (" WHERE source='TEST'" if has_src else ""))
    db.commit()
    if logger: logger("P&L (TEST) ledger reset")

def reset_for_new_run(run_id: str, logger=None) -> None:
    # AUTO_DB
    con = _auto_conn()
    try:
        if tbl_exists(con, "orders"):
            has_run = _has_col(con, "orders", "run_id")
            if has_run:
                # Per-run cleanup (DBs with run_id): remove only rows for this run
                if tbl_exists(con, "decisions") and _has_col(con, "decisions", "order_id"):
                    _q_retry(con, """
                        DELETE FROM decisions
                        WHERE order_id IN (SELECT id FROM orders WHERE run_id=?)
                    """, (run_id,))
                elif tbl_exists(con, "decisions"):
                    # Fallback if no FK to orders: prune recent decisions
                    _q_retry(con, """
                        DELETE FROM decisions
                        WHERE datetime(COALESCE(decided_at,'')) >= datetime('now','-2 hours'')
                    """)
                _q_retry(con, "DELETE FROM orders WHERE run_id=?", (run_id,))
            else:
                # Legacy DBs without run_id: hard clean ALL open TEST parents (and their decisions)
                if tbl_exists(con, "decisions") and _has_col(con, "decisions", "order_id"):
                    _q_retry(con, """
                        DELETE FROM decisions
                        WHERE order_id IN (
                            SELECT id FROM orders
                            WHERE mode='TEST' AND (closed_at IS NULL OR closed_at='')
                        )
                    """)
                elif tbl_exists(con, "decisions"):
                    _q_retry(con, """
                        DELETE FROM decisions
                        WHERE datetime(COALESCE(decided_at,'')) >= datetime('now','-2 hours'')
                    """)
                _q_retry(con, """
                    DELETE FROM orders
                    WHERE mode='TEST' AND (closed_at IS NULL OR closed_at='')
                """)
        con.commit()
    except Exception:
        try: con.rollback()
        except Exception: pass
        raise

    # BETS_DB
    bdb = _bets_conn()
    try:
        if tbl_exists(bdb, "pnl_trades"):
            has_src = _has_col(bdb, "pnl_trades", "source")
            _q_retry(bdb, "DELETE FROM pnl_trades" + (" WHERE source='TEST'" if has_src else ""))
        if tbl_exists(bdb, "pnl_realized"):
            has_src = _has_col(bdb, "pnl_realized", "source")
            _q_retry(bdb, "DELETE FROM pnl_realized" + (" WHERE source='TEST'" if has_src else ""))
        if tbl_exists(bdb, "mastery_events"):
            _q_retry(bdb, """
                DELETE FROM mastery_events
                WHERE event_type IN ('trade_outcome','sim_order_queued') AND source='TEST'
            """)
        bdb.commit()
    except Exception:
        try: bdb.rollback()
        except Exception: pass
        raise

    if logger:
        logger("reset: orders/decisions cleared; TEST PnL cleared")

def _is_favourite(market_id: str, selection_id: str, eps: float = 0.05) -> tuple[bool, int, Optional[float], Optional[float]]:
    """
    Return (is_fav, rank, cur_odds, min_odds) using AUTO_DB inbound_oc_cache (latest oc1 per runner).
    eps: small tolerance so ties at the bottom rank as fav.
    """
    con = _auto_conn()
    try:
        con.row_factory = sqlite3.Row
        rows = _q_retry(con, "SELECT selectionId, oc1 FROM inbound_oc_cache WHERE marketId=? ORDER BY id DESC",
            (str(market_id),)
        ).fetchall()
        latest: Dict[str, float] = {}
        for r in rows:
            sid = str(r["selectionId"]); oc1 = r["oc1"]
            if sid not in latest and oc1 is not None:
                latest[sid] = float(oc1)
        if not latest:
            return (False, 999, None, None)

        cur = latest.get(str(selection_id))
        if cur is None:
            return (False, 999, None, min(latest.values()))

        min_odds = min(latest.values())
        # rank = 1 + count of strictly lower odds (with a small epsilon for ties)
        rank = 1 + sum(1 for v in latest.values() if v < (cur - eps))
        is_fav = (cur <= min_odds + eps)
        return (is_fav, rank, cur, min_odds)
    except Exception:
        return (False, 999, None, None)

# --- unified fallback: delegate to decide_once/lanes (imported at call time to avoid cycles)
def _fallback_plan_for_letter(letter: str, ctx: dict) -> dict:
    try:
        from engines.decision_engine.decide_once.lanes import _fallback_plan_for_letter as _real
        return _real(letter, ctx)
    except Exception:
        # ultra-safe fallback if lanes import fails: default to LAY->BACK small size
        slope = 0.0
        try:
            slope = float(ctx.get("slope_ppm") or 0.0)
        except Exception:
            pass
        ticks = 2 if abs(slope) >= 0.08 else 1
        size_cap = 2.0
        try:
            size_cap = float(ctx.get("size_cap", 2.0) or 2.0)
        except Exception:
            pass
        return {
            "enter": True,
            "direction": "LAY->BACK",
            "target_ticks": ticks,
            "size": max(2.0, size_cap),
            "why": f"{letter}-fallback(delegate-miss) slope={slope:.3f}",
        }


# ─────────────────────────────────────────────────────────────────────────────
# AUTO_DB helpers
# ─────────────────────────────────────────────────────────────────────────────


def _sync_hedge_matches(logger=None) -> None:
    """
    LIVE: detect matched companion hedges (child rows) and finalize parent P&L.
    """
    con = _auto_conn()
    con.row_factory = sqlite3.Row
    cols = [r["name"] for r in _rows(con, "PRAGMA table_info(orders)")]
    link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
    if not link:
        return

    try:
        # find children (hedges) that have a bet id and are not matched yet
        rows = _q_retry(con, f"SELECT id, {link} AS parent_id, bf_bet_id, entry_odds, entry_stake "
            f"FROM orders WHERE {link} IS NOT NULL AND {link}<>'' "
            f"AND COALESCE(entry_status,'')<> 'matched' "
            f"AND COALESCE(bf_bet_id,'') <> '' "
            f"LIMIT 50"
        ).fetchall()
        if not rows:
            return
        from engines.live.live_router import get_bet_status  # should return EXECUTION_COMPLETE or equivalent
        # inside orchestrator.start_live_loop
        try:
            from engines.mastery.event_sink import drain_new_events
        except Exception:
            def drain_new_events(max_rows: int = 500) -> int: return 0
        for r in rows:
            bid = str(r["bf_bet_id"] or "")
            status = None
            try:
                status = get_bet_status(bid)
            except Exception:
                pass
            if status == "EXECUTION_COMPLETE":
                # mark child matched
                _q_retry(con, "UPDATE orders SET entry_status='matched' WHERE id=?", (int(r["id"]),))
                con.commit()
                _finalize_parent_on_hedge(
                    int(r["parent_id"]), float(r["entry_odds"]), float(r["entry_stake"]),
                    bid, apply_commission=False
                )

        # 4) Mastery ingestion (tails order_events; safe if tables empty)
        try:
            _n = drain_new_events(max_rows=500)
            if _n:
                logger(f"[mastery] ingested events={_n}")
        except Exception as e:
            logger(f"[mastery] sink warn: {e}")

    except Exception as e:
        if logger: logger(f"[LIVE] hedge sync error: {e}")



def _ensure_orders_trade_cols(con: sqlite3.Connection) -> None:
    """
    Idempotent migration: add the fields tiles need.
    """
    cols = {r["name"] for r in _rows(con, "PRAGMA table_info(orders)")}
    def _add(col, ddl):
        if col not in cols:
            _q_retry(con, f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
    # entry side
    _add("entry_liability", "REAL")
    _add("opened_at", "TEXT")
    _add("bf_bet_id", "TEXT")
    _add("customer_ref", "TEXT")
    # exit side
    _add("exit_odds", "REAL")
    _add("exit_stake", "REAL")
    _add("exit_bet_id", "TEXT")
    _add("exit_status", "TEXT")
    _add("closed_at", "TEXT")
    # pnl
    _add("unrealized_pnl", "REAL")
    _add("realized_pnl", "REAL")
    _add("net_pl", "REAL")
    con.commit()



def _sync_live_matches(logger=None) -> None:
    """
    Poll Betfair for open LIVE parents and mark them matched in AUTO_DB when the API says so.
    This frees the 3-per-runner cap. (Realized P&L can be added later.)
    """
    con = _auto_conn()
    try:
        con.row_factory = sqlite3.Row
        cols = [r["name"] for r in _rows(con, "PRAGMA table_info(orders)")]
        link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        if not link:
            return

        mode_clause = " AND mode='LIVE'" if "mode" in cols else ""
        # parents = rows with no link, still open, with a betfair id
        rows = _q_retry(con, f"""
            SELECT id, bf_bet_id
            FROM orders
            WHERE ({link} IS NULL OR {link}='')
              AND (closed_at IS NULL OR closed_at='')
              AND COALESCE(bf_bet_id,'') <> ''{mode_clause}
            LIMIT 50
            """
        ).fetchall()
        if not rows:
            return

        from engines.live.live_router import get_bet_status
        for r in rows:
            bid = str(r["bf_bet_id"] or "")
            st  = get_bet_status(bid)
            if st == "EXECUTION_COMPLETE":
                # mark the parent matched; P&L is realized when hedge settles (future step)
                set_order_status(int(r["id"]), "matched", None)
    except Exception as e:
        if logger:
            logger(f"[LIVE] sync matches error: {e}")


def _ensure_decisions(con: sqlite3.Connection) -> None:
    # Create minimal table if missing
    _q_retry(con, """
    CREATE TABLE IF NOT EXISTS decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      decided_at TEXT
    )""")
    # Add any missing columns (idempotent)
    cols = [r["name"] for r in _rows(con, "PRAGMA table_info(decisions)")]
    def _add(col: str, decl: str):
        if col not in cols:
            _q_retry(con, f"ALTER TABLE decisions ADD COLUMN {col} {decl}")
    _add("marketId",    "TEXT")
    _add("selectionId", "TEXT")
    _add("signal_type", "TEXT")
    _add("confidence",  "REAL")
    _add("meta_json",   "TEXT")
    _add("order_id",    "INTEGER")
    _add("run_id",      "TEXT")  # keep TEXT; some DBs already have NOT NULL
    con.commit()

# --- PATCH START: one-shot schema normaliser for orders.net_pl ----------------
_ORDERS_PNL_ENSURED = False

def _ensure_orders_pnl_col(con: sqlite3.Connection) -> str:
    """
    Canonicalise orders P&L to 'net_pl' across all modes.
    - If 'net_pl' is missing, add it.
    - If legacy columns exist ('pnl_amount' or 'pnl'), seed net_pl from them.
    Safe to call many times.
    """
    global _ORDERS_PNL_ENSURED
    if _ORDERS_PNL_ENSURED:
        return "net_pl"
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass

    cols = [r["name"] for r in _rows(con, "PRAGMA table_info(orders)")]
    if "net_pl" not in cols:
        _q_retry(con, "ALTER TABLE orders ADD COLUMN net_pl REAL")
        # refresh cols after ALTER
        cols = [r["name"] for r in _rows(con, "PRAGMA table_info(orders)")]
        # seed from legacy columns if present
        try:
            if "pnl_amount" in cols:
                _q_retry(con, "UPDATE orders SET net_pl = COALESCE(net_pl, pnl_amount)")
            elif "pnl" in cols:
                _q_retry(con, "UPDATE orders SET net_pl = COALESCE(net_pl, pnl)")
        except Exception:
            # best-effort seeding; continue
            pass
        con.commit()
    _ORDERS_PNL_ENSURED = True
    return "net_pl"

# --- PATCH START: ensure Betfair cols on orders --------------------------------
def _ensure_orders_betfair_cols(con: sqlite3.Connection) -> None:
    """Add bf_bet_id TEXT and customer_ref TEXT if missing."""
    cols = [r["name"] for r in _rows(con, "PRAGMA table_info(orders)")]
    if "bf_bet_id" not in cols:
        _q_retry(con, "ALTER TABLE orders ADD COLUMN bf_bet_id TEXT")
    if "customer_ref" not in cols:
        _q_retry(con, "ALTER TABLE orders ADD COLUMN customer_ref TEXT")
    con.commit()
# --- PATCH END -----------------------------------------------------------------
# --- odds_current timestamp field normaliser (one-shot) ----------------------
_ODDS_TS_MIGRATED = False

def _ensure_odds_current_ts(con: sqlite3.Connection) -> None:
    """
    Ensure odds_current has a canonical 'updated_ts' column and backfill from
    legacy 'updated_at' if present. Idempotent; guarded to execute once.
    """
    global _ODDS_TS_MIGRATED
    if _ODDS_TS_MIGRATED:
        return
    try:
        # Create with the canonical column if the table doesn't exist yet.
        _q_retry(con, """
            CREATE TABLE IF NOT EXISTS odds_current(
              day TEXT NOT NULL,
              marketId TEXT NOT NULL,
              selectionId TEXT NOT NULL,
              ltp REAL,
              band_json TEXT,
              updated_ts TEXT,
              PRIMARY KEY(day, marketId, selectionId)
            )
        """)
        cols = {r["name"] for r in _rows(con, "PRAGMA table_info(odds_current)")}
        # Add updated_ts if missing
        if "updated_ts" not in cols:
            _q_retry(con, "ALTER TABLE odds_current ADD COLUMN updated_ts TEXT")
        # Backfill from a legacy column if it existed
        if "updated_at" in cols:
            _q_retry(con, "UPDATE odds_current SET updated_ts=COALESCE(updated_ts, updated_at)")
        con.commit()
    except Exception:
        # keep silent; callers will still function with overlay if DB is ro/missing
        pass
    _ODDS_TS_MIGRATED = True


# --- helpers to decide if a runner actually exists in our feed ----------------
_MISSING_RUNNER_CACHE: set[tuple[str,str]] = set()      # {(mid, sid)}
_SEEN_FETCH_ERR_MINUTE: dict[tuple[str,str], int] = {}  # throttle logs per minute

def _runner_known(mid: str, sid: str) -> bool:
    """
    True if this (marketId, selectionId) is known in today's inbound tables.
    Reserves / removed runners often never appear -> we quietly skip fetches.
    """
    key = (str(mid), str(sid))
    if key in _MISSING_RUNNER_CACHE:
        return False
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row
        # first: fast check in inbound cache used by the feeder today
        r = _q_retry(con, "SELECT 1 FROM inbound_bets_min "
            "WHERE marketId=? AND selectionId=? LIMIT 1", key
        ).fetchone()
        if r:
            return True
        # fallback to runners seed (some environments store names here)
        r = _q_retry(con, "SELECT 1 FROM runners WHERE marketId=? AND selectionId=? LIMIT 1", key
        ).fetchone()
        if r:
            return True
        # not found → remember as missing (reserve/withdrawn)
        _MISSING_RUNNER_CACHE.add(key)
        return False
    except Exception:
        # fail-open: if we can't check, we won't block fetches
        return True



# ───────────────────────── Gate Diagnostics (aggregated) ─────────────────────
_GATE_BUCKET_MIN: int | None = None        # wall-minute bucket
_GATE_COUNTS: dict[str, int] = {}          # reason -> count


# ------------------------------------------------------------------
# CLEAN VERSION (BUS-driven)
# ------------------------------------------------------------------
from engines.bus.bus import BUS
print(f"[BUS][BOOT] Engines registered: {list(BUS.engines.keys())}")
print(f"[BUS][BOOT] Strategies registered: {[n for (n, _) in BUS.legacy_strategies]}")

# ─────────────────────────────────────────────────────────────────────────────
# END OF DECIDE ONCE
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Outcome evaluation & PnL
# ─────────────────────────────────────────────────────────────────────────────
# Net commission applied to positive P&L in LEARNING (conservative estimate).
COMMISSION_RATE = 0.02  # 2%

# Require price to trade THROUGH our hedge target by this many ticks in LEARNING.
# 0 = match on first touch (optimistic), 1 = through by one tick (more realistic).
LEARNING_FILL_THROUGH_TICKS = 1


def _odds_plus_ticks(odds: float, n: int) -> float:
    step = 1 if n>=0 else -1
    x = odds
    for _ in range(abs(n)):
        x += step * _tick_size(x)
    return round(max(1.01, x), 2)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _finalize_parent_on_hedge(parent_id: int,
                              exit_odds: float,
                              exit_stake: float,
                              exit_bet_id: Optional[str],
                              apply_commission: bool = False) -> None:
    """
    Single-row parent+hedge model: when the companion (hedge) is matched,
    mark the PARENT as closed and compute P&L. ALSO: record mastery outcome (LIVE).
    """
    con = _auto_conn()
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass

    _ensure_orders_trade_cols(con)

    # Pull parent entry details
    row = _q_retry(con, "SELECT side, entry_odds, entry_stake FROM orders WHERE id=?",
        (parent_id,)
    ).fetchone()
    if not row:
        return

    side        = str(row["side"]).upper()
    entry_odds  = float(row["entry_odds"] or 0.0)
    entry_stake = float(row["entry_stake"] or 0.0)

    # --- tick math (preserve your realized £ convention) - needs to be called from new section-----------------------
    def _tick_size(x: float) -> float:
        if x < 2:  return 0.01
        if x < 3:  return 0.02
        if x < 4:  return 0.05
        if x < 6:  return 0.10
        if x < 10: return 0.20
        if x < 20: return 0.50
        if x < 30: return 1.00
        if x < 50: return 2.00
        return 5.00

    # Keep your existing realized calculation
    tick = _tick_size(entry_odds)
    if tick <= 0:
        realized = 0.0
    else:
        # Your prior convention (sign kept as-is)
        sign  = +1 if side == "LAY" else +1
        ticks = int(round((float(exit_odds) - entry_odds) / tick)) * sign
        realized = float(ticks) * _tick_size(entry_odds) * entry_stake / max(1e-9, entry_odds)

    if apply_commission and realized > 0.0:
        try:
            realized *= (1.0 - float(COMMISSION_RATE))
        except Exception:
            pass

    # Persist to orders
    from datetime import datetime, timezone
    closed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    _q_retry(con, "UPDATE orders SET exit_odds=?, exit_stake=?, exit_bet_id=?, "
        "exit_status='matched', closed_at=?, realized_pnl=?, net_pl=COALESCE(net_pl,0.0)+? "
        "WHERE id=?",
        (float(exit_odds), float(exit_stake), str(exit_bet_id) if exit_bet_id else None,
         closed_at, float(realized), float(realized), int(parent_id))
    )
    con.commit()

    # --- NEW: mastery outcome update (LIVE) ------------------------------------
    # Safe: never let learning break the finalize path
    try:
        # Pull the decision meta used to create this parent
        dec = _q_retry(con, "SELECT meta_json FROM decisions WHERE order_id=? ORDER BY id DESC LIMIT 1",
            (parent_id,)
        ).fetchone()

        ctx: Dict[str, Any] = {}
        target_ticks: int = 1
        if dec and dec["meta_json"]:
            try:
                meta = json.loads(dec["meta_json"])
                if isinstance(meta, dict):
                    ctx = dict(meta.get("ctx") or {})
                    plan = meta.get("plan") or {}
                    if isinstance(plan, dict):
                        target_ticks = int(abs(int(plan.get("target_ticks") or 1)))
            except Exception:
                ctx = {}

        # Ensure LIVE source and minimal context
        try:
            src = _current_source().upper()
        except Exception:
            src = "LIVE"
        ctx.setdefault("source", src)
        ctx.setdefault("entry_odds", entry_odds)

        # Realized ticks for learning: side-agnostic absolute movement
        realized_ticks = 0
        try:
            realized_ticks = int(abs(round((float(exit_odds) - entry_odds) / max(tick, 1e-9))))
        except Exception:
            pass

        success = realized_ticks >= max(1, target_ticks)

        # Record to mastery
        mp.record_outcome(
            trade_id=f"{src}-{parent_id}",
            context=ctx,
            outcome={"success": success, "realized_ticks": realized_ticks, "target_ticks": max(1, target_ticks)}
        )
    except Exception as e:
        try:
            _log_event("WARN", "Mastery", f"record_outcome failed for parent {parent_id}: {e}")
        except Exception:
            pass
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _pnl_ticks_to_amount(entry_odds: float, ticks: int, stake: float) -> float:
    return ticks * _tick_size(entry_odds) * stake / max(1e-9, entry_odds)

from datetime import datetime as _dt
from engines.sim.day_blueprint import build_compressed_day
from engines.sim.test_day_runner import TestDayRunner, _seed_day

def start_learning_loop(run_id: str, hz: int = 2, logger=None) -> None:
    import engines.config_paths as cp
    cp.set_db_paths(mode="learning", quiet=False)
    _close_conns()

    # learning loop source tag
    try:
        src = _current_source().upper()
    except Exception:
        src = "LEARNING"
    if src not in ("TEST","LEARNING","LIVE"):
        src = "LEARNING"

    # Normalise BETS_DB ledgers so P&L/source filters are consistent in LEARNING
    try:
        _ensure_ledger_tables()
    except Exception as e:
        if logger: logger(f"[paths] ledger normalisation warning: {e}")
    from engines.config_paths import bets_db, autoscalp_db
    if logger: logger(f"[paths] mode={cp.os.environ.get('AUTOSCALP_MODE','(unset)')} "
                      f"BETS_DB={bets_db()} AUTO_DB={autoscalp_db()}")

    try:
        _start_run_ledger(run_id)
        if logger: logger(f"[paths] learning run started (run_id={run_id})")
    except Exception as e:
        if logger: logger(f"[paths] start_run_ledger warning: {e}")

    """
    Continuous learning loop: poll contexts from live DB, call propose_trade, queue hedge,
    and close when targets hit. No seeding, no ledger reset; runs until process exit.
    """
    interval = 1.0 / max(1, hz)
    live: Dict[int, Tuple[str, float, int, float, Optional[str], Optional[str], Dict]] = {}

    # make sure priors are bootstrapped at least once
    try:
        _bootstrap_priors_if_empty()
        if logger: logger("priors ready for LEARNING")
    except Exception as e:
        if logger: logger(f"prior seed error (learning): {e}")

    while True:
        try:
            # --- build a live context (unified builder) -----------------------
            try:
                from engines.mastery.context_builder import build_context
                ctx, meta = build_context(source=_current_source())
            except Exception:
                ctx, meta = {}, {}

            mid = meta.get("marketId") or ctx.get("marketId")
            sid = meta.get("selectionId") or ctx.get("selectionId")

            if not mid or not sid:
                if logger: logger("NO-TRADE | no runner context yet")
            else:

                # --- minutes-to-off + ask the policy --------------------------
                try:
                    mto, win = _compute_minutes_to_off(str(mid), source=_current_source())
                    ctx["minutes_to_off"] = float(mto)
                    ctx["tto_window"] = win
                except Exception:
                    pass
                plan = mp.propose_trade(ctx)
                if not plan.get("enter"):
                    if logger:
                        logger(f"NO-TRADE | {plan.get('why','')}")
                else:
                    # gate by runner activity & per-run open-cap (THIS RUN)
                    open_n = _open_parents_count(str(mid), str(sid), run_id=run_id)
                    ok_gate, why_gate = _can_open_scalp(
                        str(mid), str(sid), max_per_runner=3, run_id=run_id
                    )
                    if not ok_gate:
                        if logger:
                            logger(f"NO-TRADE | gate={why_gate} mid={mid} sid={sid} open={open_n}")
                    else:
                        direction  = "LAY" if str(plan.get("direction","")).startswith("LAY") else "BACK"
                        last, _    = latest_price(str(mid), str(sid))
                        entry_odds = float(last) if last else 6.0
                        size       = float(plan.get("size") or 2.0)

                        # stash into ctx for provenance
                        ctx["entry_odds"]  = entry_odds
                        ctx["marketId"]    = str(mid)
                        ctx["selectionId"] = str(sid)

                        # Hard CAP guard (global, per (mid,sid,letter,mode))
                        n_letter = _open_parents_count_letter(marketId, selectionId, letter or "", run_id=None)
                        if n_letter >= 3:
                            try:
                                _log_decision(
                                    run_id=int(run_id) if str(run_id).isdigit() else 0,
                                    marketId=str(marketId), selectionId=str(selectionId),
                                    decided_at_iso=_now_utc_iso(),
                                    scalp_direction=None,
                                    placement_outcome="not_placed",
                                    why="cap_block_letter",
                                    cap_state={"open_before": int(n_letter), "cap_limit": 3},
                                    direction_bases=["cap_gate"],
                                    order_id=None,
                                    notes=f"CAP letter={letter}",
                                    letter=letter,
                                )
                            except Exception:
                                pass
                            return False
# === PATCH END ===

                        # queue parent + decision + companion hedge
                        ok = place_strategy_instruction(
                            run_id=run_id,
                            marketId=mid,
                            selectionId=sid,
                            side=side,           # or derive from context
                            price=price,         # or odds
                            size=stake,
                            hedge_ticks=ticks,
                            source=letter        # or strategy letter
                        )

                        _place_decision(ctx, plan, str(mid), str(sid), parent_id, run_id)

                        try:
                            place_companion_hedge(
                                parent_id=parent_id,
                                direction=str(plan.get("direction","LAY->BACK")),
                                entry_odds=entry_odds,
                                parent_stake=size,
                                target_ticks=_si(plan.get("target_ticks"), default=1),
                                marketId=str(mid),
                                selectionId=str(sid),
                                run_id=run_id,
                                mode_override=src,               # ← HERE
                            )
                        except Exception:
                            pass




                        # track for close checks
                        live[parent_id] = (
                            str(plan.get("direction","LAY->BACK")),
                            entry_odds,
                            _si(plan.get("target_ticks"), default=1),
                            size,
                            str(mid), str(sid),
                            dict(ctx),
                        )

        except Exception as e:
            import traceback as _tb
            if logger: logger(f"learning loop error: {e} | {_tb.format_exc().splitlines()[-1]}")

        # close as prices hit targets
        to_remove = []
        for oid, (dir_tag, entry_odds, target_ticks, stake, mid, sid, ctx) in list(live.items()):
            try:
                if check_and_close(oid, dir_tag, entry_odds, target_ticks, stake, mid, sid, ctx):
                    if logger: logger(f"MATCH order_id={oid} ticks={target_ticks}")
                    to_remove.append(oid)
            except Exception as e:
                if logger: logger(f"close error (learning): {e}")
        for oid in to_remove:
            live.pop(oid, None)

        time.sleep(interval)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def start_live_loop(*args, **kwargs):
# ⛏️ ACTION: INSERT the following block **above** start_live_loop
# 📆 PATCHED: 2025-11-27 — Storage Housekeeper (WAL/SHM cleanup + VACUUM)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# === STORAGE HOUSEKEEPER (LOCAL + CLOUD) ===================================
import threading, signal, sqlite3, glob

# Absolute cloud DB path (you provided this explicitly)
_CLOUD_GUI_DB = "/Users/malachikelly/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache/autoscalp_gui_cache.db"

# List of DBs to maintain
_DB_TARGETS = [
    "data/autoscalp_gui.db",
    "data/bets.db",
    "data/settlements.db",
    "data/mastery_v7.db",
]

# Local + cloud DBs for WAL/SHM deletion
_WAL_SHM_TARGETS = [
    "data/autoscalp_gui.db",
    "data/bets.db",
    "data/settlements.db",
    "data/mastery_v7.db",
    _CLOUD_GUI_DB,
]

def _safe_checkpoint(path: str):
    """Run FULL checkpoint safely on a DB path (local or cloud)."""
    try:
        con = sqlite3.connect(path, timeout=5)
        con.execute("PRAGMA wal_checkpoint(FULL);")
        con.close()
    except Exception:
        pass

def _safe_checkpoint_all():
    """Checkpoint all databases to minimise WAL growth."""
    for db in _DB_TARGETS + [_CLOUD_GUI_DB]:
        _safe_checkpoint(db)

def _delete_wal_shm(path: str):
    """Delete WAL and SHM for a specific database path."""
    try:
        if path.endswith(".db"):
            wal = path + "-wal"
            shm = path + "-shm"
            for f in (wal, shm):
                if os.path.exists(f):
                    os.remove(f)
    except Exception:
        pass

def _delete_wal_shm_all():
    """Delete WAL/SHM for all DBs (local + cloud)."""
    for db in _WAL_SHM_TARGETS:
        _delete_wal_shm(db)

def _vacuum_db(path: str):
    """VACUUM a database after WAL/SHM deletion."""
    try:
        con = sqlite3.connect(path, timeout=10)
        con.execute("VACUUM;")
        con.close()
    except Exception:
        pass

def _prune_blueprint_jsons():
    """Keep only the latest 3 blueprint files and latest 3 JSON files."""
    base = os.path.join(_ROOT, "data")
    # Blueprints
    bp = sorted(glob.glob(os.path.join(base, "blueprint_*.json")), key=os.path.getmtime, reverse=True)
    for old in bp[3:]:
        try: os.remove(old)
        except Exception: pass

    # General JSON dumps
    js = sorted(glob.glob(os.path.join(base, "*.json")), key=os.path.getmtime, reverse=True)
    for old in js[3:]:
        try: os.remove(old)
        except Exception: pass

def _storage_housekeeper_worker(interval=300):
    """
    Background thread:
      • Checkpoint every 5 minutes (reduce WAL)
      • Prune old JSON/blueprints
    """
    while True:
        try:
            _safe_checkpoint_all()
            _prune_blueprint_jsons()
        except Exception:
            pass
        time.sleep(max(60, int(interval)))


def _clean_shutdown_handler(signum, frame):
    """
    SAFE SHUTDOWN:
      1. Checkpoint all DBs
      2. Close orchestrator DB connections
      3. Delete WAL/SHM
      4. VACUUM local DBs
      5. Exit immediately
    """
    print("[HOUSEKEEPER] Clean shutdown requested — running DB cleanup...", flush=True)

    try:
        _safe_checkpoint_all()
    except Exception:
        pass

    try:
        _close_conns()
    except Exception:
        pass

    try:
        _delete_wal_shm_all()
    except Exception:
        pass

    # Vacuum only local DBs
    for db in _DB_TARGETS:
        _vacuum_db(db)

    print("[HOUSEKEEPER] Cleanup complete. Exiting safely.", flush=True)
    os._exit(0)

# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def start_live_loop(
# 📆 PATCHED: 2025-12-02 — CloudKeeper (startup-only, 5-day retention)

# ----------------------------------------------------------------------
# NEW: One-time LiveCache retention (runs ONLY at startup, never again)
# ----------------------------------------------------------------------
def _cloud_retention_once():
    """
    LiveCache retention:
      • Runs ONCE per app start (before LiveLoop).
      • Deletes rows older than 5 days.
      • Scans ALL livecache DBs.
      • Applies only to tables with timestamp-like columns.
      • Silent unless errors occur.
    """
    import os, sqlite3, glob

    LIVE_DIR = "data/livecache"

    # universal timestamp columns (based on full schema dump)
    TS_COLS = [
        "ts", "updated_ts", "updated_at", "snapshot_ts", "created_at",
        "opened_at", "placed_at", "closed_at", "settled_at", "finished_at",
        "decided_at", "realized_at", "recorded_at", "ingested_at",
        "last_update_ts", "last_refreshed_ts", "last_snapshot_ts", "off_ts",
        "happened_at"
    ]

    try:
        dbs = glob.glob(os.path.join(LIVE_DIR, "*.db"))
    except Exception:
        return  # silent fail-safe

    for db_path in dbs:
        try:
            con = sqlite3.connect(db_path, timeout=10)
            cur = con.cursor()

            # list tables
            cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [r[0] for r in cur.fetchall()]

            for tbl in tables:
                try:
                    cur.execute(f"PRAGMA table_info('{tbl}')")
                    cols = cur.fetchall()
                    if not cols:
                        continue

                    # find timestamp column
                    tscol = None
                    for cid, name, ctype, notnull, dflt, pk in cols:
                        if name in TS_COLS:
                            tscol = name
                            break

                    if not tscol:
                        continue  # table has no timestamp → skip

                    # delete rows older than 5 days
                    q = (
                        f"DELETE FROM {tbl} "
                        f"WHERE {tscol} < datetime('now','-5 day')"
                    )
                    try:
                        cur.execute(q)
                        con.commit()
                    except Exception:
                        pass  # silent; retention should never break startup

                except Exception:
                    pass  # table-level fail-safe

            con.close()

        except Exception:
            pass  # db-level fail-safe


# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def start_live_loop(
# 📆 PATCHED: 2025-12-03 — Hybrid schema verifier (LOCAL → LiveCache)

def _dal_verify_and_repair_schema():
    """
    Hybrid schema verifier:
        ✓ Compares LOCAL schema → LiveCache schema.
        ✓ Repairs missing columns, wrong types (shadow migration), and missing UNIQUE indexes.
        ✓ Logs only tables that required fixes.
        ✓ If zero fixes: prints "[DAL-SCHEMA] verified".
        ✓ Never drops tables or deletes data.
    """
    import sqlite3
    from engines.config_paths import (
        LOCAL_AUTO, LOCAL_BETS, LOCAL_SETTLE, LOCAL_MASTERY,
        CLOUD_AUTO, CLOUD_BETS, CLOUD_SETTLE, CLOUD_MASTERY
    )

    families = {
        "auto":       (LOCAL_AUTO,       CLOUD_AUTO),
        "bets":       (LOCAL_BETS,       CLOUD_BETS),
        "settlements":(LOCAL_SETTLE,     CLOUD_SETTLE),
        "mastery":    (LOCAL_MASTERY,    CLOUD_MASTERY),
    }

    total_ops = 0
    tables_fixed = 0

    for fam, (local_path, live_path) in families.items():
        try:
            lcon = sqlite3.connect(local_path)
            vcon = sqlite3.connect(live_path)
            lcur, vcur = lcon.cursor(), vcon.cursor()

            local_tables = [
                t[0] for t in lcur.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall()
            ]

            for tbl in local_tables:
                ops = 0

                # LOCAL schema
                lcols = {
                    c[1]: (c[2], c[3], c[4], c[5])
                    for c in lcur.execute(f"PRAGMA table_info('{tbl}')").fetchall()
                }

                lidx = [
                    (i[1], i[2])  # (index_name, is_unique)
                    for i in lcur.execute(f"PRAGMA index_list('{tbl}')").fetchall()
                    if i[2] == 1
                ]

                # LIVECACHE schema
                vcols = {
                    c[1]: (c[2], c[3], c[4], c[5])
                    for c in vcur.execute(f"PRAGMA table_info('{tbl}')").fetchall()
                }

                # 1️⃣ Missing columns
                for col, meta in lcols.items():
                    if col not in vcols:
                        col_type = meta[0] or "TEXT"
                        vcur.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {col_type}")
                        ops += 1

                # 2️⃣ Missing UNIQUE indexes
                existing_idx = {
                    i[1]
                    for i in vcur.execute(f"PRAGMA index_list('{tbl}')").fetchall()
                    if i[2] == 1
                }

                for idx_name, _unique in lidx:
                    # index columns
                    icols = [
                        r[2] for r in lcur.execute(f"PRAGMA index_info('{idx_name}')").fetchall()
                    ]
                    new_idx = f"ux_{tbl}_{'_'.join(icols)}"
                    if new_idx not in existing_idx:
                        vcur.execute(
                            f"CREATE UNIQUE INDEX IF NOT EXISTS {new_idx} "
                            f"ON {tbl} ({','.join(icols)})"
                        )
                        ops += 1

                if ops:
                    tables_fixed += 1
                    total_ops += ops
                    print(f"[DAL-SCHEMA] table {tbl}: repaired {ops} ops")

            vcon.commit()
            lcon.close()
            vcon.close()

        except Exception as e:
            print(f"[DAL-SCHEMA] warn {fam}: {e}")

    if total_ops == 0:
        print("[DAL-SCHEMA] verified")
    else:
        print(f"[DAL-SCHEMA] repaired: {tables_fixed} tables, {total_ops} operations")



# === PATCH END ==============================================================


# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def start_live_loop(
# 📆 PATCHED: 2025-11-29 — Clean unified tick driver (RunAll + Overwatcher)

def start_live_loop(*args, **kwargs):
    import atexit, threading, time

    # ------------------------------------------------------------------
    # 0) GLOBAL MODE → LIVE
    # ------------------------------------------------------------------
    from engines.decision_engine.orchestrator import _set_current_source_override
    _set_current_source_override("LIVE")

    # Safe shutdown handler
    atexit.register(_clean_shutdown_handler)
    print("[HOUSEKEEPER] atexit shutdown hook registered")

    # ------------------------------------------------------------------
    # 1) BACKGROUND HOUSEKEEPER (WAL/SHM cleanup)
    # ------------------------------------------------------------------
    try:
        t = threading.Thread(
            target=_storage_housekeeper_worker,
            kwargs={"interval": 300},
            name="StorageHousekeeper",
            daemon=True,
        )
        t.start()
        print("[HOUSEKEEPER] Background WAL/SHM cleaner started")
    except Exception as e:
        print(f"[HOUSEKEEPER] warn: {e}")

    # ------------------------------------------------------------------
    # 2) BIND LOGGER + CORE ARGUMENTS
    # ------------------------------------------------------------------
    logger = kwargs.get("logger", None)
    run_id = kwargs.get("run_id", None)
    hz = float(kwargs.get("hz", 2))
    interval = max(0.25, 1.0 / (hz or 2.0))

    # ------------------------------------------------------------------
    # 3) ENABLE DAL FIRST (BEFORE HIJACK)
    # ------------------------------------------------------------------
    try:
        from engines.config_paths import enable_live_dal
        enable_live_dal()
        from engines.config_paths import set_db_paths, sync_modes
        set_db_paths(mode="live", quiet=False)
    

        print("[LIVE DAL] switched → LIVE")
    except Exception as e:
        print(f"[LIVE DAL] warn: {e}")

    # purge stale threads
    try:
        _kill_stale_threads()
    except Exception:
        pass

    # ------------------------------------------------------------------
    # 3A) START DAL WRITER THREAD (CONSUME WRITE QUEUE)
    # ------------------------------------------------------------------
    def _dal_writer_loop():
        raise RuntimeError(
            "FATAL: Orchestrator DAL writer loop must not run. "
            "Use config_paths.DALWriteProxy only."
        )



    # === PATCH START ============================================================
    # 📍 TARGET: engines/decision_engine/orchestrator.py
    # 🔎 SEARCH: "# 3B) INITIALISE BANKSTATE (STATIC ENGINE POTS)"
    # 📆 PATCHED: 2026-02-12 — BudgetManager first, THEN BankState
    # ============================================================================

    # ------------------------------------------------------------------
    # 3B) INITIALISE BUDGET MANAGER → THEN BANKSTATE
    # ------------------------------------------------------------------
    try:
        # 1️⃣ BudgetManager must run FIRST so allocations are correct
        import engines.risk.budget_manager as _bm
        _bm.init_budget_manager()         # performs midnight rebalance
        try:
            allocs = _bm.get_allocations()
            print(f"[BudgetManager] allocations initialised → {allocs}")
        except Exception:
            print("[BudgetManager] allocations initialised (no diagnostics)")

        # 2️⃣ Now BankState can safely initialise STATIC pots using correct allocations
        from engines.live import bank_state
        bank_state.init_bank_state()
        bank_state.start_bankstate_reporter(60)      
        try:
            pots  = bank_state.get_engine_pots()
            used  = bank_state.get_engine_used_map()
            avail = bank_state.get_engine_available_map()
            open_exp = bank_state.get_open_exposure()
            print(
                f"[BankState] initialised "
                f"pots={pots} used={used} avail={avail} open={open_exp:.2f}"
            )
        except Exception:
            print("[BankState] initialised (no diagnostics)")

    except Exception as e:
        print(f"[INIT] BudgetManager/BankState setup warn: {e}")

    # === PATCH END ==============================================================





    # ------------------------------------------------------------------
    # 4) RELOAD WRITERS AND LIVE MODULES (WITHOUT HIJACK YET)
    # ------------------------------------------------------------------
    try:
        import importlib
        import engines.odds.writers as _rw
        import engines.odds.odds_service as _os
        import engines.risk.budget_manager as _bm
        import engines.indicators.market_data as _md
        import engines.market_monitor.monitor as _mm
        import engines.live.live_router as _lr
        import engines.live.settlements as _ls
        import engines.live.overwatcher as _ow
        import engines.mastery.feedback_scheduler as _fs
        import engines.decision_engine.decide_once.helpers as _dh
        for m in (_rw, _os, _bm, _md, _mm, _lr, _ls, _ow, _fs, _dh):
            importlib.reload(m)
        print("[LIVE DAL] reload complete (writers reopened)")
    except Exception as e:
        print(f"[LIVE DAL] reload warn: {e}")

    # ------------------------------------------------------------------
    # 5) REPAIR SCHEMA (SAFE TIME)
    # ------------------------------------------------------------------
    AUTOSCALP_USE_HIJACK = False
    # Inject into live-loop startup (called once)
    try:
        _dal_verify_and_repair_schema()
    except Exception as e:
        print(f"[DAL-SCHEMA] fatal: {e}")


    # ------------------------------------------------------------------
    # 6) BLUEPRINT LOAD + SETTLEMENT LOOP + MASTERY
    # ------------------------------------------------------------------
    try:
        from engines.blueprint.runtime import load_blueprints
        obj = load_blueprints()
        total = len(obj.get("pattern_dict_export") or obj)
        print(f"[blueprints] cache ready {total}")
    except Exception as e:
        print(f"[blueprints] warn: {e}")

    try:
        from engines.live.settlements import start_all_settlement_services
        start_all_settlement_services()
    except Exception as e:
        print(f"[settlements] warn: {e}")


    # disable old paths
    global F_ENABLE_STRATS
    F_ENABLE_STRATS = False

    # --------------------------------------------------------------
    # 6C.1) START ROUTER HOUSEKEEPING LOOP (REHEDGE / CLEANUP)
    # --------------------------------------------------------------
    try:
        from engines.live.live_router import _start_rehedge_loop
        _start_rehedge_loop()
        print(f"[REHEDGE_LOOP][STARTED]")
    except Exception as e:
        print(f"[REHEDGE_LOOP][WARN] failed to start rehedge loop: {e}")


# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: "# 7) START BUS TICKER"
# 📆 PATCHED: 2026-02-15 — MarketMonitor prime before BUS ticker
# PURPOSE:
#   • LiveLoop/BUS must never see stale runner PX
#   • Ensures MarketMonitor.refresh() is called every cycle
#   • Fixes CTX px=None, band=None issues
# ============================================================================

    # --------------------------------------------------------------
    # 6B) MARKET MONITOR PRIMER (runs once before BUS starts)
    # --------------------------------------------------------------
    try:
        from engines.market_monitor.monitor import refresh as mm_refresh
        from engines.decision_engine.decide_once.scope import build_and_maintain_scope

        scope0 = build_and_maintain_scope() or {}
        mids0  = [m["marketId"] for m in scope0.get("markets", []) if isinstance(m, dict)]

        if mids0:
            print(f"[ORCH] priming MarketMonitor for {len(mids0)} markets…")
            mm_refresh(mids0, max_runners=20)
            print("[ORCH] MarketMonitor primed successfully")
        else:
            print("[ORCH] no mids found at bootstrap, MarketMonitor skipped")

    except Exception as e:
        print(f"[ORCH][WARN] MarketMonitor bootstrap failed: {e}")


    # --------------------------------------------------------------
    # 6C) START PLACEMENT WORKER
    # --------------------------------------------------------------
    # ======================================================================================================
    # 📍 TARGET: live startup / orchestrator file
    # 🔎 ANCHOR: start_live_loop / startup section
    # 🧩 ACTION: REPLACE worker startup import + call
    # 📆 PATCHED: 2025-12-17 — Start placement worker (execution owner)
    # ======================================================================================================

    # OLD (remove or comment)
    # from engines.live.live_router import start_router_worker
    # start_router_worker()

    # NEW
    from engines.decision_engine.decide_once.placement import start_placement_worker
    from engines.live.live_router import start_router_child_worker, _router_child_recovery_sweep, start_router_parent_worker

    
    start_placement_worker(run_id=run_id)
    _router_child_recovery_sweep()
    start_router_child_worker()
    start_router_parent_worker()

    # --------------------------------------------------------------
    # 6C.1) START ROUTER HOUSEKEEPING LOOP (REHEDGE / CLEANUP)
    # --------------------------------------------------------------
    try:
        from engines.live.live_router import _start_rehedge_loop
        _start_rehedge_loop()
    except Exception as e:
        print(f"[LIVE_ROUTER][WARN] failed to start rehedge loop: {e}")

    # --------------------------------------------------------------
    # 6D) START BRAIN LISTENER
    # --------------------------------------------------------------
    from engines.brain.brain_listener import start_brain_listener
    start_brain_listener()

    # --------------------------------------------------------------
    # 6E) START CONTEXT OBSERVER
    # --------------------------------------------------------------
    from engines.mastery.context_observer_v7 import start_context_observer
    start_context_observer()

    # --------------------------------------------------------------
    # 6E.1) START STRUCTURAL DIRECTION LOOP
    # --------------------------------------------------------------

    from engines.micro_scalper_v7.structural_direction_loop import start_structural_direction_loop

    start_structural_direction_loop(interval_s=5)


    # --------------------------------------------------------------
    # 6E.2) START FEEDBACK ASSIMILATOR (RIVER)
    # --------------------------------------------------------------
    try:
        from engines.mastery.feedback_assimilator import start_feedback_assimilator
        start_feedback_assimilator(interval_s=300, limit_minutes=15)
    except Exception as e:
        print(f"[ASSIMILATOR][WARN] failed to start: {e}")

    # --------------------------------------------------------------
    # 6F) START INPLAY MONITORING
    # --------------------------------------------------------------
    from engines.inplay.inplay_flag_helper import start_inplay_authority_report_loop

    start_inplay_authority_report_loop()


    # --------------------------------------------------------------
    # 6G) LIVE ROUTER BUDGET ALLOCATIONS
    # --------------------------------------------------------------
    from engines.live import bank_state

    bank_state.init_from_budget_allocations()

    # ------------------------------------------------------------------
    # 6H) AFTER BankState.init_bank_state()
    # ------------------------------------------------------------------
    try:


        from engines.exposure.exposure_guardian import ExposureGuardian
        _EXPOSURE_GUARDIAN = ExposureGuardian(interval_s=300)
        _EXPOSURE_GUARDIAN.start()
    except Exception as e:
        print(f"[EXPOSURE-GUARDIAN] failed to start: {e}")

    # ------------------------------------------------------------------
    # 6H.1) START EXECUTION SURFACE LOOP
    # ------------------------------------------------------------------
    from tools.betfair_match_surface import start_execution_surface_loop

    start_execution_surface_loop(period_s=2.0)

    # ------------------------------------------------------------------
    # 6H.2) START RUNNER SURFACE LOOP
    # ------------------------------------------------------------------

    from tools.betfair_runner_trend_surface import start_runner_trend_surface

    start_runner_trend_surface(refresh_s=5)

    # ------------------------------------------------------------------
    # 6H.3) START LIABILITY SURFACE LOOP
    # ------------------------------------------------------------------

    try:
        from tools.betfair_liability_surface import start_liability_surface_loop
        start_liability_surface_loop(period_s=5)
    except Exception as e:
        print(f"[LIABILITY SURFACE][WARN] failed to start: {e}")

    # ------------------------------------------------------------------
    # 6I) START UNIFIED REPORT LOOP
    # ------------------------------------------------------------------

    from engines.micro_scalper_v7.unified_engine import start_unified_reporter

    start_unified_reporter(interval_s=5)


    # ------------------------------------------------------------------
    # 7) START BUS LOOP (BUS-OWNED LOOP)
    # ------------------------------------------------------------------
    try:
        from engines.bus.bus import BUS
        import threading

        t = threading.Thread(
            target=BUS.run_live,
            kwargs={"hz": hz},
            name="BUSLoop",
            daemon=True,
        )
        t.start()

        print("[ORCH] BUS live loop started")

    except Exception as e:
        print(f"[ORCH][WARN] BUS loop failed to start: {e}")





def run_test_day(run_id: str, seconds: int = 600, hz: int = 4, logger=None) -> None:
    """
    NO-OP PLACEHOLDER.
    Legacy TestDay runner is disabled under BUS-mode orchestration.
    This function intentionally does nothing, preserving API compatibility.
    """
    return None

# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def _adapt_and_decide(
def _adapt_and_decide(strat, market_ctx: dict | None, runner_ctx: dict | None, logger=None):
    """
    Universal adapter to invoke a strategy in any of the 3 shapes:
      - StrategyBase.evaluate(market_ctx, runner_ctx)
      - .decide(runner_ctx)
      - function-style: strat(runner_ctx)

    Returns the strategy's Instruction(s) or None.
    """
    return None
 
# ───────── minute windows & tags ─────────
_MIN_PRE_START = 60   # start PRE passes at T-60m
_MIN_PRE_STOP  = 0    # ⬅ change from 5 to 0: stop at the off (T-0)
_MIN_IP_STOP   = -15  # stop IP passes at T-15m (negative minutes)









def _strat_letter_for(name: str) -> str:
    try:
        return STRAT_CODE.get(name.upper(), name[:1].upper())
    except Exception:
        return name[:1].upper()



# ── candidate sweep: all markets, fav+contenders with odds ≤ 8, T-60..T-5 ──
def _minute_candidates(limit_per_market: int = 4, max_markets: int = 10) -> list[tuple[str, str]]:
    import sqlite3
    mids: list[str] = []
    mids = [m for m in mids if _is_today_in_scope(m)]
    out: list[tuple[str, str]] = []

    # collect today's markets with TTO in window (60..5)
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        rows = _q_retry(bdb, "SELECT marketId, off_at_utc FROM markets_schedule "
            "WHERE date(off_at_utc)=date('now')"
        ).fetchall()
        now = _utcnow()
        for r in rows:
            try:
                off = _dt.strptime(str(r['off_at_utc'])[:19], "%Y-%m-%d %H:%M:%S")
                mto = (off - now.replace(tzinfo=None)).total_seconds() / 60.0
                if _MIN_PRE_STOP <= mto <= _MIN_PRE_START:
                    mids.append(str(r["marketId"]))
                    if len(mids) >= max_markets:
                        break
            except Exception:
                continue
        bdb.close()
    except Exception:
        pass

    if not mids:
        return out

    # for each market, take up to top-4 runners with oc1 ≤ 8 (latest anchors)
    con = _auto_conn(); con.row_factory = sqlite3.Row
    for mid in mids:
        try:
            rws = _q_retry(con, "SELECT selectionId, oc1 FROM inbound_oc_cache "
                "WHERE marketId=? ORDER BY id DESC", (mid,)
            ).fetchall()
            seen = set(); pairs = []
            for r in rws:
                sid = str(r["selectionId"]); oc1 = r["oc1"]
                if sid in seen or oc1 is None:
                    continue
                seen.add(sid)
                try:
                    if float(oc1) <= 8.0:
                        pairs.append((mid, sid))
                except Exception:
                    continue
                if len(pairs) >= limit_per_market:
                    break
            out.extend(pairs)
        except Exception:
            continue
    return out[: max_markets * limit_per_market]

def _legacy_scout_plan(ctx: dict) -> dict:
    """
    Simple direction-only scout plan if mastery is unavailable.
    BACK->LAY on steam (odds falling), LAY->BACK on drift (odds rising).
    1–2 tick target, small size from size_cap (or £2).
    """
    try:
        slope = float(ctx.get("slope_per_min", ctx.get("slope_ppm", 0.0)) or 0.0)
    except Exception:
        slope = 0.0
    try:
        odds = float(ctx.get("odds", ctx.get("entry_odds", ctx.get("current_odds", 0.0))) or 0.0)
    except Exception:
        odds = 0.0

    # only act in a reasonable odds band
    if not (1.5 <= odds <= 8.0):
        return {"enter": False, "why": "odds_out_of_band"}

    # direction from slope (tighten later if you like)
    if slope < -0.03:
        direction = "BACK->LAY"   # steamer
    elif slope > +0.03:
        direction = "LAY->BACK"   # drifter
    else:
        return {"enter": False, "why": "slope_flat"}

    size_cap = float(ctx.get("size_cap", 2.0) or 2.0)
    ticks    = 2 if abs(slope) >= 0.08 else 1
    return {
        "enter": True,
        "direction": direction,
        "target_ticks": ticks,
        "size": max(2.0, min(size_cap, 5.0)),
        "why": f"fallback_scout slope={slope:.3f}"
    }

def _alts_for(fname: str, c: dict, mid: str, sid: str, ORDER_MAP: dict[str, callable]) -> list[str]:
    """Alternate families ordered by steam/drift + fav status."""
    sig = _steam_drift_signal(c)
    try:
        fav, *_ = _is_favourite(mid, sid)
        fav = bool(fav)
    except Exception:
        fav = False

    # BACK-entry families (BTL) vs LAY-entry families (LTB)
    btl = ["BTL_SCOUT", "BTL_AGGR", "S4_CROSSOVER"]
    ltb = ["OG_BIAS", "S5_BREAKOUT"]

    if sig == "steam":
        seq = btl + ltb
    elif sig == "drift":
        seq = ltb + btl
    else:
        seq = (btl if fav else ltb) + (ltb if fav else btl)

    return [x for x in seq if x != fname and x in ORDER_MAP]

# ─────────────────────────────────────────────────────────────────────────────
# Family lanes (PRE: B,G,X,R,F,L) and (IN-PLAY: I)
# Fires one placement per decide_once, before A-lane, using scope + Active runners.
# ─────────────────────────────────────────────────────────────────────────────
def _family_lanes_pass(run_id: str, ctx: dict, logger=None) -> Optional[int]:
    def _canonical_name_for_letter(letter: str) -> str:
        return {
            "A": "ALWAYS_ON",
            "B": "BTL_SCOUT",
            "G": "BTL_AGGR",
            "X": "S4_CROSSOVER",
            "R": "S5_BREAKOUT",
            "F": "S6_STEAM_FADE",
            "M": "LADDER_STRATEGY",
            "Z": "OG_BIAS",
            "I": "IP1_SHOCK_DRIFT",
            "T": "IP2_TIRED_LEADER",
        "    C": "IP3_CLOSE_FINISH",
            "E": "IP4_FENCE_ERROR",
            "K": "IP5_COLLAPSE_FADE",
        "S": "LEGACY_S",
    }.get(letter.upper(), f"FAMILY_{letter.upper()}")
    
    # Build today scope, trim to in-scope markets
    scope = _build_scope(now_utc=_utcnow(), inplay_window_min=15)
    pre_near = list(scope.get("pre_near", []))  # [(mid, tto)]
    in_play  = list(scope.get("in_play", []))   # [(mid, elapsed)]

    # Strategy registry (name -> fn) + enabled mask if present
    try:
        from engines.decision_engine.strategies.registry import ORDER as ORDER_LIST, ENABLED as ORDER_ENABLED
    except Exception:
        ORDER_LIST, ORDER_ENABLED = [], {}
    ORDER_MAP = {nm: fn for (nm, fn) in (ORDER_LIST or [])}

    # Map: STRAT_CODE must already exist in this module (you have it)
    # Families we want lanes for:
    FAMILIES = [
        ("B", "PRE"),  # BTL_SCOUT
        ("G", "PRE"),  # BTL_AGGR
        ("X", "PRE"),  # S4_CROSSOVER
        ("R", "PRE"),  # S5_BREAKOUT
        ("F", "PRE"),  # S6_STEAM_FADE
        ("L", "PRE"),  # OG_BIAS / lay-family
        ("I", "IP"),   # IP1_SHOCK_DRIFT   (in-play only)
    ]

    # --- per-minute throttle (shared across all lanes) -----------------------
    try:
        wall_min = int(_utcnow().timestamp() // 60)
    except Exception:
        import time as _t
        wall_min = int(_t.time() // 60)

    _ATTEMPTED = getattr(decide_once, "_ATTEMPTED_MINUTE", set())
    last_wall  = getattr(decide_once, "_ATTEMPTED_WALL", None)

    if last_wall != wall_min:
        _ATTEMPTED.clear()                      # ← reset once per minute
        setattr(decide_once, "_ATTEMPTED_WALL", wall_min)

    # keep the set object in place so references elsewhere still work
    setattr(decide_once, "_ATTEMPTED_MINUTE", _ATTEMPTED)


    # Helper: all strategy names that belong to a given letter (family)
    def _names_for_letter(letter: str) -> list[str]:
        # Prefer registered strategy names that map to this letter
        names: list[str] = []
        want = (letter or "")[:1].upper()

        for nm in ORDER_MAP.keys():
            try:
                lt = STRAT_CODE.get(nm.upper(), nm[:1].upper())
            except Exception:
                lt = nm[:1].upper()
            if lt == want:
                names.append(nm)

        if names:
            return names

        # No registered strategies for this letter → still return a canonical name
        return [_canonical_name_for_letter(want)]

    # ── PRE lanes (use scope.pre_near, 0 < TTO ≤ 60, Active runners only)
    def _run_pre_family(letter: str) -> Optional[int]:
        names = _names_for_letter(letter)
        if not names:
            return None

        for (mid, tto) in pre_near:
            if not _is_today_in_scope(mid):
                continue

            # recompute TTO for this market (used for pass tag)
            try:
                mto_fix, win_fix = _compute_minutes_to_off(mid, source=ctx.get("source", "LIVE"))
            except Exception:
                mto_fix, win_fix = (float(tto), "")

            if not (0.0 < float(mto_fix) <= 60.0):
                continue

            try:
                # Active runners only
                cands = _cands_from_inbound(mid, max_runners=8)
                for (sid, odds) in cands:
                    key = (mid, sid, letter, wall_min)
                    if keyX in _ATTEMPTED:
                        continue
                    _ATTEMPTED.add(keyX)
                    setattr(_family_lanes_pass, "_TRIED", tried)
            except Exception:
                continue

                pass_tag = _pass_tag(letter, float(mto_fix))  # e.g., R60..R05
                if _already_open_pass(mid, sid, pass_tag, mode=ctx.get("source", "LIVE")):
                    continue

                ok_cap, _ = _can_open_scalp(mid, sid, max_per_runner=3, run_id=run_id, family_letter=letter)
                if not ok_cap and (ctx.get("source", "LIVE") != "TEST"):
                    continue

                # Build runner ctx for strategies
                ctx["marketId"] = mid
                ctx["selectionId"] = sid
                ctx["odds"] = float(odds)
                ctx["ltp"] = float(odds)
                ctx["minutes_to_off"] = float(mto_fix)
                ctx["tto_minutes"] = float(mto_fix)
                if win_fix:
                    ctx["tto_window"] = win_fix
                ctx["phase"] = "PRE"
                ctx["pass_tag"] = pass_tag

                # Try registered names; if none/fail, use fallback per-letter plan
                for nm in names:
                    if ORDER_ENABLED and not ORDER_ENABLED.get(nm, True):
                        continue

                    plan = None
                    fn = ORDER_MAP.get(nm)
                    if fn:
                        try:
                            # === MP plan (one call) with hardening + debug ===
                            ctx_dict = ctx if isinstance(ctx, dict) else (ctx.__dict__ if hasattr(ctx, "__dict__") else dict(ctx or {}))

                            # Coerce risky numeric fields once (prevents NoneType <= int)
                            def _coerce_num(d, k, cast, default):
                                try:
                                    v = d.get(k, None)
                                    d[k] = cast(v) if v is not None else default
                                except Exception:
                                    d[k] = default

                            _coerce_num(ctx_dict, "minutes_to_off", float, 1e9)
                            _coerce_num(ctx_dict, "fav_rank",       int,   99)
                            _coerce_num(ctx_dict, "fav_rank_now",   int,   ctx_dict.get("fav_rank", 99))
                            _coerce_num(ctx_dict, "exposure",       float, 0.0)
                            _coerce_num(ctx_dict, "target_ticks",   int,   1)
                            _coerce_num(ctx_dict, "hedge_ticks",    int,   1)

                            try:
                                plan2 = _mp_plan_for(fname, ctx_dict)
                            except Exception as e:
                                import json, traceback
                                bad = {k: ctx_dict.get(k) for k in (
                                    "minutes_to_off","fav_rank","fav_rank_now","exposure","target_ticks",
                                    "hedge_ticks","price","price_now","ltp","odds","slope_ppm"
                                )}
                                print(f"[TRACE] {fname} decide error mid={mid} sid={sid}: {e}")
                                print("        ctx=", json.dumps(bad, default=str))
                                print("        at  ", traceback.format_exc().splitlines()[-1])
                                continue


                        except Exception:
                            plan = None
                    if not plan or not plan.get("enter"):
                        plan = _fallback_plan_for_letter(letter, ctx)
                
                    from engines.blueprint.overlay import overlay_plan as _bp_overlay
                    if plan and plan.get("enter") and letter == "P":
                        if letter == "P":
                            plan = _bp_overlay(mid, sid, ctx, plan)

                    # Rulebook gate (use next tag for pass number) — no stray try/except
                    preview_code = _next_pair_tag(letter, mid)
                    skip_this, plan = _apply_rulebook(letter, preview_code, ctx, plan)
                    if skip_this:
                        continue

                    if plan and plan.get("enter"):
                        placed = _pf(nm, plan, ctx)
                        if placed:
                            return placed

        return None


    # ── In-Play lane (I) — use scope.in_play, Active runners only
    def _run_ip_family(letter: str) -> Optional[int]:
        names = _names_for_letter(letter)
        if not names:
            return None

        for (mid, _elapsed) in in_play:
            if not _is_today_in_scope(mid):
                continue

            cands = _active_candidates_for_market(mid, max_runners=8)
            for (sid, odds) in cands:
                key = (mid, sid, letter, wall_min)
                if keyX in _ATTEMPTED:
                    continue
                _ATTEMPTED.add(keyX)
                setattr(_family_lanes_pass, "_TRIED", tried)

                pass_tag = _pass_tag(letter, -1.0)  # e.g., I-01
                if _already_open_pass(mid, sid, pass_tag, mode=ctx.get("source", "LIVE")):
                    continue

                ok_cap, _ = _can_open_scalp(mid, sid, max_per_runner=3, run_id=run_id, family_letter=letter)
                if not ok_cap and (ctx.get("source", "LIVE") != "TEST"):
                    continue

                ctx["marketId"] = mid
                ctx["selectionId"] = sid
                ctx["odds"] = float(odds)
                ctx["ltp"] = float(odds)
                ctx["phase"] = "IN_PLAY"
                ctx["pass_tag"] = pass_tag

                for nm in names:
                    if ORDER_ENABLED and not ORDER_ENABLED.get(nm, True):
                        continue

                    plan = None
                    fn = ORDER_MAP.get(nm)
                    if fn:
                        try:
                            ctx_dict = ctx if isinstance(ctx, dict) else (ctx.__dict__ if hasattr(ctx, "__dict__") else dict(ctx or {}))
                            plan = fn(ctx_dict)

                        except Exception:
                            plan = None
                    if not plan or not plan.get("enter"):
                        plan = _fallback_plan_for_letter(letter, ctx)

                    from engines.blueprint.overlay import overlay_plan as _bp_overlay
                    if plan and plan.get("enter") and letter == "P":
                        if letter == "P":
                            plan = _bp_overlay(mid, sid, ctx, plan)

                    # Rulebook gate — no stray try/except
                    preview_code = _next_pair_tag(letter, mid)
                    skip_this, plan = _apply_rulebook(letter, preview_code, ctx, plan)
                    if skip_this:
                        continue

                    if plan and plan.get("enter"):
                        placed = _pf(nm, plan, ctx)
                        if placed:
                            return placed

        return None


    # Execute families in a fixed order (all PRE first, then IP)
    for fam, phase in FAMILIES:
        placed = _run_pre_family(fam) if phase == "PRE" else _run_ip_family(fam)
        if placed:
            return placed

    return None

# ---- Odds map refresher (populates AUTO_DB.odds_current) -------------------
_ODDS_OVERLAY: dict[tuple[str,str], tuple[float, list[float] | None, float]] = {}
# (mid, sid) -> (ltp, band, epoch)

def _refresh_odds_cache_for_scope(scope: dict, *, day_sql: str = "date('now')") -> int:
    """
    For all markets in scope (WIN5 + ≤20 + INPLAY + SIGNALS), upsert ltp/back1 into AUTO_DB.odds_current.
    Matches deployed schema: (day, marketId, selectionId, updated_ts, ltp, back1, lay1...).
    Returns rows-upserted (0 if none).
    """
    _ensure_odds_current_columns()
    try:
        markets = {m for (m, _t) in (scope.get("pre_near") or [])} \
                | {m for (m, _t) in (scope.get("pre_far")  or [])} \
                | {m for (m, _e) in (scope.get("in_play")  or [])} \
                | set(scope.get("signals") or [])
        if not markets:
            return 0

        con = _auto_conn(); con.row_factory = sqlite3.Row
        _q_retry(con, """
            CREATE TABLE IF NOT EXISTS odds_current(
              day TEXT NOT NULL,
              marketId TEXT NOT NULL,
              selectionId TEXT NOT NULL,
              updated_ts TEXT,
              ltp REAL, back1 REAL, lay1 REAL,
              PRIMARY KEY(day, marketId, selectionId)
            )
        """)

        upserts = 0
        now_s = _utcnow_str()

        for mid in markets:
            try:
                rows = _q_retry(con, """
                    SELECT selectionId, oc1, anchor_odd, oc1_band_json
                    FROM inbound_oc_cache
                    WHERE marketId=? ORDER BY id DESC
                """, (str(mid),)).fetchall()

                first: dict[str, float] = {}
                seen = set()
                for r in rows or []:
                    sid = str(r["selectionId"])
                    if sid in seen: continue
                    px = r["oc1"] if r["oc1"] is not None else r["anchor_odd"]
                    if px is None and r["oc1_band_json"]:
                        try:
                            import json
                            arr = json.loads(r["oc1_band_json"]) or []
                            if arr: px = float(arr[-1])
                        except Exception:
                            px = None
                    if px is not None:
                        first[sid] = float(px)
                    seen.add(sid)

                for sid, px in first.items():
                    _q_retry(con, f"""
                        INSERT INTO odds_current(day, marketId, selectionId, ltp, back1, updated_ts)
                        VALUES (({day_sql}), ?, ?, ?, ?, ?)
                        ON CONFLICT(day, marketId, selectionId)
                        DO UPDATE SET
                          ltp=excluded.ltp,
                          back1=excluded.back1,
                          updated_ts=excluded.updated_ts
                    """, (str(mid), str(sid), float(px), float(px), now_s))
                    upserts += 1
            except Exception:
                continue

        con.commit()
        return upserts
    except sqlite3.OperationalError:
        return 0
    except Exception:
        return 0


def refresh_scope_tape_once(inplay_window_min: int = 15) -> int:
    """Build scope and refresh odds_current for it. Returns upsert count."""
    try:
        sc = _build_scope(now_utc=_utcnow(), inplay_window_min=inplay_window_min)
    except Exception:
        sc = {"pre_near": [], "pre_far": [], "in_play": [], "signals": []}
    try:
        # Prefer folder-helper if present (same behavior), else local
        from engines.decision_engine.decide_once.helpers import refresh_odds_current_for_markets as _refmkts
        mids = [m for (m, _t) in (sc.get("pre_near") or [])] + \
               [m for (m, _t) in (sc.get("pre_far") or [])]  + \
               [m for (m, _e) in (sc.get("in_play") or [])]
        return int(_refmkts(list(dict.fromkeys(mids)), max_runners=8))
    except Exception:
        return int(_refresh_odds_cache_for_scope(sc))



# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def decide_once(
# ⛏️ ACTION: replace function body to call the folderized version
def decide_once(run_id: str, source_override: str | None = None, logger=None):
    from engines.decision_engine.decide_once.decide_once import decide_once as _do
    return _do(run_id, source_override=source_override, logger=logger)
# === PATCH END ===

def _get_run_bounds(run_id: str) -> tuple[Optional[str], Optional[str]]:
    db = _bets_conn()
    row = _q_retry(db, "SELECT started_at, ended_at FROM sim_runs WHERE run_id=? ORDER BY id DESC LIMIT 1",
        (run_id,)
    ).fetchone()
    if not row:
        return None, None
    return (row["started_at"] if isinstance(row, sqlite3.Row) else row[0],
            row["ended_at"]   if isinstance(row, sqlite3.Row) else row[1])


def _orders_cols(con: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in _rows(con, "PRAGMA table_info(orders)")]



def _eod_report(run_id: str, run_day: int, month_index: int, logger=None) -> None:
    """
    End-of-Day report:
      - parents/hedges matched/open, hit-rate
      - distinct runners, top runners
      - live P&L (trades in run window) + ledger P&L (pnl_daily for run_day)
      - Win % by market (market sum>0 = win, <0 = loss)
      - Scalps metrics: matched, avg scalps/hour, avg gain/scalp, P&L/hour
    """
   

    src = _current_source()

    # --- AUTO_DB: parents/hedges/open, distinct runners, top runners ----------
    con = _auto_conn()
    con.row_factory = sqlite3.Row
    link_col = _ensure_orders_link_col(con)
    has_run = ("run_id" in _orders_cols(con))

    where_run = " AND run_id=?" if has_run else ""
    args = (run_id,) if has_run else tuple()

    # parent vs child masks
    if link_col:
        parent_where = f"({link_col} IS NULL OR {link_col}='')"
        child_where  = f"({link_col} IS NOT NULL AND {link_col}<>'')"
    else:
        parent_where = "1=1"
        child_where  = "0=1"

    q_int = lambda sql, a=(): int(_exec_retry(con, sql, a).fetchone()[0] or 0)

    parents_total   = q_int(f"SELECT COUNT(*) FROM orders WHERE {parent_where}{where_run}")
    parents_matched = q_int(f"SELECT COUNT(*) FROM orders WHERE {parent_where}{where_run} AND entry_status='matched'")
    parents_open    = q_int(f"SELECT COUNT(*) FROM orders WHERE {parent_where}{where_run} AND (closed_at IS NULL OR closed_at='')")

    children_total   = q_int(f"SELECT COUNT(*) FROM orders WHERE {child_where}{where_run}")
    children_matched = q_int(f"SELECT COUNT(*) FROM orders WHERE {child_where}{where_run} AND entry_status='matched'")
    children_open    = q_int(f"SELECT COUNT(*) FROM orders WHERE {child_where}{where_run} AND (closed_at IS NULL OR closed_at='')")

    runners_distinct = q_int(f"SELECT COUNT(DISTINCT marketId || '/' || selectionId) FROM orders WHERE {parent_where}{where_run}")

    # market P&L (parents only) for win%
    mkt_rows = _q_retry(con, f"""
        SELECT marketId AS mid, COALESCE(SUM(net_pl),0.0) AS pnl
        FROM orders
        WHERE {parent_where}{where_run} AND entry_status='matched'
        GROUP BY marketId
        """,
        args
    ).fetchall()
    eps = 1e-9
    mkt_wins    = sum(1 for r in mkt_rows if float(r["pnl"]) >  eps)
    mkt_losses  = sum(1 for r in mkt_rows if float(r["pnl"]) < -eps)
    mkt_neutral = sum(1 for r in mkt_rows if abs(float(r["pnl"])) <= eps)
    win_denom   = mkt_wins + mkt_losses
    win_pct     = (mkt_wins / win_denom) if win_denom > 0 else 0.0

    # top runners by entries (parents)
    top_rows = _q_retry(con, f"""
        SELECT marketId, selectionId, COUNT(*) AS n
        FROM orders
        WHERE {parent_where}{where_run}
        GROUP BY marketId, selectionId
        ORDER BY n DESC
        LIMIT 5
        """,
        args
    ).fetchall()
    top_runners = [f"{r['marketId']}/{r['selectionId']} ({r['n']})" for r in top_rows]

    # --- BETS_DB: run window sums --------------------------------------------
    live_sum = 0.0
    started_at, ended_at = _get_run_bounds(run_id)
    db = _bets_conn()
    if started_at and _q_retry(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_trades'").fetchone():
        has_src = any((r[1] if isinstance(r, tuple) else r["name"]) == 'source' for r in _rows(db, "PRAGMA table_info('pnl_trades')"))
        if ended_at:
            if has_src:
                live_sum = float(_q_retry(db, "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE source=? AND datetime(created_at)>=datetime(?) AND datetime(created_at)<=datetime(?)",
                    (src, started_at, ended_at)
                ).fetchone()[0] or 0.0)
            else:
                live_sum = float(_q_retry(db, "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE datetime(created_at)>=datetime(?) AND datetime(created_at)<=datetime(?)",
                    (started_at, ended_at)
                ).fetchone()[0] or 0.0)
        else:
            if has_src:
                live_sum = float(_q_retry(db, "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE source=? AND datetime(created_at)>=datetime(?)",
                    (src, started_at)
                ).fetchone()[0] or 0.0)
            else:
                live_sum = float(_q_retry(db, "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE datetime(created_at)>=datetime(?)",
                    (started_at,)
                ).fetchone()[0] or 0.0)

    ledger_sum = 0.0
    if _q_retry(db, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_daily'").fetchone():
        has_src_ledger = any((r[1] if isinstance(r, tuple) else r["name"])=='source' for r in _rows(db, "PRAGMA table_info('pnl_daily')"))
        if has_src_ledger:
            ledger_sum = float(_q_retry(db, "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day=? AND source=?",
                (run_day, src)
            ).fetchone()[0] or 0.0)
        else:
            ledger_sum = float(_q_retry(db, "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day=?",
                (run_day,)
            ).fetchone()[0] or 0.0)

    # --- Durations & averages -------------------------------------------------
    def _parse(ts: Optional[str]) -> Optional[datetime]:
        if not ts: return None
        try:
            return datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None

    start_dt = _parse(started_at)
    end_dt   = _parse(ended_at) or datetime.utcnow()
    seconds  = max(0.0, (end_dt - start_dt).total_seconds() if start_dt else 0.0)
    hours    = max(seconds / 3600.0, 1e-9)

    matched_scalps       = parents_matched
    avg_scalps_per_hour  = matched_scalps / hours
    avg_gain_per_scalp   = (live_sum / matched_scalps) if matched_scalps > 0 else 0.0
    pnl_per_hour         = live_sum / hours
    hit_rate             = (children_matched / parents_total) if parents_total > 0 else 0.0


    # --- Print to logger ------------------------------------------------------
    if logger:
        logger(f"EOD — run_id={run_id}  day={run_day}  month={month_index}  source={src}")
        logger(f"Parents: total={parents_total} matched={parents_matched} open={parents_open}")
        logger(f"Hedges:  total={children_total} matched={children_matched} open={children_open}  hit={hit_rate:.2%}")
        logger(f"Runners scalped: {runners_distinct}  Top: {', '.join(top_runners) if top_runners else '-'}")
        logger(f"Market wins/losses (live in window): wins={mkt_wins} losses={mkt_losses} neutral={mkt_neutral}  win%={win_pct:.2%}")
        logger(f"Scalps: matched={matched_scalps}  avg/hour={avg_scalps_per_hour:.2f}  avg gain/scalp=£{avg_gain_per_scalp:.2f}  P&L/hour=£{pnl_per_hour:.2f}")
        logger(f"P&L live (trades window): £{live_sum:.2f}  |  P&L ledger (day): £{ledger_sum:.2f}")
        logger("—"*60)

# ─────────────────────────────────────────────────────────────────────────────
# Runner activity + per-runner open-cap (3-at-a-time) helpers
# ─────────────────────────────────────────────────────────────────────────────
# --- PATCH START: odds→status + persistence helpers --------------------------
def _status_from_odds(odds: float | None) -> str:
    """Classify a runner from current odds."""
    if odds is None:
        return "active"      # safe default
    if odds <= 8.0:
        return "active"
    if odds <= 12.0:
        return "passive"
    return "ignored"

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def _snapshot_runner_activity
# ⛏️ ACTION: replace function body

def _snapshot_runner_activity(day: str, stats: dict) -> None:
    """
    Write one row per day into dashboard_activity.
    Schema has no 'id' column → keyed by day.
    """
    con = _auto_conn(); con.row_factory = sqlite3.Row
    try:
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
        _q_retry(con, """
            INSERT INTO dashboard_activity(
              day, window_start_ts, runners_scanned, decisions, proposals,
              parents_placed, hedges_matched, cancels, timeouts, open_parents,
              recent_events_json, gate_reasons_json, last_refreshed_ts
            )
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(day) DO UPDATE SET
              window_start_ts=excluded.window_start_ts,
              runners_scanned=excluded.runners_scanned,
              decisions=excluded.decisions,
              proposals=excluded.proposals,
              parents_placed=excluded.parents_placed,
              hedges_matched=excluded.hedges_matched,
              cancels=excluded.cancels,
              timeouts=excluded.timeouts,
              open_parents=excluded.open_parents,
              recent_events_json=excluded.recent_events_json,
              gate_reasons_json=excluded.gate_reasons_json,
              last_refreshed_ts=datetime('now')
        """, (
            day,
            stats.get("window_start_ts"),
            int(stats.get("runners_scanned", 0)),
            int(stats.get("decisions", 0)),
            int(stats.get("proposals", 0)),
            int(stats.get("parents_placed", 0)),
            int(stats.get("hedges_matched", 0)),
            int(stats.get("cancels", 0)),
            int(stats.get("timeouts", 0)),
            int(stats.get("open_parents", 0)),
            json.dumps(stats.get("recent_events", {})),
            json.dumps(stats.get("gate_reasons", {})),
        ))
        con.commit()
    except Exception as e:
        print(f"[ACTIVITY] warn: {e}")
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===





def _ensure_odds_current_columns() -> None:
    """
    One-shot normalizer for AUTO_DB.odds_current to match the deployed schema:
      - ensure updated_ts exists (and backfill from updated_at if present)
      - ensure back1/lay1 exist (writers commonly set back1=ltp, lay1 optional)
    Safe to call many times.
    """
    con = _auto_conn(); con.row_factory = sqlite3.Row
    try:
        cols = {r["name"] for r in _rows(con, "PRAGMA table_info(odds_current)")}
        altered = False

        if "updated_ts" not in cols:
            _q_retry(con, "ALTER TABLE odds_current ADD COLUMN updated_ts TEXT")
            altered = True
            cols = {r["name"] for r in _rows(con, "PRAGMA table_info(odds_current)")}
        # backfill from legacy updated_at if present
        if "updated_ts" in cols and "updated_at" in cols:
            _q_retry(con, "UPDATE odds_current SET updated_ts = COALESCE(updated_ts, updated_at) "
                          "WHERE updated_ts IS NULL OR updated_ts=''")
            altered = True

        if "back1" not in cols:
            _q_retry(con, "ALTER TABLE odds_current ADD COLUMN back1 REAL")
            altered = True
        if "lay1" not in cols:
            _q_retry(con, "ALTER TABLE odds_current ADD COLUMN lay1 REAL")
            altered = True

        if altered: con.commit()
    except Exception:
        try: con.rollback()
        except Exception: pass
    finally:
        try: con.close()
        except Exception: pass

# ============================================================
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: ^def _runner_activity\(marketId: str, selectionId: str\) -> str:
# ⛏️ ACTION: replace function body to remove DB dependency
# ============================================================
def _runner_activity(marketId: str, selectionId: str) -> str:
    """
    Return 'active'|'ignored'|'passive' based purely on odds range.
    Uses autoscalp_gui.odds_current (preferred) or inbound_oc_cache (fallback).
    """
    import sqlite3
    status = "ignored"
    odds = None
    try:
        from engines.config_paths import autoscalp_db
        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
        row = con.execute("""
            SELECT ltp FROM odds_current
             WHERE day=date('now') AND marketId=? AND selectionId=?
             ORDER BY datetime(updated_ts) DESC LIMIT 1
        """, (str(marketId), str(selectionId))).fetchone()
        if row and row["ltp"] is not None:
            odds = float(row["ltp"])
        else:
            r2 = con.execute("""
                SELECT oc1 FROM inbound_oc_cache
                 WHERE date(last_sync_ts)=date('now')
                   AND marketId=? AND selectionId=?
                 ORDER BY datetime(last_sync_ts) DESC LIMIT 1
            """, (str(marketId), str(selectionId))).fetchone()
            if r2 and r2["oc1"] is not None:
                odds = float(r2["oc1"])
        con.close()
    except Exception:
        odds = None

    try:
        if odds is not None:
            if 1.5 <= odds <= 8.0:
                status = "active"
            elif odds <= 12.0:
                status = "passive"
            else:
                status = "ignored"
    except Exception:
        status = "ignored"
    return status


# engines/decision_engine/orchestrator.py
# === PATCH START ===
def _open_parents_count(market_id: str, selection_id: str) -> int:
    con = _adb(); con.row_factory = sqlite3.Row
    try:
        r = con.execute("""
            SELECT COUNT(*) AS n
              FROM orders p
             WHERE p.marketId = ?
               AND p.selectionId = ?
               AND p.mode = 'LIVE'
               AND (p.hedge_of IS NULL OR p.hedge_of = '')
               AND p.status IN ('matched','open','queued')
               AND NOT EXISTS (
                     SELECT 1 FROM orders c
                      WHERE c.hedge_of = p.id
                        AND c.mode = 'LIVE'
                        AND c.status = 'matched'
               )
        """, (str(market_id), str(selection_id))).fetchone()
        return int(r["n"] if r and r["n"] is not None else 0)
    except Exception:
        return 0
    finally:
        try: con.close()
        except Exception: pass

def _open_parents_count_letter(market_id: str, selection_id: str, letter: str, run_id: str | None = None) -> int:
    con = _adb(); con.row_factory = sqlite3.Row
    try:
        r = con.execute("""
            SELECT COUNT(*) AS n
              FROM orders p
             WHERE p.marketId = ?
               AND p.selectionId = ?
               AND p.mode = 'LIVE'
               AND (p.hedge_of IS NULL OR p.hedge_of = '')
               AND p.status IN ('matched','open','queued')
               AND UPPER(COALESCE(p.source,'')) = UPPER(?)
               AND NOT EXISTS (
                     SELECT 1 FROM orders c
                      WHERE c.hedge_of = p.id
                        AND c.mode = 'LIVE'
                        AND c.status = 'matched'
               )
        """, (str(market_id), str(selection_id), str(letter))).fetchone()
        return int(r["n"] if r and r["n"] is not None else 0)
    except Exception:
        return 0
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===



# --- PATCH START: LEARNING through-fill + commission --------------------------
def check_and_close(order_id: int, direction: str, entry_odds: float, target_ticks: int, stake: float,
                    marketId: Optional[str], selectionId: Optional[str], ctx: Optional[Dict] = None) -> bool:
    """
    Close logic:
      - Compute target hedge odds from entry + target_ticks
      - Use band tail/high/low to decide if target *hit* (TEST) or *through* (LEARNING)
      - When matched, persist child+parent and record pnl_trades
      - Apply commission to positive P&L in LEARNING (per-trade conservative)
    """
    # Read current price band
    last, band = latest_price(marketId, selectionId)
    if last is None and not band:
        return False

    high = max(band) if band else last
    low  = min(band) if band else last

    # Compute hedge target odds
    if str(direction).upper().startswith("LAY"):   # LAY->BACK (need drift to higher odds)
        target = _odds_plus_ticks(entry_odds, abs(int(target_ticks)))
        # Fill rule
        if _current_source().upper() == "LEARNING":
            # require 'through' by N ticks
            need = _odds_plus_ticks(target, abs(int(LEARNING_FILL_THROUGH_TICKS)))
            hit = (high is not None and high >= need)
        else:
            hit = (high is not None and high >= target)
    else:                                          # BACK->LAY (need steam to lower odds)
        target = _odds_plus_ticks(entry_odds, -abs(int(target_ticks)))
        if _current_source().upper() == "LEARNING":
            need = _odds_plus_ticks(target, -abs(int(LEARNING_FILL_THROUGH_TICKS)))
            hit = (low is not None and low <= need)
        else:
            hit = (low is not None and low <= target)

    if not hit:
        return False

    # Mark hedge child + parent matched — no runner_activity table
    con = _auto_conn()
    link_col = _ensure_orders_link_col(con)

    try:
        # Try to find most recent child in orders with same marketId/selectionId
        row = _q_retry(con,
            "SELECT id FROM orders "
            "WHERE marketId=? AND selectionId=? AND hedge_of IS NOT NULL "
            "ORDER BY datetime(opened_at) DESC LIMIT 1",
            (str(marketId), str(selectionId))
        ).fetchone()
        child_id = int(row["id"]) if row and row.get("id") else None
    except Exception:
        child_id = None

    if child_id is not None:
        _q_retry(con, "UPDATE orders SET entry_status='matched' WHERE id=?", (child_id,))
    _q_retry(con, "UPDATE orders SET entry_status='matched' WHERE id=?", (order_id,))

    # (Finalize sets closed_at/exit_status, so no commit is strictly required here)


    # Compute parent P&L (ticks → £)
    realized_ticks = abs(int(target_ticks))
    pnl_amt = _pnl_ticks_to_amount(entry_odds, realized_ticks, float(stake))

    # Apply commission (conservative per-trade) in LEARNING when profitable
    if _current_source().upper() == "LEARNING" and pnl_amt > 0.0:
        pnl_amt = pnl_amt * (1.0 - float(COMMISSION_RATE))

    # Persist parent P&L to orders + pnl_trades + mastery
    set_order_status(order_id, "matched", pnl_amt)

    evt_ctx = ctx or {
        "distance_band": "", "code": "", "tto_window": "",
        "entry_odds": entry_odds, "target_ticks": realized_ticks
    }
    mp.record_outcome(f"SIM-{order_id}", evt_ctx, {"success": True, "realized_ticks": realized_ticks})

    src = _current_source()
    bdb = _bets_conn()
    _q_retry(bdb, """
      CREATE TABLE IF NOT EXISTS pnl_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount REAL, created_at TEXT, settled_at TEXT, source TEXT
      )
    """)
    _q_retry(bdb, "INSERT INTO pnl_trades (amount, created_at, settled_at, source) "
        "VALUES (?, datetime('now'), datetime('now'), ?)",
        (pnl_amt, src)
    )
    _q_retry(bdb, "INSERT INTO mastery_events(event_type, details_json, delta_progress, source) "
      "VALUES ('trade_outcome', '{\"auto\":\"tick\"}', 0, ?)",
      (src,)
    )
    bdb.commit()
    return True
# --- PATCH END ----------------------------------------------------------------

# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: # (add near the bottom of the module, not inside decide_once)
def run_end_of_day_mastery_settlement(day_utc: str | None = None, logger=None) -> int:
    """
    Sweep today's (or given day_utc 'YYYY-MM-DD') completed trades:
      - join orders -> mastery_plans
      - build an outcome dict per parent order
      - call mastery.record_outcome(plan_ctx, outcome)
    Returns count of outcomes processed.
    """
    if logger is None:
        logger = lambda *_a, **_k: None

    # mastery import
    try:
        from engines.mastery import mastery_policy as mp
    except Exception as e:
        logger(f"[mastery] unavailable for settlement: {e}")
        return 0

    import sqlite3, json, datetime as _dt
    d = day_utc or _utcnow().strftime("%Y-%m-%d")

    con = _auto_conn()
    con.row_factory = sqlite3.Row

    # parent orders with exits on the day (single-row parent+hedge model)
    rows = _q_retry(con, """
        SELECT o.id AS order_id, o.marketId, o.selectionId, o.mode, o.exit_status,
               o.realized_ticks, o.realized_pnl, o.hedge_ticks, o.created_at, o.updated_at,
               mp.strategy_name, mp.plan_json, mp.ctx_json
        FROM orders o
        LEFT JOIN mastery_plans mp ON mp.order_id = o.id
        WHERE date(o.updated_at)=? AND o.exit_status IN ('matched','stopped','timeout')
        ORDER BY o.id ASC
        """, (d,)
    ).fetchall()

    n = 0
    for r in rows:
        try:
            plan = json.loads(r["plan_json"] or "{}")
            ctx  = json.loads(r["ctx_json"]  or "{}")
        except Exception:
            plan, ctx = {}, {}

        # fallback context keys if not persisted (safety)
        ctx.setdefault("marketId", r["marketId"])
        ctx.setdefault("selectionId", r["selectionId"])
        ctx.setdefault("source", (r["mode"] or "LIVE").upper())

        # outcome
        success = (str(r["exit_status"]).lower() == "matched" and float(r["realized_ticks"] or 0) > 0)
        outcome = {
            "target_ticks": int(plan.get("target_ticks") or r["hedge_ticks"] or 1),
            "realized_ticks": int(r["realized_ticks"] or 0),
            "realized_pnl": float(r["realized_pnl"] or 0.0),
            "success": bool(success),
            "fill_observed": True,  # we saw an exit
            "mae_ticks": None,      # optional: add if you store it elsewhere
        }

        try:
            mp.record_outcome(str(r["order_id"]), ctx, outcome)
            n += 1
        except Exception as e:
            logger(f"[mastery] record_outcome error order={r['order_id']}: {e}")

    logger(f"[mastery] settlement {d}: {n} outcomes")
    return n



