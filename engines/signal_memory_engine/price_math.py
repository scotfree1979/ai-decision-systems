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

def calculate_tick_distance(start_odds, end_odds):
    """
    Calculates the number of Betfair ticks between two odds.
    """
    if start_odds == end_odds:
        return 0

    def odds_list():
        odds = []
        for lower, upper, tick in TICK_SIZES:
            current = lower
            while current < upper:
                odds.append(round(current, 2))
                current += tick
        return odds

    odds_seq = odds_list()

    try:
        idx_start = odds_seq.index(round(start_odds, 2))
        idx_end = odds_seq.index(round(end_odds, 2))
        return abs(idx_end - idx_start)
    except ValueError:
        return 0
# -----------------------------
# ✅  --- WALK TICKS FUNCTION ---
# -----------------------------

def walk_ticks(start_odds, ticks, direction="down"):
    """
    Walks a given number of Betfair ticks up or down from a starting odds value.
    Returns the adjusted odds after moving the specified number of ticks.
    """
    if ticks == 0:
        return round(start_odds, 2)

    odds_list = []
    for lower, upper, tick in TICK_SIZES:
        current = lower
        while round(current, 2) < upper:
            odds_list.append(round(current, 2))
            current += tick

    try:
        idx = odds_list.index(round(start_odds, 2))
        if direction == "up":
            new_idx = min(idx + ticks, len(odds_list) - 1)
        else:
            new_idx = max(idx - ticks, 0)
        return odds_list[new_idx]
    except ValueError:
        return round(start_odds, 2)  # fallback if start_odds not on ladder

