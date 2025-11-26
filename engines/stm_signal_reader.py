# stm_signal_reader.py

import sqlite3
from config_paths import DB_PATH
from datetime import datetime


def get_latest_signals(limit=10):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, selectionId, odds, range_low, range_high,
                       tick_pattern, direction_bias, confidence, drift, spread,
                       position_ratio, volatility, signal_type, stake,
                       session_token, timestamp, customerOrderRef,
                       blueprint_match, scalp_direction, scalp_ticks,
                       forced, status
                FROM stm_live_signals
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()

            signals = []
            for row in rows:
                signals.append({
                    "marketId": row[0],
                    "selectionId": row[1],
                    "odds": row[2],
                    "range_low": row[3],
                    "range_high": row[4],
                    "tick_pattern": row[5],
                    "direction_bias": row[6],
                    "confidence": row[7],
                    "drift": row[8],
                    "spread": row[9],
                    "position_ratio": row[10],
                    "volatility": row[11],
                    "signal_type": row[12],
                    "stake": row[13],
                    "session_token": row[14],
                    "timestamp": row[15],
                    "customerOrderRef": row[16],
                    "blueprint_match": row[17],
                    "scalp_direction": row[18],
                    "scalp_ticks": row[19],
                    "forced": bool(row[20]),
                    "status": row[21]
                })
            return signals
    except Exception as e:
        print(f"⚠️ Failed to load STM signals: {e}")
        return []


def get_signal_for_runner(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT marketId, selectionId, odds, range_low, range_high,
                       tick_pattern, direction_bias, confidence, drift, spread,
                       position_ratio, volatility, signal_type, stake,
                       session_token, timestamp, customerOrderRef,
                       blueprint_match, scalp_direction, scalp_ticks,
                       forced, status
                FROM stm_live_signals
                WHERE marketId = ? AND selectionId = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if not row:
                return None

            return {
                "marketId": row[0],
                "selectionId": row[1],
                "odds": row[2],
                "range_low": row[3],
                "range_high": row[4],
                "tick_pattern": row[5],
                "direction_bias": row[6],
                "confidence": row[7],
                "drift": row[8],
                "spread": row[9],
                "position_ratio": row[10],
                "volatility": row[11],
                "signal_type": row[12],
                "stake": row[13],
                "session_token": row[14],
                "timestamp": row[15],
                "customerOrderRef": row[16],
                "blueprint_match": row[17],
                "scalp_direction": row[18],
                "scalp_ticks": row[19],
                "forced": bool(row[20]),
                "status": row[21]
            }
    except Exception as e:
        print(f"⚠️ Failed to fetch signal for {market_id} → {selection_id}: {e}")
        return None
