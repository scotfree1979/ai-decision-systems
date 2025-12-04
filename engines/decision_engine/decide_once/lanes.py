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

# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 📆 PATCHED: 2025-12-04 — Universal plan safety wrapper
# ============================================================================

def _safe_plan(fam: str, raw: Any, ctx: dict) -> dict:
    """
    Guarantee that every policy returns a valid plan dict
    with all required fields populated.

    This permanently eliminates:
        'NoneType' object is not subscriptable
        missing keys
        malformed plan objects
    """
    # If family returned None → convert to safe no-entry plan
    if raw is None:
        return {
            "enter": False,
            "letter": fam[:1].upper(),
            "direction": None,
            "size": 0.0,
            "target_ticks": 1,
            "why": f"{fam}:none"
        }

    # If returned tuple or other non-dict, wrap it
    if not isinstance(raw, dict):
        return {
            "enter": False,
            "letter": fam[:1].upper(),
            "direction": None,
            "size": 0.0,
            "target_ticks": 1,
            "why": f"{fam}:invalid_type"
        }

    # Ensure required fields
    raw.setdefault("enter", False)
    raw.setdefault("letter", fam[:1].upper())
    raw.setdefault("direction", None)
    raw.setdefault("size", 0.0)
    raw.setdefault("target_ticks", 1)
    raw.setdefault("why", f"{fam}:ok")

    # Best-effort numeric coercion
    try: raw["size"] = float(raw.get("size") or 0.0)
    except: raw["size"] = 0.0

    try: raw["target_ticks"] = int(raw.get("target_ticks") or 1)
    except: raw["target_ticks"] = 1

    return raw
# === PATCH END ==============================================================



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
        from engines.config_paths import auto_conn
        con = auto_conn(rw=False); con.row_factory = sqlite3.Row
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
        from engines.config_paths import auto_conn
        con = auto_conn(rw=False); con.row_factory = sqlite3.Row
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
        from engines.config_paths import auto_conn
        con = auto_conn(rw=False); con.row_factory = sqlite3.Row
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

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: def run_all(
# 🛠 ACTION: replace entire run_all() implementation
# 📆 PATCHED: 2025-11-29 — Unified DecideOnce + MSC tick engine
# ============================================================================

def run_all(run_id: str, source: str = "LIVE", logger=None) -> Optional[int]:
    """
    Unified DecideOnce + MSC Tick Engine
    ------------------------------------
    This version guarantees:
        • ACTIVE + PASSIVE runner processing
        • correct v7-intel context for all families
        • correct MSC + Legacy plan routing
        • clear tick output for debugging
    """
    import sys
    from engines.decision_engine.decide_once import scope
    from engines.decision_engine.decide_once.scope import build_and_maintain_scope

    from engines.decision_engine.decide_once.candidates import active_candidates_for_market, cands_for_market
    from engines.decision_engine.decide_once.placement import place_from_plan
    from engines.decision_engine.microscalper.context_adapter import build_msc_context
    from engines.decision_engine.microscalper.plan_builder import build_plan_from_msc
    from engines.live.live_router import place_parent_and_hedge


    # ------------------------------------------------------------------
    # 1) SCOPE REFRESH  (canonical: build_and_maintain_scope)
    # ------------------------------------------------------------------
    try:
        from engines.decision_engine.decide_once.scope import (
            build_and_maintain_scope,
            ordered_markets_for_tick,
        )

        # full scope snapshot (PRE + INPLAY + ACTIVE/PASSIVE maps)
        live_scope = build_and_maintain_scope(show_dashboard=False) or {}

        # canonical ordered MID list for this tick
        markets = ordered_markets_for_tick(live_scope) or []

        print(f"[LANES] scope refreshed ({len(markets)} markets in state)")

    except Exception as e:
        print(f"[LANES] scope refresh warn: {e}")
        return None

    # early abort: nothing in scope
    if not markets:
        print("[LANES] warn: no markets in scope")
        return None


    # ------------------------------------------------------------------
    # 2) BASE CONTEXT (shared legacy + MSC)
    # ------------------------------------------------------------------
    base_ctx, _ = build_context(source=source)
    if not isinstance(base_ctx, dict):
        print("[DECIDE] invalid base_ctx")
        return None

    # ------------------------------------------------------------------
    # 3) PER-MARKET LOOP
    # ------------------------------------------------------------------
    for mid in markets:
        mids = str(mid)
        active = live_scope.get("active_sids", {}).get(mids, [])
        passive = live_scope.get("passive_sids", {}).get(mids, [])

        # ACTIVE + PASSIVE combined routing
        sids = list(active) + list(passive)

        if not sids:
            print(f"[LANES] no runners for {mids}")
            continue

        # --------------------------------------------------------------
        # BUILD ORDERED PAIRS (sid → price)
        # --------------------------------------------------------------
        pairs = []
        for sid in sids:
            try:
                from engines.decision_engine.decide_once.placement import (
                    _fetch_px_from_odds_current as _px_oc,
                    _fetch_px_from_inbound      as _px_ib,
                )
                px = _px_oc(mids, sid)
                if px is None:
                    px = _px_ib(mids, sid)

                if px is None:
                    from engines.utils.api_tools import fetch_live_odds
                    odds = fetch_live_odds(None, mids, sid)
                    if isinstance(odds, dict):
                        px = float(odds.get("lay") or odds.get("back") or 0.0)

                pairs.append((sid, float(px or 0.0)))
            except Exception:
                pairs.append((sid, 0.0))

        pairs.sort(key=lambda t: (t[1], t[0]))

        # ------------------------------------------------------------------
        # PRINT TICK HEADER (UPGRADED)
        # ------------------------------------------------------------------
        px_str = ", ".join(f"{sid}:{px}" for sid, px in pairs[:8])
        print(f"[TICK] {mids}  runners={len(pairs)}  {px_str}")

        # ----------------------------------------------------------------------
        # 4) LEGACY DECIDEONCE PROCESSING
        # ----------------------------------------------------------------------
        for sid, px in pairs:
            ctx = dict(base_ctx)
            ctx["marketId"] = mids
            ctx["selectionId"] = sid
            ctx["current_price"] = px
            ctx["is_passive"] = sid in passive

            # --- MicroScalper v7 tick (correct location: full ctx available) ---
            try:
                from engines.micro_scalper_v7.micro_scalper_engine import MicroScalperEngine
                msc = run_all.__dict__.setdefault("_MSC_SINGLETON", MicroScalperEngine())

                # MSC tick uses the SAME ctx we pass into legacy lanes
                msc_plan = msc.tick(ctx)

                if msc_plan:
                    # store last MSC plan for unified tick report (epic output block)
                    run_all.__dict__["_LAST_MSC_PLAN"] = msc_plan

                    # ROUTE THROUGH LIVE ROUTER (correct — replaces queue_order)
                    from engines.live.live_router import place_parent_and_hedge

                    try:
                        place_parent_and_hedge(
                            market_id    = msc_plan.get("marketId")    or mid,
                            selection_id = msc_plan.get("selectionId") or sid,
                            side         = "LAY" if str(msc_plan.get("direction","")).upper().startswith("LAY") else "BACK",
                            entry_odds   = float(msc_plan.get("px")   or
                                                 msc_plan.get("odds") or
                                                 ctx.get("current_price") or 0.0),
                            stake        = float(msc_plan.get("size") or 0.0),
                            hedge_ticks  = int(msc_plan.get("target_ticks") or 1),
                            run_id       = run_id,
                            source       = str(msc_plan.get("letter") or "A")
                        )

                        print(
                            f"[MSC][EXEC] {mid}:{sid} "
                            f"dir={msc_plan.get('direction')} "
                            f"px={msc_plan.get('px')} "
                            f"size={msc_plan.get('size')} "
                            f"why={msc_plan.get('why','')}"
                        )

                    except Exception as e:
                        print(f"[MSC][EXEC] router fail mid={mid} sid={sid}: {e}")
            except Exception as e:
                print(f"[MSC] warn mid={mid} sid={sid}: {e}")


# === PATCH END ==============================================================


            # --- LEGACY DECIDEONCE: Correct candidate-driven execution -------
            try:
                # Load real candidate engines (no invented names)
                from engines.decision_engine.decide_once.candidates import (
                    active_candidates_for_market,
                    cands_for_market,
                )

                # Pull price map for candidates in this market
                try:
                    cand_px = dict(cands_for_market(mids, max_runners=20))
                except Exception:
                    cand_px = {}

                # If this runner is NOT in candidate set → skip
                px_cand = cand_px.get(str(sid))
                if px_cand is None:
                    continue

                # If this runner is a candidate, run ALL family policies
                for fam_name, fn in ORDER:
                    try:
                        raw_plan = fn(ctx)
                        fam_plan = _safe_plan(fam_name, raw_plan, ctx)
                    except Exception as e:
                        print(f"[DECIDE] fam {fam_name} failed mid={mids} sid={sid}: {e}")
                        continue

                    if not fam_plan or not fam_plan.get("enter"):
                        continue

                    # Family → letter code
                    letter = _FAM_LETTER.get(fam_name, fam_name[:1].upper())

                    # Attach bias + veto
                    plan = _attach_bias(dict(fam_plan), ctx)
                    veto, reason = _bias_veto(plan)
                    if veto:
                        continue

                    plan.setdefault("marketId", mids)
                    plan.setdefault("selectionId", sid)
                    plan.setdefault("letter", letter)
                    harden_plan(plan)

                    # Final placement through legacy placement engine
                    try:
                        from engines.decision_engine.decide_once.placement import place_from_plan
                        place_from_plan(fam_name, plan, ctx)

                        if logger:
                            logger(f"[PLACE] {fam_name} mid={mids} sid={sid} plan={plan}")

                    except Exception as e:
                        print(f"[LANE-ERR] place {fam_name} mid={mids} sid={sid} err={e}")

            except Exception as e:
                print(f"[DECIDE] legacy warn mid={mids} sid={sid} {e}")


        # ----------------------------------------------------------------------
        # 5) MICROSCALPER PROCESSING  (FINAL – LIVE ROUTER INTEGRATION)
        # ----------------------------------------------------------------------
        try:
            from engines.micro_scalper_v7.micro_scalper_engine import MicroScalperEngine
            msc = run_all.__dict__.setdefault("_MSC_SINGLETON", MicroScalperEngine())
        except Exception as e:
            print(f"[MSC] init warn: {e}")
            continue

        for sid, px in pairs:
            try:
                # --- Build MSC context using available DecideOnce base_ctx ---
                ctx_legacy = {
                    "marketId": mids,
                    "selectionId": sid,
                    "current_price": px,
                    "oc_phase": (live_scope.get("minutes_to_off", {}) or {}).get(mids, 10),
                    "legacy_parent_id": base_ctx.get("legacy_parent_id"),
                    "legacy_entry_side": base_ctx.get("legacy_entry_side"),
                    "dynamic_stake_fn": base_ctx.get("dynamic_stake_fn"),
                    "stoploss_triggered_for_parent": None,
                    **{k: v for k, v in base_ctx.items() if k.startswith("v7_")}
                }

                # --- MSC tick ---------------------------------------------------
                msc_ctx = build_msc_context(ctx_legacy)
                msc_plan = msc.tick(msc_ctx)

                if not msc_plan:
                    continue

                # ===============================================================
                # LIVE ROUTER EXECUTION (REPLACES queue_live_order)
                # ===============================================================
                from engines.live.live_router import place_parent_and_hedge

                try:
                    place_parent_and_hedge(
                        market_id   = msc_plan.get("marketId")    or mids,
                        selection_id= msc_plan.get("selectionId") or sid,
                        side        = "LAY" if str(msc_plan.get("direction","")).upper().startswith("LAY") else "BACK",
                        entry_odds  = float(msc_plan.get("px") or msc_plan.get("odds") or px or 0.0),
                        stake       = float(msc_plan.get("size") or 0.0),
                        hedge_ticks = int(msc_plan.get("target_ticks") or 1),
                        run_id      = run_id,
                        source      = msc_plan.get("letter") or "A"
                    )

                    print(f"[MSC] {mids}:{sid} EXEC → dir={msc_plan.get('direction')} "
                          f"px={msc_plan.get('px')} size={msc_plan.get('size')} why={msc_plan.get('why','')}")
                except Exception as e:
                    print(f"[MSC][EXEC] router fail mid={mids} sid={sid}: {e}")

            except Exception as e:
                print(f"[MSC] warn mid={mids} sid={sid}: {e}")

    return 1


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
                from engines.config_paths import auto_conn
                con = auto_conn(rw=False); con.row_factory = sqlite3.Row
                r = _q(con, """
                    SELECT 1 FROM orders
                    WHERE marketId=? AND selectionId=?
                      AND date(opened_at)=date('now','utc')
                      AND UPPER(COALESCE(entry_status,'')) IN ('PLACED','MATCHED','LIVE')
                LIMIT 1

                """, (mid, sid)).fetchone()
                con.close()
                if r:
                    continue
            except Exception:
                pass

            letters = _allowed_letters_for_tick(ctx)
            proposed_any = False

        # ----------------------------------------------------------------------
        # 5) EPIC UNIFIED TICK REPORT (MSC + LEGACY + BAND + TIMING + CONTEXT)
        # ----------------------------------------------------------------------
        try:
            from engines.market_monitor.monitor import classify as _mm_classify
        except Exception:
            def _mm_classify(mid, sid): return {"band": "?"}

        from engines.mastery.mastery_policy import _SCOPE_STATE

        print(f"\n=================== [TICK][{mids}] ===================")

        # Market name lookup (same logic you already use)
        try:
            from engines.config_paths import auto_conn, q_retry as _q
            con = auto_conn(rw=False); con.row_factory = __import__("sqlite3").Row
            row = _q(con,
                "SELECT COALESCE(market_name,event_name) AS mname "
                "FROM bets WHERE marketId=? LIMIT 1",
                (mids,)
            ).fetchone()
            con.close()
            mname = row["mname"] if row and row["mname"] else mids
        except Exception:
            mname = mids

        print(f"[MARKET] {mname}  | runners={len(pairs)}")

        # Timing (Mastery scope timing)
        timing = (_SCOPE_STATE.get("timing", {}) or {}).get(mids, {})
        mto = timing.get("minutes_to_off")
        phase = timing.get("phase", "PRE")
        print(f"[TIME]   minutes_to_off={mto}  phase={phase}")

        # Build pretty runner table
        for sid, px in pairs:
            # Horse name lookup
            try:
                from engines.config_paths import auto_conn, q_retry as _q
                con = auto_conn(rw=False); con.row_factory = __import__("sqlite3").Row
                row = _q(con,
                    "SELECT horse_name FROM bets WHERE marketId=? AND selectionId=? LIMIT 1",
                    (mids, sid)
                ).fetchone()
                con.close()
                rname = row["horse_name"] if row and row["horse_name"] else sid
            except Exception:
                rname = sid

            # Band lookup (ACTIVE / PASSIVE / IGNORED / etc.)
            try:
                band = _mm_classify(mids, sid).get("band", "?")
            except Exception:
                band = "?"

            # Price source detail
            src_note = ""
            try:
                from engines.decision_engine.decide_once.placement import (
                    _fetch_px_from_odds_current as _px_oc,
                    _fetch_px_from_inbound      as _px_ib,
                )
                oc_px = _px_oc(mids, sid)
                ib_px = _px_ib(mids, sid)
                if oc_px is not None:
                    src_note = "oc"
                elif ib_px is not None:
                    src_note = "in"
                else:
                    src_note = "api?"
            except Exception:
                src_note = "?"

            # Render row
            print(f"[RUNNER] sid={sid:<6}  px={px:<6}  src={src_note:<3}  band={band:<8}  name={rname}")

        print("-------------------------------------------------------")

        # MSC summary (if MSC tick generated a plan earlier)
        try:
            # This relies on the earlier MSC block storing its last plan:
            msc_plan = run_all.__dict__.get("_LAST_MSC_PLAN", None)
            if msc_plan:
                print(f"[MSC] {msc_plan.get('marketId')}:{msc_plan.get('selectionId')} "
                      f"{msc_plan.get('why','')} → {msc_plan.get('direction')} "
                      f"size={msc_plan.get('size')}")
            else:
                print("[MSC] no MSC plan this tick")
        except Exception:
            print("[MSC] n/a")

        # Legacy decide_once summary — direct flag from placed_any
        if placed_any:
            print("[LEGACY] placement fired this tick ✔")
        else:
            print("[LEGACY] no placement this tick")

        # Visual check: number of active + passive from live_scope
        try:
            act = live_scope.get("active_sids", {}).get(mids, [])
            pas = live_scope.get("passive_sids", {}).get(mids, [])
            print(f"[SCOPE] active={len(act)}  passive={len(pas)}")
        except Exception:
            print("[SCOPE] n/a")

        print("=======================================================\n")

        # No return here — let the loop finish normally
        continue

# === PATCH END (final runtime fix) ===

if __name__ == "__main__":
    from engines.decision_engine.decide_once.lanes import dryrun
    dryrun()

