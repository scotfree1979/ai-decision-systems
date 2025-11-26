# engines/decision_engine/decide_once/rules.py
from __future__ import annotations
from typing import Tuple, Optional, Dict

# Strategy gates (odds/time/tape/liquidity) live in registry; gate_check applies them.
try:
    from engines.decision_engine.strategies.registry import STRAT_GATES  # name -> GateSpec
    from engines.decision_engine.strategies.common import gate_check, GateSpec
except Exception:
    STRAT_GATES = {}
    class GateSpec:  # minimal placeholder
        pass
    def gate_check(_ctx, _spec):  # always allow if gate system missing
        return True, "ok"

# ──────────────────────────────────────────────────────────────────────
# Public API (single source of truth)
# ──────────────────────────────────────────────────────────────────────

def rulebook_allow(letter: str, pass_n: int, ctx: dict, plan: dict) -> Tuple[bool, Optional[dict]]:
    """
    Optional pre-check hook (return True to allow). Keep permissive for now.
    Extend here if you later want cadence/kill-switch style “allows”.
    """
    return True, plan


def apply_rulebook(letter: str, tag: str, ctx: dict, plan: dict) -> Tuple[bool, dict]:
    """
    Final rulebook gate. Return (skip, plan). When skip=True, caller must not place.
    Keep ALL letter-specific “musts” here so behavior is editable in one file.
    """
    L = (letter or "").upper()
    p = dict(plan or {})

    # P (Blueprints) MUST have a blueprint match — works PRE and IP
    if L == "P" and not bool(ctx.get("blueprint_match", False)):
        p["why"] = "rulebook:blueprint_required"
        return True, p

    # (optional) you can add per-letter hard blocks here, e.g. min/max ticks, EV caps, etc.
    # Example:
    # if L == "A" and int(p.get("target_ticks", 1)) > 2:
    #     p["why"] = "rulebook:too_many_ticks_for_A"
    #     return True, p

    return False, p


def which_rulebook() -> str:
    """For diagnostics: confirms we’re using the canonical module."""
    return __name__
