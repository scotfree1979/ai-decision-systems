# 📦 MATCHING ENGINE BUILD START
# This module will handle the end-to-end analysis and signal triggering for:
# 1. Exploratory scalp with 1 chapter
# 2. Partial match scalp with 2 chapters
# 3. Full blueprint match scalp with 3+ chapters
# It predicts next chapters using blueprint pattern keys, and tracks success/failure to improve future predictions.

import time
from datetime import datetime
from collections import defaultdict

# === Blueprint Matching Engine ===
def evaluate_matching_opportunity(self, market_id, selection_id):
    """
    Entrypoint to assess current chapter state and decide:
    - What match tier we can evaluate (exploratory, partial, full)
    - If prediction can be made from blueprints
    - If scalp should be triggered
    """
    story = self.get_runner_story(market_id, selection_id)
    chapters = story.get("chapters", [])
    if not chapters:
        return None

    num_chapters = len(chapters)
    snapshot = self.build_final_runner_snapshot(market_id, selection_id)
    tick_pattern = snapshot.get("tick_pattern")
    direction = snapshot.get("direction_bias")

    if not tick_pattern or not direction:
        return None

    pattern_key_parts = []
    for c in chapters:
        if c.get("tick_pattern"):
            pattern_key_parts.append(c["tick_pattern"])
            pattern_key_parts.append(c.get("oc_label", "OC?"))
    pattern_key_parts.append(tick_pattern)
    pattern_key_parts.append(direction)

    pattern_key = "→".join(pattern_key_parts)

    if num_chapters == 1:
        return self.evaluate_exploratory_scalp(market_id, selection_id, pattern_key)
    elif num_chapters == 2:
        return self.evaluate_partial_match_scalp(market_id, selection_id, pattern_key)
    elif num_chapters >= 3:
        return self.evaluate_full_blueprint_match(market_id, selection_id, pattern_key)

    return None


def evaluate_exploratory_scalp(self, market_id, selection_id, pattern_key):
    # Always eligible with 1 chapter
    return self.create_signal_dict(market_id, selection_id, pattern_key, "exploratory", confidence=0.51)


def evaluate_partial_match_scalp(self, market_id, selection_id, pattern_key):
    # Check if any blueprint partially matches this key
    for known in self.KNOWN_BLUEPRINT_PATTERNS:
        if known.startswith(pattern_key):
            return self.create_signal_dict(market_id, selection_id, known, "partial", confidence=0.62)
    return None


def evaluate_full_blueprint_match(self, market_id, selection_id, pattern_key):
    if pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
        return self.create_signal_dict(market_id, selection_id, pattern_key, "full", confidence=0.74)
    return None


def create_signal_dict(self, market_id, selection_id, blueprint_match, signal_type, confidence):
    odds = self.get_latest_odds(market_id, selection_id)
    if not odds:
        return None

    snapshot = self.build_final_runner_snapshot(market_id, selection_id)
    return {
        "marketId": market_id,
        "selectionId": selection_id,
        "odds": odds,
        "range_low": snapshot.get("range_low"),
        "range_high": snapshot.get("range_high"),
        "tick_pattern": snapshot.get("tick_pattern"),
        "position_ratio": snapshot.get("position_ratio"),
        "volatility": snapshot.get("volatility"),
        "scalp_direction": snapshot.get("direction_bias"),
        "signal_type": signal_type,
        "confidence": confidence,
        "minutes_to_post": snapshot.get("minutes_to_post"),
        "blueprint_match": blueprint_match
    }
