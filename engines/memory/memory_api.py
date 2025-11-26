# Phase 8: memory_api_bridge.py

"""
This file acts as a seamless interface between existing signal_memory references
and the new persistent memory DB structure. 

All calls like:
    signal_memory.memory_write[(market_id, selection_id)]

...will now map to:
    memory_api.set_runner_field(...)
    memory_api.get_runner_snapshot(...)

This layer ensures existing code continues to work with minimal rewriting.
"""

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

class MemoryAPIBridge:
    def __init__(self):
        self.db_path = DB_PATH

    def set_runner_snapshot(self, market_id, selection_id, snapshot: dict):
        """Set full snapshot record (overwrites existing)."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO ram_snapshots (
                    marketId, selectionId, odds, tick_pattern, direction_bias, anchor_odd,
                    range_low, range_high, position_ratio, volatility, tick_trail,
                    oc_snapshots, position, minutes_to_post, tier, last_updated, snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id,
                selection_id,
                snapshot.get("odds"),
                snapshot.get("tick_pattern"),
                snapshot.get("direction_bias"),
                snapshot.get("anchor_odd"),
                snapshot.get("range_low"),
                snapshot.get("range_high"),
                snapshot.get("position_ratio"),
                snapshot.get("volatility"),
                json.dumps(snapshot.get("tick_trail", [])),
                json.dumps(snapshot.get("oc_snapshots", {})),
                snapshot.get("position"),
                snapshot.get("minutes_to_post"),
                snapshot.get("tier"),
                datetime.utcnow().isoformat(),
                json.dumps(snapshot)
            ))
            conn.commit()

    def get_runner_snapshot(self, market_id, selection_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot FROM ram_snapshots
                WHERE marketId = ? AND selectionId = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if not row or not row[0]:
                return None
            try:
                return json.loads(row[0])
            except:
                return None

    def get_all_runners(self):
        """Return dict of all runners indexed by (marketId, selectionId)."""
        out = {}
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT marketId, selectionId, snapshot FROM ram_snapshots")
            for row in cursor.fetchall():
                key = (row[0], row[1])
                try:
                    snap = json.loads(row[2])
                    out[key] = snap
                except:
                    continue
        return out

    def get_snapshot_keys(self):
        """Return a list of (marketId, selectionId) keys present in RAM."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT marketId, selectionId FROM ram_snapshots")
            return cursor.fetchall()

# ✅ Singleton
memory_api = MemoryAPIBridge()


# memory_api.py

import sqlite3
import json
from config_paths import DB_PATH
from datetime import datetime
from collections import defaultdict


# 🔁 INTERNAL CACHE (optional for performance)
_memory_cache = defaultdict(dict)

# ✅ UPDATE RUNNER SNAPSHOT

def update_snapshot(market_id, selection_id, data):
    key = (market_id, selection_id)
    _memory_cache[key].update(data)
    _memory_cache[key]["last_updated"] = datetime.utcnow().isoformat()


# ✅ GET STATIC SNAPSHOT (deepcopy optional)

def get_snapshot():
    return dict(_memory_cache)


# ✅ RECORD SIGNAL OUTCOME

def record_outcome(market_id, selection_id, result, explanation=""):
    key = (market_id, selection_id)
    _memory_cache[key].setdefault("signal_outcomes", []).append({
        "timestamp": datetime.utcnow().isoformat(),
        "result": result,
        "explanation": explanation or "N/A"
    })


# ✅ BUILD SNAPSHOT ON DEMAND

def build_snapshot(key):
    runner = _memory_cache.get(key, {})
    tick_trail = runner.get("tick_trail", [])
    oc_snaps = runner.get("oc_snapshots", {})

    try:
        odds = tick_trail[-1] if tick_trail else runner.get("anchor_odd")
        range_low = runner.get("range_low")
        range_high = runner.get("range_high")
        if not (isinstance(range_low, (int, float)) and isinstance(range_high, (int, float))):
            range_low, range_high = None, None
        position_ratio = 0.5
        if range_low and range_high and range_low < range_high and odds:
            position_ratio = round((odds - range_low) / (range_high - range_low + 0.0001), 3)
        snapshot = {
            "marketId": key[0],
            "selectionId": key[1],
            "tick_pattern": runner.get("tick_pattern", "flat"),
            "direction_bias": runner.get("direction_bias", "flat"),
            "range_low": range_low,
            "range_high": range_high,
            "anchor_odd": runner.get("anchor_odd"),
            "position_ratio": position_ratio,
            "volatility": runner.get("volatility", 0.0),
            "tier": runner.get("tier", "unknown"),
            "minutes_to_post": runner.get("minutes_to_post", 999),
            "tick_trail": tick_trail,
            "oc_snapshots": oc_snaps,
            "position": runner.get("position"),
            "last_updated": runner.get("last_updated")
        }
        _memory_cache[key]["snapshot"] = snapshot
        return snapshot
    except Exception:
        return {}


# ✅ ACCESS SNAPSHOT FOR INDIVIDUAL RUNNER

def get_runner_snapshot(market_id, selection_id):
    return _memory_cache.get((market_id, selection_id), {}).get("snapshot", {})


# ✅ CHECK BOTH SIDES MATCHED

def both_sides_matched(bet_list):
    return bet_list.count("entry") == bet_list.count("hedge") and len(bet_list) > 0
