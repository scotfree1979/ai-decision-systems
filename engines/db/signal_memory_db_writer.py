import sqlite3
import json
import time
from config_paths import DB_PATH

import json
from collections import deque

def safe_json(obj):
    if isinstance(obj, deque):
        return list(obj)
    if isinstance(obj, dict):
        return {k: safe_json(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [safe_json(v) for v in obj]
    return obj




def save_snapshot_to_ram(market_id, selection_id, snapshot):
    try:
        snapshot = safe_json(snapshot)  # ✅ FIXED: clean it before saving
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO runner_ram_snapshots (market_id, selection_id, snapshot, last_updated)
                VALUES (?, ?, ?, ?)
            """, (
                market_id,
                selection_id,
                json.dumps(snapshot),  # ✅ Now safe
                time.time()
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to save snapshot: {e}")

def save_signal_to_stm(signal):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signal_tracker_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_id TEXT,
                    selection_id INTEGER,
                    timestamp REAL,
                    confidence REAL,
                    tick_pattern TEXT,
                    range_low REAL,
                    range_high REAL
                )
            """)
            cursor.execute("""
                INSERT INTO signal_tracker_memory (
                    market_id, selection_id, timestamp,
                    confidence, tick_pattern, range_low, range_high
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                signal["marketId"],
                signal["selectionId"],
                time.time(),
                signal.get("confidence", 0.0),
                signal.get("tick_pattern", "flat"),
                signal.get("range_low"),
                signal.get("range_high")
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to save signal to STM: {e}")
