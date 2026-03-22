# === PATCH START ============================================================
# 📍 TARGET: engines/math/dynamic_stake_v7.py
# 🆕 PATCHED: 2026-02-15 — True Dynamic Stake v7 (BankState + BudgetManager)
# ============================================================================

#!/usr/bin/env python3
# ======================================================================
# Dynamic Stake v7 — Full-context sizing tied to BankState + allocations
# ======================================================================

from __future__ import annotations
import math
from typing import Dict, Any

# Architecture-correct dependencies (no circulars)
from engines.risk import budget_manager
from engines.live import bank_state
from engines.daily_config import (
    MIN_STAKE,
    HARD_CAP_PRE,
    HARD_CAP_IP,
    LETTER_MULT,
    BASE_STAKE_A, BASE_STAKE_S, BASE_STAKE_Z, BASE_STAKE_L,
    BASE_STAKE_B, BASE_STAKE_G, BASE_STAKE_X, BASE_STAKE_R, BASE_STAKE_F,
    BASE_STAKE_I, BASE_STAKE_T, BASE_STAKE_C, BASE_STAKE_E, BASE_STAKE_K,
)

# Mapping letters → Max stakes (from daily_config)
from engines.daily_config import (
    STAKE_MAX_A, STAKE_MAX_S, STAKE_MAX_Z, STAKE_MAX_L,
    STAKE_MAX_B, STAKE_MAX_G, STAKE_MAX_X, STAKE_MAX_R, STAKE_MAX_F,
    STAKE_MAX_I, STAKE_MAX_T, STAKE_MAX_C, STAKE_MAX_E, STAKE_MAX_K,
)


BASE_MAP = {
    "A": BASE_STAKE_A,  "S": BASE_STAKE_S,  "Z": BASE_STAKE_Z,
    "L": BASE_STAKE_L,  "B": BASE_STAKE_B,  "G": BASE_STAKE_G,
    "X": BASE_STAKE_X,  "R": BASE_STAKE_R,  "F": BASE_STAKE_F,
    "I": BASE_STAKE_I,  "T": BASE_STAKE_T,  "C": BASE_STAKE_C,
    "E": BASE_STAKE_E,  "K": BASE_STAKE_K,
}

MAX_MAP = {
    "A": STAKE_MAX_A, "S": STAKE_MAX_S, "Z": STAKE_MAX_Z,
    "L": STAKE_MAX_L, "B": STAKE_MAX_B, "G": STAKE_MAX_G,
    "X": STAKE_MAX_X, "R": STAKE_MAX_R, "F": STAKE_MAX_F,
    "I": STAKE_MAX_I, "T": STAKE_MAX_T, "C": STAKE_MAX_C,
    "E": STAKE_MAX_E, "K": STAKE_MAX_K,
}

def _fetch_v7_intel(ctx: dict) -> dict:
    """
    DB-first v7 intelligence fetch.
    Fail-open: returns {} if unavailable.
    """
    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")

    if not mid or not sid:
        return {}

    try:
        from engines.micro_scalper_v7.v7_snapshot_helper import get_v7_snapshot
        snap = get_v7_snapshot(str(mid), str(sid))
        return snap or {}
    except Exception:
        return {}

# -------------------------------------------------------------------
# FORM (DB-TRUTH, FINAL)
# -------------------------------------------------------------------

# Last-3 recency weights (most recent first)
RECENCY_WEIGHTS = [0.5, 0.3, 0.2]

# Race strength (inferred ONLY from market_name text)
RACE_BUCKET_RANK = {
    "OPEN": 4,
    "CLASS": 3,
    "HANDICAP": 2,
    "NOVICE": 1,
}

def _infer_distance_bucket(market_name: str) -> str | None:
    if not market_name:
        return None
    s = market_name.lower()
    for token in ("5f","6f","7f","1m","1m2f","1m4f","1m6f","2m","3m"):
        if token in s:
            return token
    return None

def _infer_race_bucket(market_name: str) -> str:
    if not market_name:
        return "OPEN"

    s = market_name.upper()

    if "NOV" in s or "NHF" in s or "INHF" in s:
        return "NOVICE"
    if "HCAP" in s:
        return "HANDICAP"
    if "CLASS" in s:
        return "CLASS"

    return "OPEN"   # open / conditions / no label

def _fetch_runner_form_history(
    *, selectionId: int, limit: int = 10
):
    """
    DB-verified form query.
    Returns rows newest → oldest.
    Must already include:
      - market_name
      - event_name
      - marketStartTime
      - won (0/1)
    """

    from engines.config_paths import connect_bets_db
    import sqlite3

    con = connect_bets_db(ro=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    rows = cur.execute("""
        SELECT
            b.marketStartTime,
            b.event_name,
            b.market_name,
            CASE
                WHEN w.selectionId IS NOT NULL THEN 1
                ELSE 0
            END AS won
        FROM bets b
        LEFT JOIN bf_cleared_orders w
          ON w.marketId = b.marketId
         AND w.selectionId = b.selectionId
        WHERE b.selectionId = ?
        ORDER BY b.marketStartTime DESC
        LIMIT ?
    """, (int(selectionId), int(limit))).fetchall()

    con.close()
    return rows

def _compute_form_multiplier(
    *,
    rows,
    today_event: str,
    today_market: str,
) -> float:
    """
    Combine:
      • recency (last-3 win pattern)
      • course match
      • distance match
      • race bucket strength
    """

    if not rows:
        return 1.0

    # --- 1) Recency (last 3 runs) ---
    score = 0.0
    for i, w in enumerate(RECENCY_WEIGHTS):
        if i < len(rows):
            score += rows[i]["won"] * w

    # --- 2) Course & distance match (any recent WIN) ---
    today_dist = _infer_distance_bucket(today_market)

    course_bonus = False
    distance_bonus = False

    for r in rows[:3]:
        if not r["won"]:
            continue

        if r["event_name"] == today_event:
            course_bonus = True

            if today_dist and _infer_distance_bucket(r["market_name"]) == today_dist:
                distance_bonus = True

    if course_bonus:
        score *= 1.10
    if distance_bonus:
        score *= 1.10   # stacks only if both true

    # --- 3) Race bucket (from MOST RECENT run) ---
    bucket = _infer_race_bucket(rows[0]["market_name"])
    rank = RACE_BUCKET_RANK.get(bucket, 2)

    # scale: NOVICE→0.85 … OPEN→1.15
    bucket_mult = 0.85 + (rank - 1) * (0.30 / 3.0)
    score *= bucket_mult

    # Clamp (never destructive)
    return max(0.60, min(score, 1.25))

def get_form_adjustment(
    *,
    marketId: str,
    selectionId: int,
) -> float:
    """
    Public FORM API for Dynamic Stake.
    Safe, read-only, DB-truthful.
    """

    try:
        rows = _fetch_runner_form_history(selectionId=selectionId)
        if not rows:
            return 1.0

        today_event  = rows[0]["event_name"]
        today_market = rows[0]["market_name"]

        return _compute_form_multiplier(
            rows=rows,
            today_event=today_event,
            today_market=today_market,
        )

    except Exception:
        return 1.0




# ======================================================================
# Compute dynamic stake with REAL inputs
# ======================================================================
# ======================================================================
# EXPLORATORY dynamic stake — signal-weighted conviction (AUTHORITATIVE)
# ======================================================================

def compute_exploratory_dynamic_stake(*, ctx: dict, engine="MSC_EXPLORATORY") -> float:
    from engines.daily_config import ENGINE_MIN, ENGINE_MAX
    from tools.betfair_match_surface import get_direction_confidence
    from engines.price_math import calculate_tick_distance as ladder_ticks_between

    lo = float(ENGINE_MIN[engine])
    hi = float(ENGINE_MAX[engine])

    anchor_px = ctx.get("anchor_entry_odds")
    current_px = ctx.get("px")
    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")
    band = ctx.get("band")

    if not anchor_px or not current_px:
        return round(lo, 2)

    # -----------------------------------------
    # 1️⃣ Tick distance conviction
    # -----------------------------------------
    ticks = abs(ladder_ticks_between(anchor_px, current_px))
    tick_score = min(ticks / 8.0, 1.0)  # exploratory reacts faster than risk

    # -----------------------------------------
    # 2️⃣ Direction confirmation
    # -----------------------------------------
    dir_conf = float(get_direction_confidence(mid, sid))

    # -----------------------------------------
    # 3️⃣ Combined conviction
    # -----------------------------------------
    confidence = (0.7 * tick_score) + (0.3 * dir_conf)
    confidence = max(0.0, min(confidence, 1.0))

    # -----------------------------------------
    # 4️⃣ Band compression
    # -----------------------------------------
    if band == "ACTIVE":
        band_mult = 1.0
    elif band == "PASSIVE":
        band_mult = 0.7
    elif band == "EXTENDED":
        band_mult = 0.4
    else:  # IGNORED or UNKNOWN
        return round(lo, 2)

    # -----------------------------------------
    # 5️⃣ Exploratory cap (60% of envelope)
    # -----------------------------------------
    envelope = hi - lo
    capped_hi = lo + 0.6 * envelope

    # Apply band compression to capped envelope
    band_hi = lo + band_mult * (capped_hi - lo)

    # -----------------------------------------
    # 6️⃣ Scale stake inside compressed cap
    # -----------------------------------------
    stake = lo + confidence * (band_hi - lo)

    # -----------------------------------------
    # 7️⃣ Form adjustment (light multiplier)
    # -----------------------------------------
    try:
        stake *= get_form_adjustment(
            marketId=mid,
            selectionId=sid,
        )
    except Exception:
        pass

    return round(stake, 2)



# ======================================================================
# IN-PLAY dynamic stake — momentum & position driven (AUTHORITATIVE)
# ======================================================================

# ======================================================================================================
# 📍 TARGET: engines/math/dynamic_stake_v7.py
# 🔎 SEARCH: def compute_inplay_dynamic_stake(*, ctx: dict, engine="MSC_INPLAY") -> float:
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-04-09 — MSC_INPLAY £75 LAY ladder + 3-4-5 BACK protection
#
# INVARIANTS:
# - LAY ladder targets £75 total (front-loaded risk)
# - BACK ladder caps loss ≈ £12 (3-4-5)
# - Deterministic PX → stake
# - No confidence, no envelopes, no BankState logic
# ======================================================================================================

def compute_inplay_dynamic_stake(*, ctx: dict, engine="MSC_INPLAY") -> float:
    """
    MSC_INPLAY stake is PX-deterministic.

    LAY ladder (profit engine):
        7  → 21.43
        8  → 17.86
        9  → 14.29
        10 → 10.71
        11 → 7.14
        12 → 3.57
        TOTAL ≈ £75

    BACK protection (loss control only):
        5 → £6
        4 → £4
        3 → £2
        TOTAL ≈ £12
    """

    px = float(ctx.get("px") or 0.0)
    direction = ctx.get("direction")

    # Defensive fallback
    if px <= 0:
        return MIN_STAKE

    # --------------------------------------------------
    # BACK protection — 3-4-5 ladder
    # --------------------------------------------------
    if direction == "BACK->LAY":
        if px <= 3.0:
            return 2.0
        elif px <= 4.0:
            return 4.0
        elif px <= 5.0:
            return 6.0
        return MIN_STAKE

    # --------------------------------------------------
    # LAY ladder — £75 total, descending stake
    # --------------------------------------------------
    if px >= 12.0:
        return 3.57
    elif px >= 11.0:
        return 7.14
    elif px >= 10.0:
        return 10.71
    elif px >= 9.0:
        return 14.29
    elif px >= 8.0:
        return 17.86
    elif px >= 7.0:
        return 21.43

    return MIN_STAKE



# ======================================================================
# RISK dynamic stake — tick-distance scaling (AUTHORITATIVE)
# ======================================================================

from engines.price_math import calculate_tick_distance as ladder_ticks_between

def compute_risk_dynamic_stake(*, ctx: dict, engine="MSC_RISK") -> float:
    from engines.daily_config import ENGINE_MIN, ENGINE_MAX
    from tools.betfair_match_surface import get_direction_confidence
    from engines.price_math import calculate_tick_distance as ladder_ticks_between

    lo = float(ENGINE_MIN[engine])
    hi = float(ENGINE_MAX[engine])

    anchor_px = ctx.get("anchor_entry_odds")
    current_px = ctx.get("px")
    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")
    band = ctx.get("band")

    if not anchor_px or not current_px:
        return round(lo, 2)

    # -----------------------------------------
    # 1️⃣ Tick distance conviction
    # -----------------------------------------
    ticks = abs(ladder_ticks_between(anchor_px, current_px))
    tick_score = min(ticks / 10.0, 1.0)

    # -----------------------------------------
    # 2️⃣ Directional confirmation
    # -----------------------------------------
    dir_conf = float(get_direction_confidence(mid, sid))

    # -----------------------------------------
    # 3️⃣ Combined conviction (0 → 1)
    # -----------------------------------------
    confidence = (0.6 * tick_score) + (0.4 * dir_conf)
    confidence = max(0.0, min(confidence, 1.0))

    # -----------------------------------------
    # 4️⃣ Band compression
    # -----------------------------------------
    if band == "ACTIVE":
        band_mult = 1.0
    elif band == "PASSIVE":
        band_mult = 0.7
    elif band == "EXTENDED":
        band_mult = 0.4
    else:  # IGNORED or UNKNOWN
        return round(lo, 2)

    band_hi = lo + band_mult * (hi - lo)

    # -----------------------------------------
    # 5️⃣ Linear scaling across compressed envelope
    # -----------------------------------------
    stake = lo + confidence * (band_hi - lo)

    return round(stake, 2)



# ======================================================================================================
# 📍 TARGET: engines/math/dynamic_stake_v7.py
# 🔎 SEARCH: def compute_dynamic_stake(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-22 — Final envelope-based dynamic stake (authoritative)
#
# PURPOSE:
# - Size PARENT stakes only
# - Use engine-level min/max as a hard envelope
# - Scale linearly inside the envelope using confidence signals
# - NEVER use bank %, available balance, or exposure
#
# INVARIANTS:
# - stake >= ENGINE_MIN[engine]
# - stake <= ENGINE_MAX[engine]
# - monotonic, deterministic, snap-clamped
# ======================================================================================================


# ======================================================================================================
# 📍 TARGET: engines/math/dynamic_stake_v7.py
# 🔎 SEARCH: def compute_dynamic_stake(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-18 — HYBRID 30-POINT SCORING + EXISTING DISPATCH (FINAL)
#
# PURPOSE:
# - Preserve ALL existing bet_type + engine routing (NO BREAKAGE)
# - Inject 30-point scoring ONLY into default / legacy path
# - Use ONLY ctx fields already passed from snapshots (no new wiring)
# - Deterministic, envelope-based, no BankState usage
#
# INPUT CONTRACT (must already exist in ctx via unified):
#   px, px_prev
#   rank, rank_prev
#   trend_direction
#   closed_aligned, closed_opposing
#   closure_speed
#   trend_start_px
#
# INVARIANTS:
# - score ∈ [1, 30]
# - stake ∈ [ENGINE_MIN, ENGINE_MAX]
# - No architecture changes
# ======================================================================================================

def compute_dynamic_stake(*, engine: str, ctx: dict) -> float:

    from engines.daily_config import ENGINE_MIN, ENGINE_MAX

    bet_type = (ctx.get("bet_type") or "").upper()
    eng = (engine or "").upper()

    # --------------------------------------------------
    # 1️⃣ BET-TYPE DISPATCH (UNCHANGED)
    # --------------------------------------------------

    if bet_type == "RISK":
        return compute_risk_dynamic_stake(ctx=ctx, engine="MSC_RISK")

    if bet_type == "EXPLORATORY":
        try:
            # --------------------------------------------------
            # FIXED BUDGET SLICE (AUTHORITATIVE)
            # --------------------------------------------------
            engine_budget = budget_manager.get_engine_allocation("MSC_EXPLORATORY")

            if not engine_budget or engine_budget <= 0:
                return MIN_STAKE

            stake = float(engine_budget) / 14.0

            return round(max(stake, MIN_STAKE), 2)

        except Exception:
            return MIN_STAKE

    if bet_type == "INPLAY":
        return compute_inplay_dynamic_stake(ctx=ctx, engine="MSC_INPLAY")

    if bet_type == "STOPLOSS":
        parent_stake = float(ctx.get("anchor_entry_stake") or 0.0)
        return round(max(parent_stake, ENGINE_MIN.get("OVERWATCHER", 2.0)), 2)

    if bet_type == "CORRECTION":
        return compute_inplay_dynamic_stake(ctx=ctx, engine="MSC_INPLAY")

    # --------------------------------------------------
    # 2️⃣ ENGINE FALLBACK (UNCHANGED)
    # --------------------------------------------------

    if eng == "MSC_RISK":
        return compute_risk_dynamic_stake(ctx=ctx, engine=eng)

    if eng == "MSC_INPLAY":
        return compute_inplay_dynamic_stake(ctx=ctx, engine=eng)

    if eng == "MSC_EXPLORATORY":
        return compute_exploratory_dynamic_stake(ctx=ctx, engine=eng)

    if eng == "OVERWATCHER":
        return compute_overwatch_dynamic_stake(ctx=ctx)

    # --------------------------------------------------
    # 3️⃣ HYBRID 30-POINT MODEL (NEW DEFAULT PATH)
    # --------------------------------------------------

    lo = float(ENGINE_MIN.get(eng, 2.0))
    hi = float(ENGINE_MAX.get(eng, lo))

    # --- Extract (fail-safe, no assumptions) ---
    px              = float(ctx.get("px") or 0.0)
    px_prev         = float(ctx.get("px_prev") or px)
    rank            = int(ctx.get("rank") or 0)
    rank_prev       = int(ctx.get("rank_prev") or rank)

    trend_direction = ctx.get("trend_direction")

    closed_aligned  = int(ctx.get("closed_aligned") or 0)
    closed_opposing = int(ctx.get("closed_opposing") or 0)
    closure_speed   = float(ctx.get("closure_speed") or 999.0)

    ref_px          = float(ctx.get("trend_start_px") or px)

    if px <= 0:
        return round(lo, 2)

    # --------------------------------------------------
    # MARKET TREND (0–10)
    # --------------------------------------------------
    trend_score = 0

    if px != px_prev:
        trend_score += 2

    if rank and rank_prev and rank < rank_prev:
        trend_score += 2

    if (trend_direction == "DRIFT" and px >= px_prev) or \
       (trend_direction == "STEAM" and px <= px_prev):
        trend_score += 2

    if abs(rank - rank_prev) <= 1:
        trend_score += 2

    if trend_direction:
        trend_score += 2

    trend_score = min(trend_score, 10)

    # --------------------------------------------------
    # TRADE CONFIRMATION (0–10)
    # --------------------------------------------------
    conf_score = 0

    if closed_aligned >= 1:
        conf_score += 2

    if closed_aligned >= 3:
        conf_score += 2

    if closure_speed < 2.0:
        conf_score += 2

    if closed_opposing == 0:
        conf_score += 2

    if closed_aligned > closed_opposing:
        conf_score += 2

    conf_score = min(conf_score, 10)

    # --------------------------------------------------
    # RISK / POSITION (0–10)
    # --------------------------------------------------
    risk_score = 0

    distance = abs(px - ref_px)

    if distance < 0.5:
        risk_score += 5
    elif distance < 1.5:
        risk_score += 4
    elif distance < 3.0:
        risk_score += 3
    elif distance < 5.0:
        risk_score += 2
    else:
        risk_score += 1

    if 2.0 <= px <= 6.0:
        risk_score += 5
    elif 1.5 <= px < 2.0 or 6.0 < px <= 10.0:
        risk_score += 4
    elif 10.0 < px <= 15.0:
        risk_score += 3
    else:
        risk_score += 1

    risk_score = min(risk_score, 10)

    # --------------------------------------------------
    # FINAL SCORE (1–30)
    # --------------------------------------------------
    score = trend_score + conf_score + risk_score
    score = max(1, min(score, 30))

    # --------------------------------------------------
    # SCORE → STAKE (30-STEP LINEAR)
    # --------------------------------------------------
    step = (hi - lo) / 29.0 if hi > lo else 0.0
    stake = lo + (score - 1) * step

    # HARD CLAMP
    stake = max(lo, min(stake, hi))

    return round(stake, 2)
# ======================================================================================================

# === PATCH END ================================================================
def advance_progressive_stage(*, marketId: str, selectionId: str):
    key = (marketId, selectionId)

    state = _PROGRESSIVE_STATE.get(key)
    if not state:
        return

    state["stage"] += 1
    state["locked_pct"] = min(state["locked_pct"], 1.0)

# ======================================================================
# 📍 TARGET: engines/math/dynamic_stake_v7.py
# 🔎 SEARCH: def clear_progressive_if_no_parent(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-02-16 — Fix progressive reset guard (DB-backed)
#
# PURPOSE:
# - Clear progressive compression state when no MATCHED parent exists
# - DB is authority
# - Idempotent
# ======================================================================

def clear_progressive_if_no_parent(mid: str, sid: str) -> None:
    """
    Remove progressive compression state
    if there is no MATCHED parent for this runner.
    """

    try:
        from engines.config_paths import open_auto_db
        import sqlite3

        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        row = con.execute("""
            SELECT 1
              FROM orders
             WHERE role='PARENT'
               AND marketId=?
               AND selectionId=?
               AND entry_status='MATCHED'
               AND (exit_status IS NULL OR UPPER(exit_status) NOT IN ('CANCELLED','EXPIRED','SETTLED'))
             LIMIT 1
        """, (str(mid), str(sid))).fetchone()

        con.close()

        if not row:
            _PROGRESSIVE_STATE.pop((str(mid), str(sid)), None)

    except Exception:
        # Must never crash dynamic stake import
        pass


# =====================================================================
# OVERWATCHER — Progressive Lock Stake
# =====================================================================
_PROGRESSIVE_STATE = {}

def compute_overwatch_dynamic_stake(ctx: dict) -> float:
    """
    Progressive compression stake calculator (band-aware).

    - Locks incremental profit %
    - Uses green-up math
    - Compression speed varies by band
    - BUS enforces final min/max
    """

    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")
    parent_stake = float(ctx.get("anchor_entry_stake") or 0.0)
    anchor_odds  = float(ctx.get("anchor_entry_odds") or 0.0)
    px = float(ctx.get("px") or 0.0)
    band = ctx.get("band")

    if not parent_stake or px <= 0 or anchor_odds <= 0:
        return 0.0

    key = (mid, sid)

    state = _PROGRESSIVE_STATE.setdefault(
        key,
        {
            "stage": 0,
            "locked_pct": 0.0,
        }
    )

    # --------------------------------------------------
    # Band-aware compression ladders
    # --------------------------------------------------
    if band == "ACTIVE":
        stages = [0.10, 0.25, 0.35, 0.45, 0.55, 0.60]
    elif band == "PASSIVE":
        stages = [0.08, 0.18, 0.28, 0.38, 0.48, 0.55]
    elif band == "EXTENDED":
        stages = [0.15, 0.30, 0.45, 0.55, 0.65, 0.70]
    else:
        return 0.0  # ignore IGNORED

    if state["stage"] >= len(stages):
        return 0.0

    target_pct = stages[state["stage"]]
    delta_pct = target_pct - state["locked_pct"]

    if delta_pct <= 0:
        return 0.0

    # --------------------------------------------------
    # Canonical green-up math
    # --------------------------------------------------
    stake = (parent_stake * delta_pct * anchor_odds) / px



    return round(max(stake, 0.0), 2)


# ===============================================================
# Dynamic Stake v7 — Unified sizing & greening
# ===============================================================

import engines.daily_config as cfg

# --- Core dynamic stake calculator -----------------------------------------
def _dynamic(letter: str, phase: str, bank: float | None):
    """
    Core dynamic sizing used by both public APIs:
      • calc_dynamic_stake    (legacy router)
      • compute_dynamic_stake (BUS)
    """
    base = getattr(cfg, f"BASE_STAKE_{letter}", cfg.BASE_STAKE)
    smax = getattr(cfg, f"STAKE_MAX_{letter}", cfg.STAKE_MAX)
    mult = cfg.LETTER_MULT.get(letter, 1.0)

    # % of bank allowed for a single scalp
    risk_cap = (cfg.BANK_PCT_PER_ENTRY or 0.0) * float(bank or 0.0)

    # phase caps (PRE tighter than INPLAY)
    phase_cap = cfg.HARD_CAP_PRE if phase == "PRE" else cfg.HARD_CAP_IP

    stake = base * mult
    stake = min(stake, risk_cap or stake, smax, phase_cap)
    stake = max(cfg.MIN_STAKE, round(stake, 2))

    why = f"base={base}×{mult} bank={bank} cap={risk_cap:.2f}/{phase_cap:.2f} stake={stake:.2f}"
    return stake, why


# --- PUBLIC API #1 (router) -------------------------------------------------
def calc_dynamic_stake(letter: str, phase: str = "PRE", bank: float | None = None):
    """
    Legacy public function used by live_router.py
    """
    stake, _ = _dynamic(letter, phase, bank)
    return stake




# --- Greening stake (unchanged) ---------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/math/dynamic_stake_v7.py
# 🔎 SEARCH: def calc_greenup_stake(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-18 — GreenUp v2 (tick-aware, parent/child aligned, invariant-safe)
#
# PURPOSE:
# - Compute CHILD hedge stake using:
#       parent_stake
#       entry_odds (anchor)
#       current_odds (child)
#       tick movement (optional, for validation / spacing)
#
# - Maintain invariant:
#       WIN_PNL ≈ LOSE_PNL
#
# - Allow ladder-based progression (multiple hedge levels)
#
# INPUT CONTRACT (ctx must already contain):
#   entry_odds            → parent matched price
#   parent_stake          → parent stake
#   hedge_odds            → current px (child)
#   ticks_moved           → optional (for ladder spacing only)
#
# INVARIANTS:
# - No budget logic
# - No min/max enforcement
# - Pure price math
# ======================================================================================================

def calc_greenup_stake(
    parent_side: str,
    entry_odds: float,
    parent_stake: float,
    hedge_odds: float,
    ticks_moved: float | None = None,
) -> float:
    """
    Tick-aware green-up calculation.

    Core invariant:
        child_stake = (parent_stake * entry_odds) / hedge_odds

    ticks_moved is NOT used to change math,
    only to validate spacing / progression externally.
    """

    # --- hard inputs ---
    entry_odds   = float(entry_odds)
    hedge_odds   = float(hedge_odds)
    parent_stake = float(parent_stake)

    if entry_odds <= 0 or hedge_odds <= 0 or parent_stake <= 0:
        return 0.0

    # --------------------------------------------------
    # 1️⃣ CORE GREEN-UP MATH (LOCKED INVARIANT)
    # --------------------------------------------------
    child_stake = (parent_stake * entry_odds) / hedge_odds

    # --------------------------------------------------
    # 2️⃣ OPTIONAL: TICK-BASED VALIDATION (NO MATH CHANGE)
    # --------------------------------------------------
    # ticks_moved can be used upstream to decide:
    #   - whether to place hedge
    #   - how many ladder steps to allow
    #
    # But NEVER changes the hedge formula itself
    # --------------------------------------------------

    return round(child_stake, 2)

# ======================================================================================================



