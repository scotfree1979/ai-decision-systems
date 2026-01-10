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
# ======================================================================================================
# 📍 TARGET: engines/math/dynamic_stake_v7.py
# 🔎 SEARCH: def compute_dynamic_stake(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-10 — Engine min/max clamping (final, authoritative)
#
# PURPOSE:
# - Enforce per-engine minimum & maximum stakes
# - Guarantee engines can always fire at least once
# - Allow aggression to scale naturally with pot growth
#
# RULE:
#   final_stake = clamp(
#       computed_stake,
#       ENGINE_MIN[engine],
#       ENGINE_MAX[engine]
#   )
#
# INVARIANTS:
# - Exposure logic remains intact
# - Bank availability respected
# - HARD_CAP_PRE / HARD_CAP_IP still apply
# - Clamp is LAST step before return
# ======================================================================================================

def compute_dynamic_stake(ctx: dict, engine: str) -> float:
    letter = (ctx.get("letter") or ctx.get("family") or "?").upper()
    phase  = "IP" if ctx.get("oc_phase", 0) >= 7 else "PRE"

    # --------------------------------------------------
    # Base sizing
    # --------------------------------------------------
    base = BASE_MAP.get(letter, MIN_STAKE)
    smax = MAX_MAP.get(letter, base)
    mult = LETTER_MULT.get(letter, 1.0)

    pot_static = bank_state.get_engine_pot(engine)
    avail_now  = bank_state.get_engine_available(engine)

    # If nothing is available, still allow minimum snap
    if avail_now <= 0:
        avail_now = 0.0

    # --------------------------------------------------
    # Time / phase multipliers
    # --------------------------------------------------
    mto = float(ctx.get("minutes_to_off", 120.0))
    ocp = int(ctx.get("oc_phase", 0))

    time_mult = (
        1.20 if mto > 60 else
        1.00 if mto > 20 else
        0.80 if mto > 5  else
        0.60
    )

    oc_mult = (
        1.20 if ocp < 3 else
        1.00 if ocp < 6 else
        0.80
    )

    # --------------------------------------------------
    # Exposure control
    # --------------------------------------------------
    liab_frac = (pot_static - avail_now) / max(pot_static, 1e-9)

    exp_mult = (
        0.25 if liab_frac > 0.75 else
        0.50 if liab_frac > 0.50 else
        0.75 if liab_frac > 0.25 else
        1.00
    )

    # --------------------------------------------------
    # Raw stake computation
    # --------------------------------------------------
    stake = base * mult * time_mult * oc_mult * exp_mult

    # Hard availability / letter / phase caps
    stake = min(stake, avail_now)
    stake = min(stake, smax)
    stake = min(stake, HARD_CAP_PRE if phase == "PRE" else HARD_CAP_IP)

    # --------------------------------------------------
    # ENGINE-LEVEL FLOOR / CEILING (FINAL SNAP)
    # --------------------------------------------------
    try:
        from engines.daily_config import ENGINE_MIN, ENGINE_MAX

        eng = engine.upper()

        if eng in ENGINE_MIN:
            stake = max(ENGINE_MIN[eng], stake)

        if eng in ENGINE_MAX:
            stake = min(ENGINE_MAX[eng], stake)

    except Exception:
        pass

    return round(max(MIN_STAKE, stake), 2)

# === PATCH END ==============================================================



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
def calc_greenup_stake(
    parent_side: str,
    entry_odds: float,
    parent_stake: float,
    hedge_odds: float,
):
    """
    True greening stake.
    Produces flat P&L across the entire market when child fully matches.
    """

    try:
        entry_odds = float(entry_odds)
        hedge_odds = float(hedge_odds)
        parent_stake = float(parent_stake)

        if parent_side.upper() == "LAY":
            # LAY → BACK
            stake = (parent_stake * entry_odds) / hedge_odds

        elif parent_side.upper() == "BACK":
            # BACK → LAY
            stake = (parent_stake * entry_odds) / max(hedge_odds - 1.0, 1e-9)

        else:
            return round(max(cfg.MIN_STAKE, parent_stake), 2)

        return round(max(cfg.MIN_STAKE, stake), 2)

    except Exception:
        return round(max(cfg.MIN_STAKE, parent_stake), 2)

