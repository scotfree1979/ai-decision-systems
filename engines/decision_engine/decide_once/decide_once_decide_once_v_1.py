# engines/decision_engine/decide_once/decide_once.py
from __future__ import annotations

from typing import Optional

from .helpers import status_once
from .lanes import run_all


def decide_once(run_id: str, source_override: str | None = None, logger=None) -> Optional[int]:
    src = (source_override or "LIVE").upper()
    try:
        placed = run_all(run_id, source=src, logger=logger)
        if placed:
            status_once("decide_once:tick", True, "placed")
        else:
            status_once("decide_once:tick", True, "no placement")
        return placed
    except Exception as e:
        status_once("decide_once:tick", False, "exception")
        return None
