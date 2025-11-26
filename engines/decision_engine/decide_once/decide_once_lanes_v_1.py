# engines/decision_engine/decide_once/lanes.py
from __future__ import annotations

from typing import Optional

from .helpers import (
    status_once,
    can_open_scalp,
    already_open_pass,
    next_pair_tag,
    pass_tag,
)
from .scope import build_and_maintain_scope, ordered_markets_for_tick, advance_scope_cursor
from .candidates import _active_candidates_for_market
from .placement import place_from_plan

# Strategy registry import (best-effort)
try:
    from engines.decision_engine.strategies.registry import ORDER as ORDER_LIST, ENABLED, STRAT_CODE  # type: ignore
    ORDER_MAP = {nm: fn for (nm, fn) in (ORDER_LIST or [])}
except Exception:
    ORDER_MAP, ENABLED, STRAT_CODE = {}, {}, {}


def _names_for(letter: str):
    want = letter[:1].upper()
    names = [nm for nm in ORDER_MAP.keys() if (STRAT_CODE.get(nm.upper(), nm[:1].upper()) == want)]
    if names:
        return names
    return {"S":["OG_STRATEGY"], "A":["ALWAYS_ON"], "B":["BTL_SCOUT"], "G":["BTL_AGGR"],
            "X":["S4_CROSSOVER"], "R":["S5_BREAKOUT"], "F":["S6_STEAM_FADE"],
            "L":["LADDER_STRATEGY"], "I":["IP1_SHOCK_DRIFT"]}.get(want, [f"FAMILY_{want}"])


def run_all(run_id: str, source: str = "LIVE", logger=None) -> Optional[int]:
    log = logger or (lambda *a, **k: None)

    sc = build_and_maintain_scope(inplay_window_min=15, show_dashboard=True)
    ordered = ordered_markets_for_tick(sc)
    status_once("registry:order", bool(ORDER_MAP), "empty" if not ORDER_MAP else "")

    # execution order per your spec
    families = [("S","PRE"), ("A","PRE"), ("B","PRE"), ("G","PRE"), ("X","PRE"),
                ("R","PRE"), ("F","PRE"), ("L","PRE"), ("I","IP")]

    for (letter, phase) in families:
        for mid in ordered:
            cands = _active_candidates_for_market(mid, max_runners=8)
            if not cands:
                continue
            for (sid, last) in cands:
                # dedupe minute-pass
                tag = pass_tag(letter, 0.0)
                if already_open_pass(mid, sid, tag, mode=source):
                    continue

                ok_cap, _why = can_open_scalp(mid, sid, max_per_runner=3, run_id=run_id, family_letter=letter)
                if not ok_cap and source != "TEST":
                    continue

                # try registered names for this family
                for nm in _names_for(letter):
                    if ENABLED and not ENABLED.get(nm, True):
                        continue
                    try:
                        fn = ORDER_MAP.get(nm)
                        plan = fn and fn({"marketId": mid, "selectionId": sid, "odds": float(last), "phase": phase})
                    except Exception:
                        plan = None
                    if not (plan and plan.get("enter")):
                        continue
                    placed = place_from_plan(nm, plan, {"marketId": mid, "selectionId": sid, "odds": float(last), "phase": phase})
                    if placed:
                        return placed
    advance_scope_cursor(1, span=len(ordered) if ordered else 0)
    return None
