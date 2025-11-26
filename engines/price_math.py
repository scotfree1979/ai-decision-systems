# ✅ price_math.py – Shared Tick + Price Tools

# -----------------------------
# ✅ BETFAIR TICK TABLE
# -----------------------------

TICK_SIZES = [
    (1.01, 2.0, 0.01),
    (2.0, 3.0, 0.02),
    (3.0, 4.0, 0.05),
    (4.0, 6.0, 0.1),
    (6.0, 10.0, 0.2),
    (10.0, 20.0, 0.5),
    (20.0, 30.0, 1.0),
    (30.0, 50.0, 2.0),
    (50.0, 100.0, 5.0),
    (100.0, 1000.0, 10.0)
]

# -----------------------------
# ✅ GET TICK SIZE FOR GIVEN ODDS
# -----------------------------

def get_tick_size(odds):
    for lower, upper, tick in TICK_SIZES:
        if lower <= odds < upper:
            return tick
    return 0.01

# ✅ Add this to price_math.py

# === PATCH START ===
# 📍 TARGET: engines/price_math.py
# 📆 PATCHED: 2025-10-30Z — add cached full tick ladder + micro-scalper helpers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from functools import lru_cache

@lru_cache()
def full_tick_ladder():
    """
    Cached full Betfair tick ladder built from TICK_SIZES.
    Covers 1.01 → 1000.00 inclusive.
    """
    ladder = []
    for lower, upper, tick in TICK_SIZES:
        current = lower
        while current < upper:
            ladder.append(round(current, 2))
            current += tick
    # ensure last step included
    if ladder[-1] < 1000.0:
        ladder.append(1000.0)
    return ladder


def calculate_tick_distance(start_odds: float, end_odds: float) -> int:
    """
    Signed number of Betfair ticks between two odds using cached ladder.
    Positive = drift (price ↑), negative = shorten (price ↓).
    """
    if start_odds == end_odds:
        return 0
    ladder = full_tick_ladder()
    try:
        i0 = ladder.index(round(start_odds, 2))
        i1 = ladder.index(round(end_odds, 2))
        return i1 - i0
    except ValueError:
        return 0

def snap_to_tick(odds: float) -> float:
    for lower, upper, tick in TICK_SIZES:
        if lower <= odds < upper:
            steps = round((odds - lower) / tick)
            return round(lower + steps * tick, 2)
    return round(odds, 2)

# -----------------------------
# ✅  --- WALK TICKS FUNCTION ---
# -----------------------------

def walk_ticks(start_odds: float, ticks: int, direction: str = "up") -> float:
    """
    Walk a number of ticks from a starting odds value.
      direction='up'   → move to longer odds (drift)
      direction='down' → move to shorter odds (steam)
    """
    if ticks == 0:
        return round(start_odds, 2)
    ladder = full_tick_ladder()
    try:
        idx = ladder.index(round(start_odds, 2))
        new_idx = idx + ticks if direction == "up" else idx - ticks
        new_idx = max(0, min(new_idx, len(ladder) - 1))
        return ladder[new_idx]
    except ValueError:
        return round(start_odds, 2)
# === PATCH END ===
