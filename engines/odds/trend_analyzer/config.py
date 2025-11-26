from __future__ import annotations
from .types import AnalyzerConfig

# === bias / cap / rotation (lay-first settings) ===

# Lay-first architecture toggle
L2B_DOMINANT: bool = True

# Steam gating: ignore shallow steam; only act on significant moves
# Example: anchor 10 -> 8 (ignore) but 10 -> 5 (significant).
STEAM_MIN_MOVE_RATIO: float = 0.50   # require ≥50% drop from anchor (10→5)
STEAM_MIN_MOVE_TO: float = 5.0       # also require odds ≤ 5.0
STEAM_LOOKBACK_SEC: int = 300        # lookback window for trend confirmation


DEFAULT_CONFIG = AnalyzerConfig(
    tau_place_low={"LOW":0.25, "MED":0.30, "HIGH":0.35},
    tau_place_mid={"LOW":0.50, "MED":0.55, "HIGH":0.60},
    tau_place_high={"LOW":0.65, "MED":0.70, "HIGH":0.75},
    delta_tau_flip={"LOW":0.15, "MED":0.20, "HIGH":0.30},
    hold_ms={"LOW":300, "MED":500, "HIGH":800},
    weight_slope=0.45,
    weight_boundary=0.25,
    weight_xo=0.20,
    weight_vol=0.0,   # folded into thresholds primarily
    weight_liq=0.10,
    hard_stale_missing=5,
    band_build_up=(1,4),    # OC1-OC4
    band_pre_off=(5,7),     # OC5-OC7
    band_in_play_width=4,   # sliding OCk..OCk+3
    xo_epsilon=0.02,
    xo_hold_ms=300,
    bif_hold_ms=800,
    fav_flip_hold_ms=600,
    fav_cluster_eps=0.10,
    min_odds=1.5,
    max_odds=8.0,
)

_active_config = DEFAULT_CONFIG

def load_config(override: dict | None = None) -> AnalyzerConfig:
    global _active_config
    if override:
        # shallow override into dataclass
        data = _active_config.__dict__.copy()
        data.update(override)
        _active_config = AnalyzerConfig(**data)
    return _active_config
