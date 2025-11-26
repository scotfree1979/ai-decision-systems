# ram_snapshot_reader.py

"""
Provides read-only access to the RAM memory snapshot database,
used by tools that need access to current runner states, tick patterns,
position ratios, OC snapshots, etc.
"""

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH


def get_runner_snapshot(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot FROM ram_snapshots
                WHERE marketId = ? AND selectionId = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if not row or not row[0]:
                return None
            return json.loads(row[0])
    except Exception as e:
        print(f"⚠️ RAM read error for {market_id}-{selection_id}: {e}")
        return None


def list_all_active_snapshots(min_odds=1.01, max_odds=20.0):
    """
    Returns all snapshots where odds fall within sensible range.
    This can be used to power dashboards or engines that need visibility
    into real-time runner states without memory reliance.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, selectionId, snapshot
                FROM ram_snapshots
                WHERE odds BETWEEN ? AND ?
            """, (min_odds, max_odds))
            rows = cursor.fetchall()
            return [
                {
                    "marketId": r[0],
                    "selectionId": r[1],
                    "snapshot": json.loads(r[2]) if r[2] else {}
                }
                for r in rows
            ]
    except Exception as e:
        print(f"⚠️ RAM snapshot listing failed: {e}")
        return []


# Optional: Filtered by time or tier

def get_snapshots_updated_since(min_timestamp):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, selectionId, snapshot
                FROM ram_snapshots
                WHERE last_updated >= ?
            """, (min_timestamp,))
            rows = cursor.fetchall()
            return [
                {
                    "marketId": r[0],
                    "selectionId": r[1],
                    "snapshot": json.loads(r[2]) if r[2] else {}
                }
                for r in rows
            ]
    except Exception as e:
        print(f"⚠️ Snapshot timestamp query failed: {e}")
        return []

# ram_snapshot_reader.py

"""
This module provides utility accessors to the RAM snapshot table — acting as read-only memory lookups.
It is used by downstream logic such as:
- Signal quality filters
- Confidence adjustments
- Volatility checks
- Post-signal evaluation dashboards
"""

import sqlite3
import json
from config_paths import DB_PATH

def get_ram_snapshot():
    import sqlite3
    import json
    from config_paths import DB_PATH
    out = {}
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT market_id, selection_id, snapshot FROM runner_ram_snapshots")
        for row in cursor.fetchall():
            key = (row[0], row[1])
            try:
                snap = json.loads(row[2])
                out[key] = snap
            except:
                continue
    return out


def get_snapshot_field(market_id, selection_id, field):
    """
    Quickly fetch one field from the snapshot JSON.
    """
    snapshot = get_ram_snapshot(market_id, selection_id)
    return snapshot.get(field)

def list_ram_snapshot_keys():
    """
    Returns a list of (marketId, selectionId) keys stored in RAM.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, selectionId FROM ram_snapshots
            """)
            return cursor.fetchall()
    except Exception as e:
        print(f"⚠️ Failed to list RAM snapshot keys: {e}")
        return []

def get_all_ram_snapshots():
    """
    Fetches all runner snapshots in full.
    """
    snapshots = {}
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT marketId, selectionId, snapshot FROM ram_snapshots")
            for market_id, selection_id, snap in cursor.fetchall():
                if snap:
                    try:
                        snapshots[(market_id, selection_id)] = json.loads(snap)
                    except:
                        continue
    except Exception as e:
        print(f"⚠️ Failed to read all RAM snapshots: {e}")
    return snapshots

def ensure_odds_synced_to_snapshot(snapshot, odds):
    from collections import deque

    if "tick_trail" not in snapshot:
        snapshot["tick_trail"] = deque(maxlen=12)
    if isinstance(snapshot["tick_trail"], list):
        snapshot["tick_trail"] = deque(snapshot["tick_trail"], maxlen=12)

    snapshot["tick_trail"].append(odds)
    snapshot["odds_history"] = list(snapshot["tick_trail"])  # sync both
    snapshot["odds"] = odds
    return snapshot
