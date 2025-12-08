from __future__ import annotations

from typing import Dict, List, Tuple, Optional, Callable, Any
import os, json, sqlite3

# ── Local engine utilities ───────────────────────────────────────────────────
from .scope import advance_scope_cursor
from .placement import place_from_plan
from engines.live.live_router import place_parent_and_hedge

try:
    # decisions-table writer used by placement; keep schema consistent
    from .placement import _write_decision as _lane_write_decision
except Exception:
    _lane_write_decision = None

# === PATCH START: lanes.py compatibility ============
def _harden_ctx(ctx):
    """Legacy no-op context normaliser removed in v7.6 — now just returns the ctx."""
    return ctx
# === PATCH END ======================================


from .helpers import (
    open_auto_db as _adb,
    q_retry as _q,
    status_once,
)
# === PATCH START ============================================================
# 📍 TARGET: lanes.py (top of file, near other helper imports)
# Insert NEW import:



# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📍 TARGET: lanes.py (file-level imports)
try:
    from engines.utils.api_tools import fetch_live_odds
except:
    fetch_live_odds = None
# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📍 TARGET: lanes.py (top imports)
from engines.decision_engine.microscalper.context_adapter import build_msc_context
from engines.decision_engine.microscalper.plan_builder import build_plan_from_msc
# === PATCH END ==============================================================
# === PATCH START ===
from engines.mastery.mastery_policy import plan_for_strategy
# === PATCH END ===

# ── Plan ledger (guarded) ─────────────────────────────────────────────────────
try:
    from engines.mastery.plan_ledger import record_plan, concurrency_ok
except Exception:
    record_plan = None
    def concurrency_ok(*_a, **_k): return True



# Mastery policy (new unified API)
from engines.mastery import mastery_policy as mp
# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: from engines.mastery.context_builder_next import build_context_from_scope as build_context
# 🛠 ACTION: Replace import with canonical CTXv7 builder
# ============================================================================

# REMOVE this line:
# from engines.mastery.context_builder_next import build_context_from_scope as build_context

# ADD this line:
from engines.mastery.context_builder import build_context

# === PATCH END ==============================================================
# =====================================================================
# 📆 PATCHED: 2026-01-19
# 📍 TARGET: lanes.py (top-level queue & subscriber)
# 🛠 PURPOSE: Install the global LanesPlanQueue for v7.9.9.1 architecture
# =====================================================================

from collections import deque

# Global, high-speed, lossless plan queue
_LANES_PLAN_QUEUE = deque()

def _lanes_plan_ingest(plan: dict):
    """
    EventSink → Lanes adapter.
    Every engine pushes plans here.
    Lanes no longer calls engines; it only consumes this queue.
    """
    try:
        if isinstance(plan, dict) and plan.get("enter"):
            _LANES_PLAN_QUEUE.append(plan)
    except Exception:
        pass

# === PATCH START ============================================================
# 📆 PATCHED: 2026-01-25
# 📍 TARGET: lanes.py (after _LANES_PLAN_QUEUE definition)
# 🛠 PURPOSE: Subscribe Lanes to EventSync (MSC / STOPLOSS / Router lifecycle)

from engines.mastery.event_sink import subscribe as _es_subscribe

def _lanes_event_adapter(ev: dict):
    """
    Convert EventSync live events into Lanes-queue plans.

    Events handled:
        • MSC plans (type: "msc_plan")
        • STOPLOSS events (type: "stop_loss_triggered")
        • Router placement lifecycle (ignored except for STOPLOSS)
    """
    try:
        et = ev.get("type")

        # MSC plan → push into queue
        if et == "msc_plan":
            plan = dict(ev.get("plan") or {})
            if plan.get("enter"):
                _LANES_PLAN_QUEUE.append(plan)
            return

        # STOPLOSS → convert to plan
        if et == "stop_loss_triggered":
            plan = {
                "enter": True,
                "engine": "OVERWATCHER",
                "strategy": "W",
                "letter": "W",
                "marketId": ev.get("marketId"),
                "selectionId": ev.get("selectionId"),
                "direction": "BACK" if ev.get("entry_side") == "LAY" else "LAY",
                "px": ev.get("current_odds"),
                "size": ev.get("entry_stake"),
                "stop_loss_px": ev.get("stop_loss_px"),
                "why": "STOPLOSS_EVENTSYNC",
            }
            _LANES_PLAN_QUEUE.append(plan)
            return

        # Router-level events are not routed into Lanes
        # (placed/matched/settled events are future training inputs)
        return

    except Exception as e:
        print(f"[LANES][EVENTSYNC][WARN] {e}")

# register EventSync subscriber
_es_subscribe(_lanes_event_adapter)
# === PATCH END ==============================================================


# Subscribe to all engine decisions
from engines.mastery import event_sink



# === COMPAT PATCH: restore read_scope_window for LiveRouter ===
# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: def read_scope_window(
# 📆 PATCHED: 2025-12-05 — force lanes to use the unified scope reader
# ============================================================================

from .scope import read_scope_window as _unified_scope_reader

def read_scope_window(*args, **kwargs):
    """
    Lanes MUST use the exact same scope window as DecideOnce.
    This wrapper preserves backwards compatibility while ensuring
    mid/sid lists are identical across all consumers.
    """
    return _unified_scope_reader(*args, **kwargs)

# === PATCH END ==============================================================

# =====================================================================
# STOPLOSS → LANES plan mapping
# =====================================================================



# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🆕 ADD FUNCTION: _route_final_plan
# 📆 PATCHED: 2026-01-19
# ============================================================================

def _route_final_plan(plan: dict | None, ctx: dict) -> None:
    """
    Unified v7 plan executor.
    Routes exactly ONE plan to LiveRouter.
    No inference, no overrides, no suppression.
    """
    if not plan or not plan.get("enter"):
        return  # nothing to place

    try:
        from engines.decision_engine.decide_once.placement import place_from_plan
        engine = plan.get("engine")
        if not engine:
            print("[LANES][WARN] final plan missing engine → skip")
            return

        place_from_plan(engine, plan, ctx)
        print(f"[LANES][ROUTE] engine={engine} mid={plan.get('marketId')} "
              f"sid={plan.get('selectionId')} → {plan}")

    except Exception as e:
        print(f"[LANES][ROUTE][ERR] {e}")

# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: ^def _safe_plan
# 🔧 ACTION: Full function replacement (v7 engine/strategy/letter model)
# 📆 PATCHED: 2026-01-19

def _safe_plan(fam: str, raw: Any, ctx: dict) -> dict:
    """
    v7 Normalised safe-plan:
      • DOES NOT infer engine
      • DOES NOT infer letter beyond cosmetic fallback
      • DOES NOT overwrite MSC or Legacy engine identity
      • PRESERVES strategy (Legacy-only)
      • PRESERVES stop_loss_px (MSC STOPLOSS prep)
      • Guarantees core identity fields (marketId, selectionId)
      • Ensures 'enter' exists but never forces behaviour
    """

    # ------------------------------------------------------------------
    # 1) Guarantee raw is a dict
    # ------------------------------------------------------------------
    if raw is None or not isinstance(raw, dict):
        raw = {}

    # ------------------------------------------------------------------
    # 2) Preserve engine identity exactly as engines emit it
    #    (MSC_EXPLORATORY / MSC_RISK / MSC_INPLAY / LEGACY / OVERWATCHER)
    # ------------------------------------------------------------------
    # NEVER assign or infer engine here.
    if "engine" in raw:
        raw["engine"] = str(raw["engine"]).upper()

    # ------------------------------------------------------------------
    # 3) Strategy: Legacy-only
    # ------------------------------------------------------------------
    # If plan contains strategy already, preserve it.
    # If not, and engine=LEGACY, strategy letter comes from family.
    if "strategy" not in raw:
        if raw.get("engine") == "LEGACY":
            raw["strategy"] = fam[:1].upper()  # legacy family letter
        else:
            raw["strategy"] = None

    # ------------------------------------------------------------------
    # 4) Letter: optional cosmetic tag
    # ------------------------------------------------------------------
    # If engine provided its own 'letter', keep it.
    # If legacy and strategy exists → letter=strategy
    # Otherwise keep None (Router must NOT infer engine from letter).
    if "letter" not in raw or raw.get("letter") is None:
        if raw.get("engine") == "LEGACY" and raw.get("strategy"):
            raw["letter"] = raw["strategy"]
        else:
            raw["letter"] = None  # purely cosmetic, never routing

    # ------------------------------------------------------------------
    # 5) Minimal required v7 fields
    # ------------------------------------------------------------------
    raw.setdefault("enter", False)
    raw.setdefault("why", f"{fam}:ok")

    # ------------------------------------------------------------------
    # 6) stop_loss_px must ALWAYS survive (MSC STOPLOSS pipeline)
    # ------------------------------------------------------------------
    if "stop_loss_px" in raw:
        raw["stop_loss_px"] = raw.get("stop_loss_px")

    # ------------------------------------------------------------------
    # 7) Guarantee identity fields for Router
    # ------------------------------------------------------------------
    raw.setdefault("marketId", ctx.get("marketId"))
    raw.setdefault("selectionId", ctx.get("selectionId"))

    return raw

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

# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: def ensure_direction_in_plan(
# 🛠 ACTION: Neutralise legacy ensure_direction_in_plan usage
# ============================================================================

# REPLACE ensure_direction_in_plan with a no-op that does nothing:

try:
    from engines.odds.trend_analyzer.direction_service import ensure_direction_in_plan
except Exception:
    def ensure_direction_in_plan(plan: dict, ctx: dict) -> None:
        return  # MSC determines direction; legacy inference disabled

# === PATCH END ================================================================



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

# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: ^def _ensure_plan
# 🛠 ACTION: Full v7-safe rewrite (preserve engine, strategy, letter; no inference)
# 📆 PATCHED: 2026-01-19

def _ensure_plan(fam: str, ctx: Dict[str, Any], raw: Any) -> Dict[str, Any]:
    """
    v7 Plan Normaliser
    ------------------
    Responsibilities:
      • Normalize plan dicts without altering engine identity
      • Preserve MSC/Legacy strategy model
      • Letter = cosmetic only (never influences engine)
      • Legacy strategy letters preserved; MSC inherits only when appropriate
      • No direction/px/size inference unless explicit
      • Guaranteed minimal shape for Router safety
      • Must NOT overwrite or guess engine type
      • Must NOT overwrite MSC plan metadata
    """

    # ------------------------------------------------------------------
    # 1) Ensure raw is a dict
    # ------------------------------------------------------------------
    if not isinstance(raw, dict):
        # Minimal placeholder with NO engine inference
        return {
            "enter": False,
            "why": f"policy_none:{fam}",
            "engine": None,
            "strategy": None,
            "letter": None,
            "marketId": ctx.get("marketId"),
            "selectionId": ctx.get("selectionId"),
        }

    plan = raw.copy()

    # ------------------------------------------------------------------
    # 2) ENGINE is authoritative (never infer, never overwrite)
    # ------------------------------------------------------------------
    if "engine" in plan and plan["engine"]:
        plan["engine"] = str(plan["engine"]).upper()
    else:
        # Engine MUST be explicitly set by the engine that generated the plan.
        # Lanes NEVER infers it.
        plan["engine"] = plan.get("engine") or None

    # ------------------------------------------------------------------
    # 3) STRATEGY handling (Legacy=true, MSC only when defined)
    # ------------------------------------------------------------------
    if "strategy" not in plan:
        if plan.get("engine") == "LEGACY":
            # Legacy strategy letter comes from family name
            plan["strategy"] = fam[:1].upper()
        else:
            # MSC Risk, MSC InPlay, Overwatcher define their own strategy letters upstream
            plan["strategy"] = None

    # ------------------------------------------------------------------
    # 4) LETTER = cosmetic only
    # ------------------------------------------------------------------
    if "letter" not in plan or plan["letter"] in (None, ""):
        # Legacy letters mirror strategy
        if plan.get("engine") == "LEGACY" and plan.get("strategy"):
            plan["letter"] = plan["strategy"]
        else:
            # MSC EX inherits legacy upstream; MSC RISK = J; MSC INPLAY = V; Overwatcher = W.
            # Lanes does NOT infer ANYTHING. It simply preserves what engines sent.
            plan["letter"] = None

    # ------------------------------------------------------------------
    # 5) Minimal placement safety
    # ------------------------------------------------------------------
    plan.setdefault("enter", False)

    # ------------------------------------------------------------------
    # 6) Direction safety — DO NOT infer or override
    # ------------------------------------------------------------------
    # MSC & Legacy engines define direction explicitly.
    # Lanes must respect whatever is present.
    plan.setdefault("direction", plan.get("direction"))

    # ------------------------------------------------------------------
    # 7) Ticks / Size safety — only normalise type, never infer values
    # ------------------------------------------------------------------
    def _to_float(v, default=0.0):
        try:
            return float(v)
        except Exception:
            return default

    def _to_int(v, default=0):
        try:
            return int(v)
        except Exception:
            return default

    # Never infer target_ticks or size — only convert types.
    if "target_ticks" in plan:
        plan["target_ticks"] = _to_int(plan.get("target_ticks"), None)
    if "size" in plan:
        plan["size"] = _to_float(plan.get("size"), None)

    # px/odds safety
    if "px" in plan:
        plan["px"] = _to_float(plan.get("px"), ctx.get("odds") or ctx.get("px") or 0.0)

    # ------------------------------------------------------------------
    # 8) STOPLOSS fields must remain intact
    # ------------------------------------------------------------------
    if "stop_loss_px" in plan:
        plan["stop_loss_px"] = _to_float(plan.get("stop_loss_px"), None)

    # ------------------------------------------------------------------
    # 9) Identity fields for Router
    # ------------------------------------------------------------------
    plan.setdefault("marketId", ctx.get("marketId"))
    plan.setdefault("selectionId", ctx.get("selectionId"))

    # ------------------------------------------------------------------
    # 10) Reason field always present
    # ------------------------------------------------------------------
    plan.setdefault("why", plan.get("why") or f"{fam}:ok")

    return plan





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
    """
    MSC-safe bias veto:
      • guards against None / non-string direction
      • no crashes when MSC hasn't assigned direction yet
      • returns (False, "") when veto does NOT apply
    """
    try:
        # extract bias magnitude + confidence
        b = float(plan.get("bias", 0.0))
        c = float(plan.get("bias_conf", 0.0))

        # direction may be None, missing, or non-string → no veto
        d_raw = plan.get("direction")
        if not isinstance(d_raw, str) or not d_raw:
            return (False, "")

        d = d_raw.upper()

        # threshold check
        if c < _BIAS_VETO_CONF or abs(b) < _BIAS_VETO_MAG:
            return (False, "")

        # contradiction cases:
        if d in ("LAY->BACK", "L2B") and b < 0:
            return (True, f"bias_veto: plan=L2B vs bias=B2L({b:+.2f}) conf={c:.2f}")

        if d in ("BACK->LAY", "B2L") and b > 0:
            return (True, f"bias_veto: plan=B2L vs bias=L2B({b:+.2f}) conf={c:.2f}")

        # bias agrees → no veto
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
# === PATCH START ============================================================
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: def _fallback_plan_for_A(
# 🛠 ACTION: Remove legacy direction inference in fallback A-plan
# ============================================================================

    # REPLACE direction computation:
    # dirn = "BACK->LAY" if (...) else "LAY->BACK"

    dirn = None  # direction now determined only by MSC

# === PATCH START ============================================================
# 📍 TARGET: _fallback_plan_for_A
# ============================================================================

    return {
        "enter": True,
        "direction": None,  # MSC will determine
        "target_ticks": None,
        "size": None,
        "px": None,
        "letter": "A",
        "why": "fallback_A (awaiting MSC override)",
        "marketId": ctx.get("marketId"),
        "selectionId": ctx.get("selectionId"),
    }

# === PATCH END ================================================================


# === PATCH END ================================================================


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

# 📍 TARGET: lanes.py (top-level definitions)
# 🔎 SEARCH: "ORDER: List[Tuple[str"
# 🛠 ACTION: Insert after ORDER[]
# =================================================================

# === PATCH START — define legacy families ==========================
legacy_families = [
    "BLUEPRINTS",
    "OG_STRATEGY",
    "LADDER_STRATEGY",
    "S4_CROSSOVER",
    "S5_BREAKOUT",
    "S6_STEAM_FADE",
    "BTL_SCOUT",
    "BTL_AGGR",
    "IP1_SHOCK_DRIFT",
    "IP2_TIRED_LEADER",
    "IP3_CLOSE_FINISH",
    "IP4_FENCE_ERROR",
    "IP5_COLLAPSE_FADE",
]
# === PATCH END =======================================================


# =====================================================================================================
# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: "def run_all("
# 🎯 ACTION: Replace the entire run_all() implementation with the final PURE-EVENTSYNC version
# 📆 PATCHED: 2026-02-05
# =====================================================================================================
# 🔥 THIS IS THE LAST LANES PATCH — leaves Lanes as:
#     • EventSync → Lanes → Router (MSC / STOPLOSS / Router feedback)
#     • Legacy plan executor AFTER queue empties
#     • No MSC calls or arbitration logic live inside Lanes
#     • No DecideOnce old loops, no ORDER loop, no duplicated blocks
# =====================================================================================================

# 📍 DELETE EVERYTHING from:
#     def run_all(run_id: str, source: str = "LIVE", logger=None):
# up to just before:
#     if __name__ == "__main__":
#
# (delete entire old run_all(), all 4B, 4C, 4D blocks, arbitration remnants,
#  duplicate legacy collectors, MSC references, and old placeholders)

# 📍 INSERT THIS NEW run_all() EXACTLY IN ITS PLACE
# -----------------------------------------------------------------------------------------------------

def run_all(run_id: str, source: str = "LIVE", logger=None) -> Optional[int]:
    """
    FINAL v7.9.9.1 DecideOnce Engine
    --------------------------------
    Pure EventSync → Lanes → Router integration.
    Lanes no longer calls MSC or Overwatcher directly.
    Lanes consumes plans *only* from EventSync, then executes ONE Legacy fallback
    when no event-driven plans exist for the current runner.

    PROCESS PER RUNNER:
        1. Dequeue all EventSync plans → normalise → route to Router
        2. When queue empty → try Legacy families (MTO-gated)
        3. Route Legacy if present
        4. Move to next SID
    """

    # ------------------------------------------------------------------
    # 1) REFRESH SCOPE
    # ------------------------------------------------------------------
    try:
        from engines.decision_engine.decide_once.scope import (
            build_and_maintain_scope,
            ordered_markets_for_tick,
        )
        live_scope = build_and_maintain_scope(show_dashboard=False) or {}
        markets = ordered_markets_for_tick(live_scope) or []
        print(f"[LANES] scope refreshed ({len(markets)} markets)")
    except Exception as e:
        print(f"[LANES] scope refresh error: {e}")
        return None

    if not markets:
        return None

    # ------------------------------------------------------------------
    # 2) BUILD BASE CONTEXT (CTXv7)
    # ------------------------------------------------------------------
    try:
        base_ctx, _ = build_context(source=source)
    except Exception as e:
        print(f"[LANES] base_ctx build error: {e}")
        return None

    # ------------------------------------------------------------------
    # 3) MAIN LOOP — PER MARKET
    # ------------------------------------------------------------------
    for mid in markets:
        mids = str(mid)

        active  = live_scope.get("active_sids",  {}).get(mids, [])
        passive = live_scope.get("passive_sids", {}).get(mids, [])
        sids = list(active) + list(passive)

        if not sids:
            continue

        # ------------------------------------------------------------------
        # 4) PER-RUNNER LOOP
        # ------------------------------------------------------------------
        for sid in sids:
            sid_current = str(sid)

            # Build runner ctx
            ctx = dict(base_ctx)
            ctx["marketId"]    = mids
            ctx["selectionId"] = sid_current

            harden_ctx(ctx)

            # ------------------------------------------------------------------
            # 4A) EVENTSYNC → QUEUE FIRST (MSC, STOPLOSS, Router events)
            # ------------------------------------------------------------------
            while _LANES_PLAN_QUEUE:
                raw = _LANES_PLAN_QUEUE.popleft()

                plan = _safe_plan(
                    raw.get("strategy") or raw.get("engine") or "UNK",
                    raw,
                    ctx
                )
                harden_plan(plan)

                engine = plan.get("engine")
                if not engine:
                    print(f"[LANES][DROP] missing engine → {plan}")
                    continue

                try:
                    _route_final_plan(plan, ctx)
                except Exception as e:
                    print(f"[LANES][ERR] event-queue route failed: {e}")

            # ------------------------------------------------------------------
            # 4B) LEGACY FALLBACK (only if no EventSync plans were present)
            # ------------------------------------------------------------------
            legacy_plan = None
            try:
                for fam in legacy_families:

                    # MTO gating for legacy
                    allowed = _allowed_letters_for_tick(ctx)
                    fam_letter = _FAM_LETTER.get(fam, fam[:1].upper())
                    if fam_letter not in allowed:
                        continue

                    p = plan_for_strategy(fam, ctx)
                    if p and p.get("enter"):
                        strat = p.get("letter") or fam[:1].upper()
                        p["engine"]   = "LEGACY"
                        p["strategy"] = strat
                        p["letter"]   = strat
                        p.setdefault("marketId", mids)
                        p.setdefault("selectionId", sid_current)
                        harden_plan(p)
                        legacy_plan = p
                        break

            except Exception as e:
                print(f"[LEGACY][ERR] mid={mids} sid={sid_current}: {e}")

            # route final legacy plan if any
            if legacy_plan:
                try:
                    _route_final_plan(legacy_plan, ctx)
                except Exception as e:
                    print(f"[LANES][ERR] legacy-route failed: {e}")

    return 1

# -----------------------------------------------------------------------------------------------------
# END OF PATCH — Lanes is now final-form v7.9.9.1
# =====================================================================================================


if __name__ == "__main__":
    from engines.decision_engine.decide_once.lanes import dryrun
    dryrun()

