# ─────────────────────────────────────────────────────────────────────────────
# Strategy registry (Plan §3.1 / 1d)
# Central place to toggle configs and which strategies are active.
# ─────────────────────────────────────────────────────────────────────────────
from __future__ import annotations
from typing import List

from .og_strategy import OGStrategy
from .ladder_strategy import LadderStrategy
from .btl_scout import decide as BTL_SCOUT
from .btl_aggr  import decide as BTL_AGGR
from .og_bias   import decide as OG_BIAS

# ---- Mastery integration (wrappers) ----
from engines.mastery import mastery_policy as mp

def _mp(name: str):
    def fn(ctx: dict) -> dict:
        c = dict(ctx)
        c['strategy_name'] = name  # lets Mastery infer the letter if needed
        return mp.plan_for_strategy(name, c)
    fn.__name__ = name
    return fn



# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/registry.py
# 🔎 SEARCH: ^from __future__ import annotations
# ⛏️ ACTION: replace the file contents or insert the following block


from typing import Callable, Dict, List, Tuple

# --- Optional function imports (graceful if missing) -------------------------
def _maybe(name: str, mod: str, obj: str) -> Callable[[dict], dict] | None:
    try:
        m = __import__(mod, fromlist=[obj])
        fn = getattr(m, obj, None)
        if callable(fn):
            fn.__name__ = name
            return fn  # must be callable(ctx)->plan
    except Exception:
        pass
    return None

BLUEPRINTS = _maybe("BLUEPRINTS", "engines.decision_engine.strategies.blueprints_strategy", "decide")

# Legacy / bias families
OG_FN      = _maybe("OG_STRATEGY",       "engines.decision_engine.strategies.og_strategy",       "decide")
LADDER_FN  = _maybe("LADDER_STRATEGY",   "engines.decision_engine.strategies.ladder_strategy",   "decide")

# PRE families


XOVER_FN   = _maybe("S4_CROSSOVER",      "engines.decision_engine.strategies.crossover",         "decide")
BRK_FN     = _maybe("S5_BREAKOUT",       "engines.decision_engine.strategies.range_breakout",    "decide")
STEAM_FN   = _maybe("S6_STEAM_FADE",     "engines.decision_engine.strategies.steam_fade",        "decide")
BTL_SCOUT  = _maybe("BTL_SCOUT",         "engines.decision_engine.strategies.btl_scout",         "decide")
BTL_AGGR   = _maybe("BTL_AGGR",          "engines.decision_engine.strategies.btl_aggr",          "decide")
ALWAYS_ON  = _maybe("ALWAYS_ON",         "engines.decision_engine.strategies.always_on",         "decide")

# COOL-OFF
MLM = _maybe("MLM", "engines.decision_engine.strategies.mlm", "decide")

  
# IN-PLAY families
IP1_FN     = _maybe("IP1_SHOCK_DRIFT",   "engines.decision_engine.strategies.inplay",            "decide_shock_drift")
IP2_FN     = _maybe("IP2_TIRED_LEADER",  "engines.decision_engine.strategies.inplay",            "decide_tired_leader")
IP3_FN     = _maybe("IP3_CLOSE_FINISH",  "engines.decision_engine.strategies.inplay",            "decide_close_finish")
IP4_FN     = _maybe("IP4_FENCE_ERROR",   "engines.decision_engine.strategies.inplay",            "decide_fence_error")
IP5_FN     = _maybe("IP5_COLLAPSE_FADE", "engines.decision_engine.strategies.inplay",            "decide_collapse_fade")

# --- Public exports ----------------------------------------------------------
ORDER: List[Tuple[str, Callable[[dict], dict] | None]] = [
    # 14 strategies total
    ("BLUEPRINTS",         _mp("BLUEPRINTS")),     # P — run first (overarching)
    ("OG_STRATEGY",        _mp("OG_FN")),          # Legacy S
    ("LADDER_STRATEGY",    _mp("LADDER_FN")),      # Ladder
    ("S4_CROSSOVER",       _mp("XOVER_FN")),       # X
    ("S5_BREAKOUT",        _mp("BRK_FN")),         # R (range breakout family)
    ("S6_STEAM_FADE",      _mp("STEAM_FN")),       # F
    ("IP1_SHOCK_DRIFT",    _mp("IP1_FN")),         # I
    ("IP2_TIRED_LEADER",   _mp("IP2_FN")),         # T
    ("IP3_CLOSE_FINISH",   _mp("IP3_FN")),         # C
    ("IP4_FENCE_ERROR",    _mp("IP4_FN")),         # E
    ("IP5_COLLAPSE_FADE",  _mp("IP5_FN")),         # K
    ("ALWAYS_ON",          _mp("ALWAYS_ON")),      # A
    ("BTL_SCOUT",          _mp("BTL_SCOUT")),      # B
    ("BTL_AGGR",           _mp("BTL_AGGR")),       # G
    # === PATCH 2 START (ORDER list add MLM) ===
    ("MLM",                MLM),                   # M — cool-off / liability manager
    # === PATCH 2 END ===

]

# Enable everything by default; runner logic can filter later
ENABLED: Dict[str, bool] = {name: True for (name, _fn) in ORDER}

# Map names to family letters (used by lanes/rulebook)
STRAT_CODE: Dict[str, str] = {
    "BLUEPRINTS":         "P",
    "OG_STRATEGY":        "S",
    "LADDER_STRATEGY":    "L",
    "MLM":                "M",
    "ALWAYS_ON":          "A",
    "BTL_SCOUT":          "B",
    "BTL_AGGR":           "G",
    "S4_CROSSOVER":       "X",
    "S5_BREAKOUT":        "R",
    "S6_STEAM_FADE":      "F",
    "IP1_SHOCK_DRIFT":    "I",
    "IP2_TIRED_LEADER":   "T",
    "IP3_CLOSE_FINISH":   "C",
    "IP4_FENCE_ERROR":    "E",
    "IP5_COLLAPSE_FADE":  "K",
}
# === PATCH END ===

from engines.decision_engine.strategies.common import GateSpec

# Initial knobs (tune later)
OG_CFG = {
    "min_price": 2.2,
    "max_price": 7.0,
    "stake": 2.0,
    "hedge_ticks": 3,
    "tick_size": 0.01,
}

LADDER_CFG = {
    "min_price": 2.2,
    "max_price": 9.0,
    "tick_size": 0.01,
    "stake": 2.0,
    "hedge_ticks": 3,      # child BACK is +ticks above parent (LTB)
    "num_rungs": 3,
    "rung_gap_ticks": 2,
    "max_liability": 8.0,  # per runner
}

A_CFG = {
    "min_price": 2.2,
    "max_price": 7.0,
    "stake": 2.0,
    "hedge_ticks": 3,
    "tick_size": 0.01,
}

# Import Always-On strategy class (graceful if missing)
try:
    from .always_on import AlwaysOnStrategy
except Exception:
    AlwaysOnStrategy = None



# Per-strategy placement policy
STRAT_CFG = {
    # PRE families (child should LAPSE at the off)
    "BLUEPRINTS":     {"parent_persistence": "PERSIST",  "child_persistence": "PERSIST"},
    "S4_CROSSOVER":     {"parent_persistence": "LAPSE",  "child_persistence": "LAPSE"},
    "S5_BREAKOUT":      {"parent_persistence": "LAPSE",  "child_persistence": "LAPSE"},
    "S6_STEAM_FADE":    {"parent_persistence": "LAPSE",  "child_persistence": "LAPSE"},
    "LADDER_STRATEGY":  {"parent_persistence": "LAPSE",  "child_persistence": "LAPSE"},

    # IP families (in-play only; both can persist safely)
    "BLUEPRINTS":       {"parent_persistence": "PERSIST",  "child_persistence": "PERSIST"},
    "IP1_SHOCK_DRIFT":  {"parent_persistence": "PERSIST","child_persistence": "PERSIST"},
    "IP2_TIRED_LEADER": {"parent_persistence": "PERSIST","child_persistence": "PERSIST"},
    "IP3_CLOSE_FINISH": {"parent_persistence": "PERSIST","child_persistence": "PERSIST"},
    "IP4_FENCE_ERROR":  {"parent_persistence": "PERSIST","child_persistence": "PERSIST"},
    "IP5_COLLAPSE_FADE":{"parent_persistence": "PERSIST","child_persistence": "PERSIST"},
}

from engines.decision_engine.strategies.common import GateSpec

STRAT_GATES = {
    # A/P: any phase (time handled by universal gate: none for A/P)
    "ALWAYS_ON":    GateSpec(code_letter="A", phase="ANY"),
    "BLUEPRINTS":   GateSpec(code_letter="P", phase="ANY"),

    # PRE families (they’ll pass the universal 0..60m check)
    "OG_STRATEGY":      GateSpec(code_letter="S", phase="PRE"),
    "LADDER_STRATEGY":  GateSpec(code_letter="L", phase="PRE"),
    "BTL_SCOUT":        GateSpec(code_letter="B", phase="PRE"),
    "BTL_AGGR":         GateSpec(code_letter="G", phase="PRE"),
    "S4_CROSSOVER":     GateSpec(code_letter="X", phase="PRE"),
    "S5_BREAKOUT":      GateSpec(code_letter="R", phase="PRE"),
    "S6_STEAM_FADE":    GateSpec(code_letter="F", phase="PRE"),

    # COOL-OFF
    # === PATCH 3 START (STRAT_GATES for MLM) ===
    "MLM": GateSpec(
        code_letter="M",
        phase="PRE",
        tto_min_max=(0.0, 3.0),   # 3-minute cool-off window only
        odds_band=None,
        min_liq_back=0.0, min_liq_lay=0.0,
        min_traded_amt_60s=0.0,
        max_tape_age_s=90.0,
        cadence_sec=1.0,
        cooldown_sec=10.0
    ),
    # === PATCH 3 END ===


    # IP families
    "IP1_SHOCK_DRIFT":   GateSpec(code_letter="I", phase="IN_PLAY"),
    "IP2_TIRED_LEADER":  GateSpec(code_letter="T", phase="IN_PLAY"),
    "IP3_CLOSE_FINISH":  GateSpec(code_letter="C", phase="IN_PLAY"),
    "IP4_FENCE_ERROR":   GateSpec(code_letter="E", phase="IN_PLAY"),
    "IP5_COLLAPSE_FADE": GateSpec(code_letter="K", phase="IN_PLAY"),
}


# ─────────────────────────────────────────────────────────────────────────────
# Strategy registry: instantiate all strategies and expose ORDER for the runner
# ─────────────────────────────────────────────────────────────────────────────
from typing import List, Tuple, Callable

# Imports with graceful fallbacks (class names vary across versions)
try:
    from .og_strategy import OGStrategy  # Legacy bias / scouts family
except Exception:
    OGStrategy = None  # keep the local OG_CFG above

try:
    from .ladder_strategy import LadderStrategy
except Exception:
    LadderStrategy = None  # keep the local LADDER_CFG above

# Crossover
try:
    from .crossover import CrossOverStrategy as _CrossOverStrategy
except Exception:
    try:
        from .crossover import CrossoverStrategy as _CrossOverStrategy
    except Exception:
        try:
            from .crossover import Crossover as _CrossOverStrategy
        except Exception:
            _CrossOverStrategy = None

# Breakout
try:
    from .range_breakout import RangeBreakoutStrategy as _BreakoutStrategy
except Exception:
    try:
        from .range_breakout import BreakoutStrategy as _BreakoutStrategy
    except Exception:
        try:
            from .range_breakout import RangeBreakout as _BreakoutStrategy
        except Exception:
            _BreakoutStrategy = None

# Steam fade
try:
    from .steam_fade import SteamFadeStrategy as _SteamFadeStrategy
except Exception:
    _SteamFadeStrategy = None

# In-play (shock drift)
try:
    from .inplay import ShockDriftStrategy as _ShockDriftStrategy
except Exception:
    _ShockDriftStrategy = None

# ─────────────────────────────────────────────────────────────────────────────
# Public exports the orchestrator expects
# ─────────────────────────────────────────────────────────────────────────────
from typing import List, Tuple, Callable, Dict

ORDER: List[Tuple[str, Callable[[dict], dict]]] = []
ENABLED: Dict[str, bool] = {}

def _make(name: str, cls, *args, **kwargs):
    if cls is None:
        return None
    try:
        return cls(*args, **kwargs)
    except TypeError:
        return cls()

def _wrap_evaluate(inst):
    """
    Wrap a StrategyBase .evaluate(mkt_ctx, runner_ctx) instance into a fn(runner_ctx) callable
    with a stable name for logging / feature flags.
    """
    name = getattr(inst, "name", inst.__class__.__name__).upper()
    def fn(rctx: dict) -> dict:
        mctx = {
            "marketId": str(rctx.get("marketId") or rctx.get("market_id") or ""),
            "in_play":  str(rctx.get("phase", "")).upper() == "IN_PLAY",
            "tto_min":  float(rctx.get("tto_minutes", rctx.get("minutes_to_off", 0.0)) or 0.0),
        }
        return inst.evaluate(mctx, dict(rctx))
    fn.__name__ = name
    fn.name = name
    return fn

def _maybe_add(name: str, fn: Callable[[dict], dict]) -> None:
    global ORDER, ENABLED
    ORDER.append((name, fn))
    if name not in ENABLED:
        ENABLED[name] = True

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/registry.py
# 🔎 SEARCH: ^def active_strategies\(
# ⛏️ ACTION: replace the entire function with this version
def active_strategies() -> List[Tuple[str, Callable[[dict], dict]]]:
    """
    Build ORDER = [(strategy_name, callable), ...] including LEGACY + PRE letters we actually have.
    """
    ORDER.clear()

    # --- BLUEPRINTS (P) first ---
    try:
        from .blueprints_strategy import decide as BLUEPRINTS
    except Exception:
        BLUEPRINTS = None
    if callable(BLUEPRINTS):
        _maybe_add("BLUEPRINTS", BLUEPRINTS)

    # --- LEGACY/Bias family (S) + Ladder (L) ---
    try:
        from .og_strategy import OGStrategy
    except Exception:
        OGStrategy = None
    try:
        from .ladder_strategy import LadderStrategy
    except Exception:
        LadderStrategy = None

    # pull configs if defined in this module
    _OGC  = globals().get("OG_CFG",  {})
    _LADC = globals().get("LADDER_CFG", {})

    if OGStrategy:
        _maybe_add("OG_STRATEGY", _wrap_evaluate(OGStrategy(_OGC)))
    if LadderStrategy:
        _maybe_add("LADDER_STRATEGY", _wrap_evaluate(LadderStrategy(_LADC)))

    # --- ALWAYS-ON (A) — class-based clone of OG you added ---
    try:
        from .always_on import AlwaysOnStrategy
    except Exception:
        AlwaysOnStrategy = None
    _ACFG = globals().get("A_CFG", globals().get("OG_CFG", {}))
    if AlwaysOnStrategy:
        _maybe_add("ALWAYS_ON", _wrap_evaluate(AlwaysOnStrategy(_ACFG)))

    # --- PRE decide() functions (B, G, X, R, F) ---
    try:
        from .btl_scout import decide as BTL_SCOUT
    except Exception:
        BTL_SCOUT = None
    try:
        from .btl_aggr import decide as BTL_AGGR
    except Exception:
        BTL_AGGR = None
    try:
        from .crossover import decide as S4_CROSSOVER
    except Exception:
        S4_CROSSOVER = None
    try:
        from .range_breakout import decide as S5_BREAKOUT
    except Exception:
        S5_BREAKOUT = None
    try:
        from .steam_fade import decide as S6_STEAM_FADE
    except Exception:
        S6_STEAM_FADE = None

    for name, fn in [
        ("BTL_SCOUT", BTL_SCOUT),
        ("BTL_AGGR",  BTL_AGGR),
        ("S4_CROSSOVER", S4_CROSSOVER),
        ("S5_BREAKOUT",  S5_BREAKOUT),
        ("S6_STEAM_FADE", S6_STEAM_FADE),
    ]:
        if callable(fn):
            _maybe_add(name, fn)

# === PATCH: engines/decision_engine/strategies/registry.py ===
# inside active_strategies(), after the PRE decide() block

    # --- COOL-OFF Liability Manager (M) ---
    try:
        from .mlm import decide as MLM
    except Exception:
        MLM = None
    if callable(MLM):
        _maybe_add("MLM", MLM)


    # --- IN-PLAY decide() functions (I, T, C, E, K) ---
    try:
        from .inplay_hybrid import (
            decide_shock_drift   as IP1_SHOCK_DRIFT,
            decide_tired_leader  as IP2_TIRED_LEADER,
            decide_close_finish  as IP3_CLOSE_FINISH,
            decide_fence_error   as IP4_FENCE_ERROR,
            decide_collapse_fade as IP5_COLLAPSE_FADE,
        )
    except Exception:
        # fallback to legacy names if hybrid not present
        try:
            from .inplay import decide_shock_drift   as IP1_SHOCK_DRIFT
        except Exception: IP1_SHOCK_DRIFT = None
        try:
            from .inplay import decide_tired_leader  as IP2_TIRED_LEADER
        except Exception: IP2_TIRED_LEADER = None
        try:
            from .inplay import decide_close_finish  as IP3_CLOSE_FINISH
        except Exception: IP3_CLOSE_FINISH = None
        try:
            from .inplay import decide_fence_error   as IP4_FENCE_ERROR
        except Exception: IP4_FENCE_ERROR = None
        try:
            from .inplay import decide_collapse_fade as IP5_COLLAPSE_FADE
        except Exception: IP5_COLLAPSE_FADE = None

    for name, fn in [
        ("IP1_SHOCK_DRIFT",   IP1_SHOCK_DRIFT),
        ("IP2_TIRED_LEADER",  IP2_TIRED_LEADER),
        ("IP3_CLOSE_FINISH",  IP3_CLOSE_FINISH),
        ("IP4_FENCE_ERROR",   IP4_FENCE_ERROR),
        ("IP5_COLLAPSE_FADE", IP5_COLLAPSE_FADE),
    ]:
        if callable(fn):
            _maybe_add(name, fn)
# === PATCH END ===



# in engines/decision_engine/strategies/registry.py (bottom)
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/registry.py
# 🔎 SEARCH: ^def discover_strategies\(
# ⛏️ ACTION: replace the whole function with this hardened version
def discover_strategies():
    """
    Return (new, legacy, names, reasons) — lists, never None.
    Safe on import failures; never raises.
    """
    global ORDER, ENABLED
    reasons: List[str] = []

    # Ensure ORDER exists and is a list
    try:
        if not isinstance(ORDER, list):
            ORDER = []
    except NameError:
        ORDER = []

    # Try to build ORDER; never propagate
    try:
        active_strategies()
    except Exception as e:
        reasons.append(f"registry build error: {e.__class__.__name__}: {e}")

    names = [n for (n, _fn) in (ORDER or []) if n]
    legacy = [n for n in names if n in ("OG_STRATEGY", "LADDER_STRATEGY")]
    new    = [n for n in names if n not in legacy]

    # Quick missing-module diagnostics (optional)
    missing = []
    for lab, mod in (
        ("inplay_hybrid", "engines.decision_engine.strategies.inplay_hybrid"),
        ("inplay",        "engines.decision_engine.strategies.inplay"),
        ("crossover",     "engines.decision_engine.strategies.crossover"),
        ("range_breakout","engines.decision_engine.strategies.range_breakout"),
        ("steam_fade",    "engines.decision_engine.strategies.steam_fade"),
        ("btl_scout",     "engines.decision_engine.strategies.btl_scout"),
        ("btl_aggr",      "engines.decision_engine.strategies.btl_aggr"),
    ):
        try:
            __import__(mod)
        except Exception as e:
            missing.append(f"{lab}:{e.__class__.__name__}")
    if missing:
        reasons.append("missing modules: " + ", ".join(missing))

    return (new, legacy, names, reasons)
# === PATCH END ===



# keep ENABLED defaulting to True
ENABLED = {name: ENABLED.get(name, True) for (name, _fn) in ORDER}

"""
# Family letter map used for letter+counter tagging (S reserved for legacy)
STRAT_CODE = {
    # Legacy S-family (scouts / bias variants)
    "OG_STRATEGY":        "S",
    "S1_LEGACY_L2B":      "S",
    "S2_BIAS_L2B":        "S",
    "S3_LADDER_L2B":      "S",

    # Always-on (A)
    "ALWAYS_ON":          "A",

    # Ladder (L)
    "LADDER_STRATEGY":    "L",
    "LADDER":             "L",

    # Crossover (X) — class/alias variants
    "S4_CROSSOVER":       "X",
    "CROSSOVER":          "X",
    "CROSSOVERSTRATEGY":  "X",

    # Breakout (R) — class/alias variants
    "S5_BREAKOUT":        "R",
    "RANGEBREAKOUT":      "R",
    "RANGEBREAKOUTSTRATEGY":"R",
    "BREAKOUTSTRATEGY":   "R",

    # Steam fade (F) — class/alias variants
    "S6_STEAM_FADE":      "F",
    "STEAM_FADE":         "F",
    "STEAMFADESTRATEGY":  "F",

    # BTL families (explicit map so they don’t fall back to 'B' from first letter only)
    "BTL_SCOUT":          "B",   # PRE back-to-lay scout
    "BTL_AGGR":           "G",   # PRE aggressive BTL

    # Bias / OG bias — CHANGED to Z (no clash with L=LIVE)
    "OG_BIAS":            "Z",
    "OGBIAS":             "Z",

    # In-play families — Shock drift & friends
    "IP1_SHOCK_DRIFT":    "I",
    "SHOCK_DRIFT":        "I",
    "SHOCKDRIFTSTRATEGY": "I",

    "IP2_TIRED_LEADER":   "T",
    "TIRED_LEADER":       "T",

    "IP3_CLOSE_FINISH":   "C",
    "CLOSE_FINISH":       "C",

    "IP4_FENCE_ERROR":    "E",
    "FENCE_ERROR":        "E",

    "IP5_COLLAPSE_FADE":  "K",
    "COLLAPSE_FADE":      "K",
}

from engines.decision_engine.strategies.common import GateSpec
"""

# ─────────────────────────────────────────────────────────────────────────────
# Per-strategy gates
# PRE strategies use orchestrator minutes_to_off; IP strategies use phase=IN_PLAY.
# Windows are minutes-to-off [lo, hi]; odds bands are inclusive (lo ≤ price ≤ hi).
# Liquidity & tape thresholds are conservative; tune after a few live races.
# ─────────────────────────────────────────────────────────────────────────────
STRAT_GATES: dict[str, GateSpec] = {

    # ── LEGACY/BIASED family (run primarily via decide_once). We keep no-op gates
    #    here so the runner never blocks them if they are present in ORDER.
    "OG_STRATEGY":   GateSpec(code_letter="S", phase="PRE"),
    "S1_LEGACY_L2B": GateSpec(code_letter="S", phase="PRE"),
    "S2_BIAS_L2B":   GateSpec(code_letter="S", phase="PRE"),
    "S3_LADDER_L2B": GateSpec(code_letter="S", phase="PRE"),
    "BTL_SCOUT":     GateSpec(code_letter="S", phase="PRE"),
    "BTL_AGGR":      GateSpec(code_letter="S", phase="PRE"),
    "OG_BIAS":       GateSpec(code_letter="S", phase="PRE"),

    "BLUEPRINTS": GateSpec(
        code_letter="P",
        phase="ANY",                  # blueprint trades can occur PRE and IN_PLAY
        tto_min_max=None,             # no time restriction; the blueprint file gates the intent
        odds_band=(1.5, 18.0),        # safe/preferred operating band
        min_liq_back=0.0, min_liq_lay=0.0,
        min_traded_amt_60s=0.0,       # rely on the blueprint match gate
        max_tape_age_s=120.0,         # stale tape guard (optional)
        cadence_sec=0.5, cooldown_sec=15.0
    ),


    # ── LADDER: early, steady, wide odds; slower cadence
    "LADDER_STRATEGY": GateSpec(
        code_letter="L", phase="PRE",
        tto_min_max=(10.0, 120.0),
        odds_band=(2.0, 18.0),
        min_liq_back=100.0, min_liq_lay=100.0,
        min_traded_amt_60s=150.0, max_tape_age_s=90.0,
        cadence_sec=2.0, cooldown_sec=30.0
    ),

    # ── S4 CROSSOVER: as discussed, up to 60m pre-off
    "S4_CROSSOVER": GateSpec(
        code_letter="X", phase="PRE",
        tto_min_max=(2.0, 60.0),
        odds_band=(2.0, 12.0),
        min_liq_back=120.0, min_liq_lay=120.0,
        min_traded_amt_60s=200.0, max_tape_age_s=60.0,
        cadence_sec=0.5, cooldown_sec=20.0
    ),
    "CROSSOVER": GateSpec(code_letter="X", phase="PRE",
        tto_min_max=(2.0, 60.0), odds_band=(2.0, 12.0),
        min_liq_back=120.0, min_liq_lay=120.0,
        min_traded_amt_60s=200.0, max_tape_age_s=60.0,
        cadence_sec=0.5, cooldown_sec=20.0),
    "CROSSOVERSTRATEGY": GateSpec(code_letter="X", phase="PRE",
        tto_min_max=(2.0, 60.0), odds_band=(2.0, 12.0),
        min_liq_back=120.0, min_liq_lay=120.0,
        min_traded_amt_60s=200.0, max_tape_age_s=60.0,
        cadence_sec=0.5, cooldown_sec=20.0),

    # ── S5 RANGE BREAKOUT: later & stronger tape
    "S5_BREAKOUT": GateSpec(
        code_letter="B", phase="PRE",
        tto_min_max=(5.0, 60.0),
        odds_band=(2.4, 14.0),
        min_liq_back=180.0, min_liq_lay=180.0,
        min_traded_amt_60s=300.0, max_tape_age_s=45.0,
        cadence_sec=1.0, cooldown_sec=25.0
    ),
    "RANGEBREAKOUT": GateSpec(code_letter="B", phase="PRE",
        tto_min_max=(5.0, 60.0), odds_band=(2.4, 14.0),
        min_liq_back=180.0, min_liq_lay=180.0,
        min_traded_amt_60s=300.0, max_tape_age_s=45.0,
        cadence_sec=1.0, cooldown_sec=25.0),
    "RANGEBREAKOUTSTRATEGY": GateSpec(code_letter="B", phase="PRE",
        tto_min_max=(5.0, 60.0), odds_band=(2.4, 14.0),
        min_liq_back=180.0, min_liq_lay=180.0,
        min_traded_amt_60s=300.0, max_tape_age_s=45.0,
        cadence_sec=1.0, cooldown_sec=25.0),
    "BREAKOUTSTRATEGY": GateSpec(code_letter="B", phase="PRE",
        tto_min_max=(5.0, 60.0), odds_band=(2.4, 14.0),
        min_liq_back=180.0, min_liq_lay=180.0,
        min_traded_amt_60s=300.0, max_tape_age_s=45.0,
        cadence_sec=1.0, cooldown_sec=25.0),

    # ── S6 STEAM FADE: closer to off; conservative band
    "S6_STEAM_FADE": GateSpec(
        code_letter="F", phase="PRE",
        tto_min_max=(2.0, 15.0),
        odds_band=(2.0, 12.0),
        min_liq_back=150.0, min_liq_lay=150.0,
        min_traded_amt_60s=250.0, max_tape_age_s=45.0,
        cadence_sec=0.5, cooldown_sec=20.0
    ),
    "STEAM_FADE": GateSpec(code_letter="F", phase="PRE",
        tto_min_max=(2.0, 15.0), odds_band=(2.0, 12.0),
        min_liq_back=150.0, min_liq_lay=150.0,
        min_traded_amt_60s=250.0, max_tape_age_s=45.0,
        cadence_sec=0.5, cooldown_sec=20.0),
    "STEAMFADESTRATEGY": GateSpec(code_letter="F", phase="PRE",
        tto_min_max=(2.0, 15.0), odds_band=(2.0, 12.0),
        min_liq_back=150.0, min_liq_lay=150.0,
        min_traded_amt_60s=250.0, max_tape_age_s=45.0,
        cadence_sec=0.5, cooldown_sec=20.0),

    # ── IP1 SHOCK DRIFT: in-play only; cap odds; fresh tape
    "IP1_SHOCK_DRIFT": GateSpec(
        code_letter="D", phase="IN_PLAY",
        tto_min_max=None,
        odds_band=(1.2, 12.0),
        min_liq_back=50.0, min_liq_lay=50.0,
        min_traded_amt_60s=200.0, max_tape_age_s=10.0,
        cadence_sec=0.5, cooldown_sec=8.0
    ),
    "SHOCK_DRIFT": GateSpec(code_letter="D", phase="IN_PLAY",
        tto_min_max=None, odds_band=(1.2, 12.0),
        min_liq_back=50.0, min_liq_lay=50.0,
        min_traded_amt_60s=200.0, max_tape_age_s=10.0,
        cadence_sec=0.5, cooldown_sec=8.0),
    "SHOCKDRIFTSTRATEGY": GateSpec(code_letter="D", phase="IN_PLAY",
        tto_min_max=None, odds_band=(1.2, 12.0),
        min_liq_back=50.0, min_liq_lay=50.0,
        min_traded_amt_60s=200.0, max_tape_age_s=10.0,
        cadence_sec=0.5, cooldown_sec=8.0),

    # ── IP2 TIRED LEADER: in-play only; narrower band; quicker cadence
    "IP2_TIRED_LEADER": GateSpec(
        code_letter="T", phase="IN_PLAY",
        tto_min_max=None,
        odds_band=(1.2, 9.0),
        min_liq_back=60.0, min_liq_lay=60.0,
        min_traded_amt_60s=220.0, max_tape_age_s=8.0,
        cadence_sec=0.4, cooldown_sec=8.0
    ),
    "TIRED_LEADER": GateSpec(code_letter="T", phase="IN_PLAY",
        tto_min_max=None, odds_band=(1.2, 9.0),
        min_liq_back=60.0, min_liq_lay=60.0,
        min_traded_amt_60s=220.0, max_tape_age_s=8.0,
        cadence_sec=0.4, cooldown_sec=8.0),

    # ── IP3 CLOSE FINISH: strong flow; short prices; very fresh tape
    "IP3_CLOSE_FINISH": GateSpec(
        code_letter="C", phase="IN_PLAY",
        tto_min_max=None,
        odds_band=(1.2, 6.0),
        min_liq_back=80.0, min_liq_lay=80.0,
        min_traded_amt_60s=300.0, max_tape_age_s=6.0,
        cadence_sec=0.3, cooldown_sec=6.0
    ),
    "CLOSE_FINISH": GateSpec(code_letter="C", phase="IN_PLAY",
        tto_min_max=None, odds_band=(1.2, 6.0),
        min_liq_back=80.0, min_liq_lay=80.0,
        min_traded_amt_60s=300.0, max_tape_age_s=6.0,
        cadence_sec=0.3, cooldown_sec=6.0),

    # ── IP4 FENCE ERROR (jumps): allow a bit higher odds; moderate tape
    "IP4_FENCE_ERROR": GateSpec(
        code_letter="E", phase="IN_PLAY",
        tto_min_max=None,
        odds_band=(1.2, 12.0),
        min_liq_back=50.0, min_liq_lay=50.0,
        min_traded_amt_60s=180.0, max_tape_age_s=10.0,
        cadence_sec=0.5, cooldown_sec=8.0
    ),
    "FENCE_ERROR": GateSpec(code_letter="E", phase="IN_PLAY",
        tto_min_max=None, odds_band=(1.2, 12.0),
        min_liq_back=50.0, min_liq_lay=50.0,
        min_traded_amt_60s=180.0, max_tape_age_s=10.0,
        cadence_sec=0.5, cooldown_sec=8.0),

    # ── IP5 COLLAPSE FADE: cap at 12; fresher tape
    "IP5_COLLAPSE_FADE": GateSpec(
        code_letter="K", phase="IN_PLAY",
        tto_min_max=None,
        odds_band=(1.2, 12.0),
        min_liq_back=60.0, min_liq_lay=60.0,
        min_traded_amt_60s=240.0, max_tape_age_s=8.0,
        cadence_sec=0.4, cooldown_sec=8.0
    ),
    "COLLAPSE_FADE": GateSpec(code_letter="K", phase="IN_PLAY",
        tto_min_max=None, odds_band=(1.2, 12.0),
        min_liq_back=60.0, min_liq_lay=60.0,
        min_traded_amt_60s=240.0, max_tape_age_s=8.0,
        cadence_sec=0.4, cooldown_sec=8.0),
}

class _DecideAdapter:
    """Turns an object with .decide(ctx) into a callable(ctx)."""
    def __init__(self, inst, name: str | None = None):
        self._inst = inst
        self.name = name or getattr(inst, "name", type(inst).__name__)
    def __call__(self, ctx: dict):
        return self._inst.decide(ctx)

# ---- EXPORTS: build registry at import time so orchestrator sees strategies ----
# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/registry.py
# 🔎 SEARCH: ^# ---- EXPORTS: build registry at import time.*$
# ⛏️ ACTION: replace that section with this safe helper and call

def _rebuild_registry_on_import():
    global ORDER, ENABLED
    try:
        ORDER
    except NameError:
        ORDER = []
    try:
        active_strategies()
    except Exception:
        # Keep whatever ORDER existed; discovery will still work
        pass
    if not isinstance(ENABLED, dict):
        ENABLED = {}
    for name, _fn in (ORDER or []):
        ENABLED.setdefault(name, True)

_rebuild_registry_on_import()
# === PATCH END ===


