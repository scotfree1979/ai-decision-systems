import sqlite3
import json
from datetime import datetime
from collections import deque
from threading import Lock
import os
import sys
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engines.database_hijack_monitor import enqueue_read
from config_paths import DB_PATH


lock = Lock()

# ✅ Safe Read Helper
def safe_read(query, params=None):
    try:
        def _query_exec():
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(query, params or [])
                return cursor.fetchall()
        return enqueue_read(_query_exec)
    except Exception as e:
        print(f"❌ RAMReader safe_read failed: {e}")
        return []

# ✅ Public Interface for Downstream Use
def get_bets_row(market_id, selection_id):
    query = """
        SELECT * FROM bets WHERE marketId = ? AND selectionId = ?
    """
    rows = safe_read(query, [market_id, selection_id])
    return rows[0] if rows else None

def get_ram_snapshot(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot FROM runner_ram_snapshots
                WHERE market_id = ? AND selection_id = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if not row:
                return {}
            snapshot = json.loads(row[0])
            return {"snapshot": snapshot}
    except Exception as e:
        print(f"⚠️ get_ram_snapshot error for {market_id}, {selection_id}: {e}")
        return {}

def get_static_snapshot():
    snapshot = {}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT market_id, selection_id, snapshot FROM runner_ram_snapshots
            """)
            rows = cursor.fetchall()
            for market_id, selection_id, raw in rows:
                try:
                    data = json.loads(raw)
                    snapshot[(market_id, selection_id)] = {"snapshot": data}
                except json.JSONDecodeError:
                    continue
    except Exception as e:
        print(f"⚠️ get_static_snapshot error: {e}")
    return snapshot


def load_oc_snapshots_from_db(self):
    print("📦 [load_oc_snapshots_from_db] Starting snapshot load...")
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT marketId, selectionId, anchor_odd,
                   odds_check_1, odds_check_2, odds_check_3,
                   odds_check_4, odds_check_5, odds_check_6
            FROM bets
            WHERE anchor_odd IS NOT NULL AND DATE(marketStartTime) = ?
        """, (today_str,))
        rows = cursor.fetchall()

    from signal_memory_engine.memory_hooks import save_snapshot_to_ram
    for market_id, selection_id, anchor, *ocs in rows:
        tick_trail = [o for o in [anchor] + list(ocs) if isinstance(o, (int, float))]
        range_low = min(tick_trail) if tick_trail else None
        range_high = max(tick_trail) if tick_trail else None
        oc_snaps = {}
        if anchor is not None:
            oc_snaps["OC0"] = {"odds": anchor}
        for i, val in enumerate(ocs):
            if val is not None:
                oc_snaps[f"OC{i+1}"] = {"odds": val}
        snapshot = {
            "odds_history": deque(maxlen=12),
            "last_updated": None,
            "range_exits": 0,
            "range_exit_start": None,
            "direction_bias": "flat",
            "anchor_odd": anchor,
            "range_low": range_low,
            "range_high": range_high,
            "entry_opportunities": 0,
            "oc_snapshots": oc_snaps,
            "last_oc_breakout": None,
            "tick_pattern": "flat",
            "position": None,
            "position_ratio": 0.5,
            "volatility": 0.0,
            "tier": "active" if anchor and anchor <= 12.0 else "passive" if anchor and anchor <= 50.0 else "ignored"
        }
        save_snapshot_to_ram(market_id, selection_id, snapshot)
    print("✅ [load_oc_snapshots_from_db] Completed.")

def build_final_runner_snapshot(market_id, selection_id):
    from signal_memory_engine.ram_reader import get_ram_snapshot
    from signal_memory_engine.utils.odds_math import get_tick_difference

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

    # Band Analysis Injected Here
    from signal_memory_engine.ram_reader import get_full_band_window, analyze_band_volatility, detect_reversal_pattern, get_band_delta_series

    ocx_band = get_full_band_window(market_id, selection_id)
    band_volatility = analyze_band_volatility(ocx_band)
    reversal_detected = detect_reversal_pattern(ocx_band)
    tick_deltas = get_band_delta_series(ocx_band)

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
        "last_updated": runner.get("last_updated"),

        # Band-specific fields
        "ocx_band_volatility": band_volatility,
        "ocx_band_reversal": reversal_detected,
        "ocx_band_deltas": tick_deltas
    }

    return snapshot
import sqlite3
import json
from config_paths import DB_PATH

def get_runner_story(market_id, selection_id):
    import sqlite3
    import json
    from signal_memory_engine.snapshot_builder import safe_json
    from engines.config_paths import DB_PATH

    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT
                    chapter_0, chapter_1, chapter_2, chapter_3, chapter_4,
                    chapter_5, chapter_6, chapter_7, chapter_8, chapter_9,
                    chapter_10, chapter_11, chapter_12, chapter_13, chapter_14,
                    chapter_15, chapter_16, chapter_17, chapter_18, chapter_19, chapter_20
                FROM runner_ram_snapshots
                WHERE market_id = ? AND selection_id = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()

            if not row:
                return {"chapters": []}

            chapters = []
            for item in row:
                if item:
                    try:
                        chapters.append(json.loads(item))
                    except:
                        continue

            return {"chapters": chapters}

    except Exception as e:
        print(f"❌ get_runner_story() failed: {e}")
        return {"chapters": []}
