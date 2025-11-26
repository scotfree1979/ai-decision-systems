# stm_writer_engine.py

import sqlite3
import threading
import time
from datetime import datetime
from config_paths import DB_PATH
from signal_memory_engine import signal_memory


def write_stm_live_signals():
    while True:
        try:
            snapshot = signal_memory.get_static_snapshot()
            now = datetime.utcnow().isoformat()

            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()

                for key, runner in snapshot.items():
                    market_id, selection_id = key
                    snap = runner.get("snapshot", {})
                    if not snap:
                        continue

                    tick_pattern = snap.get("tick_pattern")
                    range_low = snap.get("range_low")
                    range_high = snap.get("range_high")
                    anchor_odd = snap.get("anchor_odd")
                    odds = snap.get("tick_trail", [])[-1] if snap.get("tick_trail") else anchor_odd
                    confidence = runner.get("confidence") or 0.72
                    direction = snap.get("direction_bias")
                    position_ratio = snap.get("position_ratio", 0.5)
                    volatility = snap.get("volatility", 0.0)
                    signal_type = snap.get("signal_type", "observation")
                    stake = runner.get("stake", 10.0)
                    scalp_direction = runner.get("scalp_direction")
                    scalp_ticks = runner.get("scalp_ticks", 2)
                    status = runner.get("status", "observed")
                    blueprint_match = runner.get("blueprint_match")
                    session_token = runner.get("session_token")

                    cursor.execute("""
                        INSERT INTO stm_live_signals (
                            marketId, selectionId, odds, range_low, range_high,
                            tick_pattern, direction_bias, confidence, drift,
                            spread, position_ratio, volatility, signal_type,
                            stake, session_token, timestamp, customerOrderRef,
                            blueprint_match, scalp_direction, scalp_ticks,
                            forced, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        market_id,
                        selection_id,
                        odds,
                        range_low,
                        range_high,
                        tick_pattern,
                        direction,
                        confidence,
                        runner.get("drift", 0.0),
                        runner.get("spread", 0.0),
                        position_ratio,
                        volatility,
                        signal_type,
                        stake,
                        session_token,
                        now,
                        runner.get("customerOrderRef"),
                        blueprint_match,
                        scalp_direction,
                        scalp_ticks,
                        int(runner.get("forced", False)),
                        status
                    ))

                conn.commit()

        except Exception as e:
            print(f"⚠️ STM Writer Error: {e}")

        time.sleep(10)  # Write every 10 seconds


def start_stm_writer_thread():
    threading.Thread(target=write_stm_live_signals, name="STMWriterThread", daemon=True).start()
    print("🧠 STM Writer thread launched.")
