# 📦 RAM WRITER – Live Snapshot Syncing Engine

import threading
import time
import sqlite3
import json
from datetime import datetime

from config_paths import DB_PATH

# Injected snapshot source
from signal_memory_engine import signal_memory  # Must provide .get_static_snapshot()


def persist_ram_snapshots():
    print("🧠 RAM Writer Thread: Started")

    while True:
        try:
            static_snapshot = signal_memory.get_static_snapshot()
            now = datetime.utcnow().isoformat()

            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()

                for key, runner in static_snapshot.items():
                    market_id, selection_id = key
                    snap = runner.get("snapshot", {})
                    if not snap:
                        continue

                    # Serialize nested fields
                    tick_trail = json.dumps(snap.get("tick_trail", []))
                    oc_snapshots = json.dumps(snap.get("oc_snapshots", {}))
                    full_snapshot = json.dumps(snap)

                    cursor.execute("""
                        INSERT OR REPLACE INTO ram_snapshots (
                            marketId, selectionId, odds, tick_pattern, direction_bias,
                            anchor_odd, range_low, range_high, position_ratio, volatility,
                            tick_trail, oc_snapshots, position, minutes_to_post,
                            tier, last_updated, snapshot
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        market_id,
                        selection_id,
                        snap.get("tick_trail", [])[-1] if snap.get("tick_trail") else snap.get("anchor_odd"),
                        snap.get("tick_pattern"),
                        snap.get("direction_bias"),
                        snap.get("anchor_odd"),
                        snap.get("range_low"),
                        snap.get("range_high"),
                        snap.get("position_ratio"),
                        snap.get("volatility"),
                        tick_trail,
                        oc_snapshots,
                        snap.get("position"),
                        snap.get("minutes_to_post"),
                        snap.get("tier"),
                        now,
                        full_snapshot
                    ))
                conn.commit()
                print(f"📦 RAM Writer: Synced {len(static_snapshot)} runner snapshots → ram_snapshots")

        except Exception as e:
            print(f"❌ RAM Writer Error: {e}")

        time.sleep(10)  # Adjustable interval


# 🚀 Launch thread from signal_memory_engine or main orchestrator

def start_ram_writer():
    thread = threading.Thread(target=persist_ram_snapshots, name="RAMWriterThread", daemon=True)
    thread.start()
    print("✅ RAM Writer: Background thread launched")
