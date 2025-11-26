import sqlite3
import time
import json
from datetime import datetime
from signal_memory_engine import signal_memory

from config_paths import DB_PATH  # Path to the database with ram_snapshots

# ✅ RAM Writer Module
# File: upgrade/memory/ram_writer.py

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH


def write_ram_snapshot(market_id, selection_id, snapshot):
    try:
        with sqlite3.connect(DB_PATH) as conn:
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
                snapshot.get("tick_trail", [None])[-1],
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
            print(f"🧠 [RAM] Snapshot saved: {selection_id} in {market_id}")
    except Exception as e:
        print(f"⚠️ [RAM] Failed to write snapshot for {selection_id}: {e}")


# Example usage from memory sync:
# from upgrade.memory.ram_writer import write_ram_snapshot
# write_ram_snapshot(market_id, selection_id, snapshot)


def write_ram_snapshots():
    while True:
        try:
            snapshot = signal_memory.get_static_snapshot()
            now = datetime.utcnow().isoformat()

            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()

                for (market_id, selection_id), data in snapshot.items():
                    snap = data.get("snapshot", {})
                    if not snap:
                        continue

                    tick_trail = json.dumps(snap.get("tick_trail", []))
                    oc_snapshots = json.dumps(snap.get("oc_snapshots", {}))
                    snapshot_blob = json.dumps(snap)

                    cursor.execute("""
                        INSERT OR REPLACE INTO ram_snapshots (
                            marketId, selectionId, odds, tick_pattern, direction_bias,
                            anchor_odd, range_low, range_high, position_ratio, volatility,
                            tick_trail, oc_snapshots, position, minutes_to_post, tier,
                            last_updated, snapshot
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
                        snapshot_blob
                    ))
                conn.commit()

            print(f"🧠 RAM Snapshots synced: {len(snapshot)} runners")

        except Exception as e:
            print(f"⚠️ RAM snapshot writer failed: {e}")

        time.sleep(10)

if __name__ == "__main__":
    write_ram_snapshots()
