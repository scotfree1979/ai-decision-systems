# ======================================================================================================
# 📍 TARGET: engines/capital_policy.py
# 🧩 ACTION: CREATE FILE
# 📆 PURPOSE: Global engine capital gating + internal pot splitting
#
# DESIGN
# ------
# - No BankState dependency (avoids circular imports)
# - Pure functions only
# - Used by all engines (Unified, Blueprint, Context)
#
# RULES
# -----
# 1. Engine-level gate (total capital)
# 2. Internal split into sub-pots
# 3. Per-pot emission threshold
# ======================================================================================================

MIN_THRESHOLD = 10.0

DEFAULT_SPLIT = {
    "EXPLORATORY": 0.2,
    "RISK":        0.5,
    "INPLAY":      0.3,
}


def split_pots(total: float, split: dict = None) -> dict:
    """
    Split total capital into sub-pots.

    Args:
        total: total available capital
        split: optional override ratios

    Returns:
        dict of pot allocations
    """

    if not total or total <= 0:
        return {
            "EXPLORATORY": 0.0,
            "RISK":        0.0,
            "INPLAY":      0.0,
        }

    ratios = split or DEFAULT_SPLIT

    return {
        k: total * ratios.get(k, 0.0)
        for k in ("EXPLORATORY", "RISK", "INPLAY")
    }


def can_emit(pot: float) -> bool:
    """
    Determines if a sub-engine is allowed to emit plans.
    """
    return pot is not None and pot >= MIN_THRESHOLD


def engine_can_run(total: float) -> bool:
    """
    Coarse engine-level gate.
    """
    return total is not None and total >= MIN_THRESHOLD