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


# ======================================================================
# Compute dynamic stake with REAL inputs
# ======================================================================
# ======================================================================
# EXPLORATORY dynamic stake — signal-weighted conviction (AUTHORITATIVE)
# ======================================================================

def compute_exploratory_dynamic_stake(*, ctx: dict, engine="MSC_EXPLORATORY") -> float:
    from engines.daily_config import ENGINE_MIN, ENGINE_MAX

    lo = float(ENGINE_MIN.get(engine, 2.0))
    hi = float(ENGINE_MAX.get(engine, lo))

    intel = _fetch_v7_intel(ctx)

    # --- signals (fail-open) ---
    success   = intel.get("success")
    weight    = intel.get("weight")
    fav_rank  = intel.get("fav_rank")
    drift_pct = intel.get("actual_drift_pct") or intel.get("drift_pct")

    conf = 0.5  # baseline

    if isinstance(success, (int, float)):
        conf *= max(0.5, min(1.2, float(success)))

    if isinstance(weight, (int, float)):
        conf *= max(0.7, min(1.3, float(weight)))

    if isinstance(fav_rank, int) and fav_rank > 0:
        conf *= max(0.6, min(1.3, 1.3 / fav_rank))

    if isinstance(drift_pct, (int, float)):
        conf *= max(0.7, min(1.0, 1.0 - abs(drift_pct) / 20.0))

    conf = max(0.0, min(conf, 1.0))
    stake = lo + conf * (hi - lo)
    return round(stake, 2)



# ======================================================================
# IN-PLAY dynamic stake — momentum & position driven (AUTHORITATIVE)
# ======================================================================

def compute_inplay_dynamic_stake(*, ctx: dict, engine="MSC_INPLAY") -> float:
    from engines.daily_config import ENGINE_MIN, ENGINE_MAX

    lo = float(ENGINE_MIN.get(engine, 2.0))
    hi = float(ENGINE_MAX.get(engine, lo))

    intel = _fetch_v7_intel(ctx)

    drift_ratio   = intel.get("drift_ratio")
    reversal_flag = bool(intel.get("reversal_flag"))
    pos_inplay    = intel.get("pos_inplay")
    success       = intel.get("success")
    fav_rank      = intel.get("fav_rank")

    conf = 0.5

    if isinstance(drift_ratio, (int, float)):
        conf *= max(0.7, min(1.4, abs(drift_ratio)))

    if reversal_flag:
        conf *= 1.15

    if isinstance(pos_inplay, int):
        conf *= max(0.6, min(1.3, 1.3 / (pos_inplay + 1)))

    if isinstance(success, (int, float)):
        conf *= max(0.7, min(1.3, float(success)))

    if fav_rank == 1:
        conf *= 1.1

    conf = max(0.0, min(conf, 1.0))
    stake = lo + conf * (hi - lo)
    return round(stake, 2)


# ======================================================================
# RISK dynamic stake — tick-distance scaling (AUTHORITATIVE)
# ======================================================================

from engines.price_math import calculate_tick_distance as ladder_ticks_between

def compute_risk_dynamic_stake(
    *,
    parent_px: float,
    current_px: float,
    engine: str = "MSC_RISK",
) -> float:
    """
    Risk stake scales with absolute tick distance from parent entry price.
    Direction is irrelevant.
    """

    from engines.daily_config import ENGINE_MIN, ENGINE_MAX

    # Safety
    if parent_px <= 0 or current_px <= 0:
        return float(ENGINE_MIN.get(engine, 2.0))

    # Tick distance (absolute)
    ticks_away = abs(
        ladder_ticks_between(parent_px, current_px)
    )

    # Normalisation domain (fixed)
    MAX_TICKS = ladder_ticks_between(1.5, 12.0)
    if MAX_TICKS <= 0:
        return float(ENGINE_MIN.get(engine, 2.0))

    pressure = min(1.0, ticks_away / MAX_TICKS)

    lo = float(ENGINE_MIN.get(engine, 2.0))
    hi = float(ENGINE_MAX.get(engine, lo))

    stake = lo + pressure * (hi - lo)
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

def compute_dynamic_stake(*, engine: str, ctx: dict) -> float:
    """
    Final Dynamic Stake v7.

    Stake is determined as:
        ENGINE_MIN + confidence * (ENGINE_MAX - ENGINE_MIN)

    Confidence is derived from existing signals (letters, phase),
    NOT from bank or exposure.

    Returns a rounded stake (2dp), always within engine envelope.
    """

    from engines.daily_config import ENGINE_MIN, ENGINE_MAX, LETTER_MULT

    # --------------------------------------------------
    # 1️⃣ Engine envelope (HARD LIMITS)
    # --------------------------------------------------
    eng = engine.upper()

    min_stake = float(ENGINE_MIN.get(eng, 2.0))
    max_stake = float(ENGINE_MAX.get(eng, min_stake))

    # Safety: broken config should never collapse stake
    if max_stake < min_stake:
        max_stake = min_stake

    # --------------------------------------------------
    # 2️⃣ Confidence score (dimensionless)
    # --------------------------------------------------
    # Base confidence
    confidence = 1.0

    # Letter-based conviction (primary signal)
    letter = (ctx.get("letter") or ctx.get("source") or "")[:1].upper()
    confidence *= float(LETTER_MULT.get(letter, 1.0))

    # Optional: time / phase signals (kept gentle by design)
    mto = ctx.get("minutes_to_off")
    if isinstance(mto, (int, float)):
        if mto > 60:
            confidence *= 1.05
        elif mto < 10:
            confidence *= 0.95

    ocp = ctx.get("oc_phase")
    if isinstance(ocp, int):
        if ocp < 3:
            confidence *= 1.05
        elif ocp > 6:
            confidence *= 0.95

    # Normalise confidence into a sane band
    # (we only care about where we sit inside the envelope)
    confidence = max(0.0, min(confidence, 1.25))

    # --------------------------------------------------
    # 3️⃣ Linear interpolation inside envelope
    # --------------------------------------------------
    stake = min_stake + confidence * (max_stake - min_stake)

    # --------------------------------------------------
    # 4️⃣ HARD SNAP (final authority)
    # --------------------------------------------------
    if stake < min_stake:
        stake = min_stake
    elif stake > max_stake:
        stake = max_stake

    return round(float(stake), 2)


# === PATCH END ================================================================
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
# 📆 PATCHED: 2026-03-22 — Canonical green-up sizing (pure, invariant-only)
#
# PURPOSE:
# - Compute CHILD hedge stake such that:
#       WIN_PNL == LOSE_PNL
# - Profit derives ONLY from price movement (ticks)
# - Direction is handled by CALLER (side selection), not math
#
# MATHEMATICAL INVARIANT (FINAL):
#   child_stake = (parent_stake * parent_odds) / child_odds
#
# ASSUMPTIONS (NOW GUARANTEED UPSTREAM):
# - parent_stake >= ENGINE_MIN (≥ £3)
# - hedge_odds > 0
# - parent_odds > 0
#
# NOTES:
# - No Betfair minimum enforcement here
# - No defensive fallbacks
# - Rounding is applied LAST
# ======================================================================================================

def calc_greenup_stake(
    parent_side: str,
    entry_odds: float,
    parent_stake: float,
    hedge_odds: float,
) -> float:
    """
    Canonical green-up calculation.

    Guarantees:
      • WIN PnL == LOSE PnL
      • Profit scales with tick distance
      • Works identically for:
            - LAY → BACK
            - BACK → LAY
    """

    # All validation is upstream — math only lives here
    entry_odds   = float(entry_odds)
    hedge_odds   = float(hedge_odds)
    parent_stake = float(parent_stake)

    stake = (parent_stake * entry_odds) / hedge_odds

    return round(stake, 2)

# === PATCH END ==============================================================



