# memory_signal_writer.py
# ✅ This module writes signal snapshots to STM, LTM, and RAM
# Used instead of calling signal_memory.memory_write directly

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

def write_to_stm_live_signals(signal):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stm_live_signals (
                    marketId, selectionId, odds, range_low, range_high, tick_pattern,
                    direction_bias, confidence, drift, spread, position_ratio, volatility,
                    signal_type, stake, session_token, timestamp, customerOrderRef,
                    blueprint_match, scalp_direction, scalp_ticks, forced, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("marketId"),
                signal.get("selectionId"),
                signal.get("odds"),
                signal.get("range_low"),
                signal.get("range_high"),
                signal.get("tick_pattern"),
                signal.get("direction_bias"),
                signal.get("confidence"),
                signal.get("drift", 0.0),
                signal.get("spread", 0),
                signal.get("position_ratio"),
                signal.get("volatility", 0.0),
                signal.get("signal_type", "None"),
                signal.get("stake"),
                signal.get("session_token"),
                signal.get("timestamp", datetime.utcnow().isoformat()),
                signal.get("customerOrderRef"),
                signal.get("blueprint_match"),
                signal.get("scalp_direction"),
                signal.get("scalp_ticks"),
                int(signal.get("forced", False)),
                signal.get("status")
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to write STM live signal: {e}")

def write_to_ram_snapshots(signal):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO ram_snapshots (
                    marketId, selectionId, odds, tick_pattern, direction_bias, anchor_odd,
                    range_low, range_high, position_ratio, volatility, tick_trail, oc_snapshots,
                    position, minutes_to_post, tier, last_updated, snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("marketId"),
                signal.get("selectionId"),
                signal.get("odds"),
                signal.get("tick_pattern"),
                signal.get("direction_bias"),
                signal.get("anchor_odd"),
                signal.get("range_low"),
                signal.get("range_high"),
                signal.get("position_ratio"),
                signal.get("volatility", 0.0),
                json.dumps(signal.get("tick_trail", [])),
                json.dumps(signal.get("oc_snapshots", {})),
                signal.get("position"),
                signal.get("minutes_to_post"),
                signal.get("tier", "unknown"),
                datetime.utcnow().isoformat(),
                json.dumps(signal)
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to write RAM snapshot: {e}")
