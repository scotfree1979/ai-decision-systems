#!/usr/bin/env python3
"""
engines/stoploss_engine.py — Stop-Loss math + equity engine (v7.9.5)

Pure logic, no order placement.

It encapsulates:

    • Direction law (LAY/BACK → what is “against us”)
    • Baseline stop-loss widths (ticks) for Legacy/Micro and risk modes
    • Market-local Stop-Loss Equity (SLEQ) from autoscalp_gui.db
    • Good/Bad trade ratio for widening_factor (Option B)
    • Monetary widening: how many extra ticks we can afford per market

Callers (Overwatcher, MicroScalper, Legacy) pass in:

    - marketId, selectionId
    - side, entry_odds, stake
    - is_micro (bool)
    - risk_mode: 'CONSERVATIVE' | 'BALANCED' | 'AGGRESSIVE'

They receive a StopLossState with:

    - should_stop      (bool)
    - ticks_against    (current ticks against entry, in the “wrong” direction)
    - stop_ticks       (final tick threshold for this parent)
    - stop_odds        (concrete stop price on the Betfair ladder)
    - loss_per_tick    (approx £ per tick against)
    - monetary_allowed (how much SLEQ is available to widen)
    - sleq, widening_factor, good_rate, bad_rate, debug info
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Dict, Any, Tuple

from engines.price_math import calculate_tick_distance, snap_to_tick, odds_plus_ticks
from engines.config_paths import autoscalp_db


# ──────────────────────────────────────────────────────────────
# Baseline widths (ticks) — LOCKED from sprint design
# ──────────────────────────────────────────────────────────────

# Legacy parents (LiveRouter strategies)
LEGACY_BASELINES = {
    "CONSERVATIVE": 3,
    "BALANCED":     4,
    "AGGRESSIVE":   5,
}

# MicroScalper parents
MICRO_BASELINES = {
    "CONSERVATIVE": 1,
    "BALANCED":     2,
    "AGGRESSIVE":   3,
}


@dataclass
class StopLossInputs:
    marketId: str
    selectionId: str
    side: str               # 'LAY' or 'BACK'
    entry_odds: float
    stake: float
    is_micro: bool = False
    risk_mode: str = "BALANCED"   # 'CONSERVATIVE' | 'BALANCED' | 'AGGRESSIVE'


@dataclass
class StopLossState:
    """Result of evaluating stop-loss for a single parent order."""
    # should we fire a STOPLOSS at or beyond current price?
    should_stop: bool
    # how far price has gone against the entry, in ticks
    ticks_against: int
    # effective tick threshold for this parent (after baselines + SLEQ + mode)
    stop_ticks: int
    # concrete stop price on the Betfair ladder (the “X” we will trade at)
    stop_odds: float
    # per-tick loss at this entry
    loss_per_tick: float
    # how many £ of loss we allow this parent to consume from SLEQ
    monetary_allowed: float
    # shared per-market stop-loss equity (good – bad)
    sleq: float
    # how much we widened/narrowed the baseline (1.0 = no change)
    widening_factor: float
    # diagnostic: good/bad trade rates in this market
    good_rate: float
    bad_rate: float
    # free-form debug info
    debug: Dict[str, Any]


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _baseline_ticks(is_micro: bool, risk_mode: str) -> int:
    """Return the configured baseline ticks for this family + risk mode."""
    rm = (risk_mode or "BALANCED").upper()
    table = MICRO_BASELINES if is_micro else LEGACY_BASELINES
    return int(table.get(rm, table["BALANCED"]))


def _open_orders_db() -> sqlite3.Connection:
    """
    Open autoscalp_gui.db for reading child outcomes.
    We keep this low-level on purpose to avoid DAL write-mode side effects.
    """
    path = autoscalp_db()
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def compute_market_equity(marketId: str) -> Tuple[float, float, float]:
    """
    Compute (SLEQ, good_rate, bad_rate) for this marketId.

      • SLEQ = sum(hedge net_pl) - sum(|stoploss net_pl|)
      • good_trade = child with exit_kind='HEDGE'
      • bad_trade  = child with exit_kind='STOPLOSS'

    All measurements are **per-market**, not global.
    """
    con = _open_orders_db()
    try:
        rows = con.execute(
            """
            SELECT exit_kind,
                   COALESCE(net_pl, realized_pnl, 0.0) AS pnl
              FROM orders
             WHERE marketId = ?
               AND role = 'CHILD'
               AND exit_status = 'matched'
               AND exit_kind IS NOT NULL
            """,
            (marketId,),
        ).fetchall()
    except Exception:
        con.close()
        return 0.0, 0.0, 0.0
    finally:
        try:
            con.close()
        except Exception:
            pass

    hedge_pnl = 0.0
    stoploss_loss = 0.0
    good = 0
    bad = 0

    for r in rows:
        kind = (r["exit_kind"] or "").upper()
        pnl = float(r["pnl"] or 0.0)
        if kind == "HEDGE":
            hedge_pnl += pnl
            good += 1
        elif kind == "STOPLOSS":
            # realised PnL is typically negative; we want loss as +ve
            stoploss_loss += abs(pnl)
            bad += 1

    sleq = hedge_pnl - stoploss_loss
    total = good + bad
    if total == 0:
        return sleq, 0.0, 0.0

    good_rate = good / total
    bad_rate = bad / total
    return sleq, good_rate, bad_rate


def _widening_factor(good_rate: float, bad_rate: float) -> float:
    """
    Option B (locked):

      • bad > good         → 0.5  (tighten)
      • roughly equal      → 1.0  (neutral)
      • good clearly ahead → 1.5  (widen)
    """
    if good_rate <= 0.0 and bad_rate <= 0.0:
        return 1.0

    if bad_rate > good_rate:
        return 0.5

    if abs(good_rate - bad_rate) < 0.10:
        return 1.0

    return 1.5


def _loss_per_tick(side: str, entry_odds: float, stake: float) -> float:
    """
    Approximate realised loss if price moves **1 tick** against us.

    Direction law (locked from your definition):

      • LAY  parent: profit if we BACK **higher** → “against” is BACK **lower**
      • BACK parent: profit if we LAY  **lower** → “against” is LAY  **higher**

    So:
      • For LAY:  against = 1 tick LOWER than entry
      • For BACK: against = 1 tick HIGHER than entry
    """
    s = (side or "").upper()
    if entry_odds <= 0.0 or stake <= 0.0:
        return 0.0

    e = snap_to_tick(entry_odds)

    if s == "LAY":
        # 1 tick lower is against us
        stop_odds = odds_plus_ticks(e, -1)
        loss = (e - stop_odds) * stake
    elif s == "BACK":
        # 1 tick higher is against us
        stop_odds = odds_plus_ticks(e, +1)
        loss = (stop_odds - e) * stake
    else:
        return 0.0

    return max(0.0, float(loss))


def _ticks_against(side: str, entry_odds: float, current_odds: float) -> int:
    """
    Effective tick movement **against** the parent, using the canonical rule:

        LAY  → “against” if odds FALL (down)
        BACK → “against” if odds RISE (up)

    We rely on engines.price_math.calculate_tick_distance(entry, current),
    which returns a signed integer tick distance.
    """
    s = (side or "").upper()
    if entry_odds <= 0.0 or current_odds <= 0.0:
        return 0

    diff = int(calculate_tick_distance(entry_odds, current_odds))

    if s == "LAY":
        # favourable if current >= entry (diff >= 0); bad if current < entry (diff < 0)
        return max(0, -diff)   # number of ticks down from entry
    elif s == "BACK":
        # favourable if current <= entry (diff <= 0); bad if current > entry (diff > 0)
        return max(0, diff)    # number of ticks up from entry
    else:
        return 0


def _stop_odds_for(side: str, entry_odds: float, stop_ticks: int) -> float:
    """
    Compute the concrete stop price (X) given an entry price and a tick threshold.

      • LAY  parent: stop is LOWER than entry by stop_ticks ticks
      • BACK parent: stop is HIGHER than entry by stop_ticks ticks
    """
    s = (side or "").upper()
    if entry_odds <= 0.0 or stop_ticks <= 0:
        return float(entry_odds)

    e = snap_to_tick(entry_odds)

    if s == "LAY":
        return float(odds_plus_ticks(e, -int(stop_ticks)))
    elif s == "BACK":
        return float(odds_plus_ticks(e, +int(stop_ticks)))
    else:
        return float(entry_odds)


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────

def evaluate_stoploss(inputs: StopLossInputs, current_odds: float) -> StopLossState:
    """
    Evaluate stop-loss state for a single parent order.

    This function is **pure** w.r.t. order placement; it only reads from
    autoscalp_gui.db to compute SLEQ and trade quality, and returns a
    StopLossState with everything the caller needs to decide whether to
    place a STOPLOSS child (and at what price).
    """
    side_u = (inputs.side or "").upper()
    entry_odds = float(inputs.entry_odds or 0.0)
    stake = float(inputs.stake or 0.0)
    current_odds = float(current_odds or 0.0)

    if entry_odds <= 0.0 or stake <= 0.0 or current_odds <= 0.0:
        # Cannot evaluate a meaningful stop-loss → fall back to baseline
        base = _baseline_ticks(inputs.is_micro, inputs.risk_mode)
        return StopLossState(
            should_stop=False,
            ticks_against=0,
            stop_ticks=base,
            stop_odds=float(entry_odds or 0.0),
            loss_per_tick=0.0,
            monetary_allowed=0.0,
            sleq=0.0,
            widening_factor=1.0,
            good_rate=0.0,
            bad_rate=0.0,
            debug={"reason": "invalid_inputs"},
        )

    # 1) Baseline ticks from risk mode + micro/legacy
    base_ticks = _baseline_ticks(inputs.is_micro, inputs.risk_mode)

    # 2) Market SLEQ + trade quality
    sleq, good_rate, bad_rate = compute_market_equity(inputs.marketId)
    w_factor = _widening_factor(good_rate, bad_rate)

    # 3) Monetary widening: how many extra ticks can SLEQ buy?
    loss_per_tick = _loss_per_tick(side_u, entry_odds, stake)
    allowed_loss = max(0.0, sleq) * w_factor

    if loss_per_tick > 0.0:
        max_extra_ticks = int(allowed_loss / loss_per_tick)
    else:
        max_extra_ticks = 0

    stop_ticks = max(1, base_ticks + max(0, max_extra_ticks))

    # 4) Actual movement against us at current price
    ticks_against = _ticks_against(side_u, entry_odds, current_odds)
    stop_odds = _stop_odds_for(side_u, entry_odds, stop_ticks)
    should_stop = ticks_against >= stop_ticks

    debug: Dict[str, Any] = {
        "base_ticks":      base_ticks,
        "sleq":            sleq,
        "good_rate":       good_rate,
        "bad_rate":        bad_rate,
        "widening_factor": w_factor,
        "allowed_loss":    allowed_loss,
        "max_extra_ticks": max_extra_ticks,
        "ticks_against":   ticks_against,
        "stop_ticks":      stop_ticks,
        "stop_odds":       stop_odds,
        "side":            side_u,
        "entry_odds":      entry_odds,
        "current_odds":    current_odds,
        "stake":           stake,
    }

    return StopLossState(
        should_stop=should_stop,
        ticks_against=ticks_against,
        stop_ticks=stop_ticks,
        stop_odds=stop_odds,
        loss_per_tick=loss_per_tick,
        monetary_allowed=allowed_loss,
        sleq=sleq,
        widening_factor=w_factor,
        good_rate=good_rate,
        bad_rate=bad_rate,
        debug=debug,
    )
