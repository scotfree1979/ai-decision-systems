# engines/decision_engine/decide_once/placement.py
from __future__ import annotations

from typing import Optional

from .helpers import (
    queue_order,
    place_decision,
    place_companion_hedge,
    set_order_status,
    mark_last_child_placed,
    status_once,
)


def place_from_plan(name: str, plan: dict, ctx: dict) -> Optional[int]:
    """Unified placer: queues parent, executes decision + companion hedge.
    Returns parent_id (int) on success, else None.
    """
    try:
        parent_id = place_decision(name, plan, ctx)
        if not parent_id:
            return None
        # optional companion hedge
        try:
            place_companion_hedge(parent_id, plan, ctx)
        except Exception:
            pass
        mark_last_child_placed(parent_id)
        status_once("router:place", True, "")
        return parent_id
    except Exception as e:
        try:
            set_order_status(int(plan.get("order_id", 0) or 0), "ERROR", str(e))
        except Exception:
            pass
        status_once("router:place", False, "exception")
        return None
