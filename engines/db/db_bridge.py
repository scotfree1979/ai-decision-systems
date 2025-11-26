import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

# 📘 This module handles writing signal data into the appropriate memory DBs

class SignalDatabaseBridge:
    def __init__(self):
        self.stm_table = "stm_live_signals"
        self.ltm_table = "ltm_playbooks"
        self.ram_table = "ram_snapshots"

    def save_to_stm(self, signal):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    INSERT INTO {self.stm_table} (
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
            print(f"⚠️ Failed to save to STM (live_signals): {e}")

    def save_to_ltm(self, playbook):
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    INSERT INTO {self.ltm_table} (
                        playbook_group_id, marketId, selectionId, pattern_key, blueprint_match,
                        scalp_direction, tick_pattern, confidence, scalp_ticks, entry_odds,
                        hedge_odds, entry_side, hedge_side, entry_stake, hedge_stake,
                        entry_bet_id, hedge_bet_id, pnl, result, opened_at, closed_at,
                        customer_order_ref, ladder_matched, ladder_total
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
                    playbook.get("status"),
                    playbook.get("opened_at"),
                    playbook.get("closed_at"),
                    playbook.get("customer_order_ref"),
                    playbook.get("ladder_matched"),
                    playbook.get("ladder_total")
                ))
                conn.commit()
        except Exception as e:
            print(f"⚠️ Failed to save to LTM (playbooks): {e}")

    def save_to_ram(self, signal):
        try:
            snapshot = signal.get("snapshot") or {}
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(f"""
                    INSERT OR REPLACE INTO {self.ram_table} (
                        marketId, selectionId, odds, tick_pattern, direction_bias, anchor_odd,
                        range_low, range_high, position_ratio, volatility, tick_trail,
                        oc_snapshots, position, minutes_to_post, tier, last_updated, snapshot
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
                    signal.get("volatility"),
                    json.dumps(snapshot.get("tick_trail", [])),
                    json.dumps(snapshot.get("oc_snapshots", {})),
                    snapshot.get("position"),
                    snapshot.get("minutes_to_post"),
                    snapshot.get("tier"),
                    datetime.utcnow().isoformat(),
                    json.dumps(snapshot)
                ))
                conn.commit()
        except Exception as e:
            print(f"⚠️ Failed to save to RAM (snapshot): {e}")

# Singleton instance
signal_db = SignalDatabaseBridge()
