# signal_memory_engine/relay.py
import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH


def relay_signal_to_memory(signal):
    """
    ✅ Relay fallback signals (low confidence or edge-case) to DB for manual approval or delayed betting.
    Signals go into `signals_approved` table.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS signals_approved (
                    marketId TEXT,
                    selectionId INTEGER,
                    odds REAL,
                    range_low REAL,
                    range_high REAL,
                    tick_pattern TEXT,
                    confidence REAL,
                    direction TEXT,
                    signal_type TEXT,
                    timestamp TEXT,
                    meta_json TEXT
                )
            """)

            cursor.execute("""
                INSERT INTO signals_approved (
                    marketId, selectionId, odds, range_low, range_high,
                    tick_pattern, confidence, direction, signal_type,
                    timestamp, meta_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("marketId"),
                signal.get("selectionId"),
                signal.get("odds"),
                signal.get("range_low"),
                signal.get("range_high"),
                signal.get("tick_pattern"),
                signal.get("confidence"),
                signal.get("scalp_direction"),
                signal.get("signal_type", "fallback"),
                datetime.utcnow().isoformat(),
                json.dumps(signal)
            ))

            conn.commit()
            print(f"✅ Signal relayed to DB → signals_approved: {signal.get('selectionId')}")

    except Exception as e:
        print(f"❌ Failed to relay signal to memory DB: {e}")
