# === PATCH START ===
# 📍 TARGET: engines/blueprint/overlay.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create file
from __future__ import annotations
from typing import Dict, Any, Optional
from engines.decision_engine.decide_once.helpers import open_bets_db, q_retry as _q, exec_rows

def _today() -> str:
    return exec_rows(open_bets_db(ro=True), "SELECT date('now','utc')")[0][0] if exec_rows(open_bets_db(ro=True), "SELECT 1") else None

def _fetch_state(day: str, mid: str, sid: str) -> Optional[Dict[str,Any]]:
    con = open_bets_db(ro=True)
    if not con: return None
    r = _q(con, """
      SELECT blueprint_key, score, features_json
        FROM blueprint_state
       WHERE day=? AND marketId=? AND selectionId=?
    """, (day, str(mid), str(sid))).fetchone()
    try: con.close()
    except Exception: pass
    if not r: return None
    return {"key": r[0], "score": r[1], "features": r[2]}

def overlay_plan(marketId: str, selectionId: str, ctx: Dict[str,Any], plan: Dict[str,Any]) -> Dict[str,Any]:
    """
    Lightweight overlay:
      - STEAM_DOMINANT: if plan would LAY the steamer in PRE, flip to BACK->LAY (1-2 ticks), cap size modestly.
      - BROAD_DRIFT: if non-fav drifting, prefer LAY->BACK (2 ticks).
      - OSCILLATION: prefer 1-tick, smaller size.
    Returns original plan if no blueprint or score too low.
    """
    day = _today()
    if not day: 
        return plan
    state = _fetch_state(day, str(marketId), str(selectionId))
    if not state or not state.get("key"):
        return plan
    key = str(state["key"] or "")
    score = float(state.get("score") or 0.0)
    if score < 0.55:  # guard
        return plan

    p = dict(plan)
    p.setdefault("meta", {})
    p["meta"]["blueprint"] = {"key": key, "score": score}

    # current direction tag in plan (string)
    dir_tag = str(p.get("direction", ""))

    # STEAM_DOMINANT: avoid shorting the steamer in PRE
    if key == "STEAM_DOMINANT" and ctx.get("phase","PRE") == "PRE":
        if dir_tag.startswith("LAY"):
            p["direction"] = "BACK->LAY"
            p["target_ticks"] = max(1, min(2, int(p.get("target_ticks", 1))))
            p["size"] = float(min(float(p.get("size",2.0)), 5.0))
            p["why"] = (p.get("why","") + " | bp:steam_flip").strip(" |")

    # BROAD_DRIFT: general drift — favour LAY->BACK on non-favs
    if key == "BROAD_DRIFT" and ctx.get("phase","PRE") == "PRE":
        if not dir_tag.startswith("LAY"):
            p["direction"] = "LAY->BACK"
        p["target_ticks"] = max(1, 2)
        p["why"] = (p.get("why","") + " | bp:broad_drift").strip(" |")

    # OSCILLATION: tight & smaller
    if key == "OSCILLATION":
        p["target_ticks"] = 1
        p["size"] = float(max(2.0, min(float(p.get("size",2.0)), 3.0)))
        p["why"] = (p.get("why","") + " | bp:osc").strip(" |")

    return p
# === PATCH END ===
