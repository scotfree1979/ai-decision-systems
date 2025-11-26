from __future__ import annotations
from typing import Optional
from .types import DirectionDecision, Basis, MarketTick, MarketView, VolRegime
from .types import Side
from .config import load_config
from .utils import safe_delta, norm_slope, clamp
from .stale_policy import StaleTracker, apply_stale_to_basis
from .range_builder import RangeState, write_range_to_basis
from .volatility import VolEstimator
from .boundary import BoundaryDetector
from .events import XODetector
from .hysteresis import Hysteresis
from .confidence import ConfidenceCombiner

# Simple in-memory state per runner
_range = {}
_vol = {}
_hyst: Optional[Hysteresis] = None
_stale: Optional[StaleTracker] = None
_xo: Optional[XODetector] = None
_bound = BoundaryDetector()
_conf: Optional[ConfidenceCombiner] = None
_last_view = {}


def _key(market_id: str, selection_id: int) -> tuple[str,int]:
    return (market_id, selection_id)


def _phase_from_oc(oc_index: int) -> str:
    if oc_index <= 4:
        return "BUILD_UP"
    if oc_index <= 7:
        return "PRE_OFF"
    return "IN_PLAY"


def _sufficiency(oc_index: int) -> str:
    if oc_index <= 0:
        return "S0"
    if oc_index <= 3:
        return "S1"
    if oc_index <= 7:
        return "S2"
    return "S3"


def _regime(vol_est: VolEstimator, odds: Optional[float]) -> VolRegime:
    return vol_est.update(odds)


def direction_for(tick: MarketTick, view_now: Optional[MarketView]) -> DirectionDecision:
    """Primary entry point. view_now may be None if market context not available; XO disabled then."""
    cfg = load_config()
    global _hyst, _stale, _xo, _conf
    if _hyst is None:
        _hyst = Hysteresis(cfg.hold_ms, cfg.delta_tau_flip)
    if _stale is None:
        _stale = StaleTracker(cfg.hard_stale_missing)
    if _xo is None:
        _xo = XODetector(cfg.xo_epsilon, cfg.xo_hold_ms)
    if _conf is None:
        _conf = ConfidenceCombiner(cfg.weight_slope, cfg.weight_boundary, cfg.weight_xo, cfg.weight_liq)

    key = _key(tick.market_id, tick.selection_id)
    rs = _range.setdefault(key, RangeState())
    ve = _vol.setdefault(key, VolEstimator())

    # Stale policy: assume each call corresponds to an expected OC for simplicity (ticker will pass flags later)
    expected_oc_present = (tick.best_back_odds is not None)
    streak = _stale.note(tick.market_id, tick.selection_id, expected_oc_present)

    # Build basis
    b = Basis()
    b.phase = _phase_from_oc(tick.oc_index)
    b.sufficiency = _sufficiency(tick.oc_index)  # type: ignore[attr-defined]
    b = apply_stale_to_basis(b, streak, cfg.hard_stale_missing)

    # Hard stale or no odds: ABSTAIN
    if b.health == "HARD_STALE" or tick.best_back_odds is None:
        b.events.append("HALT_TRADING" if b.health == "HARD_STALE" else "NO_ODDS")
        return DirectionDecision(direction="ABSTAIN", confidence=0.0, basis=b, sufficiency=b.sufficiency)  # type: ignore[arg-type]

    # Update range & vol
    rs.update(tick.best_back_odds)
    b = write_range_to_basis(b, rs, tick.best_back_odds)
    regime = _regime(ve, tick.best_back_odds)
    b.vol_regime = regime

    # Choose band label for basis only (MVP: not computing explicit OC band deltas here)
    if tick.oc_index <= 4:
        b.band_used = f"OC{cfg.band_build_up[0]}-OC{cfg.band_build_up[1]}"
    elif tick.oc_index <= 7:
        b.band_used = f"OC{cfg.band_pre_off[0]}-OC{cfg.band_pre_off[1]}"
    else:
        b.band_used = f"OC{max(8, tick.oc_index-cfg.band_in_play_width+1)}-OC{tick.oc_index}"

    # Slope score from anchor->current (available from tick)
    d_anchor = safe_delta(tick.anchor_odds, tick.best_back_odds)
    slope_norm = norm_slope(d_anchor, tick.best_back_odds, tick.spread)
    # map slope to BACK/ LAY scores [0..1]
    back_from_slope = clamp(-slope_norm, 0.0, 1.0)
    lay_from_slope = clamp(+slope_norm, 0.0, 1.0)
    b.slope = float(slope_norm)
    b.slope_window = 1

    # Boundary events
    _bound.note_move(d_anchor)
    b.events.extend(_bound.detect(tick.best_back_odds, b.range_low, b.range_high))
    boundary_score = 0.0
    if "BOUNCE_HIGH" in b.events:
        boundary_score += 0.6  # favors BACK
    if "BOUNCE_LOW" in b.events:
        boundary_score += 0.6  # favors LAY if we encode sign below
    if "BREAKOUT_UP" in b.events:
        boundary_score += 0.6  # favors LAY
    if "BREAKOUT_DOWN" in b.events:
        boundary_score += 0.6  # favors BACK

    # XO / favorite events (requires market view)
    xo_score_back = 0.0
    xo_score_lay = 0.0
    if view_now is not None:
        ev = _xo.detect(tick.selection_id, _last_view.get(tick.market_id), view_now)
        b.events.extend(ev)
        if any(e == "XO_FAV_DOWN" for e in ev):
            xo_score_back += 0.7
        if any(e == "XO_FAV_UP" for e in ev):
            xo_score_lay += 0.7
        if "BIF_SELF" in ev:
            xo_score_back += 0.3
        if "FC" in ev:
            xo_score_back *= 0.8; xo_score_lay *= 0.8
        _last_view[tick.market_id] = view_now

    # Liquidity/scaling (simple)
    liq_score = 0.0 if tick.liquidity_flag == "THIN" else (0.1 if tick.liquidity_flag == "NORMAL" else 0.2)

    # Compose confidences for both sides
    comb = ConfidenceCombiner(cfg.weight_slope, cfg.weight_boundary, cfg.weight_xo, cfg.weight_liq)
    back_conf, back_break = comb.combine(back_from_slope, boundary_score, xo_score_back, liq_score)
    lay_conf, lay_break = comb.combine(lay_from_slope, boundary_score, xo_score_lay, liq_score)

    # Hysteresis choose side
    side, diff = _hyst.update(key, regime, back_conf, lay_conf, dt_ms=250)  # dt approx; ticker will pass real later
    hs = _hyst.snapshot(key)
    b.hysteresis_state = hs.state
    b.hold_ms_remaining = hs.hold_ms_left

    # Final confidence = max of chosen side
    conf = back_conf if side == "BACK" else lay_conf
    b.confidence_breakdown = (back_break if side == "BACK" else lay_break)

    # Odds-guard penalties at edges
    if tick.best_back_odds is not None and (tick.best_back_odds <= cfg.min_odds or tick.best_back_odds >= cfg.max_odds):
        conf *= 0.8
        b.events.append("EDGE_ODDS_GUARD")

    # Placement gating by regime (use mid threshold for general analyzer output; letters will map to tiers)
    tau_mid = load_config().tau_place_mid[regime]
    if b.health == "DEGRADED":
        # degrade: only A should consider; we still output direction but note degraded
        conf *= 0.85

    # Sides ABSTAIN if confidence below very low floor (so plans can decide per-letter)
    if conf < 0.10:
        return DirectionDecision(direction="ABSTAIN", confidence=conf, basis=b, sufficiency=b.sufficiency)  # type: ignore[arg-type]

    return DirectionDecision(direction=side, confidence=conf, basis=b, sufficiency=b.sufficiency)  # type: ignore[arg-type]
