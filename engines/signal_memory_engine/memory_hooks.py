import sqlite3
import json
import time
from datetime import datetime
from collections import defaultdict
from config_paths import DB_PATH

def get_static_snapshot():
    """
    Returns a dict of all snapshots in runner_ram_snapshots.
    """
    snapshot = {}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT market_id, selection_id, snapshot
                FROM runner_ram_snapshots
            """)
            rows = cursor.fetchall()
            for market_id, selection_id, snap_json in rows:
                try:
                    snap = json.loads(snap_json)
                    snapshot[(market_id, selection_id)] = snap
                except:
                    continue
    except Exception as e:
        print(f"⚠️ get_static_snapshot failed: {e}")
    return snapshot

def save_snapshot_to_ram(market_id, selection_id, snapshot):
    """
    Replaces or inserts snapshot into runner_ram_snapshots.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runner_ram_snapshots (
                    market_id TEXT,
                    selection_id INTEGER,
                    snapshot TEXT,
                    last_updated REAL,
                    PRIMARY KEY (market_id, selection_id)
                )
            """)
            cursor.execute("""
                INSERT OR REPLACE INTO runner_ram_snapshots
                (market_id, selection_id, snapshot, last_updated)
                VALUES (?, ?, ?, ?)
            """, (
                market_id,
                selection_id,
                json.dumps(safe_json(snapshot)),

                time.time()
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to save snapshot to RAM: {e}")

from collections import deque

def safe_json(obj):
    if isinstance(obj, deque):
        return list(obj)
    elif isinstance(obj, dict):
        return {k: safe_json(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [safe_json(v) for v in obj]
    return obj


def update_runner_snapshot_field(market_id, selection_id, field, value):
    """
    Atomic update of one field inside snapshot dict.
    """
    snapshot = get_static_snapshot().get((market_id, selection_id), {})
    snapshot[field] = value
    save_snapshot_to_ram(market_id, selection_id, snapshot)

def flush_inplay_odds_to_db(snapshot_source):
    """
    Flush in-play odds history to DB from live_inplay_odds dict.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            today = datetime.utcnow().strftime("%Y-%m-%d")

            for market_id, runners in snapshot_source.items():
                for selection_id, odds_series in runners.items():
                    if not odds_series:
                        continue
                    cursor.execute("""
                        INSERT INTO inplay_odds_log (
                            market_id, selection_id, date, start_time, end_time, odds_series
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        market_id,
                        selection_id,
                        today,
                        now,
                        now,
                        json.dumps(odds_series)
                    ))
            conn.commit()
        print(f"✅ Flushed in-play odds to DB.")
    except Exception as e:
        print(f"⚠️ Failed to flush in-play odds: {e}")
