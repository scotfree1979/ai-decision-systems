from __future__ import annotations
from typing import Dict, Any, Optional, Tuple
import time

# Runtime: OC/bands → prefix → next-OC posterior → plan
from engines.blueprint.runtime import (
    oc_presence,
    median_series_from_bands,
    match_transition,
    plan_from_transition,
    liability_snapshot, 
    current_ranks, 
    latest_oc1_snapshot,
    adverse_ticks, 
    hedge_plan_for_liability,
)

import os, json
from datetime import datetime, timezone

# cache to avoid reloading repeatedly
_BLUEPRINT_PATTERNS: dict | None = None
_BLUEPRINT_DAY: str | None = None

                # === PATCH START ===
                # 📍 TARGET: engines/decision_engine/strategies/blueprints_strategy.py
                # 📆 PATCHED: 2025-10-28Z — use unified loader from engines.get_blueprints_for_market
                # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _load_blueprints_for_today() -> dict:
    """
    Load today's blueprint patterns from /data/blueprints using the shared
    engines.get_blueprints_for_market loader.  Falls back to most recent JSON.
    """
    global _BLUEPRINT_PATTERNS, _BLUEPRINT_DAY
    from engines.blueprint_loader import get_blueprints_for_market
    import glob

    today = datetime.now(timezone.utc).date().isoformat()
    if _BLUEPRINT_PATTERNS is not None and _BLUEPRINT_DAY == today:
        return _BLUEPRINT_PATTERNS

    try:
        # First, use in-memory cache if already warm
        if getattr(get_blueprints_for_market, "_BLUEPRINTS_CACHE", None):
            _BLUEPRINT_PATTERNS = get_blueprints_for_market(None)
            _BLUEPRINT_DAY = today
            print(f"[blueprints] linked cache {len(_BLUEPRINT_PATTERNS)} patterns via get_blueprints_for_market")
            return _BLUEPRINT_PATTERNS

        # Otherwise load most recent file manually
        bp_dir = os.path.join("data", "blueprints")
        files = sorted(glob.glob(os.path.join(bp_dir, "blueprint_signals_*_2025-*.json")))
        if not files:
            raise FileNotFoundError("no blueprint_signals_*.json files found")
        latest = max(files, key=os.path.getmtime)
        with open(latest, "r", encoding="utf-8") as f:
            _BLUEPRINT_PATTERNS = json.load(f) or {}
            _BLUEPRINT_DAY = today
            print(f"[blueprints] fallback loaded {len(_BLUEPRINT_PATTERNS)} from {os.path.basename(latest)}")
            return _BLUEPRINT_PATTERNS
    except Exception as e:
        print(f"[blueprints] warn: cache load failed: {e}")
        _BLUEPRINT_PATTERNS, _BLUEPRINT_DAY = {}, today
        return {}
                # === PATCH END ===


# ===== Dynamic stake (pull from daily_config if present; safe fallbacks) =====
try:
    # your config snippet lives here (or equivalent)
    from engines.daily_config import (
        MIN_STAKE,
        STAKE_MAX,
        BANK_PCT_PER_ENTRY,
        HARD_CAP_PRE,
        HARD_CAP_IP,
        L1_FRACTION_CAP,
        LETTER_MULT,
        fetch_available_budget,
    )
except Exception:
    MIN_STAKE          = 2.00
    STAKE_MAX          = 12.00
    BANK_PCT_PER_ENTRY = 0.004
    HARD_CAP_PRE       = 5.00
    HARD_CAP_IP        = 3.00
    L1_FRACTION_CAP    = 0.35
    LETTER_MULT        = {}
    def fetch_available_budget() -> float:  # fallback
        return 1000.0

# Add a sensible default multiplier for P if not defined
P_MULT = float(LETTER_MULT.get("P", 1.20))

# small budget cache to avoid hammering the account endpoint
_BUDGET_CACHE: Tuple[float, float] = (0.0, 1000.0)  # (ts, value)

def _budget_now() -> float:
    ts, val = _BUDGET_CACHE
    now = time.time()
    if (now - ts) > 180.0:  # 3min cache
        try:
            val = float(fetch_available_budget() or 1000.0)
        except Exception:
            val = 1000.0
        globals()["_BUDGET_CACHE"] = (now, val)
    return float(val)

def _liq_cap_for_side(ctx: Dict[str, Any], side: str) -> Optional[float]:
    """
    Soft-cap stake by L1 depth if available (better execution realism).
    Use the side-appropriate liquidity if present in ctx (non-blocking).
    """
    try:
        if side.upper().startswith("LAY"):   # entry lay → check lay side liquidity
            v = ctx.get("liq_best_lay") or ctx.get("avail_lay") or ctx.get("l1_available_lay")
        else:                                # entry back → check back side liquidity
            v = ctx.get("liq_best_back") or ctx.get("avail_back") or ctx.get("l1_available")
        if v is None: return None
        return float(v) * float(L1_FRACTION_CAP)
    except Exception:
        return None

def _phase(ctx: Dict[str, Any]) -> str:
    p = str(ctx.get("phase") or "").upper()
    if p in ("PRE", "IN_PLAY", "WAIT_INPLAY"):
        return "IN_PLAY" if p in ("IN_PLAY", "WAIT_INPLAY") else "PRE"
    # fallback by minutes_to_off
    try:
        mto = float(ctx.get("minutes_to_off", 1.0))
        return "IN_PLAY" if mto <= 0.0 else "PRE"
    except Exception:
        return "PRE"

def _dynamic_size(ctx: Dict[str, Any], direction: str, confidence: float) -> float:
    """
    Position sizing = min( bank_risk, hard_phase_cap, STAKE_MAX, ctx.size_cap, l1_fraction_cap ).
    Scaled by a multiplier tied to the BLUEPRINTS family and the posterior confidence.
    """
    # bank fraction
    bank   = _budget_now()
    risk   = max(MIN_STAKE, bank * float(BANK_PCT_PER_ENTRY))

    # confidence scaling: 0.55..1.0 → 0.75..1.25 (same shape as runtime)
    conf_scale = 0.75 + 0.5 * max(0.0, (confidence - 0.55))
    base_scale = P_MULT * conf_scale

    # phase hard caps
    cap_phase = HARD_CAP_PRE if _phase(ctx) == "PRE" else HARD_CAP_IP

    # ctx-provided cap from mastery/runtime (if any)
    ctx_cap = None
    try:
        ctx_cap = float(ctx.get("size_cap")) if ctx.get("size_cap") is not None else None
    except Exception:
        ctx_cap = None

    # L1 liquidity soft cap (if present)
    l1_cap = _liq_cap_for_side(ctx, "LAY" if direction.upper().startswith("LAY") else "BACK")

    # candidate base
    base = max(MIN_STAKE, risk * base_scale)

    # compose caps
    caps = [cap_phase, STAKE_MAX]
    if ctx_cap is not None: caps.append(ctx_cap)
    if l1_cap  is not None: caps.append(l1_cap)

    size = min([c for c in caps if c is not None] or [base])
    size = min(size, base)   # never exceed bank-scaled base
    return max(MIN_STAKE, float(round(size, 2)))

# Liability guard knobs (tune small first; keep it conservative)
LIAB_MIN_POUNDS     = 3.0   # start reducing if per-runner LAY liability > £3 (or BACK risk > £3)
ADVERSE_TICKS_MIN   = 2     # only intervene if we’re ≥ 2 ticks the wrong way
FAV_RANK_ONLY       = True  # focus hedging on current favourite by default


# ===== Strategy ===============================================================
NAME = "BLUEPRINTS"

from typing import Dict, Any, Optional

# ============================================================
# 📍 TARGET: engines/decision_engine/strategies/blueprints_strategy.py
# 🔎 SEARCH: ^def decide\(ctx: Dict\[str, Any\]\) -> Optional\[Dict\[str, Any\]\]:
# ⛏️ ACTION: replace the entire decide(...) function with the block below
# 📆 PATCHED: 2025-09-18
# ============================================================
def decide(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    BLUEPRINTS (hybrid):
      • Gate: OC presence AND freshness (OC2+; IN_PLAY needs OC7+)
      • Risk-first: hedge adverse liability on favs
      • Posterior → MP-shaped plan; confidence fallback if MP says "no"
      • Never interferes with other letters (caps/rotation are external)
    """
    from typing import Dict, Any, Optional
    import sqlite3
    from datetime import datetime, timezone

    try:
        mid = str(ctx.get("marketId") or ctx.get("market_id") or "")
        sid = str(ctx.get("selectionId") or ctx.get("selection_id") or "")
        if not mid or not sid:
            ctx["bp_dbg"] = "P:missing_ids"
            return None

        # ---- OC presence (must be at least OC2) ----------------------------
        t = oc_presence(mid, sid)
        if t < 2:
            ctx["bp_dbg"] = "P:oc<t2"
            return None

        # Ensure today’s blueprint cache is warm (robust loader)
        try:
            from engines.mastery.blueprint_loader import ensure_today_loaded
            ensure_today_loaded(key="bp", max_age_s=300)
        except Exception:
            pass


        # ---- Freshness check (stale cache must NOT trigger P) --------------
        max_age_s = int(ctx.get("bp_max_age_s", 30))  # override-able via ctx
        is_fresh = False
        age = None
        try:
            from engines.config_paths import autoscalp_db
            con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
            r = con.execute(
                "SELECT last_sync_ts FROM inbound_oc_cache "
                "WHERE marketId=? AND selectionId=? "
                "ORDER BY id DESC LIMIT 1",
                (mid, sid)
            ).fetchone()
            if r and r["last_sync_ts"]:
                now = datetime.now(timezone.utc)
                # tolerate "YYYY-MM-DD HH:MM:SS" or ISO with/without tz
                ts_str = str(r["last_sync_ts"]).replace(" ", "T")
                try:
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    ts = ts.astimezone(timezone.utc)
                    age = (now - ts).total_seconds()
                    is_fresh = (age <= max(5, max_age_s))
                except Exception:
                    is_fresh = False
        except Exception:
            is_fresh = False
        finally:
            try: con.close()
            except Exception: pass

        if not is_fresh:
            ctx["bp_dbg"] = f"P:stale_or_missing_tape age={age}"
            return None

        # ---- Phase gate: IN_PLAY requires deeper tape ----------------------
        ph = _phase(ctx)
        if ph == "IN_PLAY" and t < 7:
            ctx["bp_dbg"] = f"P:hold IP OC{t}"
            return None

        # ---- Anchor: bands → oc1 → ctx odds --------------------------------
        anchor, _meds = median_series_from_bands(mid, sid)
        if anchor is None:
            snap = latest_oc1_snapshot(mid)
            a = snap.get(sid)
            if a is not None:
                anchor = float(a)
            else:
                od = ctx.get("odds") or ctx.get("ltp")
                if od is not None:
                    anchor = float(od)
        if anchor is None:
            ctx["bp_dbg"] = "P:no_anchor"
            return None

        # ---- RISK-FIRST hedging on fav if adverse --------------------------
        try:
            # fallbacks so missing constants don't crash
            try:  FAV_RANK_ONLY
            except NameError: FAV_RANK_ONLY = False
            try:  LIAB_MIN_POUNDS
            except NameError: LIAB_MIN_POUNDS = 1.00
            try:  ADVERSE_TICKS_MIN
            except NameError: ADVERSE_TICKS_MIN = 2

            ranks = current_ranks(mid) or {}
            is_fav = (int(ranks.get(sid, 99)) == 1) if ranks else False
            if (not FAV_RANK_ONLY) or is_fav:
                snap = latest_oc1_snapshot(mid)
                cur_px = float(snap.get(sid)) if sid in snap else float(ctx.get("odds") or ctx.get("ltp") or anchor or 0.0)
                liab = liability_snapshot(mid).get(sid, {})
                lay_liab  = float(liab.get("lay_liab", 0.0))
                back_risk = float(liab.get("back_at_risk", 0.0))

                # Hedge LAY exposure on steam
                if lay_liab >= LIAB_MIN_POUNDS:
                    entry_odds = float((ctx.get("parent_info")() or {}).get("odds", anchor)) if callable(ctx.get("parent_info")) else float(anchor)
                    adv = adverse_ticks(entry_odds, cur_px, "LAY")
                    if adv >= ADVERSE_TICKS_MIN:
                        plan = hedge_plan_for_liability("LAY", cur_px, lay_liab, tgt_ticks=1)
                        plan["size"] = min(_dynamic_size(ctx, plan["direction"], 0.70), lay_liab)
                        plan["why"] = (plan.get("why","") + f" | fav={is_fav} adv={adv}t").strip()
                        return plan

                # Hedge BACK exposure on drift
                if back_risk >= LIAB_MIN_POUNDS:
                    entry_odds = float((ctx.get("parent_info")() or {}).get("odds", anchor)) if callable(ctx.get("parent_info")) else float(anchor)
                    adv = adverse_ticks(entry_odds, cur_px, "BACK")
                    if adv >= ADVERSE_TICKS_MIN:
                        plan = hedge_plan_for_liability("BACK", cur_px, back_risk, tgt_ticks=1)
                        plan["size"] = min(_dynamic_size(ctx, plan["direction"], 0.70), back_risk)
                        plan["why"] = (plan.get("why","") + f" | fav={is_fav} adv={adv}t").strip()
                        return plan
        except Exception:
            # risk-first is best-effort; never crash P
            pass

        # ---- Posterior over next-OC transition -----------------------------
        tr = match_transition(mid, sid, t)
        if not tr:
            ctx["bp_dbg"] = "P:no_transition"
            return None

        probs = tr.get("probs") or {}
        pD = float(probs.get("D", 0.0))  # drift (LAY->BACK)
        pS = float(probs.get("S", 0.0))  # steam (BACK->LAY)
        conf = max(pD, pS)

        # ---- MP-shaped plan; fallback if MP declines -----------------------
        from engines.mastery import mastery_policy as mp
        plan = mp.propose_trade(ctx)

        if not plan or not plan.get("enter"):
            # confidence-based fallback (conservative)
            if conf >= 0.55:
                direction = "LAY->BACK" if pD >= pS else "BACK->LAY"
                plan = {"enter": True, "direction": direction, "target_ticks": 1, "size": 2.0, "why": "P-fallback"}
            else:
                ctx["bp_dbg"] = "P:no_plan"
                return None

        # ---- Dynamic size with budget/hard caps/L1 fraction ----------------
        plan["size"] = _dynamic_size(ctx, plan["direction"], conf)

        # ---- Tag breadcrumbs -----------------------------------------------
        if "pass_tag" not in ctx:
            try:
                mto = float(ctx.get("minutes_to_off", 9999.0) or 9999.0)
                tag = (f"P{int(max(0.0, min(60.0, mto))):02d}") if ph == "PRE" else (f"P-L{max(7, t)}")
            except Exception:
                tag = "P"
            ctx["pass_tag"] = tag

        plan["why"] = (plan.get("why", "") + f" | conf={conf:.2f}").strip()
        return plan

    except Exception:
        ctx["bp_dbg"] = "P:error"
        return None
