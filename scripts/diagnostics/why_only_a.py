#!/usr/bin/env python3
# Run: python3 scripts/diagnostics/why_only_a.py
import os, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engines.decision_engine.decide_once.scope import build_and_maintain_scope, ordered_markets_for_tick
from engines.decision_engine.decide_once.candidates import cands_for_market
from engines.mastery.context_builder import build_context

# Prefer registry (new strategies) but fall back to lanes.ORDER if needed
def _load_strategies():
    """
    Try registry first; fall back to lanes.ORDER if registry is absent or returns None/empty.
    Returns (order: List[(name, fn)], fam_letter: Dict[str,str]).
    """
    # 1) registry (new stack)
    try:
        from engines.decision_engine.strategies import registry as reg
        order = []
        try:
            order = reg.active_strategies() or []
        except Exception:
            pass
        fam_letter = getattr(reg, "STRAT_CODE", {})
        if order:
            return order, fam_letter
    except Exception:
        pass

    # 2) lanes (legacy / decide_once)
    try:
        from engines.decision_engine.decide_once import lanes as ln
        order = getattr(ln, "ORDER", []) or []
        fam_letter = getattr(ln, "_FAM_LETTER", {})
        return order, fam_letter
    except Exception:
        return [], {}


def main():
    os.environ.setdefault("AUTOSCALP_LIGHT_GATES", "1")  # ensure LIGHT
    sc = build_and_maintain_scope(15, show_dashboard=True)
    mids = ordered_markets_for_tick(sc)[:5]
    if not mids:
        print("No markets in scope.")
        return

    order, fam_letter = _load_strategies()
    base_ctx, _ = build_context(source="LIVE")
    run_id = f"DRY-{int(time.time())}"

    print("\n[WHY-ONLY-A] Inspecting WIN5 ACTIVE runners (px ≤ 8.0):\n")
    for mid in mids:
        cands = cands_for_market(mid, max_runners=10) or []
        active = [(sid, px) for (sid, px) in cands if isinstance(px, (int, float)) and px <= 8.0]
        print(f"MARKET {mid} ACTIVE={len(active)}")
        for sid, px in active[:6]:  # top few
            print(f"  runner sid={sid} px={px:.2f}")
            ctx = dict(base_ctx)
            ctx.update({
                "run_id": run_id, "mode": "DRY",
                "marketId": mid, "selectionId": sid, "odds": float(px)
            })
            # Evaluate each family WITHOUT placing
            any_enter = False
            for fam_name, fn in order:
                if not callable(fn):
                    continue
                letter = fam_letter.get(fam_name, "?")
                try:
                    plan = fn(ctx) or {}
                except Exception as e:
                    print(f"    {letter} {fam_name:<18} → ERROR {e}")
                    continue

                enter = bool(plan.get("enter"))
                why   = plan.get("why") or plan.get("plan_why") or ""
                direction = plan.get("direction") or plan.get("edge") or ""
                if enter:
                    any_enter = True
                    print(f"    {letter} {fam_name:<18} → ENTER dir={direction} ticks={plan.get('target_ticks')} size={plan.get('size')}  {why}")
                else:
                    if why:
                        print(f"    {letter} {fam_name:<18} → no  ({why})")
            if not any_enter:
                print("    -- no families proposed (pre-placement) --")
        print()

if __name__ == "__main__":
    main()
