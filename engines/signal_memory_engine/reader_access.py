# 📁 signal_memory_engine/reader_access.py
# ✅ Updated to include DB-based fallback + legacy compatibility functions

from signal_memory_engine.ram_reader import get_ram_snapshot, get_static_snapshot
from signal_memory_engine.utils.odds_math import get_tick_difference
from datetime import datetime

# ✅ Get the most recent odds (tick trail) from DB-backed snapshot

def get_active_runner_odds(market_id, selection_id):
    snapshot = get_ram_snapshot(market_id, selection_id)
    return snapshot.get("snapshot", {}).get("tick_trail", [])[-1] if snapshot else None


# ✅ Get market start time from DB-based static snapshot

def get_market_start_time(market_id):
    static = get_static_snapshot()
    for (mid, _), data in static.items():
        if mid == market_id:
            return data.get("snapshot", {}).get("marketStartTime")
    return None


# ✅ Get minutes to off for a runner using static snapshot

def get_minutes_to_off(runner_id):
    try:
        market_id = runner_id.split("_")[0]
        start = get_market_start_time(market_id)
        if not start:
            return 999
        dt = datetime.fromisoformat(start)
        diff = (dt - datetime.utcnow()).total_seconds() / 60.0
        return round(diff, 1)
    except:
        return 999


# ✅ Get all active runner IDs (marketID_selectionID) from DB snapshot keys

def get_active_runner_ids():
    static = get_static_snapshot()
    return [f"{mid}_{sid}" for (mid, sid) in static.keys()]


# ✅ Legacy compatibility: Final runner snapshot evaluator

def build_final_runner_snapshot(market_id, selection_id):
    key = (market_id, selection_id)
    runner = get_ram_snapshot(*key).get("snapshot", {})

    range_low = runner.get("range_low")
    range_high = runner.get("range_high")
    tick_trail = runner.get("tick_trail", [])
    current_odds = tick_trail[-1] if tick_trail else runner.get("anchor_odd", 0.0)

    position_ratio = 0.5
    ticks_from_low = 0
    ticks_from_high = 0
    ticks_available = 0

    if isinstance(current_odds, (int, float)) and isinstance(range_low, (int, float)) and isinstance(range_high, (int, float)) and range_low < range_high:
        position_ratio = round((current_odds - range_low) / (range_high - range_low + 0.0001), 3)
        position_ratio = max(0.0, min(1.0, position_ratio))
        ticks_from_low = get_tick_difference(range_low, current_odds)
        ticks_from_high = get_tick_difference(current_odds, range_high)
        ticks_available = get_tick_difference(range_low, range_high)

    snapshot = {
        "marketId": market_id,
        "selectionId": selection_id,
        "tick_pattern": runner.get("tick_pattern", "flat"),
        "direction_bias": runner.get("direction_bias", "flat"),
        "range_low": range_low,
        "range_high": range_high,
        "anchor_odd": runner.get("anchor_odd"),
        "position_ratio": position_ratio,
        "ticks_from_low": ticks_from_low,
        "ticks_from_high": ticks_from_high,
        "ticks_available": ticks_available,
        "is_near_top": ticks_from_high <= 2,
        "is_near_bottom": ticks_from_low <= 2,
        "is_breakout_imminent": ticks_from_high <= 1 or ticks_from_low <= 1,
        "volatility": runner.get("volatility", 0.0),
        "tier": runner.get("tier", "unknown"),
        "minutes_to_post": runner.get("minutes_to_post", 999),
        "tick_trail": tick_trail,
        "oc_snapshots": runner.get("oc_snapshots", {}),
        "position": runner.get("position"),
        "last_updated": runner.get("last_updated")
    }

    return snapshot
