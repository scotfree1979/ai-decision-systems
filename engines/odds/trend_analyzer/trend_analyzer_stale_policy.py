from __future__ import annotations
from .types import Basis

class StaleTracker:
    """Tracks consecutive missing OCs per runner.
    Missing OCs increment the counter; a present expected OC resets it to 0.
    """
    def __init__(self, hard_stale_missing: int) -> None:
        self.hard = hard_stale_missing
        self.missing_streak: dict[tuple[str,int], int] = {}

    def note(self, market_id: str, selection_id: int, expected_oc_present: bool) -> int:
        k = (market_id, selection_id)
        n = self.missing_streak.get(k, 0)
        if expected_oc_present:
            n = 0
        else:
            n += 1
        self.missing_streak[k] = n
        return n

    def health(self, streak: int) -> str:
        if streak >= self.hard:
            return "HARD_STALE"
        elif streak >= 1:
            return "DEGRADED"
        return "GREEN"


def apply_stale_to_basis(b: Basis, streak: int, hard: int) -> Basis:
    b.oc_missing_streak = streak
    if streak >= hard:
        b.health = "HARD_STALE"
    elif streak >= 1:
        b.health = "DEGRADED"
    else:
        b.health = "GREEN"
    return b
