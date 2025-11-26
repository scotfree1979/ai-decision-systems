# ✅ OC Snapshot Tracker – signal_memory_engine/update_oc_snapshots.py

import json
from datetime import datetime, timezone
from database_hijack_monitor import enqueue_write
from ram_reader import safe_read

# Internal tracker for band data
runner_band_tracker = {}  # key: (market_id, selection_id)

# 🧠 OC schedule in minutes to post
OC_SCHEDULE = {
    1: 80,
    2: 60,
    3: 40,
    4: 20,
    5: 10,
    6: 5,
    7: 0,
    8: -1,
    9: -2,
    10: -3,
    11: -4,
    12: -5,
    13: -6,
    14: -7,
    15: -8,
    16: -9,
    17: -10,
    18: -11,
    19: -12,
    20: -13
}

# Convert OC schedule to ordered list of tuples: (OCx, threshold_minutes)
OC_TIMELINE = sorted(OC_SCHEDULE.items(), key=lambda x: -x[1])

def get_current_oc(minutes_to_post):
    for oc_num, min_threshold in OC_TIMELINE:
        if minutes_to_post >= min_threshold:
            return oc_num
    return None

def update_oc_snapshot_if_due(market_id, selection_id, current_odds, market_start_time_str):
    now = datetime.now(timezone.utc)
    market_start = datetime.fromisoformat(market_start_time_str.replace("Z", "+00:00"))
    minutes_to_post = round((market_start - now).total_seconds() / 60.0, 1)

    oc_key = (market_id, selection_id)
    current_oc = get_current_oc(minutes_to_post)
    if current_oc is None or current_oc > 20:
        return  # Out of range

    # Initialize memory for this runner if needed
    if oc_key not in runner_band_tracker:
        runner_band_tracker[oc_key] = {
            "active_oc": current_oc,
            "band": [],
            "saved_ocs": set()
        }

    memory = runner_band_tracker[oc_key]

    # If new OC started, save previous one
    if current_oc > memory["active_oc"]:
        memory["band"] = []  # Reset trail for new OC
        memory["active_oc"] = current_oc

    memory["band"].append(current_odds)

    # If this OC already saved, skip
    if current_oc in memory["saved_ocs"]:
        return

    # Get table column names
    oc_field = f"odds_check_{current_oc}"
    band_field = f"oc{current_oc}_band"

    # Write to bets table
    query = f"""
        UPDATE bets
        SET {oc_field} = ?, {band_field} = ?
        WHERE marketId = ? AND selectionId = ?
    """
    values = [current_odds, json.dumps(memory["band"]), market_id, selection_id]
    enqueue_write(query, values)
    memory["saved_ocs"].add(current_oc)
