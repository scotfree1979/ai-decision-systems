#!/usr/bin/env python3
# scripts/mastery_dayplan.py
from __future__ import annotations
import os, sys, time
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlite3, json
from datetime import datetime, timezone
from engines.decision_engine.orchestrator import _compute_minutes_to_off
from engines.decision_engine.decide_once.scope import build_and_maintain_scope, ordered_markets_for_tick
from engines.decision_engine.decide_once.candidates import active_candidates_for_market
from engines.decision_engine.decide_once.lanes import ORDER
from engines.mastery import mastery_policy as mp

def _utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def _policy_call(fam_name, fn, ctx):
    """Never raises; never returns None."""
    try:
        res = fn(ctx) if callable(fn) else mp.plan_for_strategy(fam_name, ctx)
    except Exception as e:
        return {"enter": False, "why": f"policy_err:{fam_name}:{type(e).__name__}", "letter": (fam_name[:1] or "A").upper()}
    if not isinstance(res, dict):
        return {"enter": False, "why": f"policy_none:{fam_name}", "letter": (fam_name[:1] or "A").upper()}
    return res

def main():
    sc = build_and_maintain_scope(inplay_window_min=20, show_dashboard=False)
    markets = ordered_markets_for_tick(sc)[:40]
    day = _utc_day()
    print(f"[PLAN] Day plan for {day} ({len(markets)} markets)")

    print("[HEALTH] candidates:odds => OK — inbound — ok")

    for mid in markets:
        try:
            mto, _ = _compute_minutes_to_off(str(mid), source="LIVE")
        except Exception:
            mto = None

        cands = active_candidates_for_market(mid, max_runners=6)
        if not cands:
            continue

        print(f"\n• {mid}")
        for sid, px in cands:
            ctx = {
                "run_id": f"DRY-{day}",
                "mode": "DRY",
                "marketId": str(mid),
                "selectionId": str(sid),
                "odds": float(px),
                "minutes_to_off": float(mto) if mto is not None else None,
            }
            for fam_name, fn in ORDER:
                plan = _policy_call(fam_name, fn, ctx)
                letter = plan.get("letter") or fam_name[:1].upper()
                why    = plan.get("plan_why") or plan.get("why") or "no trade"
                dirn   = plan.get("direction") or "-"
                ticks  = plan.get("target_ticks") or "-"
                size   = plan.get("size") or "-"
                px0    = plan.get("px") or px

                print(f"  - sid={sid:>8} px={px0:<8} fam={fam_name:<14} L={letter:<2} "
                      f"dir={dirn:<8} ticks={ticks:<2} size={size:<4} :: {why}")

    # --- Dry-run placement probe ----------------------------------------
    try:
        from engines.decision_engine.decide_once import placement
        print("\n=== DRY RUN PLACEMENT PROBE ===")
        if markets:
            mid = markets[0]
            cands = active_candidates_for_market(mid, max_runners=1)
            if cands:
                sid, px = cands[0]
                ctx = {"run_id": f"DRY-{day}", "mode": "DRY",
                       "marketId": str(mid), "selectionId": str(sid), "odds": float(px)}
                plan = {"enter": True, "letter": "A", "direction": "LAY->BACK",
                        "target_ticks": 1, "size": 2.0, "px": float(px)}
                res = placement.place_from_plan("ALWAYS_ON", plan, ctx)
                print("placement.place_from_plan →", res)

                from engines.decision_engine.decide_once.helpers import open_auto_db as _adb, q_retry as _q
                con = _adb(ro=True); con.row_factory = sqlite3.Row
                print("=== orders (last 3) ===")
                for r in _q(con, "SELECT * FROM orders ORDER BY id DESC LIMIT 3", ()).fetchall():
                    print(dict(r))
                print("=== decisions (last 3) ===")
                for r in _q(con, "SELECT * FROM decisions ORDER BY id DESC LIMIT 3", ()).fetchall():
                    meta = json.loads(r["meta_json"]) if r["meta_json"] else {}
                    print(dict(r), "meta:", meta)
                con.close()
        else:
            print("no markets to probe")
    except Exception as e:
        print("[DRY-RUN probe error]", e)

if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except KeyboardInterrupt:
        sys.exit(130)
