# engines/decision_engine/decide_once/candidates.py
from __future__ import annotations

from typing import List, Tuple

from .helpers import (
    latest_prices_for_market,
    status_once,
    _runner_activity,
)


def _active_candidates_for_market(mid: str, max_runners: int = 8) -> List[Tuple[str, float]]:
    """Return up to max_runners (selectionId, odds) for ACTIVE runners in target band.
    Implements stable RR rotation and removes the broken fallback tail from legacy code.
    """
    odds = latest_prices_for_market(mid)
    if not odds:
        status_once("candidates:odds", False, "no odds map")
        return []

    # Filter to ACTIVE band + classifier
    def _is_active_pair(sid: str, o: float) -> bool:
        try:
            if not (1.5 <= float(o) <= 8.0):
                return False
        except Exception:
            return False
        try:
            return _runner_activity(mid, sid) == "active"
        except Exception:
            return False

    candidates = [(sid, px) for sid, px in odds.items() if _is_active_pair(sid, px)]
    if not candidates:
        return []

    # Round-robin: stable per-market rotation
    try:
        rr_key = "_RR_CURSOR"
        order_key = "_RR_ORDER"
        cursors = globals().setdefault(rr_key, {})    # type: ignore[assignment]
        orders  = globals().setdefault(order_key, {}) # type: ignore[assignment]

        cur_ids = [sid for (sid, _px) in sorted(candidates, key=lambda t: (float(t[1]), str(t[0])))]
        prev_order = list(orders.get(mid, []))
        if prev_order != cur_ids:
            orders[mid] = cur_ids
            cur = int(cursors.get(mid, 0)) if mid in cursors else 0
            cursors[mid] = (cur % max(1, len(cur_ids)))

        cur = int(cursors.get(mid, 0))
        rot_ids   = (orders[mid][cur:] + orders[mid][:cur]) if orders.get(mid) else cur_ids
        rot_pairs = [(sid, odds.get(sid)) for sid in rot_ids if sid in odds]

        # advance cursor by the batch length returned
        if orders.get(mid):
            cursors[mid] = (cur + max(1, max_runners)) % len(orders[mid])

        out: list[tuple[str, float]] = []
        seen_ids: set[str] = set()
        for sid, px in rot_pairs:
            if sid in seen_ids:
                continue
            seen_ids.add(sid)
            try:
                o = float(px)
            except Exception:
                continue
            out.append((sid, o))
            if len(out) >= max_runners:
                break
        return out

    except Exception:
        # Fallback: simple sorted slice
        sorted_pairs = sorted(candidates, key=lambda t: (float(t[1]), str(t[0])))
        return sorted_pairs[:max_runners]
