# writer_access_layer.py

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

# -------------------
# RAM (Real-Time Memory)
# -------------------
def write_to_ram_snapshot(snapshot: dict):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO ram_snapshots (
                    marketId, selectionId, odds, tick_pattern, direction_bias, anchor_odd,
                    range_low, range_high, position_ratio, volatility,
                    tick_trail, oc_snapshots, position, minutes_to_post,
                    tier, last_updated, snapshot
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                snapshot.get("marketId"),
                snapshot.get("selectionId"),
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
                snapshot.get("last_updated"),
                json.dumps(snapshot)
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to write RAM snapshot: {e}")

# -------------------
# STM (Short-Term Memory)
# -------------------
def insert_into_stm_live_signals(signal: dict):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stm_live_signals (
                    marketId, selectionId, odds, range_low, range_high, tick_pattern, direction_bias,
                    confidence, drift, spread, position_ratio, volatility, signal_type,
                    stake, session_token, timestamp, customerOrderRef,
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
                signal.get("drift"),
                signal.get("spread"),
                signal.get("position_ratio"),
                signal.get("volatility"),
                signal.get("signal_type"),
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
        print(f"⚠️ Failed to insert into STM live_signals: {e}")

# -------------------
# LTM (Long-Term Memory)
# -------------------
def insert_into_ltm_playbooks(playbook: dict):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ltm_playbooks (
                    playbook_group_id, marketId, selectionId, pattern_key, blueprint_match,
                    scalp_direction, tick_pattern, confidence, scalp_ticks,
                    entry_odds, hedge_odds, entry_side, hedge_side,
                    entry_stake, hedge_stake, entry_bet_id, hedge_bet_id,
                    pnl, result, opened_at, closed_at, customer_order_ref, ladder_matched, ladder_total
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                playbook.get("playbook_group_id"),
                playbook.get("marketId"),
                playbook.get("selectionId"),
                playbook.get("pattern_key"),
                playbook.get("blueprint_match"),
                playbook.get("scalp_direction"),
                playbook.get("tick_pattern"),
                playbook.get("confidence"),
                playbook.get("scalp_ticks"),
                playbook.get("entry_odds"),
                playbook.get("hedge_odds"),
                playbook.get("entry_side"),
                playbook.get("hedge_side"),
                playbook.get("entry_stake"),
                playbook.get("hedge_stake"),
                playbook.get("entry_bet_id"),
                playbook.get("hedge_bet_id"),
                playbook.get("pnl"),
                playbook.get("result"),
                playbook.get("opened_at"),
                playbook.get("closed_at"),
                playbook.get("customer_order_ref"),
                playbook.get("ladder_matched"),
                playbook.get("ladder_total")
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to insert into LTM playbooks: {e}")
# writer_access_layer.py

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

def write_ram_snapshot(market_id, selection_id, snapshot):
    """
    Writes or updates a runner's snapshot in RAM database.
    """
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

def write_stm_signal(signal):
    """
    Writes a new signal into STM (short-term memory) signal table.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO stm_live_signals (
                marketId, selectionId, odds, range_low, range_high,
                tick_pattern, direction_bias, confidence, drift, spread,
                position_ratio, volatility, signal_type, stake, session_token,
                timestamp, customerOrderRef, blueprint_match, scalp_direction,
                scalp_ticks, forced, status
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
            signal.get("drift"),
            signal.get("spread"),
            signal.get("position_ratio"),
            signal.get("volatility"),
            signal.get("signal_type"),
            signal.get("stake"),
            signal.get("session_token"),
            signal.get("time_signal") or datetime.utcnow().isoformat(),
            signal.get("customerOrderRef"),
            signal.get("blueprint_match"),
            signal.get("scalp_direction"),
            signal.get("scalp_ticks"),
            signal.get("forced", 0),
            signal.get("status")
        ))
        conn.commit()

def write_ltm_playbook_entry(playbook):
    """
    Writes a completed playbook entry to long-term memory.
    """
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ltm_playbooks (
                playbook_group_id, marketId, selectionId, pattern_key, blueprint_match,
                scalp_direction, tick_pattern, confidence, scalp_ticks,
                entry_odds, hedge_odds, entry_side, hedge_side,
                entry_stake, hedge_stake, entry_bet_id, hedge_bet_id,
                pnl, result, opened_at, closed_at, customer_order_ref,
                ladder_matched, ladder_total
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            playbook.get("playbook_group_id"),
            playbook.get("marketId"),
            playbook.get("selectionId"),
            playbook.get("pattern_key"),
            playbook.get("blueprint_match"),
            playbook.get("scalp_direction"),
            playbook.get("tick_pattern"),
            playbook.get("confidence"),
            playbook.get("scalp_ticks"),
            playbook.get("entry_odds"),
            playbook.get("hedge_odds"),
            playbook.get("entry_side"),
            playbook.get("hedge_side"),
            playbook.get("entry_stake"),
            playbook.get("hedge_stake"),
            playbook.get("entry_bet_id"),
            playbook.get("hedge_bet_id"),
            playbook.get("pnl"),
            playbook.get("result"),
            playbook.get("opened_at"),
            playbook.get("closed_at"),
            playbook.get("customer_order_ref"),
            playbook.get("ladder_matched"),
            playbook.get("ladder_total")
        ))
        conn.commit()
