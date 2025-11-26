from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Literal

Side = Literal["BACK", "LAY", "ABSTAIN"]
VolRegime = Literal["LOW", "MED", "HIGH"]
Phase = Literal["BUILD_UP", "PRE_OFF", "IN_PLAY"]

@dataclass
class MarketTick:
    ts: float  # unix seconds
    market_id: str
    selection_id: int
    oc_index: int  # OC0..OC20+
    best_back_odds: Optional[float]
    anchor_odds: Optional[float]
    # Optional context
    in_play: bool = False
    spread: Optional[float] = None
    liquidity_flag: Literal["THIN", "NORMAL", "THICK"] = "NORMAL"

@dataclass
class RunnerView:
    selection_id: int
    best_back_odds: Optional[float]
    traded_volume: Optional[float] = None

@dataclass
class MarketView:
    ts: float
    market_id: str
    runners: List[RunnerView]
    anchor_favorite_id: Optional[int]

@dataclass
class Basis:
    band_used: str = ""
    slope: float = 0.0
    slope_window: int = 0
    vol_regime: VolRegime = "MED"
    range_low: Optional[float] = None
    range_high: Optional[float] = None
    pos_in_range: Optional[float] = None
    distance_to_boundary: Optional[float] = None
    events: List[str] = field(default_factory=list)
    hysteresis_state: str = "NEUTRAL"
    hold_ms_remaining: int = 0
    flip_count_5m: int = 0
    liquidity_flag: str = "NORMAL"
    phase: Phase = "BUILD_UP"
    coverage: float = 0.0
    aoe: float = 0.0
    oc_missing_streak: int = 0
    health: Literal["GREEN", "DEGRADED", "HARD_STALE", "RECOVERING"] = "GREEN"
    confidence_breakdown: Dict[str, float] = field(default_factory=dict)

@dataclass
class DirectionDecision:
    direction: Side
    confidence: float
    basis: Basis
    sufficiency: Literal["S0","S1","S2","S3"]

@dataclass
class AnalyzerConfig:
    # thresholds per regime
    tau_place_low: Dict[VolRegime, float]
    tau_place_mid: Dict[VolRegime, float]
    tau_place_high: Dict[VolRegime, float]
    delta_tau_flip: Dict[VolRegime, float]
    hold_ms: Dict[VolRegime, int]
    # event weights
    weight_slope: float
    weight_boundary: float
    weight_xo: float
    weight_vol: float
    weight_liq: float
    # stale policy
    hard_stale_missing: int
    # bands
    band_build_up: Tuple[int,int]
    band_pre_off: Tuple[int,int]
    band_in_play_width: int
    # debounces
    xo_epsilon: float
    xo_hold_ms: int
    bif_hold_ms: int
    fav_flip_hold_ms: int
    fav_cluster_eps: float
    # odds guards
    min_odds: float
    max_odds: float
