from __future__ import annotations
import json, sqlite3, time, os
from typing import Dict, Tuple, Optional
from engines.config_paths import connect_db, autoscalp_db
import engines.mastery.mastery_policy as mp

from datetime import datetime, timezone
import datetime as dt  # only if you still reference dt.datetime elsewhere

from engines.mastery.context_builder import build_context_from_test_db
from engines.mastery.policy_lookup import get_bin_key
from engines.mastery.priors import upsert_prior

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


def _safe_float(x, default=None):
    try:
        if x is None:
            return default
        v = float(x)
        return default if (_math.isnan(v)) else v
    except Exception:
        return default

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


# --- PATCH 4/6: signal→direction helper -------------------------------------
# 🔎 SEARCH: def _odds_plus_ticks(
def _direction_from_signal(c: dict, fallback_odds: float | None = None) -> str:
    sig = _steam_drift_signal(c)
    if sig == "steam": return "BACK->LAY"
    if sig == "drift": return "LAY->BACK"
    try:
        o = float(fallback_odds if fallback_odds is not None else c.get("odds") or 0.0)
        return "LAY->BACK" if o >= 4.0 else "BACK->LAY"
    except Exception:
        return "LAY->BACK"

# --- PATCH 5/6: scope-first driver ------------------------------------------
# 🔎 SEARCH: def _build_scope(
def _scope_pass(run_id: str, ctx: dict, logger=None) -> Optional[int]:
    log = (logger or (lambda *a, **k: None))
    scope = _build_scope(now_utc=_utcnow(), inplay_window_min=15)
    ordered = list(scope.get("pre_far", [])) + list(scope.get("pre_near", []))
    if not ordered: return None

    # wall-minute throttle
    try: wall_min = int(_utcnow().timestamp() // 60)
    except Exception:
        from time import time as _t; wall_min = int(_t() // 60)

    attempted = getattr(_scope_pass, "_WALL_MIN_ATTEMPTED", set())
    if getattr(_scope_pass, "_LAST_WALL", None) != wall_min:
        attempted = set()
        setattr(_scope_pass, "_WALL_MIN_ATTEMPTED", attempted)
        setattr(_scope_pass, "_LAST_WALL", wall_min)

    # strategy registry snapshot
    try:
        from engines.decision_engine.strategies.registry import ORDER as ORDER_LIST, ENABLED as ORDER_ENABLED
    except Exception:
        ORDER_LIST, ORDER_ENABLED = [], {}
    ORDER_MAP = {nm: fn for (nm, fn) in (ORDER_LIST or [])}

    MAX_MARKETS = 8
    MAX_RUNNERS = 3

    for mid, _unused in ordered[:MAX_MARKETS]:
        cands = _active_candidates_for_market(mid, max_runners=12)
        if not cands:
            log(f"[A] market={mid} active=0"); continue
        log(f"[A] market={mid} active={len(cands)}")

        picked = 0
        for sid, odds in cands:
            key = (mid, sid, wall_min)
            if key in attempted: continue
            attempted.add(key); setattr(_scope_pass, "_WALL_MIN_ATTEMPTED", attempted)

            # runner ctx
            ctx["marketId"]    = mid
            ctx["selectionId"] = sid
            ctx["odds"]        = float(odds)
            ctx["ltp"]         = float(odds)

            # A-lane tiny scalp (signal-aware)
            direction = _direction_from_signal(ctx, fallback_odds=float(odds))
            ticks     = 2 if abs(float(ctx.get("slope_ppm") or 0.0)) >= 0.08 else 1
            size_cap  = float(ctx.get("size_cap", 2.0) or 2.0)
            planA = {
                "enter": True, "direction": direction,
                "target_ticks": max(1, int(ticks)),
                "size": max(2.0, min(size_cap, 5.0)),
                "why": f"A-lane dir={direction} sppm={float(ctx.get('slope_ppm') or 0.0):.3f}"
            }
            placed = _pf("ALWAYS_ON", planA, ctx)
            if placed: return placed

            # New families (registry ORDER) for this runner
            for (fname, ffn) in (ORDER_LIST or []):
                if ORDER_ENABLED and not ORDER_ENABLED.get(fname, True): 
                    continue
                if fname.upper() in {"OG_STRATEGY","S1_LEGACY_L2B","S2_BIAS_L2B","S3_LADDER_L2B"}:
                    continue  # S handled next

                pass_tag = _pass_tag(_strat_letter_for(fname), None)
                if _already_open_pass(mid, sid, pass_tag, mode=ctx.get("source","LIVE")):
                    continue
                ctx["pass_tag"] = pass_tag

                plan2 = None
                try: plan2 = ffn(ctx)
                except Exception: plan2 = None

                if not plan2:
                    for alt in _alts_for(fname, ctx, mid, sid, ORDER_MAP):
                        try:
                            alt_fn = ORDER_MAP.get(alt)
                            if not alt_fn: continue
                            probe = alt_fn(ctx)
                            if probe and probe.get("enter"):
                                placed = _pf(alt, probe, ctx)
                                if placed: return placed
                        except Exception:
                            pass
                    continue

                placed = _pf(fname, plan2, ctx)
                if placed: return placed

            # Legacy S-family via scope (simple scout)
            # Odds band 1.5..8, small/tight plan; signal aware (BTL on steam)
            if 1.5 <= float(odds) <= 8.0:
                s_dir = _direction_from_signal(ctx, fallback_odds=float(odds))
                s_ticks = 2 if abs(float(ctx.get("slope_ppm") or 0.0)) >= 0.08 else 1
                s_plan = {"enter": True, "direction": s_dir, "target_ticks": s_ticks, "size": max(2.0, min(size_cap, 5.0)), "why":"S-scout scope"}
                placed = _pf("OG_STRATEGY", s_plan, ctx)  # tag letter resolves to 'S'
                if placed: return placed

            picked += 1
            if picked >= MAX_RUNNERS: break

    return None


# ── Feature flags (defaults: Legacy=ON, NewStrats=OFF, Mastery=OFF)
import os
F_ENABLE_LEGACY   = (os.getenv("AUTOSCALP_ENABLE_LEGACY", "1") == "1")
F_ENABLE_STRATS   = (os.getenv("AUTOSCALP_ENABLE_NEW_STRATS", "1") == "1")
F_ENABLE_MASTERY  = (os.getenv("AUTOSCALP_ENABLE_MASTERY", "1") == "1")


# ── Persistent DB connections to avoid FD exhaustion ─────────────────────────
_AUTO_CON: sqlite3.Connection | None = None
_BETS_CON: sqlite3.Connection | None = None

def _auto_conn() -> sqlite3.Connection:
    """Singleton connection to AUTO_DB (orders/decisions) with lock-tolerant pragmas."""
    global _AUTO_CON
    if _AUTO_CON is None:
        db_path = autoscalp_db()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        _AUTO_CON = sqlite3.connect(db_path, timeout=30, check_same_thread=False)
        _AUTO_CON.row_factory = sqlite3.Row
        try:
            _AUTO_CON.execute("PRAGMA journal_mode=WAL;")
            _AUTO_CON.execute("PRAGMA busy_timeout=5000;")
            _AUTO_CON.execute("PRAGMA synchronous=NORMAL;")
        except Exception:
            pass
    return _AUTO_CON

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
    except Exception:
        pass
    _SCHEMA_DONE = True

# helper: best-effort 'next' marketId from dashboard_markets
def _next_market_id_today() -> Optional[str]:
    try:
        import sqlite3
        from engines.config_paths import autoscalp_db
        con = sqlite3.connect(autoscalp_db(), timeout=6); con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT marketId FROM dashboard_markets "
            "WHERE day=date('now','utc') AND is_next=1 "
            "ORDER BY datetime(last_refreshed_ts) DESC LIMIT 1"
        ).fetchone()
        con.close()
        return str(row["marketId"]) if row and row["marketId"] else None
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def place_strategy_instruction
def place_strategy_instruction(run_id: str, marketId: str, selectionId: str,
                               side: str, price: float, size: float,
                               hedge_ticks: int, source: str) -> bool:
    """
    Public entry for strategy placements (LIVE).
    Creates a parent (e.g., LAY) then the companion child hedge (BACK) using your
    existing queue/hedge helpers. Invariant: LTB (Lay first → Back higher).
    """
    try:
        # Optional per-runner guard; adjust to your helper signature if it differs
        try:
            can_open, why = _can_open_scalp(marketId, selectionId, max_per_runner=3, run_id=run_id)
            if not can_open:
                return False
        except NameError:
            pass  # if you don't have _can_open_scalp, it's fine to skip

        parent_id = _queue_order(
            run_id=run_id,
            side=side.upper(),
            odds=float(price),
            stake=float(size),
            marketId=str(marketId),
            selectionId=str(selectionId),
            mode_override="LIVE",
        )

        direction = "LAY->BACK" if side.upper() == "LAY" else "BACK->LAY"
        _place_companion_hedge(
            parent_id=parent_id,
            direction=direction,
            entry_odds=float(price),
            parent_stake=float(size),
            target_ticks=int(hedge_ticks),
            marketId=str(marketId),
            selectionId=str(selectionId),
            run_id=run_id,
        )

        # Mark parent as live now; adapter callbacks will flip to matched/cancelled later
        try:
            _set_order_status(parent_id, "live")
        except NameError:
            pass

        return True
    except Exception:
        # keep loop resilient
        return False



def _bets_conn() -> sqlite3.Connection:
    """Singleton connection to BETS_DB (pnl_trades, pnl_daily, mastery_*)."""
    global _BETS_CON
    if _BETS_CON is None:
        _BETS_CON = connect_db(ro=False)          # <— DO NOT call _bets_conn() here
        try: _BETS_CON.execute("PRAGMA journal_mode=WAL")
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




import sqlite3 as _sqlite3

def _utcnow():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc)

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


def _utcnow_str() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

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
        con = _sqlite3.connect(_adb_path(), timeout=6); con.row_factory = _sqlite3.Row
        row = con.execute(
            f"SELECT COUNT(*) AS n FROM orders "
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
        con = _sqlite3.connect(_adb_path(), timeout=6)
        con.execute("UPDATE orders SET notes=? WHERE id=?", (str(scout_pass), int(order_id)))
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
            px = _latest_price(str(market_id), str(selection_id))
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
            _, mto_win = _compute_minutes_to_off(str(market_id), source=_current_source())
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

def _log_event(level: str, source: str, message: str) -> None:
    """Lightweight event logger to autoscalp_gui.db.events."""
    try:
        con = sqlite3.connect(autoscalp_db(), timeout=8)
        con.execute(
            "INSERT INTO events(ts, level, source, message) VALUES(?,?,?,?)",
            (_utcnow_str(), level, source, message)
        )
        con.commit()
    except Exception:
        pass
    finally:
        try: con.close()
        except Exception: pass

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
            con = sqlite3.connect(bdb_path, timeout=6); con.row_factory = sqlite3.Row
            row = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_daily'"
            ).fetchone()
            if row:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                exists = con.execute(
                    "SELECT COUNT(*) AS n FROM pnl_daily "
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
        row = _safe(lambda: bdb.execute("PRAGMA database_list;").fetchone())
        bets_path = row[2] if row and len(row) >= 3 else None
        # today schedule (future)
        sched_cnt = int(_safe(lambda: bdb.execute(
            "SELECT COUNT(*) FROM markets_schedule "
            "WHERE date(off_at_utc)=date('now','utc') "
            "AND datetime(off_at_utc)>=datetime('now','utc')"
        ).fetchone()[0], 0) or 0)
        # today oc_series markets
        series_markets = int(_safe(lambda: bdb.execute(
            "SELECT COUNT(DISTINCT marketId) FROM oc_series "
            "WHERE date(snapshot_ts)=date('now','utc')"
        ).fetchone()[0], 0) or 0)
        # next two from schedule
        rows = _safe(lambda: bdb.execute(
            "SELECT COALESCE(venue,course,event_name,market_name) AS course, off_at_utc, marketId "
            "FROM markets_schedule "
            "WHERE date(off_at_utc)=date('now','utc') "
            "AND datetime(off_at_utc)>=datetime('now','utc') "
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
    try:
        adb = sqlite3.connect(auto_path, timeout=6); adb.row_factory = sqlite3.Row
        # LIVE orders today
        orders_live_today = int(_safe(lambda: adb.execute(
            "SELECT COUNT(*) FROM orders WHERE mode='LIVE' AND date(opened_at)=date('now')"
        ).fetchone()[0], 0) or 0)
        # decisions today
        if _safe(lambda: adb.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()):
            cols = [c[1] for c in adb.execute("PRAGMA table_info(decisions)")]
            col = "decided_at" if "decided_at" in cols else ("opened_at" if "opened_at" in cols else None)
            if col:
                decisions_today = int(_safe(lambda: adb.execute(
                    f"SELECT COUNT(*) FROM decisions WHERE date({col})=date('now')"
                ).fetchone()[0], 0) or 0)
        # inbound markets today
        if _safe(lambda: adb.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_oc_cache'"
        ).fetchone()):
            inbound_markets = int(_safe(lambda: adb.execute(
                "SELECT COUNT(DISTINCT marketId) FROM inbound_oc_cache WHERE date(last_sync_ts)=date('now')"
            ).fetchone()[0], 0) or 0)
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
    log(f"[RUNCFG] ActiveGate: odds_band=[1.50,8.00] liq: L1>=50 top3>=200 time<=20m")
    log(f"[RUNCFG] Scouts: S1<=20m, S2<=10m, S3<=5m; max 3 per runner (cap enforced)")
    log(f"[RUNCFG] Hedge: PERSIST (we do not cancel), auto-rehedge sweep enabled if scheduled")

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
        bdb2 = connect_db(ro=True); bdb2.row_factory = sqlite3.Row
        sch_has = bool(_safe(lambda: bdb2.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='markets_schedule'"
        ).fetchone(), None))
        ser_has = bool(_safe(lambda: bdb2.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oc_series'"
        ).fetchone(), None))
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




def _current_source() -> str:
    try:
        from engines.config_paths import get_mode
        m = (get_mode() or "TEST").upper()
        return "TEST" if m not in ("LEARNING","LIVE") else m
    except Exception:
        return "TEST"


# ─────────────────────────────────────────────────────────────────────────────
# Priors bootstrap
# ─────────────────────────────────────────────────────────────────────────────
# _bootstrap_priors_for_ctx
def _bootstrap_priors_for_ctx(ctx: dict) -> None:
    bin_key = get_bin_key(ctx["distance_band"], ctx["code"], ctx["tto_window"],
                          ctx["class_band"], ctx["fav_rank_bin"])
    db = _bets_conn()
    row = db.execute(
        "SELECT prior_p1_alpha, prior_p1_beta FROM mastery_priors WHERE bin_key=?",
        (bin_key,)
    ).fetchone()
    if row and float(row[0] or 0) + float(row[1] or 0) >= 20:
        return
    upsert_prior(db, bin_key, (55.0,20.0), (45.0,25.0), (35.0,25.0), (40.0,10.0), 1.2, 0.5)
    db.commit()

def _bootstrap_priors_if_empty() -> None:
    bin_key = "5-7f|FLAT|30-10|mid|fav"
    db = _bets_conn()
    row = db.execute(
        "SELECT prior_p1_alpha, prior_p1_beta FROM mastery_priors WHERE bin_key=?",
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
    db.execute("""
    CREATE TABLE IF NOT EXISTS pnl_daily (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_day INTEGER NOT NULL,
      month_index INTEGER NOT NULL,
      amount REAL NOT NULL,
      created_at TEXT NOT NULL,
      run_id TEXT,
      source TEXT
    )""")
    db.execute("""
    CREATE TABLE IF NOT EXISTS sim_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_day INTEGER NOT NULL,
      month_index INTEGER NOT NULL,
      run_id TEXT NOT NULL UNIQUE,
      started_at TEXT NOT NULL,
      ended_at TEXT
    )""")

    # Ensure mastery tables (idempotent)
    db.execute("""
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
    db.execute("""
    CREATE TABLE IF NOT EXISTS mastery_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      event_type TEXT NOT NULL,
      details_json TEXT,
      delta_progress INTEGER DEFAULT 0,
      source TEXT,
      created_at TEXT DEFAULT (datetime('now','utc'))
    )""")
    if not db.execute("SELECT 1 FROM mastery_state WHERE id=1").fetchone():
        db.execute("INSERT INTO mastery_state(id,progress,updated_at) VALUES (1,0,datetime('now','utc'))")

    # Create pnl_trades with source (idempotent)
    db.execute("""
    CREATE TABLE IF NOT EXISTS pnl_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      amount REAL,
      created_at TEXT,
      settled_at TEXT,
      source TEXT
    )""")

    # --- Schema normalisation: add missing columns & backfill 'source' ---
    def _cols(tab: str) -> set[str]:
        return {r["name"] for r in db.execute(f"PRAGMA table_info({tab})")}

    src = _current_source()

    # pnl_trades: ensure 'source'
    c = _cols("pnl_trades")
    if "source" not in c:
        db.execute("ALTER TABLE pnl_trades ADD COLUMN source TEXT")
    db.execute("UPDATE pnl_trades SET source=? WHERE source IS NULL OR source=''", (src,))

    # pnl_daily: ensure 'source'
    c = _cols("pnl_daily")
    if "source" not in c:
        db.execute("ALTER TABLE pnl_daily ADD COLUMN source TEXT")
    db.execute("UPDATE pnl_daily SET source=? WHERE source IS NULL OR source=''", (src,))

    db.commit()

def _next_run_day() -> int:
    db = _bets_conn()
    row = db.execute("SELECT COALESCE(MAX(run_day),0) FROM pnl_daily").fetchone()
    return int((row[0] if isinstance(row, tuple) else row[0]) or 0) + 1

def _start_run_ledger(run_id: str) -> tuple[int, int]:
    _ensure_ledger_tables()
    db = _bets_conn()
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT run_day, month_index FROM sim_runs WHERE run_id=?", (run_id,)).fetchone()
    if row:
        return int(row["run_day"]), int(row["month_index"])
    run_day = _next_run_day()
    month_index = 1 + (run_day - 1) // 30
    db.execute(
        "INSERT INTO sim_runs (run_day, month_index, run_id, started_at) "
        "VALUES (?,?,?, datetime('now','utc'))",
        (run_day, month_index, run_id)
    )
    db.commit()
    return run_day, month_index

def _end_run_ledger(run_id: str, amount: float, run_day: int, month_index: int):
    db = _bets_conn()
    src = _current_source()
    db.execute(
        "INSERT INTO pnl_daily (run_day, month_index, amount, created_at, run_id, source) "
        "VALUES (?,?,?, datetime('now','utc'), ?, ?)",
        (run_day, month_index, float(amount), run_id, src)
    )
    db.execute("UPDATE sim_runs SET ended_at=datetime('now','utc') WHERE run_id=?", (run_id,))

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
        db.execute(
            "INSERT INTO mastery_events(event_type, details_json, delta_progress, source) "
            "VALUES ('run_complete', ?, ?, ?)",
            (json.dumps({"run_id": run_id, "pnl": float(amount), "run_day": run_day}), int(delta), src)
        )
        if delta != 0:
            db.execute(
                "UPDATE mastery_state "
                "SET progress = MIN(100, MAX(0, progress + ?)), updated_at=datetime('now','utc') "
                "WHERE id=1",
                (int(delta),)
            )
    except Exception:
        pass

    db.commit()

def _sum_pnl_trades_test() -> float:
    db = _bets_conn()
    exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_trades'").fetchone()
    if not exists:
        return 0.0
    has_src = any(r[1] == 'source' for r in db.execute("PRAGMA table_info('pnl_trades')"))
    sql = ("SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE source='TEST'"
           if has_src else "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades")
    return float(db.execute(sql).fetchone()[0] or 0.0)

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

def _compute_minutes_to_off(market_id: str, *, source: str) -> tuple[float, str]:
    """
    Returns (minutes_to_off, tto_window_label) using BETS_DB.markets_schedule.off_at_utc.
    - TEST: uses compressed time factor sim_params.speed_min_per_sec (minutes per real second).
    - LEARNING/LIVE: uses real minutes to off.
    Falls back safely on errors.
    """
    try:
        db = _bets_conn()
        db.row_factory = sqlite3.Row

        row = db.execute(
            "SELECT off_at_utc FROM markets_schedule WHERE marketId=?",
            (str(market_id),)
        ).fetchone()
        if not row or not row["off_at_utc"]:
            return (0.0, "2-0")

        off = _dt.strptime(str(row["off_at_utc"])[:19], "%Y-%m-%d %H:%M:%S")
        now = _dt.utcnow()
        seconds = (off - now).total_seconds()

        if str(source).upper() == "TEST":
            srow = db.execute(
                "SELECT v FROM sim_params WHERE k='speed_min_per_sec'"
            ).fetchone()
            # speed is 'virtual minutes per real second'
            speed = float((srow["v"] if isinstance(srow, sqlite3.Row) else srow[0])) if srow else 0.5
            mto = float(seconds) * speed                     # → minutes (compressed)
        else:
            mto = float(seconds) / 60.0                      # → minutes (real)

        return (mto, _window_from_minutes(mto))
    except Exception:
        return (0.0, "2-0")

from datetime import datetime, timezone, timedelta

def _build_scope(now_utc: Optional[datetime] = None,
                 inplay_window_min: int = 15,
                 max_markets: int = 200) -> dict:
    """
    Partition markets into scope segments for this tick:
      - pre_far  : TTO > 60
      - pre_near : 0 < TTO ≤ 60
      - in_play  : OFF/IN_PLAY and elapsed ≤ inplay_window_min
      - exposure : markets with open parents (parent matched, child not matched)

    Returns: {
      "pre_far":  [(mid, tto_min_float), ...]               (sorted by TTO asc)
      "pre_near":[(mid, tto_min_float), ...]               (sorted by TTO asc)
      "in_play": [(mid, elapsed_min_float), ...]           (sorted by elapsed asc)
      "exposure":[mid1, mid2, ...]                         (no order guarantee)
    }
    Safe: never raises; returns empty lists on error.
    """
    out = {"pre_far": [], "pre_near": [], "in_play": [], "exposure": []}

    def _parse_off(txt: Optional[str]) -> Optional[datetime]:
        """Robust ISO parser: handles Z, fractional seconds, and naïve strings as UTC."""
        if not txt:
            return None
        s = str(txt).strip()
        try:
            if s.endswith("Z"):
                return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)
            dt = datetime.fromisoformat(s)
            return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
        except Exception:
            pass
        # Fallback to seconds precision
        try:
            return datetime.fromisoformat(s[:19]).replace(tzinfo=timezone.utc)
        except Exception:
            return None

    try:
        now_utc = (now_utc or _utcnow()).astimezone(timezone.utc)
    except Exception:
        now_utc = datetime.now(timezone.utc)

    # ---------- Load schedule rows (today UTC; fallback to local; then tomorrow UTC) ----------
    try:
        bdb = _bets_conn(); bdb.row_factory = sqlite3.Row
    except Exception:
        return out

    def _load_where(day_sql: str) -> list:
        try:
            return bdb.execute(
                "SELECT marketId, off_at_utc FROM markets_schedule "
                f"WHERE date(off_at_utc)={day_sql} "
                "ORDER BY off_at_utc ASC"
            ).fetchall()
        except Exception:
            return []

    rows = _load_where("date('now','utc')")
    if not rows:
        rows = _load_where("date('now')")  # guard for naïve dates stored in schedule

    # Keep only rows with a valid OFF time
    rows = [r for r in (rows or []) if _parse_off(r["off_at_utc"])]

    # If no future races remain today, look at tomorrow so lanes still have scope early
    if rows:
        # retain all rows; we’ll classify by TTO below
        pass
    else:
        rows = _load_where("date('now','utc','+1 day')")
        rows = [r for r in (rows or []) if _parse_off(r["off_at_utc"])]

    # ---------- Classify by TTO / phase ----------
    pre_far, pre_near, in_play = [], [], []

    for r in rows:
        mid = str(r["marketId"])
        off_dt = _parse_off(r["off_at_utc"])
        if not off_dt:
            continue

        # Minutes-to-off (positive before off, negative after off)
        mto = (off_dt - now_utc).total_seconds() / 60.0

        # Phase hint (Betfair)
        phase = None
        try:
            from engines.betfair_status import get_or_update_phase
            raw = str(get_or_update_phase(mid) or "").upper()
            if raw == "OFF":
                phase = "IN_PLAY"
            elif raw == "PRE":
                phase = "PRE"
        except Exception:
            phase = None

        # IN-PLAY bucket (phase wins; else schedule)
        if phase == "IN_PLAY" or mto <= 0.0:
            elapsed = max(0.0, (now_utc - off_dt).total_seconds() / 60.0)
            if elapsed <= float(inplay_window_min):
                in_play.append((mid, elapsed))
            continue

        # PRE buckets
        if mto > 60.0:
            pre_far.append((mid, mto))
        elif mto > 0.0:
            pre_near.append((mid, mto))

    # Sort and slice
    pre_far.sort(key=lambda x: x[1])     # TTO asc
    pre_near.sort(key=lambda x: x[1])    # TTO asc
    in_play.sort(key=lambda x: x[1])     # elapsed asc

    out["pre_far"]  = pre_far[:max_markets]
    out["pre_near"] = pre_near[:max_markets]
    out["in_play"]  = in_play[:max_markets]

    # ---------- Exposure overlay (parents with NO matched child) ----------
    try:
        con = _auto_conn(); con.row_factory = sqlite3.Row
        cols = {r["name"] for r in con.execute("PRAGMA table_info(orders)")}
        link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        if link:
            exp_rows = con.execute(
                f"""
                SELECT DISTINCT p.marketId AS mid
                FROM orders p
                WHERE (p.{link} IS NULL OR p.{link}='')
                  AND UPPER(COALESCE(p.entry_status,''))='MATCHED'
                  AND NOT EXISTS (
                        SELECT 1 FROM orders c
                        WHERE c.{link}=p.id
                          AND UPPER(COALESCE(c.entry_status,''))='MATCHED'
                    )
                """
            ).fetchall()
            out["exposure"] = [str(r["mid"]) for r in exp_rows]
        else:
            out["exposure"] = []
    except Exception:
        out["exposure"] = []

    # ---------- Optional fallback: if schedule is empty, harvest marketIds from inbound feed ----------
    if not out["pre_far"] and not out["pre_near"] and not out["in_play"]:
        try:
            mids = con.execute(
                "SELECT DISTINCT marketId FROM inbound_oc_cache "
                "WHERE datetime(COALESCE(last_sync_ts,'')) >= datetime('now','-24 hours','utc')"
            ).fetchall()
            mids = [str(r[0]) for r in mids or []]
            # treat them as pre_far with unknown TTO (float('+inf') to keep order)
            out["pre_far"] = [(m, float('inf')) for m in mids][:max_markets]
        except Exception:
            pass

    try: bdb.close()
    except Exception: pass
    try:
        print(f"[SCOPE] pre_far={len(out['pre_far'])} pre_near={len(out['pre_near'])} in_play={len(out['in_play'])}")
    except Exception:
        pass

    return out


def _tbl_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,)
    ).fetchone()
    return bool(row)

def _has_col(conn: sqlite3.Connection, table: str, col: str) -> bool:
    """
    True if 'col' exists on 'table'. Works with tuple rows or sqlite3.Row.
    """
    try:
        cur = conn.execute(f"PRAGMA table_info({table})")
        for r in cur:
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
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_daily'").fetchone():
        has_src = any(r[1]=='source' for r in db.execute("PRAGMA table_info('pnl_daily')"))
        db.execute("DELETE FROM pnl_daily" + (" WHERE source='TEST'" if has_src else ""))
    # pnl_trades
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_trades'").fetchone():
        has_src = any(r[1]=='source' for r in db.execute("PRAGMA table_info('pnl_trades')"))
        db.execute("DELETE FROM pnl_trades" + (" WHERE source='TEST'" if has_src else ""))
    # pnl_realized
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_realized'").fetchone():
        has_src = any(r[1]=='source' for r in db.execute("PRAGMA table_info('pnl_realized')"))
        db.execute("DELETE FROM pnl_realized" + (" WHERE source='TEST'" if has_src else ""))
    db.commit()
    if logger: logger("P&L (TEST) ledger reset")

def reset_for_new_run(run_id: str, logger=None) -> None:
    # AUTO_DB
    con = _auto_conn()
    try:
        if _tbl_exists(con, "orders"):
            has_run = _has_col(con, "orders", "run_id")
            if has_run:
                # Per-run cleanup (DBs with run_id): remove only rows for this run
                if _tbl_exists(con, "decisions") and _has_col(con, "decisions", "order_id"):
                    con.execute("""
                        DELETE FROM decisions
                        WHERE order_id IN (SELECT id FROM orders WHERE run_id=?)
                    """, (run_id,))
                elif _tbl_exists(con, "decisions"):
                    # Fallback if no FK to orders: prune recent decisions
                    con.execute("""
                        DELETE FROM decisions
                        WHERE datetime(COALESCE(decided_at,'')) >= datetime('now','-2 hours','utc')
                    """)
                con.execute("DELETE FROM orders WHERE run_id=?", (run_id,))
            else:
                # Legacy DBs without run_id: hard clean ALL open TEST parents (and their decisions)
                if _tbl_exists(con, "decisions") and _has_col(con, "decisions", "order_id"):
                    con.execute("""
                        DELETE FROM decisions
                        WHERE order_id IN (
                            SELECT id FROM orders
                            WHERE mode='TEST' AND (closed_at IS NULL OR closed_at='')
                        )
                    """)
                elif _tbl_exists(con, "decisions"):
                    con.execute("""
                        DELETE FROM decisions
                        WHERE datetime(COALESCE(decided_at,'')) >= datetime('now','-2 hours','utc')
                    """)
                con.execute("""
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
        if _tbl_exists(bdb, "pnl_trades"):
            has_src = _has_col(bdb, "pnl_trades", "source")
            bdb.execute("DELETE FROM pnl_trades" + (" WHERE source='TEST'" if has_src else ""))
        if _tbl_exists(bdb, "pnl_realized"):
            has_src = _has_col(bdb, "pnl_realized", "source")
            bdb.execute("DELETE FROM pnl_realized" + (" WHERE source='TEST'" if has_src else ""))
        if _tbl_exists(bdb, "mastery_events"):
            bdb.execute("""
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
        rows = con.execute(
            "SELECT selectionId, oc1 FROM inbound_oc_cache WHERE marketId=? ORDER BY id DESC",
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


# ─────────────────────────────────────────────────────────────────────────────
# AUTO_DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def _next_pair_tag(letter: str, market_id: str) -> str:
    """
    Return next tag like 'A1' / 'S2' / 'X3' scoped per market and family.
    Counts PARENT rows in orders with source starting with that letter.
    """
    from engines.config_paths import autoscalp_db
    import sqlite3
    try:
        con = sqlite3.connect(autoscalp_db(), timeout=5.0)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT COUNT(*) AS n FROM orders "
            "WHERE marketId=? AND role='PARENT' AND source LIKE ?",
            (str(market_id), f"{letter}%")
        ).fetchone()
        n = int(row["n"] or 0)
        return f"{letter}{n+1}"
    except Exception:
        return f"{letter}1"



def _sync_hedge_matches(logger=None) -> None:
    """
    LIVE: detect matched companion hedges (child rows) and finalize parent P&L.
    """
    con = _auto_conn()
    con.row_factory = sqlite3.Row
    cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
    link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
    if not link:
        return

    try:
        # find children (hedges) that have a bet id and are not matched yet
        rows = con.execute(
            f"SELECT id, {link} AS parent_id, bf_bet_id, entry_odds, entry_stake "
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
                con.execute("UPDATE orders SET entry_status='matched', closed_at=datetime('now','utc') WHERE id=?", (int(r["id"]),))
                con.commit()
                # finalize parent using child's entry_odds/stake as exit
                _finalize_parent_on_hedge(int(r["parent_id"]), float(r["entry_odds"]), float(r["entry_stake"]), bid, apply_commission=False)

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
    cols = {r["name"] for r in con.execute("PRAGMA table_info(orders)")}
    def _add(col, ddl):
        if col not in cols:
            con.execute(f"ALTER TABLE orders ADD COLUMN {col} {ddl}")
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
        cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
        link = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        if not link:
            return

        mode_clause = " AND mode='LIVE'" if "mode" in cols else ""
        # parents = rows with no link, still open, with a betfair id
        rows = con.execute(
            f"""
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
                _set_order_status(int(r["id"]), "matched", None)
    except Exception as e:
        if logger:
            logger(f"[LIVE] sync matches error: {e}")


def _ensure_decisions(con: sqlite3.Connection) -> None:
    # Create minimal table if missing
    con.execute("""
    CREATE TABLE IF NOT EXISTS decisions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      decided_at TEXT
    )""")
    # Add any missing columns (idempotent)
    cols = [r["name"] for r in con.execute("PRAGMA table_info(decisions)")]
    def _add(col: str, decl: str):
        if col not in cols:
            con.execute(f"ALTER TABLE decisions ADD COLUMN {col} {decl}")
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

    cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
    if "net_pl" not in cols:
        con.execute("ALTER TABLE orders ADD COLUMN net_pl REAL")
        # refresh cols after ALTER
        cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
        # seed from legacy columns if present
        try:
            if "pnl_amount" in cols:
                con.execute("UPDATE orders SET net_pl = COALESCE(net_pl, pnl_amount)")
            elif "pnl" in cols:
                con.execute("UPDATE orders SET net_pl = COALESCE(net_pl, pnl)")
        except Exception:
            # best-effort seeding; continue
            pass
        con.commit()
    _ORDERS_PNL_ENSURED = True
    return "net_pl"

# --- PATCH START: ensure Betfair cols on orders --------------------------------
def _ensure_orders_betfair_cols(con: sqlite3.Connection) -> None:
    """Add bf_bet_id TEXT and customer_ref TEXT if missing."""
    cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
    if "bf_bet_id" not in cols:
        con.execute("ALTER TABLE orders ADD COLUMN bf_bet_id TEXT")
    if "customer_ref" not in cols:
        con.execute("ALTER TABLE orders ADD COLUMN customer_ref TEXT")
    con.commit()
# --- PATCH END -----------------------------------------------------------------
def _mark_last_child_placed(parent_id: int) -> None:
    con = _auto_conn(); con.row_factory = sqlite3.Row
    link_col = _ensure_orders_link_col(con)
    row = con.execute(f"SELECT id FROM orders WHERE {link_col}=? ORDER BY id DESC LIMIT 1", (int(parent_id),)).fetchone()
    if row:
        con.execute("UPDATE orders SET entry_status='placed' WHERE id=?", (int(row["id"]),))
        con.commit()

# ─────────────────────────────────────────────────────────────────────────────
# Price reads (AUTOSCALP_DB)
# ─────────────────────────────────────────────────────────────────────────────
def _latest_price(marketId: Optional[str], selectionId: Optional[str]) -> Tuple[Optional[float], Optional[list]]:
    if not marketId or not selectionId:
        return (None, None)
    mid = str(marketId); sid = str(selectionId)

    # 1) inbound_oc_cache.oc1
    try:
        con = _auto_conn()
        if _tbl_exists(con, "inbound_oc_cache"):
            row = con.execute(
                "SELECT oc1, oc1_band_json FROM inbound_oc_cache "
                "WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
                (mid, sid)
            ).fetchone()
            if row and row["oc1"] is not None:
                try:
                    band = json.loads(row["oc1_band_json"]) if row["oc1_band_json"] else None
                except Exception:
                    band = None
                return (float(row["oc1"]), band)
    except Exception:
        pass

    # 2) BETS_DB.oc_series latest odd
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        r = bdb.execute(
            "SELECT odd FROM oc_series WHERE marketId=? AND selectionId=? "
            "ORDER BY datetime(snapshot_ts) DESC LIMIT 1",
            (mid, sid)
        ).fetchone()
        bdb.close()
        if r and r["odd"] is not None:
            return (float(r["odd"]), None)
    except Exception:
        pass

    # 3) Direct odds (optional, only if enabled)
    try:
        if os.getenv("AUTOSCALP_ENABLE_DIRECT_ODDS", "0") == "1":
            from engines.utils.api_tools import fetch_live_odds
            live = fetch_live_odds(None, mid, sid)
            if live:
                price = live.get("lay") or live.get("back")
                if isinstance(price, (int, float)):
                    return (float(price), None)
    except Exception:
        pass

    # 4) Fallback: always return a 2-tuple
    return (None, None)

def _active_candidates_for_market(market_id: str, max_runners: int = 4) -> list[tuple[str, float]]:
    """
    Return up to max_runners [(selectionId, odds)] classified Active (1.5..8.0).
    Sources, in order:
      1) inbound_oc_cache latest oc1 per runner (fast)
      2) oc_series latest odd per runner (if oc1 missing)
      3) runners table + _latest_price(mid,sid) fallback (series/inbound/direct)
    Also snapshots runner activity for transitions.
    """
    out: list[tuple[str, float]] = []
    seen: set[str] = set()

    con = _auto_conn(); con.row_factory = sqlite3.Row

    def _try_add(sid: str, odds: float | None) -> None:
        nonlocal out, seen
        if sid in seen or odds is None:
            return
        seen.add(sid)
        try:
            o = float(odds)
        except Exception:
            return
        # Active only
        if 1.5 <= o <= 8.0:
            try:
                _snapshot_runner_activity(str(market_id), sid, o, None)
            except Exception:
                pass
            out.append((sid, o))

    # 1) inbound_oc_cache.oc1 (latest per sid)
    try:
        rows = con.execute(
            "SELECT selectionId, oc1 FROM inbound_oc_cache WHERE marketId=? ORDER BY id DESC",
            (str(market_id),)
        ).fetchall()
        latest: dict[str, float] = {}
        for r in rows:
            sid = str(r["selectionId"])
            if sid not in latest and r["oc1"] is not None:
                latest[sid] = float(r["oc1"])
        for sid, o in latest.items():
            _try_add(sid, o)
            if len(out) >= max_runners:
                return out
    except Exception:
        pass

    # 2) oc_series.latest odd per sid (if oc1 missing or insufficient)
    if len(out) < max_runners:
        try:
            rows2 = con.execute(
                "SELECT selectionId, odd FROM oc_series WHERE marketId=? "
                "ORDER BY datetime(snapshot_ts) DESC",
                (str(market_id),)
            ).fetchall()
            latest2: dict[str, float] = {}
            for r in rows2:
                sid = str(r["selectionId"])
                if sid not in latest2 and r["odd"] is not None:
                    latest2[sid] = float(r["odd"])
            for sid, o in latest2.items():
                _try_add(sid, o)
                if len(out) >= max_runners:
                    return out
        except Exception:
            pass

    # 3) runners + _latest_price(mid,sid) (series/inbound/direct odds fallback)
    if len(out) < max_runners:
        try:
            rnames = con.execute(
                "SELECT selectionId FROM runners WHERE marketId=?",
                (str(market_id),)
            ).fetchall()
            for rr in (rnames or []):
                sid = str(rr["selectionId"])
                if sid in seen:
                    continue
                price, _band = _latest_price(str(market_id), sid)
                _try_add(sid, price)
                if len(out) >= max_runners:
                    return out
        except Exception:
            pass

    return out[:max_runners]

# ─────────────────────────────────────────────────────────────────────────────
# Orders & decisions (AUTO_DB)
# ─────────────────────────────────────────────────────────────────────────────
# Some environments lost this helper during earlier edits; define it if missing.
if "_ensure_orders_link_col" not in globals():
    def _ensure_orders_link_col(con: sqlite3.Connection) -> str:
        """
        Ensure 'orders' has a parent link column and return the column name.
        Prefer 'hedge_of'; fall back to 'parent_id'; create 'hedge_of' if neither exists.
        """
        try:
            con.row_factory = sqlite3.Row
        except Exception:
            pass
        cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
        if "hedge_of" in cols:
            return "hedge_of"
        if "parent_id" in cols:
            return "parent_id"
        con.execute("ALTER TABLE orders ADD COLUMN hedge_of INTEGER")
        con.commit()
        return "hedge_of"

def _queue_order(run_id: str, side: str, odds: float, stake: float,
                 marketId: Optional[str], selectionId: Optional[str],
                 mode_override: Optional[str] = None) -> int:
    con = _auto_conn()
    _ensure_orders_trade_cols(con)
    cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
    link_col = _ensure_orders_link_col(con)
    has_run = "run_id" in cols
    has_source = "source" in cols
    has_role = "role" in cols

    mode = (mode_override or _current_source()).upper()
    opened_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    entry_liability = ((float(odds) - 1.0) * float(stake)) if side.upper() == "LAY" else float(stake)

    base_cols = ["mode","side","entry_odds","entry_stake","entry_liability",
                 "entry_status","marketId","selectionId","opened_at"]
    base_vals = [mode, side.upper(), float(odds), float(stake), float(entry_liability),
                 "queued", str(marketId), str(selectionId), opened_at]

    if has_role:
        base_cols.append("role"); base_vals.append("PARENT")
    if has_run:
        base_cols.append("run_id"); base_vals.append(run_id)
    if has_source:
        base_cols.append("source"); base_vals.append(mode)

    sql = f"INSERT INTO orders ({', '.join(base_cols)}, {link_col}) VALUES ({', '.join(['?']*len(base_cols))}, NULL)"
    con.execute(sql, base_vals)
    oid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
    con.commit()
    return oid


def _place_decision(ctx: Dict, plan: Dict, marketId: Optional[str], selectionId: Optional[str],
                    order_id: Optional[int], run_id: str) -> None:
    con = _auto_conn()
    _ensure_decisions(con)
    # crude confidence from why
    conf = 0.0
    try:
        why = plan.get("why","")
        if "p_fill=" in why:
            conf = float(why.split("p_fill=")[1].split()[0])
    except Exception:
        conf = 0.0
    meta = {"ctx": ctx, "plan": plan}

    cols = ["decided_at","marketId","selectionId","signal_type","confidence","meta_json","order_id"]
    vals = (marketId, selectionId, "trend_follow", conf, json.dumps(meta), order_id)
    has_run = any(r["name"] == "run_id" for r in con.execute("PRAGMA table_info(decisions)"))
    if has_run:
        con.execute(
            "INSERT INTO decisions (decided_at, marketId, selectionId, signal_type, confidence, meta_json, order_id, run_id) "
            "VALUES (datetime('now','utc'), ?, ?, ?, ?, ?, ?, ?)",
            (*vals, run_id)
        )
    else:
        con.execute(
            "INSERT INTO decisions (decided_at, marketId, selectionId, signal_type, confidence, meta_json, order_id) "
            "VALUES (datetime('now','utc'), ?, ?, ?, ?, ?, ?)",
            vals
        )
    con.commit()

def _place_companion_hedge(parent_id: int, direction: str, entry_odds: float, parent_stake: float,
                           target_ticks: int, marketId: str, selectionId: str, run_id: str,
                           mode_override: Optional[str] = None) -> Optional[int]:
    """
    Insert the opposite-side companion order at the tick target with the correct hedge stake.
    Returns child order id or None on failure.
    """
    # Compute target odds & hedge stake using green-up formulas
    n_ticks = abs(_si(target_ticks, 1))
    if str(direction).startswith("LAY"):  # LAY->BACK (need drift to higher odds)
        target_odds = _odds_plus_ticks(float(entry_odds), n_ticks)
        hedge_side  = "BACK"
    else:                                 # BACK->LAY (need steam to lower odds)
        target_odds = _odds_plus_ticks(float(entry_odds), -n_ticks)
        hedge_side  = "LAY"

    # universal hedge stake formula (works for both directions):
    # S_hedge = S_parent * entry_odds / target_odds
    hedge_stake = float(parent_stake) * float(entry_odds) / max(1e-9, float(target_odds))

    con = _auto_conn()
    try:
        link_col = _ensure_orders_link_col(con)
        has_run  = _has_col(con, "orders", "run_id")
        has_src  = _has_col(con, "orders", "source")
        has_role  = _has_col(con, "orders", "role")

        base_cols = ["mode","side","entry_odds","entry_stake","entry_status","marketId","selectionId"]
        src = (mode_override or _current_source()).upper()
        base_vals = [src, hedge_side, float(target_odds), float(hedge_stake), "queued",
                     str(marketId), str(selectionId)]

        # link to parent
        base_cols.append(link_col); base_vals.append(int(parent_id))

        # session/source
        if has_run: base_cols.append("run_id"); base_vals.append(run_id)
        if has_src: base_cols.append("source"); base_vals.append(src)
        if has_role: base_cols.append("role"); base_vals.append("CHILD")

        sql = f"INSERT INTO orders ({', '.join(base_cols)}, opened_at) VALUES ({', '.join(['?']*len(base_cols))}, datetime('now','utc'))"
        con.execute(sql, base_vals)
        child_id = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        con.commit()
        return child_id
    except Exception:
        return None




# ─────────────────────────────────────────────────────────────────────────────
# Outcome evaluation & PnL
# ─────────────────────────────────────────────────────────────────────────────
# Net commission applied to positive P&L in LEARNING (conservative estimate).
COMMISSION_RATE = 0.02  # 2%

# Require price to trade THROUGH our hedge target by this many ticks in LEARNING.
# 0 = match on first touch (optimistic), 1 = through by one tick (more realistic).
LEARNING_FILL_THROUGH_TICKS = 1

def _tick_size(odds: float) -> float:
    if odds < 2: return 0.01
    if odds < 3: return 0.02
    if odds < 4: return 0.05
    if odds < 6: return 0.10
    if odds < 10: return 0.20
    if odds < 20: return 0.50
    if odds < 30: return 1.00
    if odds < 50: return 2.00
    return 5.00

def _odds_plus_ticks(odds: float, n: int) -> float:
    step = 1 if n>=0 else -1
    x = odds
    for _ in range(abs(n)):
        x += step * _tick_size(x)
    return round(max(1.01, x), 2)

def _set_order_status(order_id: int, status: str, net_pl: float | None = None) -> None:
    """
    Update order status; write P&L to canonical 'net_pl' (auto-created/seeded if missing).
    """
    con = _auto_conn()
    try:
        con.row_factory = sqlite3.Row
    except Exception:
        pass

    # Ensure schema is ready (adds 'net_pl' if missing and seeds from legacy columns)
    pnl_col = _ensure_orders_pnl_col(con)  # always returns 'net_pl'

    if status in ("matched", "cancelled"):
        if net_pl is None:
            con.execute(
                "UPDATE orders SET entry_status=?, closed_at=datetime('now','utc') WHERE id=?",
                (status, order_id),
            )
        else:
            con.execute(
                f"UPDATE orders SET entry_status=?, closed_at=datetime('now','utc'), "
                f"{pnl_col}=COALESCE({pnl_col},0.0)+? WHERE id=?",
                (status, float(net_pl), order_id),
            )
    else:
        con.execute("UPDATE orders SET entry_status=? WHERE id=?", (status, order_id))

    con.commit() 

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
    row = con.execute(
        "SELECT side, entry_odds, entry_stake FROM orders WHERE id=?",
        (parent_id,)
    ).fetchone()
    if not row:
        return

    side        = str(row["side"]).upper()
    entry_odds  = float(row["entry_odds"] or 0.0)
    entry_stake = float(row["entry_stake"] or 0.0)

    # --- tick math (preserve your realized £ convention) -----------------------
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
    con.execute(
        "UPDATE orders SET exit_odds=?, exit_stake=?, exit_bet_id=?, "
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
        dec = con.execute(
            "SELECT meta_json FROM decisions WHERE order_id=? ORDER BY id DESC LIMIT 1",
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
                        last, _    = _latest_price(str(mid), str(sid))
                        entry_odds = float(last) if last else 6.0
                        size       = float(plan.get("size") or 2.0)

                        # stash into ctx for provenance
                        ctx["entry_odds"]  = entry_odds
                        ctx["marketId"]    = str(mid)
                        ctx["selectionId"] = str(sid)

                        # queue parent + decision + companion hedge
                        parent_id = _queue_order(
                            run_id=run_id,
                            side=direction,
                            odds=entry_odds,
                            stake=size,
                            marketId=str(mid),
                            selectionId=str(sid),
                            mode_override=src,               # ← HERE
                        )
                        _place_decision(ctx, plan, str(mid), str(sid), parent_id, run_id)

                        try:
                            _place_companion_hedge(
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


# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def start_live_loop
# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: ^def start_live_loop\(.*\):
def start_live_loop(run_id: str, interval: float = 0.8, logger=None, **kwargs):
    import time, threading
    if logger is None:
        logger = lambda *a, **k: None

    hz = kwargs.pop("hz", None)
    if hz:
        try:
            interval = 1.0 / float(hz)
        except Exception:
            pass

    # Optional: start re-hedge loop stays harmless (hedges only), or guard it if you want
    try:
        from live_router import _start_rehedge_loop as start_rehedge_loop
        try:
            start_rehedge_loop(default_ticks=1)
        except Exception:
            pass
    except Exception:
        pass

    # Safe import mastery sink (guarded by flag)
    try:
        from engines.mastery.event_sink import drain_new_events  # type: ignore
    except Exception:
        def drain_new_events(max_rows: int = 500) -> int: return 0

    # ───────────────────────── IMPORTANT ─────────────────────────
    # Disable the old "runner.run_active_strategies" path.
    # The minute-pass engine inside decide_once() now owns all placements.
    # This prevents a second, stale gate path that reports tto=0.0m.
    global F_ENABLE_STRATS
    F_ENABLE_STRATS = False
    # ─────────────────────────────────────────────────────────────

    while True:
        try:
            # 1) NEW strategies — DISABLED (minute-pass engine supersedes this)
            # if F_ENABLE_STRATS and 'run_active_strategies' in locals() and run_active_strategies:
            #     try:
            #         placed = run_active_strategies(run_id, logger=logger)
            #         if placed: logger(f"[strategies] placed={placed}")
            #     except Exception as e:
            #         msg = str(e)
            #         if "cannot convert float nan to integer" in msg.lower():
            #             pass
            #         else:
            #             logger(f"[strategies] runner warn: {e}")

            # 2) Legacy/minute-pass engine — ON
            try:
                decide_once(run_id, source_override="LIVE", logger=logger)
            except Exception as e:
                logger(f"[legacy] warn: {e}")

            # 3) Lifecycle sync (always safe)
            try:
                _sync_live_matches(logger=logger)
                _sync_hedge_matches(logger=logger)
                threading.Thread(target=lambda: _sync_hedge_matches(), daemon=True).start()
            except Exception as e:
                logger(f"[sync] warn: {e}")

            # 3b) Advisory green sweep (no orders; prints suggestion near off)
            try:
                import sqlite3
                from engines.config_paths import autoscalp_db
                from engines.live.live_router import maybe_green_sweep_market
                mid = _next_market_id_today()
                if mid:
                    mto, _ = _compute_minutes_to_off(str(mid), source=_current_source())
                    con = sqlite3.connect(autoscalp_db(), timeout=6); con.row_factory = sqlite3.Row
                    maybe_green_sweep_market(con, str(mid), tto_min=float(mto), max_disp=5.0)
                    con.close()
            except Exception as e:
                logger(f"[green-sweep] advise warn: {e}")

            # 4) Mastery ingestion — OFF by default
            if F_ENABLE_MASTERY:
                try:
                    n = drain_new_events(max_rows=500)
                    if n:
                        logger(f"[mastery] ingested events={n}")
                except Exception as e:
                    logger(f"[mastery] sink warn: {e}")

        except Exception as e:
            logger(f"[live] loop warn: {e}")

        time.sleep(interval)


def run_test_day(run_id: str, seconds: int = 600, hz: int = 4, logger=None) -> None:
    import engines.config_paths as cp
    cp.set_db_paths(mode="test", quiet=False)   # force TEST file paths
    _close_conns()                              # drop any old connections created in other modes
    from engines.config_paths import bets_db, autoscalp_db
    if logger: logger(f"[paths] mode={cp.os.environ.get('AUTOSCALP_MODE','(unset)')} "
                      f"BETS_DB={bets_db()} AUTO_DB={autoscalp_db()}")

    """Compressed session: full-day markets streamed; decisions/hedges operate off the feed."""
    # Stop-file kill switch (so the UI can ask us to stop safely)
    stop_flag = os.path.join(os.path.dirname(autoscalp_db()), ".stop_testday")
    try:
        if os.path.exists(stop_flag):
            os.remove(stop_flag)
    except Exception:
        pass

    # reset per-run
    try:
        reset_for_new_run(run_id, logger=logger)
    except Exception as e:
        if logger:
            logger(f"reset error: {e}")

    run_day, month_index = _start_run_ledger(run_id)
    if logger:
        logger(f"run_day={run_day} month={month_index}")

    try:
        _bootstrap_priors_if_empty()
        if logger:
            logger("priors ready for TEST")
    except Exception as e:
        if logger:
            logger(f"prior seed error: {e}")

    # build blueprint + seed + start day streamer
    now = _dt.utcnow()
    markets, speed = build_compressed_day(
        now=now, real_duration_sec=seconds, virtual_span_minutes=300, n_markets=27
    )
    _seed_day(markets, speed_min_per_sec=speed)
    day = TestDayRunner(markets=markets, seconds=seconds, hz=hz, logger=logger)
    day.start()

    interval = 1.0 / max(1, hz)
    t0 = time.time()
    live: Dict[int, Tuple[str, float, int, float, Optional[str], Optional[str], Dict]] = {}

    # One-shot: show which mastery_policy is actually executing
    try:
        import engines.mastery.mastery_policy as _mp_dbg
        if logger:
            logger(f"[POLICY] using {_mp_dbg.__file__} line={_mp_dbg.propose_trade.__code__.co_firstlineno}")
    except Exception:
        pass

    try:
        while (time.time() - t0) < seconds:
            # kill switch (safe early exit)
            if os.path.exists(stop_flag):
                if logger:
                    logger("STOP requested — ending TestDay loop")
                break

            # --- Heartbeat so you can see feed activity every tick
            try:
                bdb_dbg = connect_db(ro=True)
                oc1_total = int(
                    bdb_dbg.execute("SELECT COUNT(*) FROM inbound_oc_cache WHERE oc1 IS NOT NULL").fetchone()[0] or 0
                )
                recent = int(
                    bdb_dbg.execute(
                        "SELECT COUNT(*) FROM inbound_oc_cache "
                        "WHERE oc1 IS NOT NULL AND datetime(COALESCE(last_sync_ts,'')) >= datetime('now','-60 seconds','utc')"
                    ).fetchone()[0] or 0
                )
                if logger:
                    logger(f"HB | oc1_total={oc1_total} recent60s={recent}")
            except Exception as _e:
                if logger:
                    logger(f"HB | error: {_e}")
            finally:
                try:
                    bdb_dbg.close()
                except Exception:
                    pass

            # --- Decide once
            try:
                oid = decide_once(run_id, source_override="TEST", logger=logger)

                if oid is None:
                    # Always emit a useful reason so stalls are visible
                    try:
                        from engines.mastery.context_builder import build_context
                        ctx_dbg, _m = build_context(source="TEST")
                        plan_dbg = mp.propose_trade(ctx_dbg)
                        if plan_dbg.get("enter"):
                            if logger:
                                logger(f"DBG would ENTER | dir={plan_dbg.get('direction')} "
                                       f"n={plan_dbg.get('target_ticks')} why={plan_dbg.get('why','')}")
                        else:
                            if logger:
                                logger(f"NO-TRADE | {plan_dbg.get('why','(no reason)')}")
                    except Exception as _e:
                        if logger:
                            logger(f"NO-TRADE | (debug fail) {_e}")
                else:
                    if logger:
                        logger(f"ENTER order_id={oid}")
                    con = _auto_conn()  # DO NOT close this singleton
                    row = con.execute(
                        "SELECT meta_json FROM decisions WHERE order_id=? ORDER BY id DESC LIMIT 1",
                        (oid,)
                    ).fetchone()
                    if row:
                        meta = json.loads(row["meta_json"])
                        plan = meta.get("plan", {})
                        ctx = meta.get("ctx", {})
                        live[oid] = (
                            plan.get("direction", "LAY->BACK"),
                            float(ctx.get("entry_odds", 6.0)) if ctx.get("entry_odds") else 6.0,
                            int(plan.get("target_ticks") or 1),
                            float(plan.get("size") or 2.0),
                            ctx.get("marketId"),
                            ctx.get("selectionId"),
                            ctx,
                        )
            except Exception as e:
                import traceback as _tb
                if logger:
                    logger(f"decide error: {e} | {_tb.format_exc().splitlines()[-1]}")

            # --- Try to close any live orders
            to_remove = []
            for oid, (dir_tag, entry_odds, target_ticks, stake, mid, sid, ctx) in list(live.items()):
                try:
                    if check_and_close(oid, dir_tag, entry_odds, target_ticks, stake, mid, sid, ctx):
                        if logger:
                            logger(f"MATCH order_id={oid} ticks={target_ticks}")
                        to_remove.append(oid)
                except Exception as e:
                    if logger:
                        logger(f"close error: {e}")
            for oid in to_remove:
                live.pop(oid, None)

            time.sleep(interval)
    finally:
        # stop the day and finish ledger/eod
        try:
            if os.path.exists(stop_flag):
                os.remove(stop_flag)
        except Exception:
            pass
        day.stop(join=True)
        try:
            day_amount = _sum_pnl_trades_test()
        except Exception:
            day_amount = 0.0
        _end_run_ledger(run_id, amount=day_amount, run_day=run_day, month_index=month_index)
        _eod_report(run_id, run_day, month_index, logger=logger)
        if logger:
            logger(f"day P&L={day_amount:+.2f}")
        _close_conns()

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
    name = getattr(strat, "name", getattr(strat, "__name__", type(strat).__name__))
    try:
        # New family (OOP): StrategyBase.evaluate(market_ctx, runner_ctx)
        if hasattr(strat, "evaluate") and callable(getattr(strat, "evaluate")):
            return strat.evaluate(market_ctx or {}, runner_ctx or {})

        # Legacy OOP: .decide(runner_ctx)
        if hasattr(strat, "decide") and callable(getattr(strat, "decide")):
            return strat.decide(runner_ctx or {})

        # Function-style strategy: decide(ctx) at module level
        if callable(strat):
            return strat(runner_ctx or {})

        if logger:
            logger(f"[STRAT {name}] decide warn: '{type(strat).__name__}' object is not callable")
        return None
    except Exception as e:
        if logger:
            logger(f"[STRAT {name}] decide error: {e!s}")
        return None

# ───────── minute windows & tags ─────────
_MIN_PRE_START = 60   # start PRE passes at T-60m
_MIN_PRE_STOP  = 0    # ⬅ change from 5 to 0: stop at the off (T-0)
_MIN_IP_STOP   = -15  # stop IP passes at T-15m (negative minutes)

def _minute_bucket(tto: float | None) -> int:
    """Clamp minutes-to-off to [-15..60] integer bucket."""
    try:
        m = int(_math.floor(float(tto or 0.0)))
    except Exception:
        m = 0
    return max(_MIN_IP_STOP, min(_MIN_PRE_START, m))

def _in_pre_window(tto: float | None) -> bool:
    m = _minute_bucket(tto)
    return (0 <= m <= _MIN_PRE_START)   # ⬅ PRE now 60..0 inclusive

def _in_ip_window(tto: float | None) -> bool:
    m = _minute_bucket(tto)
    return (_MIN_IP_STOP <= m <= -1)    # ⬅ IP now -15..-1 (starts right after OFF)

def _pass_tag(letter: str, tto: float | None) -> str:
    m = _minute_bucket(tto)
    if m >= 0:
        return f"{(letter or 'S')}{m:02d}"         # e.g., S60..S05, X60..X05
    return f"{(letter or 'D')}-{abs(m):02d}"       # e.g., D-01..D-15

def _strat_letter_for(name: str) -> str:
    try:
        return (STRAT_CODE.get(name.upper()) or name[:1].upper())
    except Exception:
        return name[:1].upper()

def _already_open_pass(market_id: str, selection_id: str, pass_tag: str, *, mode: str = "LIVE") -> bool:
    """
    True if a PARENT with notes=pass_tag exists where the hedge is NOT yet matched.
    This releases the minute-pass immediately on child match, even if parent isn't finalized.
    """
    import sqlite3
    con = _auto_conn()
    con.row_factory = sqlite3.Row

    cols = {r["name"] for r in con.execute("PRAGMA table_info(orders)")}
    has = lambda c: c in cols
    link = "hedge_of" if has("hedge_of") else ("parent_id" if has("parent_id") else None)
    mode_col = "mode" if has("mode") else None

    # Parent discriminator
    if has("role"):
        parent_pred = "p.role='PARENT'"
    elif link:
        parent_pred = f"(p.{link} IS NULL OR p.{link}='')"
    else:
        parent_pred = "1=1"

    where = [parent_pred, "p.notes=?", "p.marketId=?", "p.selectionId=?"]
    args = [str(pass_tag), str(market_id), str(selection_id)]

    if mode_col:
        where.append("UPPER(COALESCE(p.mode,''))=UPPER(?)")
        args.append(str(mode).upper())

    where_sql = " AND ".join(where)

    if link:
        sql = f"""
            SELECT EXISTS(
                SELECT 1 FROM orders p
                WHERE {where_sql}
                  AND NOT EXISTS (
                        SELECT 1 FROM orders c
                        WHERE c.{link} = p.id
                          AND UPPER(COALESCE(c.entry_status,''))='MATCHED'
                    )
            ) AS open_pass
        """
    else:
        # fallback: treat un-finalized parent as open
        sql = f"""
            SELECT EXISTS(
                SELECT 1 FROM orders p
                WHERE {where_sql} AND (p.closed_at IS NULL OR p.closed_at='')
            ) AS open_pass
        """

    row = con.execute(sql, tuple(args)).fetchone()
    return bool(row and int(row["open_pass"] or 0) == 1)


# ── candidate sweep: all markets, fav+contenders with odds ≤ 8, T-60..T-5 ──
def _minute_candidates(limit_per_market: int = 4, max_markets: int = 10) -> list[tuple[str, str]]:
    import sqlite3
    mids: list[str] = []
    out: list[tuple[str, str]] = []

    # collect today's markets with TTO in window (60..5)
    try:
        bdb = connect_db(ro=True); bdb.row_factory = sqlite3.Row
        rows = bdb.execute(
            "SELECT marketId, off_at_utc FROM markets_schedule "
            "WHERE date(off_at_utc)=date('now','utc')"
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
            rws = con.execute(
                "SELECT selectionId, oc1 FROM inbound_oc_cache "
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

  
 




# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def decide_once(run_id: str, source_override: str | None = None, logger=None) -> Optional[int]:
def decide_once(run_id: str, source_override: str | None = None, logger=None) -> Optional[int]:
    """
    One decision attempt. Returns parent order_id or None.
    LIVE lane only. Mastery-first families via registry.ORDER + legacy S-pass.
    """
    if logger is None:
        logger = lambda *_a, **_k: None

    # --- mastery planner (lazy import) ---
    try:
        mp  # type: ignore[name-defined]
    except NameError:
        try:
            from engines.mastery import mastery_policy as mp
        except Exception as e:
            logger(f"[mastery] unavailable: {e}")
            mp = None  # type: ignore[assignment]

    # --- ALWAYS define ctx in this scope before any logging uses it ---
    ctx: dict = {}
    direction: str = ""
    ctx["run_id"] = run_id

    # --- build + normalize ctx (unchanged in spirit; short form here) ---
    try:
        from engines.mastery.context_builder import build_context
        ctx, meta = build_context(source=(source_override or _current_source()).upper())
    except Exception as e:
        logger(f"[DEC] build_context error: {e}")
        return None

    market_id    = (meta.get("marketId")    if isinstance(meta, dict) else None) or ctx.get("marketId")
    selection_id = (meta.get("selectionId") if isinstance(meta, dict) else None) or ctx.get("selectionId")
    if not market_id or not selection_id:
        logger(f"[DEC] no market/selection in ctx (src={ctx.get('source')})")
        return None

    # minutes-to-off + odds fallbacks
    def _n(v, d=0.0):
        import math as _m
        try:
            f = float(v);  return d if _m.isnan(f) else f
        except Exception: return d
    def _ni(v, d=0):
        try: return int(v)
        except Exception:
            try: return int(float(v))
            except Exception: return d

    if "minutes_to_off" not in ctx or ctx.get("minutes_to_off") is None:
        try:
            mto, win = _compute_minutes_to_off(str(market_id), source=ctx.get("source","LIVE"))
            ctx["minutes_to_off"] = float(mto); ctx["tto_window"] = win
        except Exception: pass

    odds_val = _n(ctx.get("entry_odds", ctx.get("current_odds")), 0.0)
    mto_val  = _n(ctx.get("minutes_to_off"), 0.0)

    # phase + mastery-friendly aliases
    def _infer_phase(mid: str, mto: float) -> str:
        try:
            from engines.betfair_status import get_or_update_phase
            raw = str(get_or_update_phase(str(mid)) or "").upper()
            if raw == "OFF": return "IN_PLAY"
            if raw == "PRE": return "PRE"
        except Exception: pass
        return "PRE" if mto > 0.0 else "IN_PLAY"

    ctx["phase"]        = ctx.get("phase") or _infer_phase(str(market_id), mto_val)
    ctx["tto_minutes"]  = mto_val
    ctx["tto_min"] = mto_val   # ← gate_check expects this key


    ctx["odds"]         = odds_val
    ctx.setdefault("ltp", odds_val)  # ← OG/Ladder expect runner_ctx['ltp']
    ctx.setdefault("price", odds_val)
    ctx.setdefault("last_price", odds_val)
    if "slope_ppm" not in ctx:
        ctx["slope_ppm"] = float(ctx.get("flow_ppm", 0.0))
    if "liq_best_back" not in ctx:
        ctx["liq_best_back"] = float(ctx.get("l1_available_back", ctx.get("l1_available", 0.0)))
    if "liq_best_lay" not in ctx:
        ctx["liq_best_lay"]  = float(ctx.get("l1_available_lay",  ctx.get("l1_available", 0.0)))

    for k, dflt, cast in (
        ("l1_available",       0.0, _n),
        ("top3_available",     0.0, _n),
        ("traded_recent_amt",  0.0, _n),
        ("traded_recent_sec", 9999.0, _n),
        ("tick_vel_1s_up",       0, _ni),
        ("tick_vel_3s_up",       0, _ni),
        ("down_ticks_10s",       0, _ni),
        ("up_ticks_10s",         0, _ni),
        ("range_span_ticks",     0, _ni),
        ("range_pos",          0.5, _n),
        ("flow_ppm",           0.0, _n),
        ("size_cap",           2.0, _n),
    ):
        ctx[k] = cast(ctx.get(k), dflt)
    class _DotCtx(dict): __getattr__ = dict.get
    _ctx = _DotCtx(ctx)

    # --- optional diag (AFTER ctx & IDs exist) ---
    _missing = [k for k, v in ctx.items() if v is None]
    if _missing:
        logger(f"[CTX MISSING] mid={market_id} sid={selection_id} missing={_missing[:8]} "
               f"tto={ctx.get('tto_minutes')} odds={ctx.get('odds')}")

# --- PATCH 6/6: hook scope-first into decide_once ---------------------------
# 🔎 SEARCH: # 1) FAMILIES via registry.ORDER + STRAT_GATES
# (Insert the following just ABOVE that comment, after ctx/_ctx are ready)
    # expose local placer + last ctx for the global _pf shim
    globals()["_PLACE_FROM_PLAN_LOCAL"] = _place_from_plan
    globals()["_LAST_CTX"] = ctx

    # scope-first pass (single source of truth)
    placed_id = _scope_pass(run_id, ctx, logger=logger)
    if placed_id:
        return placed_id

    # =====================================================================
    # 1) FAMILIES via registry.ORDER + STRAT_GATES  (no _active_strategies!)
    # =====================================================================
    # build once above the family loop
    try:
        from engines.decision_engine.strategies.registry import ORDER as ORDER_LIST, ENABLED as ORDER_ENABLED
    except Exception:
        ORDER_LIST, ORDER_ENABLED = [], {}

    # Map for quick alternate routing
    ORDER_MAP = {nm: fn for (nm, fn) in (ORDER_LIST or [])}

    # per-minute throttle (clear on minute change)
    cur_min = _minute_bucket(ctx.get("tto_min", ctx.get("tto_minutes")))
    last_min = getattr(decide_once, "_LAST_MIN", None)
    if last_min != cur_min:
        setattr(decide_once, "_ATTEMPTED_MINUTE", set())
        setattr(decide_once, "_LAST_MIN", cur_min)
    _ATTEMPTED = getattr(decide_once, "_ATTEMPTED_MINUTE", set())

    def _is_fav_now(mid: str, sid: str) -> bool:
        try:
            fav, rank, *_ = _is_favourite(mid, sid)
            return bool(fav)
        except Exception:
            return False

    def _dir_hint(c: dict) -> float:
        # negative = steam (odds falling) → BACK->LAY; positive = drift → LAY->BACK
        return float(c.get("slope_per_min", c.get("slope_ppm", 0.0)) or 0.0)

    # --- PATCH 2/6: signal-aware alternates -------------------------------------
    # 🔎 SEARCH: def _alts_for(fname: str, c: dict, mid: str, sid: str) -> list[str]:
    def _alts_for(fname: str, c: dict, mid: str, sid: str, ORDER_MAP: dict[str, callable]) -> list[str]:
        sig = _steam_drift_signal(c)
        try:
            fav, *_ = _is_favourite(mid, sid)
            fav = bool(fav)
        except Exception:
            fav = False
        btl = ["BTL_SCOUT", "BTL_AGGR", "S4_CROSSOVER"]  # BACK entries
        ltb = ["OG_BIAS", "S5_BREAKOUT"]                # LAY entries
        if sig == "steam": seq = btl + ltb
        elif sig == "drift": seq = ltb + btl
        else: seq = (btl if fav else ltb) + (ltb if fav else btl)
        return [x for x in seq if x != fname and x in ORDER_MAP]



    try:
        from engines.decision_engine.strategies.common import gate_check, STRAT_GATES, SourceTagger, STRAT_CODE
    except Exception:
        gate_check = None; STRAT_GATES = {}
        class SourceTagger:
            _c = {}
            @classmethod
            def next_tag(cls, letter):
                n = cls._c.get(letter or "S", 0) + 1; cls._c[letter or "S"] = n; return f"{(letter or 'S')}{n}"
        STRAT_CODE = {}
    try:
        from engines.decision_engine.strategies.registry import ORDER as _ORDER, ENABLED as _ENABLED
    except Exception:
        _ORDER, _ENABLED = [], {}

    LEGACY_UMBRELLA = {"OG_STRATEGY", "S1_LEGACY_L2B", "S2_BIAS_L2B", "S3_LADDER_L2B"}

    # small helper to persist plan+ctx for mastery learning
    def _persist_plan_context(order_id: int, strat_name: str, plan: dict, ctx_dict: dict):
        try:
            import json as _json
            con = _auto_conn()
            con.execute("""
              CREATE TABLE IF NOT EXISTS mastery_plans(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER, strategy_name TEXT, plan_json TEXT, ctx_json TEXT,
                created_at TEXT DEFAULT (datetime('now','utc'))
              )""")
            con.execute(
                "INSERT INTO mastery_plans(order_id,strategy_name,plan_json,ctx_json) VALUES (?,?,?,?)",
                (int(order_id), str(strat_name).upper(), _json.dumps(plan), _json.dumps(dict(ctx_dict)))
            )
            con.commit()
        except Exception: pass

    def _place_from_plan(self, _name: str, _plan_like, _ctx: dict) -> Optional[int]:
        """
        Common path that turns a plan into a parent (+ child) placement.
        Letter-aware so caps are per-family and ALWAYS_ON maps to 'A'.
        Safe to call from any strategy (function or class) with either dict-plan
        or an instruction-like object exposing side/stake/target_ticks.
        """
        # ---- tiny logger shim (does nothing if no logger available) ----------
        _log = getattr(self, "logger", None)
        def _dbg(msg: str) -> None:
            try:
                (_log or (lambda *_a, **_k: None))(msg)
            except Exception:
                pass

        # ---- normalise the incoming "plan" -----------------------------------
        def _as_plan(pl) -> dict:
            if not pl:
                return {}
            if isinstance(pl, dict):
                return pl
            side  = getattr(pl, "side", None)
            stake = getattr(pl, "stake", None)
            ticks = getattr(pl, "hedge_ticks", getattr(pl, "target_ticks", None))
            if side:
                dirn = "LAY->BACK" if str(side).upper() == "LAY" else "BACK->LAY"
                return {
                    "enter": True,
                    "direction": dirn,
                    "target_ticks": int(ticks or 1),
                    "size": float(stake or _ctx.get("size_cap", 2.0)),
                }
            return {}

        plan = _as_plan(_plan_like)
        if not (plan and plan.get("enter")):
            _dbg(f"[PLACE] skip: no-plan-or-enter=false name={_name}")
            return None

        # ---- core fields ------------------------------------------------------
        NAME      = str(_name or "").upper()
        try:
            letter = "A" if NAME == "ALWAYS_ON" else STRAT_CODE.get(NAME, "S")
        except Exception:
            letter = "A" if NAME == "ALWAYS_ON" else "S"

        direction = str(plan.get("direction") or "")
        side      = "LAY" if direction.startswith("LAY") else "BACK"
        ticks     = max(1, abs(int(plan.get("target_ticks") or 1)))
        size      = float(plan.get("size") or 2.0)

        # runner IDs from ctx
        mid = str(_ctx.get("marketId") or _ctx.get("market_id") or "")
        sid = str(_ctx.get("selectionId") or _ctx.get("selection_id") or "")
        if not mid or not sid:
            _dbg(f"[PLACE {NAME}] skip: no-runner-ids mid='{mid}' sid='{sid}'")
            return None

        # ---- resolve a price --------------------------------------------------
        px = 0.0
        try:
            from engines.decision_engine.strategies.common import get_price_from_sources as _safe_px
            px = float(_safe_px(mid, sid) or 0.0)
        except Exception:
            px = 0.0
        if not px or px <= 0.0:
            try:
                px = float(_ctx.get("current_odds") or _ctx.get("entry_odds") or _ctx.get("odds") or 0.0)
            except Exception:
                px = 0.0
        if not px or px <= 0.0:
            _dbg(f"[PLACE {NAME}] skip: no-price mid={mid} sid={sid} ctx.odds={_ctx.get('odds')}")
            return None

        # ---- liquidity-aware clamp (≤35% of L1 on the relevant side) ---------
        try:
            l1_side = _ctx.get("l1_available_lay") if side == "LAY" else _ctx.get("l1_available_back")
            if l1_side is None:
                l1_side = _ctx.get("l1_available")
            l1f = float(l1_side) if l1_side is not None else 0.0
        except Exception:
            l1f = 0.0
        if l1f > 0.0:
            liq_cap = max(2.0, 0.35 * l1f)
            size = min(size, liq_cap)

        # ---- per-market family code (A1/S2/X3/…) & per-letter cap ------------
        code = _next_pair_tag(letter, mid)
        ok_cap, cap_reason = _can_open_scalp(
            mid, sid, max_per_runner=3,
            run_id=_ctx.get("run_id") or "",
            family_letter=letter,
        )
        if not ok_cap and (_ctx.get("source") or "LIVE").upper() != "TEST":
            _dbg(f"[PLACE {NAME}] skip: cap {cap_reason} mid={mid} sid={sid} letter={letter}")
            return None

        # ---- queue parent in AUTO_DB -----------------------------------------
        parent_id = _queue_order(
            run_id=_ctx.get("run_id") or "",
            side=side, odds=float(px), stake=size,
            marketId=mid, selectionId=sid,
            mode_override=_ctx.get("source", "LIVE"),
        )

        # stamp notes + source
        try:
            _mark_order_notes(parent_id, _ctx.get("pass_tag") or code)
        except Exception:
            pass
        try:
            con = _auto_conn()
            con.execute("UPDATE orders SET source=?, notes=? WHERE id=?",
                        (code, _ctx.get("pass_tag") or code, parent_id))
            con.commit()
        except Exception:
            pass

        # decision provenance
        try:
            _place_decision(_ctx, plan, mid, sid, parent_id, _ctx.get("run_id") or "")
        except Exception:
            pass

        # ---- LIVE route (parent now, child after) ----------------------------
        if (_ctx.get("source") or "LIVE").upper() == "LIVE":
            try:
                from engines.live.live_router import place_parent_and_hedge
                # Pre-off letters should LAPSE the child at the off
                child_persist = "LAPSE" if letter in ("A", "S", "B", "F", "L", "X") else "PERSIST"
                bet_id, cref = place_parent_and_hedge(
                    market_id=mid, selection_id=sid,
                    side=side, entry_odds=float(px),
                    stake=size, hedge_ticks=int(ticks),
                    source=code,
                    parent_persistence="LAPSE",
                    child_persistence=child_persist,
                )
                try:
                    _mark_last_child_placed(parent_id)
                except Exception:
                    pass
                try:
                    con2 = _auto_conn()
                    con2.execute("UPDATE orders SET bf_bet_id=?, customer_ref=?, source=? WHERE id=?",
                                 (bet_id, cref, code, parent_id))
                    con2.commit()
                except Exception:
                    pass
            except Exception:
                # live_router logs its own errors; continue with DB model
                pass

        # ---- DB companion hedge (internal model) -----------------------------
        try:
            _place_companion_hedge(
                parent_id=parent_id, direction=(direction or "LAY->BACK"),
                entry_odds=float(px), parent_stake=size, target_ticks=int(ticks),
                marketId=mid, selectionId=sid, run_id=_ctx.get("run_id") or ""
            )
        except Exception:
            pass

        _set_order_status(parent_id, "placed")
        try:
            print(f"[DEC] ENTER {code} mid={mid} sid={sid} dir={direction} px={float(px):.2f} size={size:.2f}")
        except Exception:
            pass
        return parent_id

    # --- ensure minutes-to-off is populated for gate checks (PRE only) ---
    try:
        mto_now = float(ctx.get("tto_minutes") if ctx.get("tto_minutes") is not None else -1.0)
    except Exception:
        mto_now = -1.0

    if (ctx.get("phase") == "PRE") and (mto_now <= 0.0):
        try:
            mto_fix, win = _compute_minutes_to_off(str(market_id), source=ctx.get("source","LIVE"))
            if mto_fix is not None:
                ctx["minutes_to_off"] = float(mto_fix)
                ctx["tto_minutes"]    = float(mto_fix)
                if win is not None:
                    ctx["tto_window"] = win
        except Exception:
            pass

    # Back-compat shim: some strategies call _pf(name, plan[, ctx])
    def _pf(name, plan_like, maybe_ctx=None):
        return _place_from_plan(name, plan_like, maybe_ctx or ctx)


    # --- guarantee PRE minutes-to-off for gate checks (use current ctx runner) ---
    def _fix_tto_for_gate(_ctx: dict):
        try:
            mto_now = float(_ctx.get("tto_minutes") if _ctx.get("tto_minutes") is not None else -1.0)
        except Exception:
            mto_now = -1.0
        if (_ctx.get("phase") == "PRE") and (mto_now <= 0.0):
            try:
                mid = str(_ctx.get("marketId") or _ctx.get("market_id") or "")
                mto_fix, win = _compute_minutes_to_off(mid, source=_ctx.get("source", "LIVE"))
                if mto_fix is not None:
                    _ctx["minutes_to_off"] = float(mto_fix)
                    _ctx["tto_minutes"]    = float(mto_fix)
                if win is not None:
                    _ctx["tto_window"] = win
            except Exception:
                pass

    def _as_ctx_dict(x):
        # Always hand strategies a mapping that supports .get
        if isinstance(x, dict) or hasattr(x, "get"):
            return x
        try:
            return dict(x)
        except Exception:
            try:
                return x.__dict__
            except Exception:
                return {}

    # Use current minute bucket; reset throttle when it changes
    cur_min = _minute_bucket(ctx.get("tto_min", ctx.get("tto_minutes")))
    last_min = getattr(decide_once, "_LAST_MIN", None)
    if last_min != cur_min:
        setattr(decide_once, "_ATTEMPTED_MINUTE", set())
        setattr(decide_once, "_LAST_MIN", cur_min)
    _ATTEMPTED = getattr(decide_once, "_ATTEMPTED_MINUTE", set())

    # produce minute candidates across all markets
    CANDS = _minute_candidates(limit_per_market=4, max_markets=10)
    # always include the current context runner so we don't regress current behavior
    if market_id and selection_id and (market_id, selection_id) not in CANDS:
        CANDS.insert(0, (str(market_id), str(selection_id)))

    # ──────────────────────────────────────────────────────────────────
    # PER-CANDIDATE family pass loop (run families for EACH runner)
    # ──────────────────────────────────────────────────────────────────
    for (mid_c, sid_c) in CANDS:
        # ensure core ctx reflects the candidate runner
        ctx["marketId"] = mid_c
        ctx["selectionId"] = sid_c

        # recompute per-runner odds + tto (cheap refresh)
        last_px, _ = _latest_price(mid_c, sid_c)
        if last_px is not None:
            ctx["odds"] = float(last_px)
            ctx["ltp"] = float(last_px)
            ctx["price"] = float(last_px)
        try:
            mto_fix, win_fix = _compute_minutes_to_off(mid_c, source=ctx.get("source", "LIVE"))
            ctx["minutes_to_off"] = float(mto_fix)
            ctx["tto_minutes"]    = float(mto_fix)
            ctx["tto_min"]        = float(mto_fix)

            try:
                ctx["phase"] = _infer_phase(mid_c, ctx.get("tto_min", 0.0))
            except Exception:
                ctx["phase"] = ("PRE" if float(ctx.get("tto_min", 0.0)) > 0.0 else "IN_PLAY")


            if win_fix:
                ctx["tto_window"] = win_fix
        except Exception:
            pass

        # rebuild dot-access view AFTER mutating ctx
        class _DotCtx(dict): __getattr__ = dict.get
        _ctx = _DotCtx(ctx)

        # --- FAST-PATH: try BTL/OG_BIAS explicitly once per minute per runner ---
        try:
            od = float(ctx.get("odds") or 0.0)
        except Exception:
            od = 0.0
        if 1.5 <= od <= 8.0 and _in_pre_window(ctx.get("tto_min", ctx.get("tto_minutes"))):
            for quick in ("BTL_SCOUT", "BTL_AGGR", "OG_BIAS"):
                alt_fn = ORDER_MAP.get(quick)
                if not alt_fn:
                    continue

                # per-minute throttle for this quick family
                qkey = (mid_c, sid_c, quick, cur_min)
                if qkey in _ATTEMPTED:
                    continue
                _ATTEMPTED.add(qkey)
                setattr(decide_once, "_ATTEMPTED_MINUTE", _ATTEMPTED)

                # minute pass tag for B/O families (B60..B00 / O60..O00)
                letter   = _strat_letter_for(quick)          # 'B' or 'O'
                pass_tag = _pass_tag(letter, ctx.get("tto_min", ctx.get("tto_minutes")))
                if _already_open_pass(mid_c, sid_c, pass_tag, mode=ctx.get("source", "LIVE")):
                    continue
                ctx["pass_tag"] = pass_tag

                # try decide
                plan_q = None
                try:
                    plan_q = alt_fn(_ctx)
                except Exception as e:
                    logger(f"[STRAT {quick}] decide error: {e}")

                if plan_q and plan_q.get("enter"):
                    placed = _pf(quick, plan_q, ctx)
                    if placed:
                        logger(f"[FAST] {quick} placed mid={mid_c} sid={sid_c} tag={pass_tag}")
                        return placed


        for (fname, ffn) in (ORDER_LIST or []):
            # ensure TTO present for gate checks for THIS candidate
            _fix_tto_for_gate(ctx)
            try:
                if ORDER_ENABLED and not ORDER_ENABLED.get(fname, True):
                    continue
                if fname.upper() in LEGACY_UMBRELLA:
                    continue

                # family phase intent (treat unknown as PRE)
                spec = STRAT_GATES.get(fname)
                is_ip_family = (getattr(spec, "phase", "PRE") == "IN_PLAY")

                # time-window: PRE families 60..5, IP families 0..-15
                if is_ip_family:
                    if not _in_ip_window(ctx.get("tto_min", ctx.get("tto_minutes"))):
                        continue
                else:
                    if not _in_pre_window(ctx.get("tto_min", ctx.get("tto_minutes"))):
                        continue

                # one attempt per minute per (market, runner, family)
                key = (mid_c, sid_c, fname.upper(), cur_min)
                if key in _ATTEMPTED:
                    continue
                _ATTEMPTED.add(key)
                setattr(decide_once, "_ATTEMPTED_MINUTE", _ATTEMPTED)

                # minute pass tag and open-pass dedupe
                letter   = _strat_letter_for(fname)
                pass_tag = _pass_tag(letter, ctx.get("tto_min", ctx.get("tto_minutes")))
                if _already_open_pass(mid_c, sid_c, pass_tag, mode=ctx.get("source", "LIVE")):
                    continue
                # expose pass tag so placement can brand notes/source
                ctx["pass_tag"] = pass_tag

                # gate
                ok, why = True, "ok"
                if gate_check and spec:
                    ok, why = gate_check(dict(ctx), spec)

                if not ok:
                    if why == "no_liquidity":
                        # Shadow decide: allow tiny, book-fitting stake
                        plan_probe = None
                        try:
                            plan_probe = ffn(_ctx) if callable(ffn) else None
                        except Exception as e:
                            logger(f"[STRAT {fname}] decide error (probe): {e}")
                            plan_probe = None

                        if plan_probe and plan_probe.get("enter"):
                            _dir  = str(plan_probe.get("direction") or "")
                            _side = "LAY" if _dir.startswith("LAY") else "BACK"
                            l1_side = ctx.get("l1_available_lay") if _side == "LAY" else ctx.get("l1_available_back")
                            if l1_side is None:
                                l1_side = ctx.get("l1_available")
                            try:
                                l1f  = float(l1_side) if l1_side is not None else 0.0
                                want = float(plan_probe.get("size") or 0.0)
                            except Exception:
                                l1f, want = 0.0, 0.0

                            # soft-pass: hide in the book (≤30% of L1) and L1≥£2
                            if l1f >= 2.0 and want > 0.0 and want <= (0.30 * l1f):
                                placed = _pf(fname, plan_probe, ctx)
                                if placed:
                                    return placed

                    # still blocked → log real tto and continue
                    logger(
                        f"[GATE {fname}] {why} "
                        f"odds={ctx.get('current_odds') or ctx.get('entry_odds') or ctx.get('odds')} "
                        f"tto={ctx.get('tto_min')} mid={mid_c} sid={sid_c}"
                    )
                    continue

                # mastery-first
                plan2 = None
                if mp is not None and hasattr(mp, "plan_for_strategy"):
                    try:
                        plan2 = mp.plan_for_strategy(fname, ctx)
                    except Exception as e:
                        logger(f"[mastery:{fname}] plan error: {e}")

                # fallback to strategy decide
                if (not plan2) and callable(ffn):
                    try:
                        plan2 = ffn(_ctx)
                    except Exception as e:
                        logger(f"[STRAT {fname}] decide error: {e}")
                        plan2 = None

                # if still nothing, route to alternates (fav vs others, drift vs steam)
                if not plan2:
                    for alt in _alts_for(fname, ctx, mid_c, sid_c):
                        try:
                            alt_fn = ORDER_MAP.get(alt)
                            if not alt_fn:
                                continue
                            probe = alt_fn(_ctx)
                            if probe and probe.get("enter"):
                                # keep same minute pass tag
                                ctx["pass_tag"] = pass_tag
                                placed = _pf(alt, probe, ctx)
                                if placed:
                                    logger(f"[ROUTE] {fname}→{alt} placed")
                                    return placed
                        except Exception as e:
                            logger(f"[ROUTE] {fname}→{alt} error: {e}")

                placed = _pf(fname, plan2 or {}, ctx)
                if placed:
                    return placed

            except Exception as _e:
                logger(f"[STRAT {fname}] outer error: {_e}")
                continue

    # =====================================================================
    # A-lane (ALWAYS_ON): scan PRE markets (TTO > 0) and place A1/A2…
    # Priority: PRE-far (>60m) first, then PRE-near (0..60m)
    # Uses existing cap/throttle/tagging via _place_from_plan("ALWAYS_ON", …)
    # =====================================================================
    try:
        scope = _build_scope(now_utc=_utcnow(), inplay_window_min=15)

        # Diag: how much scope we have for A
        logger(f"[A] scope pre_far={len(scope.get('pre_far', []))} "
               f"pre_near={len(scope.get('pre_near', []))}")

        # Order: far first (keeps engine alive long before off), then near
        ordered_markets = list(scope.get("pre_far", [])) + list(scope.get("pre_near", []))

        # Scan at most these many markets/runners per tick (tunable guards)
        MAX_MARKETS_A   = 6
        MAX_RUNNERS_PER = 2

        for mid, _tto in ordered_markets[:MAX_MARKETS_A]:
            # Resilient candidate picker (oc_cache → oc_series → _latest_price)
            cands = _active_candidates_for_market(mid, max_runners=8)
            if not cands:
                # Diag: no active runners in this market
                logger(f"[A] market={mid} active=0")
                continue
            logger(f"[A] market={mid} active={len(cands)}")

            picked = 0
            for sid, odds in cands:
                # Per-minute throttle keyed by wall UTC minute so it resets each minute
                try:
                    wall_min_bucket = int(_utcnow().timestamp() // 60)
                except Exception:
                    from time import time as _t
                    wall_min_bucket = int(_t() // 60)
                _ATTEMPTED = getattr(decide_once, "_ATTEMPTED_MINUTE", set())
                keyA = (mid, sid, "ALWAYS_ON", wall_min_bucket)
                if keyA in _ATTEMPTED:
                    continue
                _ATTEMPTED.add(keyA)
                setattr(decide_once, "_ATTEMPTED_MINUTE", _ATTEMPTED)

                # Build a minimal ctx for this runner (reusing the outer ctx safely)
                ctx["marketId"]    = mid
                ctx["selectionId"] = sid
                ctx["odds"]        = float(odds)
                ctx["ltp"]         = float(odds)

                # recompute minutes-to-off quickly (guard PRE only)
                try:
                    mto_fix, win_fix = _compute_minutes_to_off(mid, source=ctx.get("source","LIVE"))
                except Exception:
                    mto_fix, win_fix = (9999.0, "")
                if float(mto_fix) <= 0.0:
                    # PRE-only for A; skip if already OFF
                    continue
                ctx["minutes_to_off"] = float(mto_fix)
                ctx["tto_minutes"]    = float(mto_fix)
                ctx["tto_min"]        = float(mto_fix)
                if win_fix:
                    ctx["tto_window"] = win_fix

                # Direction: small, conservative bias using slope if present; else odds pivot
                try:
                    sppm = float(ctx.get("slope_ppm") or 0.0)
                except Exception:
                    sppm = 0.0
                if sppm < -0.03:
                    direction = "BACK->LAY"   # steamer
                elif sppm > +0.03:
                    direction = "LAY->BACK"   # drifter
                else:
                    direction = ("LAY->BACK" if float(odds) >= 4.0 else "BACK->LAY")

                # Tiny plan (1 tick; 2 if strong slope)
                ticks = 2 if abs(sppm) >= 0.08 else 1
                size_cap = float(ctx.get("size_cap", 2.0) or 2.0)
                planA = {
                    "enter": True,
                    "direction": direction,
                    "target_ticks": max(1, int(ticks)),
                    "size": max(2.0, min(size_cap, 5.0)),
                    "why": f"A-lane sppm={sppm:.3f}"
                }

                placed = _pf("ALWAYS_ON", planA, ctx)
                if placed:
                    # one placement per tick is enough for A
                    return placed

                picked += 1
                if picked >= MAX_RUNNERS_PER:
                    break
    except Exception as e:
        logger(f"[ALWAYS-ON] warn: {e}")

    # =====================================================================
    # 2) LEGACY S-pass — runs T-60..T-0 each minute (with fallback if mp missing)
    #     Scope-first (PRE-near + Active), else fall back to ctx-based S
    # =====================================================================
    try:
        # -------- SCOPE-FIRST: iterate PRE-near markets (0 < TTO ≤ 60) and Active runners --------
        scope = _build_scope(now_utc=utcnow(), inplay_window_min=15)
        pre_near = list(scope.get("pre_near", []))   # [(mid, tto)]
        MAX_MARKETS_S = 6
        MAX_RUNNERS_S = 2

        for (mid_s, _tto_s) in pre_near[:MAX_MARKETS_S]:
            # Active candidates for this market (odds in 1.5..8.0); also snapshots status
            cands = _active_candidates_for_market(mid_s, max_runners=8)
            picked = 0
            for (sid_s, odds_s) in cands:
                # recompute minutes-to-off for this market; S only runs in 0..60
                try:
                    mto_fix, win_fix = _compute_minutes_to_off(mid_s, source=ctx.get("source","LIVE"))
                except Exception:
                    mto_fix, win_fix = (0.0, "")
                if not (0.0 < float(mto_fix) <= 60.0):
                    continue

                # minute throttle: one attempt per (market, runner, family) per current minute
                cur_min = _minute_bucket(float(mto_fix))
                keyS = (mid_s, sid_s, "S", cur_min)
                if keyS in _ATTEMPTED:
                    continue
                _ATTEMPTED.add(keyS)
                setattr(decide_once, "_ATTEMPTED_MINUTE", _ATTEMPTED)

                # minute pass for notes (S60..S05) + open-pass dedupe
                pass_tag = _scout_pass_for_tto(float(mto_fix))
                if pass_tag and _already_open_pass(mid_s, sid_s, pass_tag, mode=ctx.get("source","LIVE")):
                    continue

                # build runner ctx for decide/plan
                ctx["marketId"]      = mid_s
                ctx["selectionId"]   = sid_s
                ctx["odds"]          = float(odds_s)
                ctx["ltp"]           = float(odds_s)
                ctx["minutes_to_off"]= float(mto_fix)
                ctx["tto_minutes"]   = float(mto_fix)
                ctx["tto_min"]       = float(mto_fix)
                if win_fix:
                    ctx["tto_window"] = win_fix

                # plan from mastery or legacy scout
                if mp is not None and hasattr(mp, "propose_trade"):
                    try:
                        plan = mp.propose_trade(ctx)
                    except Exception as e:
                        logger(f"[mastery] propose warn: {e}")
                        plan = _legacy_scout_plan(ctx)
                else:
                    plan = _legacy_scout_plan(ctx)

                if not plan.get("enter"):
                    continue
                ticks_int = _si(plan.get("target_ticks"), default=0)
                if ticks_int <= 0:
                    continue

                # cap: max 3 open parents per runner — S family
                ok_cap, cap_reason = _can_open_scalp(mid_s, sid_s, max_per_runner=3, run_id=run_id, family_letter="S")
                if not ok_cap and (ctx.get("source","LIVE") != "TEST"):
                    continue

                direction  = str(plan.get("direction") or "")
                side       = "LAY" if direction.startswith("LAY") else "BACK"
                entry_odds = float(odds_s)
                parent_sz  = float(plan.get("size") or 2.0)

                # stamp per-market code: S1/S2/...
                letter = "S"
                code   = _next_pair_tag(letter, mid_s)

                # preview (optional)
                try:
                    t = max(1, abs(_si(plan.get("target_ticks"), default=1)))
                    preview_side = "BACK" if side == "LAY" else "LAY"
                    preview_odds = _odds_plus_ticks(entry_odds, +t) if side == "LAY" else _odds_plus_ticks(entry_odds, -t)
                    logger(f"[TRACE] PLAN {side}->{preview_side} entry={entry_odds:.2f} ticks={t} hedge*={preview_odds:.2f}")
                except Exception:
                    pass

                # place parent
                try:
                    parent_id = _queue_order(
                        run_id=run_id, side=side, odds=entry_odds, stake=parent_sz,
                        marketId=str(mid_s), selectionId=str(sid_s)
                    )
                    # source = S#, notes = minute pass
                    try: _mark_order_notes(parent_id, pass_tag or code)
                    except Exception: pass
                    try:
                        con = _auto_conn()
                        con.execute("UPDATE orders SET source=?, notes=? WHERE id=?", (code, pass_tag or code, parent_id))
                        con.commit()
                    except Exception:
                        pass

                    # decision + companion hedge (DB path)
                    _place_decision(ctx, plan, str(mid_s), str(sid_s), parent_id, run_id)
                    try:
                        _place_companion_hedge(
                            parent_id=parent_id, direction=direction or "LAY->BACK",
                            entry_odds=entry_odds, parent_stake=parent_sz,
                            target_ticks=_si(plan.get("target_ticks"), default=1),
                            marketId=str(mid_s), selectionId=str(sid_s), run_id=run_id
                        )
                    except Exception:
                        pass

                    _set_order_status(parent_id, "placed")
                    logger(f"[DEC] ENTER {code} order_id={parent_id} mid={mid_s} sid={sid_s} notes={pass_tag or code}")

                    # LIVE route: child inherits S# (code), CHILD must LAPSE per your spec
                    if ctx.get("source","LIVE") == "LIVE":
                        try:
                            from engines.live.live_router import place_parent_and_hedge
                            ht = max(1, abs(_si(plan.get("target_ticks"), default=1)))
                            bet_id, cref = place_parent_and_hedge(
                                market_id=str(mid_s), selection_id=str(sid_s),
                                side=side, entry_odds=entry_odds, stake=parent_sz,
                                hedge_ticks=ht, source=code,
                                parent_persistence="LAPSE", child_persistence="LAPSE",
                            )
                            try: _mark_last_child_placed(parent_id)
                            except Exception: pass
                            con2 = _auto_conn()
                            con2.execute("UPDATE orders SET bf_bet_id=?, customer_ref=? WHERE id=?",
                                         (bet_id, cref, parent_id))
                            con2.commit()
                        except Exception as e:
                            logger(f"[LIVE] route error (S): {e}")

                    # one S placement per tick is enough
                    return parent_id

                except Exception as e:
                    logger(f"[DEC] queue error (S scope): {e}")
                    continue

                picked += 1
                if picked >= MAX_RUNNERS_S:
                    break

        # -------- FALLBACK: original ctx-based S (keeps existing behaviour if scope yields nothing) --------
        # Safe per-block runner IDs for fallback (avoid unbound name errors)
        mid_s = str(ctx.get("marketId") or market_id or "")
        sid_s = str(ctx.get("selectionId") or selection_id or "")

        letter = "S"
        code   = _next_pair_tag(letter, mid_s)

        # Gate S with the live ctx only (no schedule recompute)
        try:
            od = float(ctx.get("odds") or ctx.get("entry_odds") or ctx.get("current_odds") or 0.0)
        except Exception:
            od = 0.0
        if not (1.5 <= od <= 8.0):
            return None

        # PRE time window for S (0..60 inclusive)
        m = _minute_bucket(ctx.get("tto_min", ctx.get("tto_minutes")))
        if not (0 <= m <= _MIN_PRE_START):
            return None

        pass_tag = _scout_pass_for_tto(ctx.get("tto_min", ctx.get("tto_minutes")))
        if not pass_tag:
            return None
        if _already_open_pass(mid_s, sid_s, pass_tag, mode=ctx.get("source","LIVE")):
            return None

        # plan from mastery if available, else legacy
        if mp is not None and hasattr(mp, "propose_trade"):
            try:
                plan = mp.propose_trade(ctx)
            except Exception as e:
                logger(f"[mastery] propose warn: {e}")
                plan = _legacy_scout_plan(ctx)
        else:
            plan = _legacy_scout_plan(ctx)

        if not plan.get("enter"):
            logger(f"[DEC] NO-TRADE | {plan.get('why','(no reason)')}")
            return None

        ticks_int = _si(plan.get("target_ticks"), default=0)
        if ticks_int <= 0:
            logger("[DEC] NO-TRADE | target_ticks <= 0 (blocked)")
            return None

        # S family cap
        ok_cap, cap_reason = _can_open_scalp(mid_s, sid_s, max_per_runner=3, run_id=run_id, family_letter="S")
        if not ok_cap and (ctx.get("source","LIVE") != "TEST"):
            msg = f"[DEC] gate block | {(cap_reason or 'unknown')} mid={mid_s} sid={sid_s}"
            logger(msg)
            try: _log_event("INFO", "DecisionEngine", msg)
            except Exception: pass
            return None

        direction = str(plan.get("direction") or "")
        side = "LAY" if direction.startswith("LAY") else "BACK"

        # price from latest for these exact runner IDs
        last_fallback, _ = _latest_price(mid_s, sid_s)
        entry_odds  = float(last_fallback) if last_fallback else float(ctx.get("odds") or 6.0)
        parent_size = float(plan.get("size") or 2.0)

        ctx["entry_odds"] = entry_odds

        try:
            t = max(1, abs(_si(plan.get("target_ticks"), default=1)))
            preview_side = "BACK" if side == "LAY" else "LAY"
            preview_odds = _odds_plus_ticks(entry_odds, +t) if side == "LAY" else _odds_plus_ticks(entry_odds, -t)
            logger(f"[TRACE] PLAN {side}->{preview_side} entry={entry_odds:.2f} ticks={t} hedge*={preview_odds:.2f}")
        except Exception:
            pass

        parent_id = _queue_order(
            run_id=run_id, side=side, odds=entry_odds, stake=parent_size,
            marketId=mid_s, selectionId=sid_s
        )

        # Stamp order: source = S{n} (code), notes = minute pass (Sxx)
        try:
            _mark_order_notes(parent_id, pass_tag or code)
        except Exception:
            pass
        try:
            con = _auto_conn()
            con.execute(
                "UPDATE orders SET source=?, notes=? WHERE id=?",
                (code, pass_tag or code, parent_id)
            )
            con.commit()
        except Exception:
            pass

        _place_decision(ctx, plan, mid_s, sid_s, parent_id, run_id)
        try:
            _place_companion_hedge(
                parent_id=parent_id, direction=direction or "LAY->BACK",
                entry_odds=entry_odds, parent_stake=parent_size,
                target_ticks=_si(plan.get("target_ticks"), default=1),
                marketId=mid_s, selectionId=sid_s, run_id=run_id
            )
        except Exception:
            pass

        _set_order_status(parent_id, "placed")
        logger(
            f"[DEC] ENTER {code} order_id={parent_id} mid={mid_s} sid={sid_s} "
            f"src={ctx.get('source','LIVE')} notes={pass_tag or code}"
        )

        # LIVE route with CHILD set to LAPSE
        if ctx.get("source","LIVE") == "LIVE":
            try:
                from engines.live.live_router import place_parent_and_hedge
                ht = max(1, abs(_si(plan.get("target_ticks"), default=1)))
                bet_id, cref = place_parent_and_hedge(
                    market_id=mid_s, selection_id=sid_s,
                    side=side, entry_odds=entry_odds, stake=parent_size,
                    hedge_ticks=ht, source=code,
                    parent_persistence="LAPSE", child_persistence="LAPSE",
                )
                try:
                    _mark_last_child_placed(parent_id)
                except Exception:
                    pass

                con = _auto_conn()
                con.execute("UPDATE orders SET bf_bet_id=?, customer_ref=? WHERE id=?",
                            (bet_id, cref, parent_id))
                con.commit()
            except Exception as e:
                logger(f"[LIVE] route error: {e}")

        return parent_id

    except Exception as e:
        logger(f"[S] hybrid warn: {e}")
        return None



def _get_run_bounds(run_id: str) -> tuple[Optional[str], Optional[str]]:
    db = _bets_conn()
    row = db.execute(
        "SELECT started_at, ended_at FROM sim_runs WHERE run_id=? ORDER BY id DESC LIMIT 1",
        (run_id,)
    ).fetchone()
    if not row:
        return None, None
    return (row["started_at"] if isinstance(row, sqlite3.Row) else row[0],
            row["ended_at"]   if isinstance(row, sqlite3.Row) else row[1])


def _orders_cols(con: sqlite3.Connection) -> list[str]:
    return [r["name"] for r in con.execute("PRAGMA table_info(orders)")]



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

    q_int = lambda sql, a=args: int(con.execute(sql, a).fetchone()[0] or 0)

    parents_total   = q_int(f"SELECT COUNT(*) FROM orders WHERE {parent_where}{where_run}")
    parents_matched = q_int(f"SELECT COUNT(*) FROM orders WHERE {parent_where}{where_run} AND entry_status='matched'")
    parents_open    = q_int(f"SELECT COUNT(*) FROM orders WHERE {parent_where}{where_run} AND (closed_at IS NULL OR closed_at='')")

    children_total   = q_int(f"SELECT COUNT(*) FROM orders WHERE {child_where}{where_run}")
    children_matched = q_int(f"SELECT COUNT(*) FROM orders WHERE {child_where}{where_run} AND entry_status='matched'")
    children_open    = q_int(f"SELECT COUNT(*) FROM orders WHERE {child_where}{where_run} AND (closed_at IS NULL OR closed_at='')")

    runners_distinct = q_int(f"SELECT COUNT(DISTINCT marketId || '/' || selectionId) FROM orders WHERE {parent_where}{where_run}")

    # market P&L (parents only) for win%
    mkt_rows = con.execute(
        f"""
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
    top_rows = con.execute(
        f"""
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
    if started_at and db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_trades'").fetchone():
        has_src = any(r[1]=='source' for r in db.execute("PRAGMA table_info('pnl_trades')"))
        if ended_at:
            if has_src:
                live_sum = float(db.execute(
                    "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE source=? AND datetime(created_at)>=datetime(?) AND datetime(created_at)<=datetime(?)",
                    (src, started_at, ended_at)
                ).fetchone()[0] or 0.0)
            else:
                live_sum = float(db.execute(
                    "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE datetime(created_at)>=datetime(?) AND datetime(created_at)<=datetime(?)",
                    (started_at, ended_at)
                ).fetchone()[0] or 0.0)
        else:
            if has_src:
                live_sum = float(db.execute(
                    "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE source=? AND datetime(created_at)>=datetime(?)",
                    (src, started_at)
                ).fetchone()[0] or 0.0)
            else:
                live_sum = float(db.execute(
                    "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades "
                    "WHERE datetime(created_at)>=datetime(?)",
                    (started_at,)
                ).fetchone()[0] or 0.0)

    ledger_sum = 0.0
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pnl_daily'").fetchone():
        has_src_ledger = any(r[1]=='source' for r in db.execute("PRAGMA table_info('pnl_daily')"))
        if has_src_ledger:
            ledger_sum = float(db.execute(
                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day=? AND source=?",
                (run_day, src)
            ).fetchone()[0] or 0.0)
        else:
            ledger_sum = float(db.execute(
                "SELECT COALESCE(SUM(amount),0.0) FROM pnl_daily WHERE run_day=?",
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
    seconds  = max(0.0, (end_dt - start_dt).total_seconds()) if start_dt else 0.0
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

def _snapshot_runner_activity(marketId: str, selectionId: str,
                              odds: float | None, slope_ppm: float | None) -> None:
    """
    Classify runner status (active/passive/ignored) and append a snapshot row to BETS_DB.runner_activity.
    If status changed vs the previous snapshot, write a RunnerStatus event via _log_event.
    Safe: never raises.
    """
    try:
        status_now = _status_from_odds(odds)  # uses your [1.5..8]=active, (8..12]=passive, >12=ignored logic
    except Exception:
        status_now = "ignored"

    # read last status (if any)
    last_status = None
    try:
        db = _bets_conn()
        db.row_factory = sqlite3.Row
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_activity'").fetchone():
            row = db.execute(
                "SELECT status FROM runner_activity WHERE marketId=? AND selectionId=? ORDER BY id DESC LIMIT 1",
                (str(marketId), str(selectionId))
            ).fetchone()
            if row and row["status"]:
                last_status = str(row["status"]).lower()
    except Exception:
        last_status = None

    # emit transition event if changed
    try:
        if (last_status is not None) and (last_status != status_now):
            # include slope_ppm for BTL/LTB “why”
            msg = (f"{marketId}/{selectionId} {last_status}→{status_now}"
                   f"{(' slope=' + f'{float(slope_ppm):.3f}' if slope_ppm is not None else '')}")
            _log_event("INFO", "RunnerStatus", msg)
    except Exception:
        pass

    # append snapshot row
    try:
        db = _bets_conn()
        db.execute("""
          CREATE TABLE IF NOT EXISTS runner_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT, selectionId TEXT, status TEXT, created_at TEXT
          )
        """)
        db.execute(
          "INSERT INTO runner_activity(marketId,selectionId,status,created_at) "
          "VALUES (?,?,?, datetime('now','utc'))",
          (str(marketId), str(selectionId), str(status_now))
        )
        db.commit()
    except Exception:
        try: db.rollback()
        except Exception: pass


def _persist_runner_activity(marketId: str, selectionId: str, status: str) -> None:
    """Write a status snapshot so other views (and LIVE) can read it."""
    db = _bets_conn()
    try:
        db.execute("""
          CREATE TABLE IF NOT EXISTS runner_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT, selectionId TEXT, status TEXT, created_at TEXT
          )
        """)
        db.execute(
          "INSERT INTO runner_activity(marketId,selectionId,status,created_at) "
          "VALUES (?,?,?, datetime('now','utc'))",
          (str(marketId), str(selectionId), str(status))
        )
        db.commit()
    except Exception:
        try: db.rollback()
        except Exception: pass
# --- PATCH END ----------------------------------------------------------------

def _runner_activity(marketId: str, selectionId: str) -> str:
    """
    Return 'active'|'ignored'|'passive'.
    Priority:
      1) Existing snapshots (runner_activity / inbound_bets_min.active / runners.status)
      2) Fallback to current odds from AUTO_DB.inbound_oc_cache → persist snapshot
    """
    db = _bets_conn()
    try:
        db.row_factory = sqlite3.Row
    except Exception:
        pass

    # 1) Try existing signals (latest row)
    try:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runner_activity'").fetchone():
            r = db.execute(
                "SELECT status FROM runner_activity WHERE marketId=? AND selectionId=? "
                "ORDER BY rowid DESC LIMIT 1", (str(marketId), str(selectionId))
            ).fetchone()
            if r and r["status"]:
                return str(r["status"]).lower()

        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbound_bets_min'").fetchone():
            cols = [c["name"] for c in db.execute("PRAGMA table_info(inbound_bets_min)")]
            if "active" in cols:
                r = db.execute(
                    "SELECT active FROM inbound_bets_min WHERE marketId=? AND selectionId=? "
                    "ORDER BY rowid DESC LIMIT 1", (str(marketId), str(selectionId))
                ).fetchone()
                if r is not None:
                    return "active" if int(r[0] or 0) == 1 else "ignored"

        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='runners'").fetchone():
            cols = [c["name"] for c in db.execute("PRAGMA table_info(runners)")]
            if "status" in cols:
                r = db.execute(
                    "SELECT status FROM runners WHERE marketId=? AND selectionId=? "
                    "ORDER BY rowid DESC LIMIT 1", (str(marketId), str(selectionId))
                ).fetchone()
                if r and r["status"]:
                    return str(r["status"]).lower()
    except Exception:
        pass

    # 2) Fallback: read current odds from AUTO_DB and classify → persist snapshot
    oc1, band = _latest_price(str(marketId), str(selectionId))
    odds = oc1
    if odds is None and band:
        try:
            odds = float(band[-1])
        except Exception:
            odds = None

    status = _status_from_odds(odds)
    try:
        _persist_runner_activity(marketId, selectionId, status)
    except Exception:
        pass
    return status

def _open_parents_count(marketId: str, selectionId: str, run_id: str | None = None) -> int:
    """
    Count OPEN parent (entry) orders for this *runner* in AUTO_DB.
    Parent = no hedge link (hedge_of/parent_id is NULL/'')
    Open   = closed_at IS NULL/''
    Filter strictly by (marketId, selectionId); restrict to this run if run_id is provided;
    always restrict by current mode if the column exists.
    """
    con = _auto_conn()
    try:
        con.row_factory = sqlite3.Row
        cols = [r["name"] for r in con.execute("PRAGMA table_info(orders)")]
        link_col = "hedge_of" if "hedge_of" in cols else ("parent_id" if "parent_id" in cols else None)
        parent_pred = f"({link_col} IS NULL OR {link_col}='')" if link_col else "1=1"

        args: list = [str(marketId), str(selectionId)]
        where = f"WHERE {parent_pred} AND (closed_at IS NULL OR closed_at='') AND marketId=? AND selectionId=?"

        if run_id and "run_id" in cols:
            where += " AND run_id=?"
            args.append(str(run_id))

        # Always restrict by current source if column present
        if "mode" in cols:
            src = _current_source().upper()
            if src in ("TEST","LEARNING","LIVE"):
                where += " AND mode=?"
                args.append(src)

        row = con.execute(f"SELECT COUNT(*) AS n FROM orders {where}", tuple(args)).fetchone()
        return int(row["n"] if row else 0)
    except Exception:
        return 0


# 📍 TARGET: engines/decision_engine/orchestrator.py
# 🔎 SEARCH: def _can_open_scalp(
def _can_open_scalp(market_id: str,
                    selection_id: str,
                    max_per_runner: int = 3,
                    *,
                    run_id: str | None = None,
                    mode: str | None = None,
                    family_letter: str | None = None) -> tuple[bool, str]:
    """
    Cap = number of PARENT rows where the hedge (child) is NOT yet matched.
    A slot frees immediately when any child for that parent matches, even if
    the parent hasn't been finalized/closed yet.

    If `family_letter` is provided (e.g. 'A','S','X'...), the cap is applied
    *per family* using orders.source LIKE 'A%' etc. If not provided, the cap
    is global across all families (legacy behaviour).
    """
    import sqlite3
    from engines.config_paths import autoscalp_db

    con = None
    try:
        src_mode = (mode or "").upper()
        if not src_mode:
            try:
                src_mode = (ctx.get("source") or "LIVE").upper()  # ctx exists in decide_once
            except Exception:
                src_mode = "LIVE"

        con = sqlite3.connect(autoscalp_db(), timeout=8)
        con.row_factory = sqlite3.Row

        cols = {r[1] for r in con.execute("PRAGMA table_info(orders)")}
        has = lambda c: c in cols
        link = "hedge_of" if has("hedge_of") else ("parent_id" if has("parent_id") else None)
        mode_col = "mode" if has("mode") else None
        src_col  = "source" if has("source") else None
        role_col = "role"  if has("role")  else None

        # Parent discriminator
        if role_col:
            parent_pred = "p.role='PARENT'"
        elif link:
            parent_pred = f"(p.{link} IS NULL OR p.{link}='')"
        else:
            parent_pred = "1=1"

        # child matched predicate/link
        child_link = "c.hedge_of = p.id" if has("hedge_of") else ("c.parent_id = p.id" if has("parent_id") else None)
        child_matched = "UPPER(COALESCE(c.entry_status,''))='MATCHED'"

        # Base WHERE (runner)
        where = [parent_pred, "p.marketId=?", "p.selectionId=?"]
        args: list = [str(market_id), str(selection_id)]

        # Per-family letter filter (orders.source LIKE 'A%') if requested and column exists
        if src_col and isinstance(family_letter, str) and family_letter.strip():
            where.append("UPPER(COALESCE(p.source,'')) LIKE UPPER(?)")
            args.append(f"{family_letter.strip().upper()}%")

        # Mode filter if present
        if mode_col:
            where.append("UPPER(COALESCE(p.mode,''))=UPPER(?)")
            args.append(src_mode)

        where_sql = " AND ".join(where)

        # Count OPEN parents = parents with NO matched child
        if child_link:
            sql = f"""
                SELECT COUNT(*) AS n_open
                FROM orders p
                WHERE {where_sql}
                  AND NOT EXISTS (
                        SELECT 1 FROM orders c
                        WHERE {child_link}
                          AND {child_matched}
                    )
            """
        else:
            # Fallback: treat 'no closed_at' as open parent when no link column
            sql = f"""
                SELECT COUNT(*) AS n_open
                FROM orders p
                WHERE {where_sql}
                  AND (p.closed_at IS NULL OR p.closed_at='')
            """

        n_open = int(con.execute(sql, tuple(args)).fetchone()[0] or 0)

        if n_open >= int(max_per_runner):
            fam = (f":{family_letter.strip().upper()}" if family_letter else "")
            return (False, f"cap_reached:{n_open}{fam}")
        return (True, "ok")
    except Exception as e:
        try:
            if con: con.close()
        except Exception:
            pass
        # fail-open (prefer to trade rather than deadlock)
        return (True, f"soft_allow:{e!s}")
    finally:
        try:
            if con: con.close()
        except Exception:
            pass



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
    last, band = _latest_price(marketId, selectionId)
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

    # Mark hedge child + parent matched
    con = _auto_conn()
    link_col = _ensure_orders_link_col(con)
    row = con.execute(
        f"SELECT id FROM orders WHERE {link_col}=? ORDER BY id DESC LIMIT 1",
        (order_id,)
    ).fetchone()
    child_id = int(row["id"]) if row else None
    if child_id is not None:
        con.execute("UPDATE orders SET entry_status='matched', closed_at=datetime('now','utc') WHERE id=?", (child_id,))
    con.execute("UPDATE orders SET entry_status='matched', closed_at=datetime('now','utc') WHERE id=?", (order_id,))
    # After marking matched
    _finalize_parent_on_hedge(order_id, target, stake, exit_bet_id=None, apply_commission=(_current_source().upper()=="LEARNING"))

    con.commit()

    # Compute parent P&L (ticks → £)
    realized_ticks = abs(int(target_ticks))
    pnl_amt = _pnl_ticks_to_amount(entry_odds, realized_ticks, float(stake))

    # Apply commission (conservative per-trade) in LEARNING when profitable
    if _current_source().upper() == "LEARNING" and pnl_amt > 0.0:
        pnl_amt = pnl_amt * (1.0 - float(COMMISSION_RATE))

    # Persist parent P&L to orders + pnl_trades + mastery
    _set_order_status(order_id, "matched", pnl_amt)

    evt_ctx = ctx or {
        "distance_band": "", "code": "", "tto_window": "",
        "entry_odds": entry_odds, "target_ticks": realized_ticks
    }
    mp.record_outcome(f"SIM-{order_id}", evt_ctx, {"success": True, "realized_ticks": realized_ticks})

    src = _current_source()
    bdb = _bets_conn()
    bdb.execute("""
      CREATE TABLE IF NOT EXISTS pnl_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        amount REAL, created_at TEXT, settled_at TEXT, source TEXT
      )
    """)
    bdb.execute(
        "INSERT INTO pnl_trades (amount, created_at, settled_at, source) "
        "VALUES (?, datetime('now','utc'), datetime('now','utc'), ?)",
        (pnl_amt, src)
    )
    bdb.execute(
      "INSERT INTO mastery_events(event_type, details_json, delta_progress, source) "
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
    rows = con.execute(
        """
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



