import time
import copy
import uuid
from db.signal_memory_db_writer import save_snapshot_to_ram
from signal_memory_engine.ram_reader import get_ram_snapshot
from signal_memory_engine.signal_blueprint_match import detect_blueprint_match
from signal_memory_engine.utils.bet_router import fire_initial_scalp

def evaluate_signal_for_runner(self, market_id, selection_id):
    key = (market_id, selection_id)
    runner = get_ram_snapshot(market_id, selection_id)
    if not runner:
        return

    snapshot = copy.deepcopy(runner.get("snapshot", {}))
    if not snapshot or "oc_snapshots" not in snapshot or "OC0" not in snapshot["oc_snapshots"]:
        return

    tick_pattern = snapshot.get("tick_pattern", "flat")
    odds = snapshot.get("tick_trail", [])[-1] if snapshot.get("tick_trail") else snapshot.get("anchor_odd", 0.0)
    direction = "lay_to_back" if tick_pattern == "drifted" else "back_to_lay"
    confidence = 0.7

    signal = {
        "marketId": market_id,
        "selectionId": selection_id,
        "odds": odds,
        "range_low": snapshot.get("range_low"),
        "range_high": snapshot.get("range_high"),
        "tick_pattern": tick_pattern,
        "position_ratio": snapshot.get("position_ratio", 0.5),
        "volatility": snapshot.get("volatility", 0.0),
        "scalp_direction": direction,
        "signal_type": "evaluate_signal",
        "confidence": confidence,
        "minutes_to_post": snapshot.get("minutes_to_post", 999),
        "forced": True
    }

    self.signal_eval_per_market[market_id] += 1
    total = sum(self.signal_eval_per_market.values())
    if total != self._last_signal_eval_total:
        self._last_signal_eval_total = total
        print(f"📊 Total signals evaluated: {total}")

    # Blueprint match logic
    match_result = detect_blueprint_match(snapshot)

    if match_result:
        signal.update({
            "blueprint_match": match_result.get("blueprint_key"),
            "match_type": match_result["match_type"],
            "match_depth": match_result["match_depth"],
            "story_confidence": match_result["confidence"],
            "signal_type": "blueprint_match",
            "scalp_ticks": 2 if match_result["match_type"] == "full_story" else 1,
            "forced": True,
            "customerOrderRef": f"bp_{match_result['match_type']}_{uuid.uuid4().hex[:6]}"
        })

        if signal["marketId"] in self.active_tradable_markets:
            response = fire_initial_scalp(signal, signal["customerOrderRef"])
            if response:
                print(f"✅ Blueprint scalp fired ({match_result['match_type']}) for {signal['selectionId']} at {signal['odds']}")
        else:
            print(f"⚠️ Match found but market is inactive: {signal['marketId']}")

        self.track_unmatched_signal(signal)

    # Always re-persist snapshot at end
    snapshot["snapshot"] = self.build_final_runner_snapshot(market_id, selection_id)
    save_snapshot_to_ram(market_id, selection_id, snapshot)
