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


# ======================================================================
# Compute dynamic stake with REAL inputs
# ======================================================================
def compute_dynamic_stake(ctx: Dict[str, Any], engine: str) -> float:
    """
    The OFFICIAL Dynamic Stake v7:
        - reads BankState static pot
        - auto-reduces when exposure is high
        - uses oc-phase + letter policy
        - enforces caps from DailyConfig
        - always >= MIN_STAKE
    """
    # -------------------------------
    # 1) Identify letter
    # -------------------------------
    letter = (ctx.get("letter") or ctx.get("family") or "?").upper()

    base = BASE_MAP.get(letter)
    if base is None:
        return MIN_STAKE  # unknown family → minimal safe stake

    smax = MAX_MAP.get(letter, base)
    mult = LETTER_MULT.get(letter, 1.0)

    # -------------------------------
    # 2) Engine pot + available
    # -------------------------------
    pot_static = bank_state.get_engine_pot(engine)
    avail_now  = bank_state.get_engine_available(engine)

    # If pot exhausted → safest fallback
    if avail_now <= MIN_STAKE:
        return MIN_STAKE

    # -------------------------------
    # 3) Context-based multipliers
    # -------------------------------
    mto = float(ctx.get("minutes_to_off", 120.0) or 120.0)
    ocp = int(ctx.get("oc_phase", 0))

    # PRE-off scaling (bigger early, smaller late)
    if mto > 60:
        time_mult = 1.20
    elif mto > 20:
        time_mult = 1.00
    elif mto > 5:
        time_mult = 0.80
    else:
        time_mult = 0.60

    # OC-phase scaling (earlier OC = higher conviction)
    if ocp < 3:
        oc_mult = 1.20
    elif ocp < 6:
        oc_mult = 1.00
    else:
        oc_mult = 0.80

    # -------------------------------
    # 4) Exposure safety reduction
    # -------------------------------
    open_liab = pot_static - avail_now
    liab_frac = max(0.0, min(1.0, open_liab / (pot_static + 1e-9)))

    # If engine is heavily committed, shrink aggressively
    if liab_frac > 0.75:
        exp_mult = 0.25
    elif liab_frac > 0.50:
        exp_mult = 0.50
    elif liab_frac > 0.25:
        exp_mult = 0.75
    else:
        exp_mult = 1.00

    # -------------------------------
    # 5) Compute raw stake
    # -------------------------------
    stake = base * mult * time_mult * oc_mult * exp_mult

    # cap by engine available balance
    stake = min(stake, avail_now)

    # respect letter max
    stake = min(stake, smax)

    # respect phase caps
    phase = "PRE" if mto > 0 else "IP"
    stake = min(stake, HARD_CAP_PRE if phase == "PRE" else HARD_CAP_IP)

    # never drop below minimum
    stake = max(MIN_STAKE, round(stake, 2))

    return stake

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


# --- PUBLIC API #2 (BUS) ----------------------------------------------------
def compute_dynamic_stake(ctx: dict, engine: str) -> float:
    """
    BUS wrapper — extracts letter + phase + pot.
    """
    letter = (engine or "X")[:1].upper()
    phase = "INPLAY" if ctx.get("oc_phase", 0) >= 7 else "PRE"

    try:
        from engines.live.bank_state import get_engine_pot
        bank = get_engine_pot(engine)
    except Exception:
        bank = None

    stake, why = _dynamic(letter, phase, bank)

    # Debug breadcrumb (optional)
    ctx["dyn_stake_why"] = why

    return stake


# --- Greening stake (unchanged) ---------------------------------------------
def calc_greenup_stake(parent_side: str, entry_odds: float, parent_stake: float, hedge_odds: float):
    """True greening stake (flat profit across all runners)."""
    try:
        s = float(parent_stake) * float(entry_odds) / float(hedge_odds)
        return round(max(cfg.MIN_STAKE, s), 2)
    except Exception:
        return round(max(cfg.MIN_STAKE, float(parent_stake)), 2)
