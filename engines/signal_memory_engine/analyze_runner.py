import time
from collections import deque
from volatility_check import get_recent_odds, get_volatility_meta
from price_math import get_tick_size
from db.signal_memory_db_writer import save_snapshot_to_ram
from signal_memory_engine.ram_reader import get_ram_snapshot
from signal_memory_engine.ram_reader import get_runner_story


def analyse_runner(self, market_id, runners):
    # Step 1: Rank runners by current odds
    sorted_runners = sorted(
        [r for r in runners if isinstance(r.get("odds"), (int, float))],
        key=lambda x: x["odds"]
    )
    for idx, runner in enumerate(sorted_runners):
        runner["position"] = idx + 1

    for r in runners:
        selection_id = r["selectionId"]
        current_odds = r.get("odds")
        if not isinstance(current_odds, (int, float)):
            continue

        key = (market_id, selection_id)

        # === Tier assignment ===
        if current_odds <= 12.0:
            tier = "active"
        elif current_odds <= 50.0:
            tier = "passive"
        else:
            tier = "ignored"

        if tier == "ignored":
            continue  # Skip entirely

        # === Use completed chapters only ===
        story = get_runner_story(market_id, selection_id)
        chapters = story.get("chapters", [])
        current_label = self.get_current_oc_label(r.get("minutes_to_post", 999))
        completed = [c for c in chapters if c.get("oc_label") != current_label]
        if not completed:
            continue

        latest = completed[-1]

        range_low = latest.get("range_low")
        range_high = latest.get("range_high")
        position_ratio = latest.get("position_ratio", 0.5)
        tick_pattern = latest.get("tick_pattern", "flat")
        direction_bias = latest.get("direction_bias", "flat")
        volatility = latest.get("volatility", 0.0)

        # === Build snapshot for memory
        snapshot = get_ram_snapshot(market_id, selection_id).get("snapshot", {})
        snapshot.update({
            "range_low": range_low,
            "range_high": range_high,
            "tick_pattern": tick_pattern,
            "direction_bias": direction_bias,
            "position_ratio": position_ratio,
            "volatility": volatility,
            "position": r.get("position"),
            "tier": tier,
            "last_updated": time.time(),
            "chapter_source": latest.get("oc_label")
        })

        save_snapshot_to_ram(market_id, selection_id, snapshot)
