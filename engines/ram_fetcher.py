# ram_fetcher.py
# ✅ Fetch signal snapshots from RAM for live use by any module (NextGen, Scalper, etc.)

import sqlite3
import json
from config_paths import DB_PATH
from datetime import datetime

def get_ram_snapshot(market_id, selection_id):
    """
    Returns latest RAM snapshot for given runner. Falls back to empty dict.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot FROM ram_snapshots
                WHERE marketId = ? AND selectionId = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row and row[0]:
                return json.loads(row[0])
    except Exception as e:
        print(f"⚠️ RAM snapshot fetch error for {selection_id}: {e}")
    return {}

def get_all_active_runners():
    """
    Returns list of all market/selection pairs currently stored in RAM.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, selectionId FROM ram_snapshots
            """)
            return cursor.fetchall()
    except Exception as e:
        print(f"⚠️ Failed to fetch active runners from RAM: {e}")
    return []

def get_snapshot_field(market_id, selection_id, field):
    """
    Direct field accessor for lightweight RAM reads (e.g. confidence, tick pattern).
    """
    snap = get_ram_snapshot(market_id, selection_id)
    return snap.get(field)

def is_runner_near_top(market_id, selection_id):
    """
    Boolean shortcut for ladder stop conditions and breakout flags.
    """
    snap = get_ram_snapshot(market_id, selection_id)
    return snap.get("is_near_top", False)

def get_tick_distance_to_range_top(market_id, selection_id):
    """
    Returns number of ticks remaining to top of range.
    """
    snap = get_ram_snapshot(market_id, selection_id)
    return snap.get("ticks_from_high", 999)

def get_tick_distance_to_range_bottom(market_id, selection_id):
    """
    Returns number of ticks remaining to bottom of range.
    """
    snap = get_ram_snapshot(market_id, selection_id)
    return snap.get("ticks_from_low", 999)

def get_tick_pattern(market_id, selection_id):
    """
    Returns tick pattern from RAM (e.g. drifted, steamed, flat).
    """
    return get_snapshot_field(market_id, selection_id, "tick_pattern")

def get_confidence(market_id, selection_id):
    """
    Returns confidence value from RAM.
    """
    return get_snapshot_field(market_id, selection_id, "confidence")
