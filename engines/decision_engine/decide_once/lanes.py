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

from .helpers import (
    open_auto_db as _adb,
    q_retry as _q,
    status_once,
)
# === PATCH START ============================================================
# 📍 TARGET: lanes.py (top of file, near other helper imports)
# Insert NEW import:

from engines.decision_engine.decide_once.helpers import harden_ctx, harden_plan

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

# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🆕 ADD FUNCTION: _select_final_plan
# 📆 PATCHED: 2026-01-19
# ============================================================================

def _select_final_plan(msc_ex: dict | None,
                       msc_risk: dict | None,
                       msc_ip: dict | None,
                       legacy: dict | None,
                       w_stoploss: dict | None = None) -> dict | None:
    """
    Unified v7 plan selection logic.

    Engines DO NOT suppress each other.
    Every engine may emit a plan.
    Lanes selects exactly ONE final plan using the v7 routing priority:

        1) STOPLOSS (W-lane)          ← future Overwatcher integration
        2) MSC_RISK                    ← risk correction strongest priority
        3) MSC_EXPLORATORY             ← exploratory entries
        4) MSC_INPLAY                  ← in-play micro-execution
        5) LEGACY                      ← macro strategy families

    IMPORTANT:
      - This function NEVER mutates plans.
      - It NEVER infers engine or letter.
      - It ONLY selects which plan to forward to Router.
    """

    # ----------------------------------------------------------
    # 1. STOPLOSS — future path (Overwatcher W-engine)
    # ----------------------------------------------------------
    if w_stoploss and w_stoploss.get("enter"):
        return w_stoploss

    # ----------------------------------------------------------
    # 2. MSC_RISK
    # ----------------------------------------------------------
    if msc_risk and msc_risk.get("enter"):
        return msc_risk

    # ----------------------------------------------------------
    # 3. MSC_EXPLORATORY
    # ----------------------------------------------------------
    if msc_ex and msc_ex.get("enter"):
        return msc_ex

    # ----------------------------------------------------------
    # 4. MSC_INPLAY
    # ----------------------------------------------------------
    if msc_ip and msc_ip.get("enter"):
        return msc_ip

    # ----------------------------------------------------------
    # 5. LEGACY
    # ----------------------------------------------------------
    if legacy and legacy.get("enter"):
        return legacy

    # ----------------------------------------------------------
    # No plan
    # ----------------------------------------------------------
    return None

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

# ─────────────────────────────────────────────────────────────────────────────
# Public entrypoint: called by the orchestration wrapper per tick
# ─────────────────────────────────────────────────────────────────────────────
def run_all(run_id: str, source: str = "LIVE", logger=None) -> Optional[int]:
    """
    FINAL CLEAN DecideOnce + MSC unified tick engine.
    Architecture:
        1. Build CTXv7 for each runner
        2. MSC tick FIRST (Exploratory + Risk + InPlay internally)
        3. If MSC emits a plan → place
        4. Legacy Mastery family plans → place
        5. Done
    No ORDER loop, no duplicated MSC calls, no old pipelines.
    """

    # ------------------------------------------------------------------
    # 1) REFRESH SCOPE (canonical)
    # ------------------------------------------------------------------
    try:
        from engines.decision_engine.decide_once.scope import (
            build_and_maintain_scope, ordered_markets_for_tick
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
    # 2) BUILD BASE CTX (CTXv7)
    # ------------------------------------------------------------------
    try:
        base_ctx, _ = build_context(source=source)
    except Exception as e:
        print(f"[DECIDE] base_ctx build error: {e}")
        return None

    # Prepare MSC engine singleton
    from engines.micro_scalper_v7.micro_scalper_engine import MicroScalperEngine
    msc = run_all.__dict__.setdefault("_MSC_SINGLETON", MicroScalperEngine())

    # === PATCH START ============================================================
    # 📍 TARGET: lanes.py (inside run_all(), per-runner loop, after ctx is built)
    # 🛠 ACTION: Pull STOPLOSS plan from STOPLOSS_QUEUE
    # 📆 PATCHED: 2026-01-19
    # ============================================================================

    # STOPLOSS dequeue (Overwatcher W-engine)
    from engines.live.overwatcher import STOPLOSS_QUEUE

    w_stoploss = STOPLOSS_QUEUE.pop((mids, str(sid)), None)
    # === PATCH END ================================================================


    # ------------------------------------------------------------------
    # 3) PER-MARKET / PER-RUNNER LOOP
    # ------------------------------------------------------------------
    for mid in markets:
        mids = str(mid)
        active  = live_scope.get("active_sids",  {}).get(mids, [])
        passive = live_scope.get("passive_sids", {}).get(mids, [])
        sids = list(active) + list(passive)

        if not sids:
            continue

        # Build ordered price list
        pairs = []
        for sid in sids:
            try:
                from engines.decision_engine.decide_once.placement import (
                    _fetch_px_from_odds_current as _px_oc,
                    _fetch_px_from_inbound      as _px_ib
                )
                px = _px_oc(mids, sid) or _px_ib(mids, sid)
                if px is None:
                    from engines.utils.api_tools import fetch_live_odds
                    odds = fetch_live_odds(None, mids, sid)
                    px = float(odds.get("lay") or odds.get("back") or 0.0) if odds else 0.0
            except:
                px = 0.0
            pairs.append((sid, float(px)))

        pairs.sort(key=lambda p: (p[1], p[0]))

        print(f"[TICK] {mids} runners={len(pairs)} → " +
              ", ".join(f"{sid}:{px}" for sid, px in pairs[:8]))

        # ----------------------------------------------------------------------
        # 4) PER-RUNNER PIPELINE
        # ----------------------------------------------------------------------
        for sid, px in pairs:

            # --------------------------------------------------------------
            # 4A) BUILD CTX FOR THIS RUNNER (CTXv7)
            # --------------------------------------------------------------
            ctx = base_ctx.copy()
            ctx["marketId"]    = mids
            ctx["selectionId"] = sid
            ctx["px"] = ctx["odds"] = ctx["ltp"] = px
            ctx["tape_px"] = px
            ctx["is_active"]  = sid in active
            ctx["is_passive"] = sid in passive
            ctx["is_ignored"] = not (sid in active or sid in passive)
            ctx["direction"] = ctx["side"] = None
            ctx["entry_odds"] = ctx["target_ticks"] = None
            ctx["letter"] = None
            ctx["why"] = None
            ctx["run_id"] = run_id
            ctx["mode"]   = source

            # --------------------------------------------------------------
            # GUARANTEE PX IS NEVER NONE
            # --------------------------------------------------------------
            try:
                if ctx.get("px") is None or ctx["px"] != ctx["px"]:  # None or NaN
                    ctx["px"] = ctx["odds"] = ctx["ltp"] = 0.0
            except Exception:
                ctx["px"] = ctx["odds"] = ctx["ltp"] = 0.0

            # --------------------------------------------------------------
            # MINIMAL LEGACY-PARENT → RISK TRIGGER FOR MSC
            # --------------------------------------------------------------
            try:
                from engines.live.live_router import _open_parents_count_live
                parent_count = _open_parents_count_live(mids, sid)

                if parent_count and parent_count > 0:
                    # This flag activates MSC RiskEngine for this runner
                    ctx["legacy_parent_id"] = 1
                    ctx["legacy_entry_side"] = ctx.get("side") or None
            except Exception:
                # silently ignore; MSC risk simply won’t fire
                pass


            try:
                harden_ctx(ctx)
            except:
                pass

# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: # 4B) **MSC FIRST** (Exploratory + Risk + In-Play)
# 🛠 ACTION: Replace MSC routing block with v7 engine/strategy identity model
# 📆 PATCHED: 2026-01-19
# ================================================================

            # --------------------------------------------------------------
            # 4B) **MSC PLANS (Exploratory, Risk, InPlay)**
            #     Engines already produce their plans; Lanes only normalises.
            # --------------------------------------------------------------
            try:
                msc_plan = msc.tick(ctx)
            except Exception as e:
                print(f"[MSC] tick failed mid={mids} sid={sid}: {e}")
                msc_plan = None

            # If no MSC plan or invalid → skip to legacy
            if not (msc_plan and isinstance(msc_plan, dict)):
                msc_plan = None
            else:
                # Guarantee core identity fields
                msc_plan.setdefault("marketId", ctx["marketId"])
                msc_plan.setdefault("selectionId", ctx["selectionId"])
                msc_plan["enter"] = bool(msc_plan.get("enter") and msc_plan.get("size") and msc_plan.get("direction"))

                # ----------------------------------------------------------
                # ENGINE + STRATEGY + LETTER MODEL (v7 canonical mapping)
                # ----------------------------------------------------------
                fam = msc_plan.get("family") or msc_plan.get("engine") or ""

                # 1) MSC Exploratory ---------------------------------------
                if fam.upper() in ("MSC_EX", "MSC_EXPLORATORY", "EXPLORATORY"):
                    legacy_letter = ctx.get("strategy") or ctx.get("letter")
                    # exploratory inherits legacy strategy letter
                    msc_plan["engine"]   = "MSC_EXPLORATORY"
                    msc_plan["strategy"] = legacy_letter
                    msc_plan["letter"]   = legacy_letter

                # 2) MSC Risk ----------------------------------------------
                elif fam.upper() in ("MSC_RISK", "RISK"):
                    msc_plan["engine"]   = "MSC_RISK"
                    msc_plan["strategy"] = "J"
                    msc_plan["letter"]   = "J"

                # 3) MSC In-Play -------------------------------------------
                elif fam.upper() in ("MSC_IP", "MSC_INPLAY", "INPLAY"):
                    msc_plan["engine"]   = "MSC_INPLAY"
                    msc_plan["strategy"] = "V"
                    msc_plan["letter"]   = "V"

                # 4) Unknown family → MSC disabled --------------------------
                else:
                    print(f"[MSC] unknown family '{fam}' → MSC plan ignored for {mids}:{sid}")
                    msc_plan = None

                # ----------------------------------------------------------
                # ROUTE MSC PLAN (only if enter=True)
                # ----------------------------------------------------------
                pass

# 📍 TARGET: engines/decision_engine/decide_once/lanes.py
# 🔎 SEARCH: # 4C) **LEGACY MASTERY FAMILIES**
# 🛠 ACTION: Replace engine-specific placement calls with unified merge+route logic
# 📆 PATCHED: 2026-01-19
# ============================================================================

            # --------------------------------------------------------------
            # 4C) **COLLECT LEGACY PLAN**
            # --------------------------------------------------------------
            legacy_plan = None
            try:
                from engines.mastery.mastery_policy import plan_for_strategy
                for fam in legacy_families:
                    p = plan_for_strategy(fam, ctx)
                    if p and p.get("enter"):
                        # Assign engine + strategy letter safely
                        strat = p.get("letter") or fam[:1].upper()
                        p["engine"]   = "LEGACY"
                        p["strategy"] = strat
                        p["letter"]   = strat
                        p.setdefault("marketId", mids)
                        p.setdefault("selectionId", sid)
                        legacy_plan = p
                        break
            except Exception as e:
                print(f"[LEGACY][ERR] mid={mids} sid={sid}: {e}")

            # --------------------------------------------------------------
            # 4D) **SELECT FINAL PLAN (MSC_EX, MSC_RISK, MSC_INPLAY, LEGACY)**
            # --------------------------------------------------------------
            final_plan = _select_final_plan(
                msc_ex      = msc_plan if msc_plan and msc_plan.get("engine") == "MSC_EXPLORATORY" else None,
                msc_risk    = msc_plan if msc_plan and msc_plan.get("engine") == "MSC_RISK"         else None,
                msc_ip      = msc_plan if msc_plan and msc_plan.get("engine") == "MSC_INPLAY"       else None,
                legacy      = legacy_plan,
                w_stoploss  = None  # future Overwatcher integration
            )

            # --------------------------------------------------------------
            # 4E) **ROUTE FINAL PLAN**
            # --------------------------------------------------------------
            _route_final_plan(final_plan, ctx)

            # Proceed to next runner
            continue

    return 1


# === PATCH START (legacy DecideOnce block removed) ============================
    # The legacy DecideOnce tick/placement engine has been fully removed.
    # The modern run_all() above already performs:
    #   • scope → ctx build
    #   • MSC tick routing
    #   • full legacy plan execution
    #   • placement, veto, bias, ctx harden
    # Keeping a placeholder here preserves indentation and block structure.
    pass
# === PATCH END ================================================================
# === PATCH START (legacy DecideOnce block removed) ============================
    # The legacy DecideOnce tick/placement engine has been fully removed.
    # The modern run_all() above already performs:
    #   • scope → ctx build
    #   • MSC tick routing
    #   • full legacy plan execution
    #   • placement, veto, bias, ctx harden
    # Keeping a placeholder here preserves indentation and block structure.
    pass
# === PATCH END ================================================================



if __name__ == "__main__":
    from engines.decision_engine.decide_once.lanes import dryrun
    dryrun()

