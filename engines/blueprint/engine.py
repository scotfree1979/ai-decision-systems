# === PATCH START ===
# 📍 TARGET: engines/blueprint/engine.py
# 🔎 SEARCH: (file does not exist)
# ⛏️ ACTION: create file
from __future__ import annotations
from typing import Dict, Tuple
import json

from engines.odds.features import detect_steam_dominant, detect_broad_drift, detect_oscillation
from engines.odds.writers import record_blueprint_event, upsert_blueprint_state

def update_for_market(day: str,
                      marketId: str,
                      odds_now: Dict[str,float],
                      odds_then_30s: Dict[str,float],
                      runner_feats: Dict[str,Dict],
                      ranks_now: Dict[str,int],
                      mto_minutes: float,
                      market_breadth_val: float,
                      window_tag: str = "PRE") -> None:
    """
    Evaluate detectors, write events/state.
    """
    # Collect detector outputs
    steam = detect_steam_dominant(runner_feats, ranks_now, market_breadth_val)
    broad = detect_broad_drift(runner_feats, ranks_now, market_breadth_val)
    osci  = detect_oscillation(runner_feats, ranks_now)

    # Build per-runner best suggestion
    # blueprint_state keeps 1 best key per runner, but events can log multiple
    for sid, (score, reason) in steam.items():
        ctx = {"reason": reason, "mto": mto_minutes, "breadth": market_breadth_val}
        record_blueprint_event(day, marketId, sid, "STEAM_DOMINANT", score, window_tag, json.dumps(ctx))
    for sid, (score, reason) in broad.items():
        ctx = {"reason": reason, "mto": mto_minutes, "breadth": market_breadth_val}
        record_blueprint_event(day, marketId, sid, "BROAD_DRIFT", score, window_tag, json.dumps(ctx))
    for sid, (score, reason) in osci.items():
        ctx = {"reason": reason, "mto": mto_minutes}
        record_blueprint_event(day, marketId, sid, "OSCILLATION", score, window_tag, json.dumps(ctx))

    # Choose best per runner
    by_sid: Dict[str, Tuple[str,float,str]] = {}
    def _consider(key, bag):
        for sid,(sc, rsn) in bag.items():
            if sid not in by_sid or sc > by_sid[sid][1]:
                by_sid[sid] = (key, sc, rsn)
    _consider("STEAM_DOMINANT", steam)
    _consider("BROAD_DRIFT", broad)
    _consider("OSCILLATION", osci)

    # Write state
    for sid in odds_now.keys():
        if sid in by_sid:
            key, sc, rsn = by_sid[sid]
        else:
            key, sc, rsn = (None, None, "")
        feat_pack = dict(runner_feats.get(sid, {}))
        feat_pack.update({"rank_now": int(ranks_now.get(sid,99)), "mto": mto_minutes, "breadth": market_breadth_val})
        upsert_blueprint_state(day, marketId, sid, key, sc, json.dumps(feat_pack))
# === PATCH END ===
