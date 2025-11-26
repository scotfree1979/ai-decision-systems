# 📁 signal_memory_engine/snapshot_builder.py

import sqlite3
import json
from datetime import datetime
from signal_memory_engine.reader_access import get_market_start_time
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engines.config_paths import DB_PATH
from engines.database_hijack_monitor import enqueue_write


def safe_json(obj):
    try:
        import json
        return json.loads(json.dumps(obj))
    except Exception:
        return {}


def get_db_snapshot(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT snapshot FROM runner_ram_snapshots
                WHERE market_id = ? AND selection_id = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            return json.loads(row[0]) if row else {}
    except Exception as e:
        print(f"❌ DB snapshot load failed for {market_id}_{selection_id}: {e}")
        return {}

def build_band_json(runner_id):
    try:
        market_id, selection_id = runner_id.split("_")
        snapshot = get_db_snapshot(market_id, selection_id)
        if not snapshot or not isinstance(snapshot, dict):
            return {}

        oc_snapshots = snapshot.get("oc_snapshots", {})
        band_json = {
            key: val for key, val in oc_snapshots.items()
            if key.endswith("_band") and isinstance(val, list)
        }

        # estimate minutes to off
        mto = 999
        start = get_market_start_time(market_id)
        if start:
            try:
                dt = datetime.fromisoformat(start)
                mto = (dt - datetime.utcnow()).total_seconds() / 60.0
            except:
                pass

        return band_json, round(mto, 1)
    except Exception as e:
        print(f"❌ Error in build_band_json: {e}")
        return {}, 999
