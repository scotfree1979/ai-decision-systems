from __future__ import annotations

from typing import Dict, List, Tuple, Optional, Callable, Any
import os, json, sqlite3

# ── Local engine utilities ───────────────────────────────────────────────────
from .scope import advance_scope_cursor
from .placement import place_from_plan
try:
    # decisions-table writer used by placement; keep schema consistent
    from .placement import _write_decision as _lane_write_decision
except Exception:
    _lane_write_decision = None

from .helpers import (
    open_auto_db as _adb,
    q_retry as _q,
    status_once,
)
# ── Plan ledger (guarded) ─────────────────────────────────────────────────────
try:
    from engines.mastery.plan_ledger import record_plan, concurrency_ok
except Exception:
    record_plan = None
    def concurrency_ok(*_a, **_k): return True

# Optional helper imports (we provide fallbacks if not available)
try:
    from .helpers import harden_ctx as _harden_ctx, harden_plan as _harden_plan
except Exception:
    _harden_ctx = None
    _harden_plan = None

# Mastery policy (new unified API)
from engines.mastery import mastery_policy as mp
from engines.mastery.context_builder_next import build_context_from_scope as build_context


# Optional market monitor
try:
    from engines.market_monitor import monitor as marketmon
except Exception:
    marketmon = None

# Optional: RangeGate (range + bias + time-window pre-checks)
try:
    from range_gate import assess_letters_for_plan
except Exception:
    assess_letters_for_plan = None

# Optional: bias engine (plan veto / annotation)
try:
    from engines.bias import compute_bias
except Exception:
    try:
        from engines.bias.engine import compute_bias
    except Exception:
        compute_bias = None

# Optional: trend analyzer to ensure direction in plan
try:
    from engines.odds.trend_analyzer.direction_service import ensure_direction_in_plan
except Exception:
    def ensure_direction_in_plan(plan: dict, ctx: dict) -> None:
        if not plan.get("direction"):
            plan["direction"] = "LAY->BACK" if float(ctx.get("odds", 0) or 0) >= 4.0 else "BACK->LAY"
        plan.setdefault("edge", "L2B" if plan["direction"] == "LAY->BACK" else "B2L")


# ── Rotation cursor per market (round-robin runners) ─────────────────────────
SID_CURSOR: Dict[str, int] = {}

# ── Blueprint strategy (P) is bespoke; other families are via Mastery ───────
try:
    from engines.decision_engine.strategies.blueprints_strategy import decide as bp_decide  # P
except Exception:
    bp_decide = None

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: ^def dryrun\(
# 📆 PATCHED: 2025-10-09T23:59Z — scope-agnostic version
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def dryrun():
    """
    Quick diagnostic dry-run: tests DecideOnce → Mastery linkage.
    Works with both old and new scope shapes.
    """
    from engines.mastery.context_builder_next import build_context_from_scope
    from engines.mastery.mastery_policy import plan_for_strategy
    from engines.decision_engine.decide_once.scope import read_scope_window

    print("[DRYRUN] testing DecideOnce→Mastery linkage")

    scope = read_scope_window(ahead_min=60)
    markets = scope.get("markets", [])
    print(f"[STEP] scope mids: {len(markets)}")

    # handle either dicts or strings
    mids = []
    if markets and isinstance(markets[0], dict) and "marketId" in markets[0]:
        mids = [m["marketId"] for m in markets]
    else:
        mids = [str(m) for m in markets]

    for m in mids[:5]:
        entry = None
        if isinstance(markets[0], dict):
            entry = next((x for x in markets if str(x.get("marketId")) == str(m)), None)
        sids = []
        if entry and isinstance(entry.get("active_sids"), list):
            sids = entry["active_sids"]
        elif "active_sids" in scope:
            sids = list(scope["active_sids"].get(str(m), []))
        else:
            sids = []

        print(f"[MARKET] {m} → {len(sids)} runners")
        for sid in sids[:3]:
            try:
                ctx, _ = build_context_from_scope(m, sid)
                plan = plan_for_strategy("MASTER", ctx)
                print(f"   {sid} → {plan.get('letter')} {plan.get('direction')} {plan.get('why')}")
            except Exception as e:
                print(f"   {sid} → error: {e}")
# === PATCH END ===

from typing import Callable, Dict, Any, List, Tuple


def _ensure_plan(fam: str, ctx: Dict[str, Any], res: Any) -> Dict[str, Any]:
    """
    Normalize any policy result to a dict. Never return None.
    """
    if isinstance(res, dict):
        # minimal shape so printers/routers are safe
        res.setdefault("enter", False)
        res.setdefault("letter", (str(res.get("letter") or fam[:1] or "A")[:1]).upper())
        res.setdefault("direction", res.get("direction"))
        res.setdefault("target_ticks", res.get("target_ticks") or res.get("ticks"))
        # be tolerant on numeric fields
        try:  res["size"] = float(res.get("size") or 0.0)
        except Exception: res["size"] = 0.0
        try:  res["px"] = float(res.get("px") or ctx.get("odds") or 0.0)
        except Exception: res["px"] = 0.0
        return res
    return {"enter": False, "why": f"policy_none:{fam}", "letter": (fam[:1] or "A").upper()}




def _policy_for(fam: str) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """
    Build a callable for a family that never raises and never returns None.
    If a specific plan function exists (plan_for_<lowercase>), use it;
    otherwise fall back to mp.plan_for_strategy(fam, ctx).
    """
    fn = getattr(mp, f"plan_for_{fam.lower()}", None)
    def _call(ctx: Dict[str, Any]) -> Dict[str, Any]:
        try:
            raw = fn(ctx) if callable(fn) else mp.plan_for_strategy(fam, ctx)
        except Exception as e:
            # never propagate—return a safe non‑enter plan with the error reason
            return {"enter": False, "why": f"policy_err:{fam}:{type(e).__name__}", "letter": (fam[:1] or "A").upper()}
        return _ensure_plan(fam, ctx, raw)
    return _call

# Replace your existing ORDER with this normalized one:
ORDER: List[Tuple[str, Callable[[Dict[str, Any]], Dict[str, Any]]]] = [
    ("ALWAYS_ON",         _policy_for("ALWAYS_ON")),
    ("BLUEPRINTS",        _policy_for("BLUEPRINTS")),
    ("OG_STRATEGY",       _policy_for("OG_STRATEGY")),
    ("LADDER_STRATEGY",   _policy_for("LADDER_STRATEGY")),
    ("S4_CROSSOVER",      _policy_for("S4_CROSSOVER")),
    ("S5_BREAKOUT",       _policy_for("S5_BREAKOUT")),
    ("S6_STEAM_FADE",     _policy_for("S6_STEAM_FADE")),
    ("BTL_SCOUT",         _policy_for("BTL_SCOUT")),
    ("BTL_AGGR",          _policy_for("BTL_AGGR")),
    ("IP1_SHOCK_DRIFT",   _policy_for("IP1_SHOCK_DRIFT")),
    ("IP2_TIRED_LEADER",  _policy_for("IP2_TIRED_LEADER")),
    ("IP3_CLOSE_FINISH",  _policy_for("IP3_CLOSE_FINISH")),
    ("IP4_FENCE_ERROR",   _policy_for("IP4_FENCE_ERROR")),
    ("IP5_COLLAPSE_FADE", _policy_for("IP5_COLLAPSE_FADE")),
]


# Map family name → letter code (for compact auditing)
_FAM_LETTER = {
    "ALWAYS_ON": "A",
    "BLUEPRINTS": "P",
    "OG_STRATEGY": "Z",
    "LADDER_STRATEGY": "L",
    "BTL_SCOUT": "B",
    "BTL_AGGR": "G",
    "S4_CROSSOVER": "X",
    "S5_BREAKOUT": "R",
    "S6_STEAM_FADE": "F",
    "MLM": "M",  # ← ADD THIS
    "IP1_SHOCK_DRIFT": "I",
    "IP2_TIRED_LEADER": "T",
    "IP3_CLOSE_FINISH": "C",
    "IP4_FENCE_ERROR": "E",
    "IP5_COLLAPSE_FADE": "K",
}


# ── Bias settings (safe defaults if bias module missing) ─────────────────────
_BIAS_VETO_CONF = float(os.environ.get("BIAS_VETO_CONF", "0.60"))
_BIAS_VETO_MAG  = float(os.environ.get("BIAS_VETO_MAG",  "0.15"))

def _attach_bias(plan: dict, ctx: dict) -> dict:
    if not compute_bias:
        plan.setdefault("bias", 0.0)
        plan.setdefault("bias_dir", "FLAT")
        plan.setdefault("bias_conf", 0.0)
        return plan
    try:
        out = compute_bias(ctx, plan)  # expected: value∈[-1,1], dir∈{'L2B','B2L','FLAT'}, conf∈[0..1], why:str
        plan["bias"]      = float(out.value)
        plan["bias_dir"]  = str(out.dir)
        plan["bias_conf"] = float(out.conf)
        if out.why:
            plan["why"] = (plan.get("why","") + f" | bias={out.dir}({out.value:+.2f}) conf={out.conf:.2f} :: {out.why}").strip()
    except Exception:
        plan.setdefault("bias", 0.0)
        plan.setdefault("bias_dir", "FLAT")
        plan.setdefault("bias_conf", 0.0)
    return plan

def _bias_veto(plan: dict) -> tuple[bool, str]:
    try:
        b = float(plan.get("bias", 0.0))
        c = float(plan.get("bias_conf", 0.0))
        d = (plan.get("direction") or "").upper()
        if c < _BIAS_VETO_CONF or abs(b) < _BIAS_VETO_MAG:
            return (False, "")
        # contradiction?
        if d in ("LAY->BACK","L2B") and b < 0:
            return (True, f"bias_veto: plan=L2B vs bias=B2L({b:+.2f}) conf={c:.2f}")
        if d in ("BACK->LAY","B2L") and b > 0:
            return (True, f"bias_veto: plan=B2L vs bias=L2B({b:+.2f}) conf={c:.2f}")
        return (False, "")
    except Exception:
        return (False, "")

def check_decision_engine(con):
    rows = con.execute("SELECT DISTINCT letter FROM decisions LIMIT 100;").fetchall()
    active_letters = [r["letter"] for r in rows if r["letter"]]
    print(f"[CHECK] DecisionEngine: {len(active_letters)} letters → {','.join(active_letters) or 'none'}")
    return bool(active_letters)


# ── Logging helpers so 'no place' reasons land in decisions ──────────────────
def _log_lane_skip(ctx: dict, *, mid=None, sid=None, letter: str = "A", why: str = "lanes:skip") -> None:
    if _lane_write_decision is None:
        return
    try:
        run_id = (ctx or {}).get("run_id")
        mid = mid or (ctx or {}).get("marketId")
        sid = sid or (ctx or {}).get("selectionId")
        if not mid or not sid:
            return
        _lane_write_decision(
            run_id=run_id, mid=str(mid), sid=str(sid),
            outcome="not_placed", why=str(why), letter=str(letter)[:1]
        )
    except Exception:
        pass

def _log_range_gate_skip(run_id: str | None, marketId: str, selectionId: str, letter: str, verdict: dict) -> None:
    try:
        needs = verdict.get("needs") or []
        short = verdict.get("why") or "range_gate"
        reason = "range_gate:" + (",".join(needs) if needs else short)
        if _lane_write_decision:
            _lane_write_decision(run_id=run_id, mid=str(marketId), sid=str(selectionId),
                                 outcome="not_placed", why=str(reason), letter=str(letter)[:1])
        try:
            from .helpers import record_decision_meta
        except Exception:
            record_decision_meta = None
        if record_decision_meta:
            record_decision_meta(run_id, marketId, selectionId, {
                "why": reason,
                "needs": needs,
                "window": verdict.get("window"),
                "bias": verdict.get("bias"),
                "range": verdict.get("range"),
                "dir": verdict.get("direction"),
                "conf": verdict.get("conf"),
            })
    except Exception:
        pass

# ── Hardeners (fallbacks if not provided by helpers) ─────────────────────────
_NUM_DEFAULTS_CTX = {
    "minutes_to_off": 1e9,
    "fav_rank": 99, "fav_rank_now": 99,
    "exposure": 0.0,
    "target_ticks": 1, "hedge_ticks": 1,
    "price": 0.0, "ltp": 0.0, "odds": 0.0,
    "traded_recent_sec": 0, "traded_recent_amt": 1.0,
}
_NUM_DEFAULTS_PLAN = {
    "target_ticks": 1, "stop_ticks": 0, "timeout_sec": 30,
    "size": 2.0, "pyramid_add_at": 0,
}
def _coerce_num(d: dict, key: str, default):
    typ = int if isinstance(default, int) and not isinstance(default, bool) else float if isinstance(default, float) else type(default)
    try:
        v = d.get(key, None)
        d[key] = typ(v) if v is not None else default
    except Exception:
        d[key] = default
def _harden_ctx_local(ctx: dict) -> None:
    for k, default in _NUM_DEFAULTS_CTX.items():
        _coerce_num(ctx, k, default)
def _harden_plan_local(plan: dict) -> None:
    for k, default in _NUM_DEFAULTS_PLAN.items():
        _coerce_num(plan, k, default)

def harden_ctx(ctx: dict) -> None:
    if _harden_ctx: 
        try: _harden_ctx(ctx); return
        except Exception: pass
    _harden_ctx_local(ctx)

def harden_plan(plan: dict) -> None:
    if _harden_plan:
        try: _harden_plan(plan); return
        except Exception: pass
    _harden_plan_local(plan)

# ── Plan-driven letter selection (window policy with future DB hook) ─────────
def _load_market_plan(mid: str) -> dict | None:
    """Hook to load persisted per-market plan: {'allow_letters': ['A','B',...]}."""
    # Intentionally returns None for now; we’ll rely on window policy.
    # When you persist plans, read them here (ensure schema first).
    return None

def _letters_by_window(mto_min: float | None) -> List[str]:
    # Keep ALWAYS_ON only as a fallback (runner loop tries it last anyway)
    if mto_min is None:               # unknown → keep minimal set without A
        return ["BLUEPRINTS"]

    m = float(mto_min)

    if m > 60.0:                      # >60m
        return ["BLUEPRINTS"]

    if 60.0 >= m > 30.0:              # 60m → 30m
        return ["BLUEPRINTS", "OG_STRATEGY", "S4_CROSSOVER", "S5_BREAKOUT", "S6_STEAM_FADE"]

    if 30.0 >= m > 20.0:              # 30m → 20m
        return ["BLUEPRINTS", "OG_STRATEGY", "S4_CROSSOVER", "S5_BREAKOUT", "S6_STEAM_FADE", "LADDER_STRATEGY"]

    if 20.0 >= m > 5.0:               # 20m → 5m
        return ["BLUEPRINTS", "OG_STRATEGY", "S4_CROSSOVER", "S5_BREAKOUT", "S6_STEAM_FADE", "LADDER_STRATEGY", "BTL_SCOUT", "BTL_AGGR"]

    if 5.0 >= m >= 3.0:               # 5m → 3m
        return ["BLUEPRINTS", "OG_STRATEGY", "S4_CROSSOVER", "S5_BREAKOUT", "S6_STEAM_FADE", "LADDER_STRATEGY", "BTL_AGGR"]

    if 3.0 > m > 0.0:                 # COOL_OFF
        return ["BLUEPRINTS", "MLM"]

    # in-play (m <= 0)
    return ["BLUEPRINTS", "IP1_SHOCK_DRIFT", "IP2_TIRED_LEADER", "IP3_CLOSE_FINISH", "IP4_FENCE_ERROR", "IP5_COLLAPSE_FADE"]


def _allowed_letters_for_tick(ctx: dict) -> List[str]:
    mid = str(ctx.get("marketId") or "")
    # Accept either 'minutes_to_off' or 'tto_minutes'
    mto = ctx.get("minutes_to_off", ctx.get("tto_minutes"))
    try:
        mto = float(mto) if mto is not None else None
    except Exception:
        mto = None
    plan = _load_market_plan(mid)
    if plan and isinstance(plan, dict) and plan.get("allow_letters"):
        return [str(x).upper() for x in plan["allow_letters"]]
    return _letters_by_window(mto)

# ── Fallback plan to force a "why" when nobody proposes ──────────────────────
def _fallback_plan_for_A(ctx: dict) -> dict:
    # DEFAULT stance (L2B), strict B2L only for clear steam on fav
    try: delta = float(ctx.get("oc1", 0.0)) - float(ctx.get("anchor_odd", 0.0))
    except Exception: delta = 0.0
    try: fav = int(ctx.get("fav_rank_est") or ctx.get("fav_rank") or 999)
    except Exception: fav = 999
    dirn = "BACK->LAY" if (delta <= -0.05 and fav == 1) else "LAY->BACK"
    return {
        "enter": True,
        "direction": dirn,
        "target_ticks": 1,
        "size": float(max(2.0, float(ctx.get("size_cap", 2.0) or 2.0))),
        "why": f"A-fallback dir={dirn} Δ={delta:.3f} fav={fav}",
        "letter": "A",
    }

# ── Small utility: recent 'not placed' summary for health line ───────────────
def _recent_no_place_summary(run_id: str, seconds: int = 30) -> str:
    try:
        con = _adb(ro=True); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT meta_json
              FROM decisions
             WHERE run_id = ?
               AND datetime(decided_at) >= datetime('now', ?)
             ORDER BY datetime(decided_at) DESC
             LIMIT 200
        """, (str(run_id), f"-{int(max(1, seconds))} seconds")).fetchall() or []
        # 🧩 ADD THIS DEBUG BLOCK ↓↓↓
        print(f"[DEBUG] recent_no_place_summary: rows={len(rows)} for run_id={run_id}")
        if rows:
            for i, r in enumerate(rows[:5]):
                try:
                    j = json.loads(r["meta_json"])
                except Exception:
                    j = {}
                print(f"[DEBUG] row {i}: why={j.get('why')} meta={j}")
        # 🧩 END DEBUG BLOCK ↑↑↑
        try: con.close()
        except Exception: pass
        counts: Dict[str,int] = {}
        for r in rows:
            why = None
            try:
                m = json.loads(r["meta_json"]) if r["meta_json"] else {}
                why = m.get("why") or m.get("placement_reason") or m.get("reason")
            except Exception:
                pass
            if not why:
                continue
            key = str(why).split(":", 1)[0].strip()
            counts[key] = counts.get(key, 0) + 1
        if not counts:
            return "recent reasons: (none)"
        parts = [f"{k}={v}" for k,v in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))][:8]
        return "recent reasons: " + ", ".join(parts)
    except Exception:
        return "recent reasons: (error)"

from datetime import datetime, timezone

def _minutes_to_off_from_schedule(mid: str) -> Optional[float]:
    try:
        con = _adb(ro=True); con.row_factory = sqlite3.Row
        r = _q(con, "SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (str(mid),)).fetchone()
        try: con.close()
        except Exception: pass
        if not r or not r["off_at_utc"]:
            return None
        iso = str(r["off_at_utc"]).replace("T", " ").replace("Z", "")
        off_dt = datetime.fromisoformat(iso).replace(tzinfo=timezone.utc)
        return (off_dt - datetime.now(timezone.utc)).total_seconds() / 60.0
    except Exception:
        return None


def _market_alive(mid: str) -> bool:
    try:
        con = _adb(ro=True); con.row_factory = sqlite3.Row
        # either OFF is in schedule
        off = con.execute("SELECT off_at_utc FROM markets_schedule WHERE marketId=? LIMIT 1", (mid,)).fetchone()
        if off and off["off_at_utc"]:
            return con.execute("SELECT datetime(?) >= datetime('now','utc','-4 minutes')", (off["off_at_utc"],)).fetchone()[0] == 1
        # or freshness from odds_current
        r = con.execute("SELECT MAX(datetime(updated_ts)) AS mx FROM odds_current WHERE marketId=? AND date(day)=date('now','utc')", (mid,)).fetchone()
        return bool(r and r["mx"] and con.execute("SELECT datetime(?) >= datetime('now','utc','-180 seconds')", (r["mx"],)).fetchone()[0] == 1)
    except: return False
    finally:
        try: con.close()
        except: pass

# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint: called by the orchestration wrapper per tick
# ─────────────────────────────────────────────────────────────────────────────
# === PATCH BLOCK: refresh live scope each tick (build_and_maintain_scope)
# 📍 FILE: engines/decision_engine/decide_once/lanes.py
# 🔎 ANCHOR: def run_all(run_id:
# 🧩 TYPE: REPLACEMENT (top section only)
# 📆 DATE: 2025-10-10T20:45Z
# ---------------------------------------------------------------------

def run_all(run_id: str, *, source: str = "LIVE", logger=None) -> Optional[int]:
    """
    DecideOnce main loop (LIVE edition).
    Ensures live scope is rebuilt each tick so strategies see current markets.
    Prints:
      • [LANES] scope refreshed (n markets)
      • [TICK] course — runner | A=✅ P=❌ …
      • [PLACE] fam mid=… sid=… letter dir px stake
    """
    import sys

    # --- banner --------------------------------------------------------
    try:
        if not hasattr(run_all, "_banner"):
            print("[LANES] run_all active — LIVE mode (Scope refresh each tick)")
            run_all._banner = True
    except Exception:
        pass

    # --- refresh live scope -------------------------------------------
    from engines.decision_engine.decide_once import scope
    try:
        snap = scope.build_and_maintain_scope(show_dashboard=False) or scope._SCOPE_STATE
        # normalise shape like dryrun does
        if isinstance(snap, dict):
            live = snap
        elif hasattr(scope, "_SCOPE_STATE"):
            live = scope._SCOPE_STATE or {}
        else:
            live = {}

        markets = live.get("markets") or []
        active_map = live.get("active_sids") or {}
        n = len(markets)
        print(f"[LANES] scope refreshed ({n} markets in state)")
    except Exception as e:
        live, markets, active_map = {}, [], {}
        print(f"[LANES] scope refresh warn: {e}")

    mids = [m["marketId"] if isinstance(m, dict) else str(m)
            for m in markets]

    if not mids:
        print("[LANES] warn: live scope empty — no active markets")
# ---------------------------------------------------------------------

    try:
        mp.ingest_scope(live)
    except Exception as e:
        raise RuntimeError(f"[DECIDE] Mastery ingest failed: {type(e).__name__}: {e}")

    # if scope is empty, synthesize candidates from NEXT-5 (bets.db) + inbound_oc_cache
    if not mids:
        raise RuntimeError("[DECIDE] mids empty after scope refresh")
        status_once("decide_once:tick", True, "scope empty — deferring to Mastery")
        try:
            from engines.fallbackscope import _next5_from_bets_with_active as _next5
            # feed Mastery something to chew on every tick:
            mids = _next5(max_items=5) or []
        except Exception:
            mids = []

    # --- Optional monitor
    try:
        if marketmon:
            marketmon.refresh(mids, max_runners=8)
    except Exception:
        pass

    base_ctx, _ = build_context(source=source)
    assert isinstance(base_ctx, dict), "[DECIDE] base_ctx invalid"

    # --- Build EPICs (sorted odds per market)
    all_pairs: List[Tuple[str, List[Tuple[str, float]]]] = []

    # === FIXED LOOP: preserve dict structure so active_sids are not lost ===
    for m_entry in (live.get("markets") or []):
        # normalise marketId and active_sids exactly like dryrun()
        if isinstance(m_entry, dict):
            mid = str(m_entry.get("marketId"))
            sids = list(m_entry.get("active_sids") or [])
        else:
            mid = str(m_entry)
            sids = list(live.get("active_sids", {}).get(mid, []))

        # ✅ local odds helpers
        from engines.decision_engine.decide_once.placement import (
            _fetch_px_from_odds_current as _px_oc,
            _fetch_px_from_inbound      as _px_ib,
        )

        pairs = []
        for sid in sids:
            # 1️⃣ Try odds_current first
            px = _px_oc(mid, sid)
            # 2️⃣ Then inbound_oc_cache
            if px is None:
                px = _px_ib(mid, sid)
            # 3️⃣ If still empty, fetch from Betfair API (verified working)
            if px is None or px == 0.0:
                try:
                    from engines.utils.api_tools import fetch_live_odds
                    odds = fetch_live_odds(None, mid, sid)
                    if isinstance(odds, dict):
                        px = float(odds.get("lay") or odds.get("back") or 0.0)
                except Exception as e:
                    print(f"[LANES][WARN] live API fallback failed mid={mid} sid={sid}: {e}")
            # Always append, even if px=0.0, so ticks still print
            pairs.append((sid, float(px or 0.0)))

        # === DEBUG: Verify scope linkage and odds retrieval ===
        if not sids:
            print(f"[LANES][DEBUG] no sids for {mid} "
                  f"(markets={len(live.get('markets', []))}, "
                  f"active_sids keys={len(live.get('active_sids', {}))})")
        elif not pairs or all(px == 0.0 for _, px in pairs):
            print(f"[LANES][DEBUG] no prices for {mid} sids={len(sids)} "
                  f"sample={sids[:5]}")

        # fallback: odds_current if empty
        if not pairs:

            try:
                from engines.config_paths import auto_conn, q_retry as _q
                con = auto_conn(); con.row_factory = __import__("sqlite3").Row
                rows = _q(con, """
                    SELECT DISTINCT selectionId, ltp
                      FROM odds_current
                     WHERE marketId=? AND ltp BETWEEN 1.5 AND 12.0
                     ORDER BY CAST(ltp AS REAL) ASC
                     LIMIT 12
                """, (mid,)).fetchall()
                con.close()
                pairs = [(str(r["selectionId"]), float(r["ltp"])) for r in rows if r["ltp"] is not None]
                if pairs:
                    print(f"[PATCH] odds_current fallback used for {mid} → {len(pairs)} runners")
            except Exception as e:
                print(f"[PATCH WARN] odds_current fallback failed: {e}")
                pairs = []

        pairs.sort(key=lambda x: float(x[1] or 9999))
        all_pairs.append((mid, pairs))


# === PATCH END ===


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: any_candidates = False
# 📆 PATCHED: 2025-10-07T09:45Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# === PATCH START (final runtime fix) ===
    any_candidates = False
    placed_any = False

    # ── iterate over all scoped markets + runners ──
    for mid, pairs in all_pairs:
        if not pairs:
            SID_CURSOR[mid] = 0
            continue

        any_candidates = True
        epic_size = len(pairs)
        epic_fav = pairs[0][0] if epic_size >= 1 else None
        epic_second = pairs[1][0] if epic_size >= 2 else None
        epic_field = [sid for sid, _ in pairs[2:]] if epic_size > 2 else []

        orig_len = epic_size
        for rank, (sid, px) in enumerate(pairs, start=1):
            ctx = dict(base_ctx)

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py (inside run_all, runner loop)
# 🔎 SEARCH: ctx = dict(base_ctx)
# 📆 PATCHED: 2025-11-20
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # attach band from MarketMonitor
            try:
                from engines.market_monitor.monitor import classify
                c = classify(mid, sid)
                ctx["band"] = c.get("band", "UNKNOWN")
            except Exception:
                ctx["band"] = "UNKNOWN"
# === PATCH END ===


            # ── attach live timing from Mastery scope (preserve scope→plan link) ──
            from engines.mastery.mastery_policy import _SCOPE_STATE
            timing = (_SCOPE_STATE.get("timing", {}) or {}).get(mid, {})
            ctx.update({
                "run_id": run_id,
                "mode": source,
                "marketId": mid,
                "selectionId": sid,
                "odds": float(px),
                "minutes_to_off": timing.get("minutes_to_off"),
                "phase": timing.get("phase", "PRE"),
                # EPIC context
                "fav_rank": rank,
                "epic_size": epic_size,
                "epic_fav": epic_fav,
                "epic_second": epic_second,
                "epic_field": epic_field,
            })
            harden_ctx(ctx)

            # --- skip if already placed today
            try:
                con = _adb(ro=True); con.row_factory = sqlite3.Row
                r = _q(con, """
                    SELECT 1 FROM orders
                     WHERE marketId=? AND selectionId=? AND date(opened_at)=date('now','utc')
                       AND UPPER(COALESCE(entry_status,status,'')) IN ('PLACED','MATCHED','LIVE')
                     LIMIT 1
                """, (mid, sid)).fetchone()
                con.close()
                if r:
                    continue
            except Exception:
                pass

            letters = _allowed_letters_for_tick(ctx)
            proposed_any = False

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: try:\n                    oid = place_from_plan\(fam_name, plan, ctx\)
# 📆 PATCHED: 2025-10-08T20:15Z — visual tick report (✅❌) with names, no duplicates
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # --- multi-letter decision + visual tick summary --------------------
            tick_report = {L: "❌" for L in _FAM_LETTER.values()}  # pre-seed all letters ❌

            for fam_name, fn in ORDER:
                letter = _FAM_LETTER.get(fam_name, "?")
                tick_report.setdefault(letter, "❌")  # ensure visible even if skipped

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/decide_once.py:run_all
# 🔎 SEARCH: fam_plan = fn(ctx)
# 📆 PATCHED: 2025-11-28 — preserve stoploss_mode from Mastery in plan
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
                try:
                    fam_plan = fn(ctx) if callable(fn) else mp.plan_for_strategy(fam_name, ctx)
                except Exception:
                    fam_plan = {}

                # NEW: ensure stoploss_mode persists from Mastery → DecideOnce → placement
                try:
                    slm = fam_plan.get("stoploss_mode")
                    if slm:
                        fam_plan["stoploss_mode"] = str(slm).upper()
                except Exception:
                    pass
# === PATCH END ===


                if not fam_plan or not fam_plan.get("enter"):
                    continue

                proposed_any = True
                plan = _attach_bias(dict(fam_plan), ctx)
                veto, reason = _bias_veto(plan)
                if veto:
                    continue

                plan.setdefault("marketId", mid)
                plan.setdefault("selectionId", sid)
                plan.setdefault("letter", letter)
                harden_plan(plan)

                try:
                    oid = place_from_plan(fam_name, plan, ctx)
                    if oid:
                        tick_report[letter] = "✅"
                        placed_any = True
                        if logger:
                            logger(f"[PLACE] {fam_name} mid={mid} sid={sid} plan={plan}")
                except Exception as e:
                    print(f"[LANE-ERR] place {fam_name} mid={mid} sid={sid} err={e}")

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: # === A-lane fallback (run at END when no family entered) ============
# 📆 PATCHED: 2025-10-08T22:40Z — “A” always fires every tick (executed last)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
            # === A-lane (always-on baseline; fires last) ===
            try:
                odds_val = float(ctx.get("odds") or 0.0)
                if odds_val > 0.0:
                    direction = "LAY->BACK" if odds_val >= 4.0 else "BACK->LAY"
                    a_plan = {
                        "enter": True,
                        "letter": "A",
                        "direction": direction,
                        "target_ticks": 1,
                        "hedge_ticks": 1,
                        "size": 2.0,
                        "px": odds_val,
                        "plan_why": "A-always-on baseline",
                    }
                    oid = place_from_plan("ALWAYS_ON", a_plan, ctx)
                    if oid:
                        tick_report["A"] = "✅"
                        placed_any = True
                    else:
                        tick_report["A"] = "❌"
                else:
                    tick_report["A"] = "❌"  # no px → no attempt
            except Exception as e:
                print(f"[A-LANE] fail mid={mid} sid={sid}: {e}")

# === PATCH END ===

            # --- print all letters even if blocked ---
            try:
                from engines.config_paths import connect_db, q_retry as _q
                con = connect_db(ro=True); con.row_factory = __import__("sqlite3").Row
                row = _q(con,
                    "SELECT COALESCE(market_name,event_name) AS mname, horse_name "
                    "FROM bets WHERE marketId=? AND selectionId=? LIMIT 1",
                    (mid, sid)
                ).fetchone(); con.close()
                mname = row["mname"] if row and row["mname"] else mid
                rname = row["horse_name"] if row and row["horse_name"] else sid
            except Exception:
                mname, rname = mid, sid

            status_line = "  ".join(f"{ltr}={tick_report[ltr]}" for ltr in sorted(tick_report))
            print(f"[TICK] {mname} — {rname} | {status_line}")

    # --- post-loop status line -----------------------------------------
    if not any_candidates:
        status_once("decide_once:tick", True, "no candidates — skip")
    elif placed_any:
        status_once("decide_once:tick", True, "OK — placed")
    else:
        summary = _recent_no_place_summary(run_id, seconds=30)
        status_once("decide_once:tick", True, f"OK — no placement ({summary})")

    try:
        advance_scope_cursor(step=1)
    except Exception:
        pass

    return None
# === PATCH END (final runtime fix) ===

if __name__ == "__main__":
    from engines.decision_engine.decide_once.lanes import dryrun
    dryrun()

