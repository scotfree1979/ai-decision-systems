# /engines/micro_scalper_v7/utils.py

from typing import Optional

# --- DIRECTION MODEL (locked in) ---
# LAY -> BACK  = DRIFT (price should go UP)
# BACK -> LAY  = STEAM (price should go DOWN)

DRIFT = "DRIFT"
STEAM = "STEAM"

def classify_direction_from_legacy(entry_side: str) -> str:
    """
    Given the legacy entry side, return the intended price behaviour.
    """
    entry_side = entry_side.upper()
    if entry_side == "LAY":
        return DRIFT   # price should rise
    if entry_side == "BACK":
        return STEAM   # price should fall
    return DRIFT


def opposite_exit_direction(entry_side: str) -> str:
    """
    The direction required to CLOSE the legacy parent during a CSL.
    """
    entry_side = entry_side.upper()
    if entry_side == "LAY":
        return "BACK"   # to close drift parent during adverse steam
    if entry_side == "BACK":
        return "LAY"    # to close steam parent during adverse drift
    return "BACK"


def same_direction_microtrade(direction: str) -> str:
    """
    Convert drift/steam into actual order-side for micro scalps.
    """
    if direction == DRIFT:
        return "LAY"   # drift micro: LAY first, BACK higher
    if direction == STEAM:
        return "BACK"  # steam micro: BACK first, LAY lower
    return "BACK"


def tick_diff(px_new: float, px_old: float) -> int:
    """
    Simple tick difference helper. In practice you'd route to 
    tickmath or Betfair tick ladder, but this version keeps 
    integration modular.
    """
    if px_new is None or px_old is None:
        return 0
    return int(round((px_new - px_old) * 100))  # placeholder scaling
