#!/usr/bin/env python3
# ===============================================================
# Dynamic Stake v7 — Unified sizing & greening
# ===============================================================

import engines.daily_config as cfg

def calc_dynamic_stake(letter: str, phase: str = "PRE", bank: float | None = None):
    """Unified dynamic stake (BASE * MULT capped by bank % and phase caps)."""
    base = getattr(cfg, f"BASE_STAKE_{letter}", cfg.BASE_STAKE)
    smax = getattr(cfg, f"STAKE_MAX_{letter}", cfg.STAKE_MAX)
    mult = cfg.LETTER_MULT.get(letter, 1.0)
    risk_cap = (cfg.BANK_PCT_PER_ENTRY or 0.0) * float(bank or 0.0)
    phase_cap = cfg.HARD_CAP_PRE if phase == "PRE" else cfg.HARD_CAP_IP
    stake = base * mult
    stake = min(stake, risk_cap or stake, smax, phase_cap)
    stake = max(cfg.MIN_STAKE, round(stake, 2))
    why = f"base={base}×{mult} bank={bank} cap={risk_cap:.2f}/{phase_cap:.2f} stake={stake:.2f}"
    return stake, why

def calc_greenup_stake(parent_side: str, entry_odds: float, parent_stake: float, hedge_odds: float):
    """True greening stake (flat profit across all runners)."""
    try:
        s = float(parent_stake) * float(entry_odds) / float(hedge_odds)
        return round(max(cfg.MIN_STAKE, s), 2)
    except Exception:
        return round(max(cfg.MIN_STAKE, float(parent_stake)), 2)
