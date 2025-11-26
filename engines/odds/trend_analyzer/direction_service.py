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

# === direction decisions (lay-first bias) ===
from dataclasses import dataclass
from typing import Optional, Dict, Any
from engines.odds.trend_analyzer.config import (
    L2B_DOMINANT, STEAM_MIN_MOVE_RATIO, STEAM_MIN_MOVE_TO, STEAM_LOOKBACK_SEC
)

@dataclass(frozen=True)
class BiasDecision:
    allow_l2b: bool
    allow_b2l: bool
    b2l_hedge_only: bool
    reason: str

def _is_significant_steam(anchor: float, price: float) -> bool:
    if anchor <= 0 or price <= 0:
        return False
    drop_ratio = price <= anchor * STEAM_MIN_MOVE_RATIO
    abs_ok = price <= STEAM_MIN_MOVE_TO
    return drop_ratio and abs_ok

def _context_confirms(trend: Dict[str, Any]) -> bool:
    try:
        window = float(trend.get("window_sec", 0))
        steam_score = float(trend.get("steam_score", 0))
        drift_score = float(trend.get("drift_score", 0))
    except Exception:
        return False
    if window < STEAM_LOOKBACK_SEC * 0.6:
        return False
    return steam_score >= max(0.65, drift_score + 0.20)

def choose_bias(*, anchor: float, price: float, trend: Optional[Dict[str, Any]] = None) -> BiasDecision:
    """
    Lay-to-Back dominant by default.
    Back-to-Lay is allowed only if:
      1) significant steam (big drop from anchor),
      2) context confirms steam dominance, and
      3) the move is at least ~3 ticks (range gate).
    Even then, B2L is hedge-only.
    """
    # Keep your global mode check semantics
    if not L2B_DOMINANT:
        return BiasDecision(
            allow_l2b=True,
            allow_b2l=True,
            b2l_hedge_only=False,
            reason="balanced_mode"
        )

    # --- local helpers -------------------------------------------------------
    def _tick_step(o: float) -> float:
        # Betfair tick ladder approximation
        if o < 2.0:    return 0.01
        if o < 3.0:    return 0.02
        if o < 4.0:    return 0.05
        if o < 6.0:    return 0.10
        if o < 10.0:   return 0.20
        if o < 20.0:   return 0.50
        if o < 30.0:   return 1.00
        if o < 50.0:   return 2.00
        if o < 100.0:  return 5.00
        return 10.00

    # Robust anchor/price parsing
    try:
        a = float(anchor)
        p = float(price)
    except (TypeError, ValueError):
        # No reliable inputs → default L2B and ignore B2L
        return BiasDecision(
            allow_l2b=True,
            allow_b2l=False,
            b2l_hedge_only=True,
            reason="no_anchor_or_price"
        )

    # --- core signals --------------------------------------------------------
    sig = _is_significant_steam(a, p)       # your existing significance test
    ctx_ok = _context_confirms(trend or {}) # your existing context test

    # Range/momentum gate (~3 ticks between anchor and current price)
    step = _tick_step((a + p) / 2.0)
    ticks_moved = 0.0 if step <= 0 else abs(p - a) / step
    RANGE_TICKS_MIN = 3.0

    # If steam is strong, context agrees, the move is >= 3 ticks AND price < anchor → allow B2L (hedge-only)
    if sig and ctx_ok and ticks_moved >= RANGE_TICKS_MIN and p < a:
        return BiasDecision(
            allow_l2b=True,
            allow_b2l=True,
            b2l_hedge_only=True,
            reason=f"significant_steam Δ≈{ticks_moved:.1f}ticks (anchor={a}, price={p})"
        )

    # Otherwise, L2B-first and ignore shallow/uncertain steam
    return BiasDecision(
        allow_l2b=True,
        allow_b2l=False,
        b2l_hedge_only=True,
        reason="lay_first_ignore_shallow_or_no_steam"
    )
# === end direction decisions ===




# =========================================
# 📍 TARGET: engines/odds/trend_analyzer/direction_service.py
# 🔎 SEARCH: def direction_for(tick: MarketTick, view_now: Optional[MarketView]) -> DirectionDecision:
# =========================================
def direction_for(tick: MarketTick, view_now: Optional[MarketView]) -> DirectionDecision:
    """Primary entry point. view_now may be None if market context not available; XO disabled then."""
    cfg = load_config()
    global _hyst, _stale, _xo, _conf
    if _hyst is None:
        _hyst = Hysteresis(cfg.hold_ms, cfg.delta_tau_flip)
    if _stale is None:
        _stale = StaleTracker(cfg.hard_stale_missing)
# =========================================
# 📍 TARGET: engines/odds/trend_analyzer/direction_service.py
# 🔎 SEARCH: _xo = XODetector(cfg.xo_epsilon, cfg.xo_hold_ms)
# =========================================
    if _xo is None:
        _xo = XODetector(
            cfg.xo_epsilon,
            cfg.xo_hold_ms,
            cfg.fav_cluster_eps,
            cfg.bif_hold_ms,
            cfg.fav_flip_hold_ms,
        )

    if _conf is None:
        _conf = ConfidenceCombiner(cfg.weight_slope, cfg.weight_boundary, cfg.weight_xo, cfg.weight_liq)

    key = _key(tick.market_id, tick.selection_id)
    rs = _range.setdefault(key, RangeState())
    ve = _vol.setdefault(key, VolEstimator())

    # Stale policy
    expected_oc_present = (tick.best_back_odds is not None)
    streak = _stale.note(tick.market_id, tick.selection_id, expected_oc_present)

    # Basis
    b = Basis()
    phase = _phase_from_oc(tick.oc_index)
    suff = _sufficiency(tick.oc_index)
    b.phase = phase
    b = apply_stale_to_basis(b, streak, cfg.hard_stale_missing)

    # Hard stale or no odds → ABSTAIN
    if b.health == "HARD_STALE" or tick.best_back_odds is None:
        b.events.append("HALT_TRADING" if b.health == "HARD_STALE" else "NO_ODDS")
        return DirectionDecision(direction="ABSTAIN", confidence=0.0, basis=b, sufficiency=suff)  # type: ignore[arg-type]

    # Update range & vol
    rs.update(tick.best_back_odds)
    b = write_range_to_basis(b, rs, tick.best_back_odds)
    regime = _regime(ve, tick.best_back_odds)
    b.vol_regime = regime

    # Band label (informational)
    if tick.oc_index <= 4:
        b.band_used = f"OC{cfg.band_build_up[0]}-OC{cfg.band_build_up[1]}"
    elif tick.oc_index <= 7:
        b.band_used = f"OC{cfg.band_pre_off[0]}-OC{cfg.band_pre_off[1]}"
    else:
        b.band_used = f"OC{max(8, tick.oc_index-cfg.band_in_play_width+1)}-OC{tick.oc_index}"

    # Slope from anchor→current
    d_anchor = safe_delta(tick.anchor_odds, tick.best_back_odds)
    slope_norm = norm_slope(d_anchor, tick.best_back_odds, tick.spread)
    back_from_slope = clamp(-slope_norm, 0.0, 1.0)
    lay_from_slope  = clamp(+slope_norm, 0.0, 1.0)
    b.slope = float(slope_norm)
    b.slope_window = 1

    # Boundary events → directional scores
    _bound.note_move(d_anchor)
    b.events.extend(_bound.detect(tick.best_back_odds, b.range_low, b.range_high))
    boundary_back = 0.0
    boundary_lay  = 0.0
    if "BOUNCE_HIGH" in b.events:   # mean-revert down from high → BACK
        boundary_back += 0.6
    if "BOUNCE_LOW" in b.events:    # mean-revert up from low → LAY
        boundary_lay  += 0.6
    if "BREAKOUT_UP" in b.events:   # continuation up → LAY
        boundary_lay  += 0.6
    if "BREAKOUT_DOWN" in b.events: # continuation down → BACK
        boundary_back += 0.6

    # XO / favorite events (directional)
    xo_score_back = 0.0
    xo_score_lay  = 0.0
    if view_now is not None:
        ev = _xo.detect(tick.selection_id, _last_view.get(tick.market_id), view_now)
        b.events.extend(ev)
        if any(e == "XO_FAV_DOWN" for e in ev):
            xo_score_back += 0.7
        if any(e == "XO_FAV_UP" for e in ev):
            xo_score_lay  += 0.7
        if "BIF_SELF" in ev:
            xo_score_back += 0.3
        if "FC" in ev:
            xo_score_back *= 0.8
            xo_score_lay  *= 0.8
        _last_view[tick.market_id] = view_now

    # Liquidity factor
    liq_score = 0.0 if tick.liquidity_flag == "THIN" else (0.1 if tick.liquidity_flag == "NORMAL" else 0.2)

    # Compose confidences for both sides (directional boundary/xo)
    back_conf, back_parts = _conf.combine(back_from_slope, boundary_back, xo_score_back, liq_score)
    lay_conf,  lay_parts  = _conf.combine(lay_from_slope,  boundary_lay,  xo_score_lay,  liq_score)

    # Hysteresis choose side
    side, _ = _hyst.update(key, regime, back_conf, lay_conf, dt_ms=250)
    hs = _hyst.snapshot(key)
    b.hysteresis_state   = hs.state
    b.hold_ms_remaining  = hs.hold_ms_left

    # Final chosen confidence + breakdown
    conf = back_conf if side == "BACK" else lay_conf
    b.confidence_breakdown = (back_parts if side == "BACK" else lay_parts)

    # Edge-odds guard
    if tick.best_back_odds is not None and (tick.best_back_odds <= cfg.min_odds or tick.best_back_odds >= cfg.max_odds):
        conf *= 0.8
        b.events.append("EDGE_ODDS_GUARD")

    # Degraded mode haircut (still return a side; letters will gate placement)
    if b.health == "DEGRADED":
        conf *= 0.85

    return DirectionDecision(direction=side, confidence=conf, basis=b, sufficiency=suff)  # type: ignore[arg-type]

# === PATCH: direction fallback helpers (drop in anywhere at module top-level) ===

def choose_direction(anchor: float | None,
                     price: float | None,
                     slope_ppm: float | None = None,
                     default: str = "LAY->BACK") -> str:
    """
    Deterministic direction with no 'None' ever:
      - anchor vs price primary
      - slope_ppm secondary
      - final fallback = default
    """
    try:
        if anchor is not None and price is not None:
            if float(price) > float(anchor):   # DRIFT (odds↑)
                return "LAY->BACK"
            if float(price) < float(anchor):   # STEAM (odds↓)
                return "BACK->LAY"
    except Exception:
        pass
    try:
        if slope_ppm is not None:
            s = float(slope_ppm)
            if s > 0:  return "LAY->BACK"
            if s < 0:  return "BACK->LAY"
    except Exception:
        pass
    return default


def ensure_direction_in_plan(plan: dict, ctx: dict) -> str:
    """
    Make plan['direction'] concrete; also normalize plan['edge'] ('L2B'/'B2L').
    """
    # 1) honor existing if usable
    d = (plan.get("direction") or ctx.get("direction"))
    if isinstance(d, str) and d and d.upper() not in ("NONE", "NULL"):
        d = d.upper()
        plan["direction"] = d
        plan["edge"] = "L2B" if d.startswith("LAY") else "B2L"
        return d

    # Enforce B2L allow-list by letter (BTL is rare; L2B is default)
    letter = str((plan.get("letter") or ctx.get("letter") or "")[:1]).upper()
    if str(plan.get("direction","")).upper() == "BACK->LAY":
        if letter not in {"B","G","X"}:
            # Normalize to drift instead of outright returning None,
            # so upstream can still record the decision cleanly.
            plan["direction"] = "LAY->BACK"
            plan["edge"] = "L2B"
            plan["why"] = (plan.get("why","") + " | dirsvc:b2l_block_non_whitelist").strip()


    # 2) derive from anchor/price/slope
    anchor = plan.get("anchor_price") or ctx.get("anchor_price") or plan.get("entry_odds") or ctx.get("entry_odds")
    price  = plan.get("px") or plan.get("ltp") or plan.get("price") or ctx.get("odds") or ctx.get("ltp") or ctx.get("price")
    slope  = ctx.get("slope_ppm") or ctx.get("slope_per_min")
    try:
        dd = choose_direction(
            float(anchor) if anchor is not None else None,
            float(price)  if price  is not None else None,
            float(slope)  if slope  is not None else None,
            default="LAY->BACK"
        )
    except Exception:
        dd = "LAY->BACK"

    plan["direction"] = dd
    plan["edge"] = "L2B" if dd.startswith("LAY") else "B2L"
    return dd

    # Enforce B2L allow-list AFTER derivation as well (belt-and-braces)
    _letter = str((plan.get("letter") or ctx.get("letter") or "")[:1]).upper()
    if plan["direction"].upper() == "BACK->LAY" and _letter not in {"B","G","X"}:
        plan["direction"] = "LAY->BACK"
        plan["edge"] = "L2B"
        plan["why"] = (plan.get("why","") + " | dirsvc:b2l_block_postderive").strip()



