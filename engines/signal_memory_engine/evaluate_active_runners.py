import sqlite3
import json
from config_paths import DB_PATH


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
                except:
                    continue
    except Exception as e:
        print(f"⚠️ get_static_snapshot error: {e}")
    return snapshot
