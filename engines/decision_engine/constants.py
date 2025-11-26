"""
Phase 1 constants for the Decision Engine.
Keep these small, explicit, and easy to tune from one spot.
"""

# ── Tiering (display + gating) ───────────────────────────────────────────────
# Active:   odds < 8.0
# Passive:  8.0 <= odds <= 12.0
# Ignored:  odds > 12.0
TIER_ACTIVE_MAX = 8.0
TIER_PASSIVE_MAX = 12.0

# ── Engine cadence ───────────────────────────────────────────────────────────
BAND_SAMPLE_SECS = 2  # keep aligned with the OC timeline loop
ENGINE_TICK_SECS = 2  # decision loop cadence

# ── Range / breakout parameters ──────────────────────────────────────────────
RANGE_BUFFER_TICKS = 2      # how far beyond edge before we call it a breakout
RANGE_HOLD_SECS = 10        # persistence before confirming breakout

# ── Confidence shaping ───────────────────────────────────────────────────────
CONF_BUMP_EDGE = 0.05       # near edge & trend-to-edge
CONF_BUMP_BREAKOUT = 0.15   # confirmed breakout in our direction
CONF_PENALTY_FALSE_BREAK = -0.10

# ── Proposals ────────────────────────────────────────────────────────────────
MIN_CONF_TO_PROPOSE = 0.55
TARGET_TICKS_DEFAULT = 2
TARGET_TICKS_ON_BREAKOUT = 3

# ── Sizing ──────────────────────────────────────────────────────────────────
DEFAULT_UNIT_STAKE = 2.0     # used if daily_config has no explicit baseline
SIZE_MULTIPLIER_BREAKOUT = 1.35

# ── Safety rails (optional in Phase 1) ───────────────────────────────────────
MAX_BUDGET_FRACTION_PER_TRADE = 0.02   # 2% of available in Phase 1
