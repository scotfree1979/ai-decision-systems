# engines/decision_engine/decide_once/rules.py
from __future__ import annotations

from typing import Dict, Tuple, Optional

from .helpers import status_once

GateSpec = Dict[str, float]  # minimal placeholder (cadence, bands, etc.)


def rulebook_allow(letter: str, pass_n: int, ctx: dict, plan: dict) -> Tuple[bool, Optional[dict]]:
    """Thin adapter; extend with real checks (bands/cadence/liquidity)."""
    try:
        # TODO: wire to real rulebook if present; for now always allow
        return True, plan
    finally:
        status_once("rulebook:allow", True, "")


def apply_rulebook(letter: str, tag: str, ctx: dict, plan: dict) -> Tuple[bool, dict]:
    """Return (skip, plan). When skip=True, caller must not place."""
    try:
        # TODO: implement gates, cadence, blocks with reasons
        return False, plan
    finally:
        status_once("rulebook:apply", True, "")
