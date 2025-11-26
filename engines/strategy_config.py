import os, yaml

CONFIG_PATH = os.environ.get("STRATEGY_CONFIG", "engines/daily_strategy_config.yaml")
DEFAULT_CONFIG = {
  "bands": {"active_min": 1.5, "active_max": 8.0, "passive_max": 12.0},
  "momentum": {"slope_cutoff": 0.03, "oc_momentum_ticks": 2, "strong_steam_ticks": 3},
  "direction": {"default": "L2B", "b2l_require_fav": True, "b2l_strong_only": True},
  "letters": {"A": {"allowed_bands": ["ACTIVE"], "target_ticks": 2, "base_stake": 1.0}},
  "caps": {"per_runner": 3, "rotation_sec": 30},
  "late_preoff": {"enabled": True, "window_sec": 60, "target_ticks": 2, "max_attempts_per_runner": 1},
  "risk": {"stake_scale_by_fav_rank": True, "stake_scale_by_slope": True, "micro_stake_fraction": 0.2},
  "plans": {"corridors": {"fav_drift_ticks": 4, "breakout_ticks": 6}, "triggers": {"fav_switch": True, "breakout": True}},
  "run_mode": "GATES_ONLY",
}

def load_strategy_config() -> dict:
    try:
        with open(CONFIG_PATH, "r") as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict):
            raise ValueError("Invalid config format")
        return cfg
    except Exception as e:
        print(f"[strategy_config] WARN: using DEFAULT_CONFIG ({e})")
        return DEFAULT_CONFIG

CONFIG = load_strategy_config()
