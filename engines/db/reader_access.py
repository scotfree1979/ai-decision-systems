# reader_access_layer.py
# ✅ This module reads from RAM, STM, and LTM database tables

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

# ------------------
# RAM ACCESS (Live Snapshots)
# ------------------
def get_ram_snapshot(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT odds, tick_pattern, direction_bias, anchor_odd,
                       range_low, range_high, position_ratio, volatility,
                       tick_trail, oc_snapshots, position, minutes_to_post,
                       tier, last_updated, snapshot
                FROM ram_snapshots
                WHERE marketId = ? AND selectionId = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row:
                return {
                    "odds": row[0],
                    "tick_pattern": row[1],
                    "direction_bias": row[2],
                    "anchor_odd": row[3],
                    "range_low": row[4],
                    "range_high": row[5],
                    "position_ratio": row[6],
                    "volatility": row[7],
                    "tick_trail": json.loads(row[8]) if row[8] else [],
                    "oc_snapshots": json.loads(row[9]) if row[9] else {},
                    "position": row[10],
                    "minutes_to_post": row[11],
                    "tier": row[12],
                    "last_updated": row[13],
                    "snapshot": json.loads(row[14]) if row[14] else {}
                }
    except Exception as e:
        print(f"⚠️ get_ram_snapshot error: {e}")
    return {}

# ------------------
# STM ACCESS (Live Signals)
# ------------------

def get_latest_stm_signal(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT odds, range_low, range_high, tick_pattern,
                       direction_bias, confidence, drift, spread, position_ratio,
                       volatility, signal_type, stake, session_token,
                       timestamp, customerOrderRef, blueprint_match,
                       scalp_direction, scalp_ticks, forced, status
                FROM stm_live_signals
                WHERE marketId = ? AND selectionId = ?
                ORDER BY id DESC
                LIMIT 1
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row:
                return {
                    "marketId": market_id,
                    "selectionId": selection_id,
                    "odds": row[0],
                    "range_low": row[1],
                    "range_high": row[2],
                    "tick_pattern": row[3],
                    "direction_bias": row[4],
                    "confidence": row[5],
                    "drift": row[6],
                    "spread": row[7],
                    "position_ratio": row[8],
                    "volatility": row[9],
                    "signal_type": row[10],
                    "stake": row[11],
                    "session_token": row[12],
                    "timestamp": row[13],
                    "customerOrderRef": row[14],
                    "blueprint_match": row[15],
                    "scalp_direction": row[16],
                    "scalp_ticks": row[17],
                    "forced": bool(row[18]),
                    "status": row[19]
                }
    except Exception as e:
        print(f"⚠️ get_latest_stm_signal error: {e}")
    return {}

# ------------------
# LTM ACCESS (Playbook History)
# ------------------

def get_ltm_playbook(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pattern_key, blueprint_match, scalp_direction,
                       tick_pattern, confidence, scalp_ticks, entry_odds,
                       hedge_odds, entry_side, hedge_side, entry_stake,
                       hedge_stake, pnl, result, opened_at, closed_at,
                       customer_order_ref, ladder_matched, ladder_total
                FROM ltm_playbooks
                WHERE marketId = ? AND selectionId = ?
                ORDER BY closed_at DESC
                LIMIT 1
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row:
                return {
                    "pattern_key": row[0],
                    "blueprint_match": row[1],
                    "scalp_direction": row[2],
                    "tick_pattern": row[3],
                    "confidence": row[4],
                    "scalp_ticks": row[5],
                    "entry_odds": row[6],
                    "hedge_odds": row[7],
                    "entry_side": row[8],
                    "hedge_side": row[9],
                    "entry_stake": row[10],
                    "hedge_stake": row[11],
                    "pnl": row[12],
                    "result": row[13],
                    "opened_at": row[14],
                    "closed_at": row[15],
                    "customer_order_ref": row[16],
                    "ladder_matched": row[17],
                    "ladder_total": row[18]
                }
    except Exception as e:
        print(f"⚠️ get_ltm_playbook error: {e}")
    return {}

# ✅ Reader Layer now complete — ready for integration

# reader_access_layer.py

"""
Unified access layer for reading signal metadata from STM (short-term), RAM (snapshot), and LTM (long-term playbooks).
This replaces direct memory lookups with structured SQL access.
"""

import sqlite3
import json
from config_paths import DB_PATH

# --- STM Access: Fetch Live Signal By Market and Selection ID ---
def get_latest_signal(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT odds, range_low, range_high, tick_pattern, direction_bias, confidence,
                       drift, spread, position_ratio, volatility, signal_type, stake, session_token,
                       customerOrderRef, blueprint_match, scalp_direction, scalp_ticks, forced, status,
                       timestamp
                FROM stm_live_signals
                WHERE marketId = ? AND selectionId = ?
                ORDER BY timestamp DESC
                LIMIT 1
            """, (market_id, selection_id))
            row = cursor.fetchone()

            if row:
                keys = [d[0] for d in cursor.description]
                return dict(zip(keys, row))
            else:
                return None
    except Exception as e:
        print(f"❌ STM lookup failed for {market_id}/{selection_id}: {e}")
        return None

# --- RAM Access: Fetch Snapshot by Market and Selection ID ---
def get_runner_snapshot(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT odds, tick_pattern, direction_bias, anchor_odd, range_low, range_high,
                       position_ratio, volatility, tick_trail, oc_snapshots, position,
                       minutes_to_post, tier, last_updated, snapshot
                FROM ram_snapshots
                WHERE marketId = ? AND selectionId = ?
            """, (market_id, selection_id))
            row = cursor.fetchone()
            if row:
                keys = [d[0] for d in cursor.description]
                result = dict(zip(keys, row))
                # Convert JSON fields
                for field in ["tick_trail", "oc_snapshots", "snapshot"]:
                    if result.get(field):
                        try:
                            result[field] = json.loads(result[field])
                        except:
                            result[field] = {}
                return result
            return None
    except Exception as e:
        print(f"❌ RAM snapshot lookup failed for {market_id}/{selection_id}: {e}")
        return None

# --- LTM Access: Fetch Playbook History for Pattern Key ---
def get_playbook_stats(pattern_key):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*), SUM(pnl) FROM ltm_playbooks
                WHERE pattern_key = ? AND result IN ('success', 'poor_edge')
            """, (pattern_key,))
            row = cursor.fetchone()
            if row:
                count, total_pnl = row
                return {"trades": count, "net_pnl": total_pnl or 0.0}
            return {"trades": 0, "net_pnl": 0.0}
    except Exception as e:
        print(f"❌ LTM stats lookup failed for pattern {pattern_key}: {e}")
        return {"trades": 0, "net_pnl": 0.0}
