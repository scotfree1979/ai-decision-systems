# memory_router.py

"""
📦 Memory Router Core
This module decides whether a signal read or write should be routed to:
- RAM (real-time signal memory)
- STM (live signals tracking)
- LTM (full playbook + bet lifecycle archive)

It acts as a controller across all memory types.
"""

import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

class MemoryRouter:
    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path

    def route_signal(self, signal):
        """
        🧠 Main entry point to route signal based on signal_type, tier, or other logic.
        """
        signal_type = signal.get("signal_type", "unknown")
        tier = signal.get("tier", "active")

        if signal_type in ["blueprint_match", "partial_blueprint"] or tier == "active":
            self._write_to_stm(signal)
        else:
            self._write_to_ram(signal)

    def archive_playbook(self, playbook):
        """
        🗃️ Finalised playbooks are sent to LTM.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO ltm_playbooks (
                        playbook_group_id, marketId, selectionId, pattern_key, blueprint_match,
                        scalp_direction, tick_pattern, confidence, scalp_ticks,
                        entry_odds, hedge_odds, entry_side, hedge_side,
                        entry_stake, hedge_stake, entry_bet_id, hedge_bet_id,
                        pnl, result, opened_at, closed_at, customer_order_ref, ladder_matched, ladder_total
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            print(f"❌ [MemoryRouter] Failed to archive playbook: {e}")

    def _write_to_stm(self, signal):
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO stm_live_signals (
                        marketId, selectionId, odds, range_low, range_high, tick_pattern,
                        direction_bias, confidence, drift, spread, position_ratio,
                        volatility, signal_type, stake, session_token, timestamp,
                        customerOrderRef, blueprint_match, scalp_direction, scalp_ticks, forced, status
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
                    signal.get("timestamp"),
                    signal.get("customerOrderRef"),
                    signal.get("blueprint_match"),
                    signal.get("scalp_direction"),
                    signal.get("scalp_ticks"),
                    signal.get("forced", 0),
                    signal.get("status")
                ))
                conn.commit()
        except Exception as e:
            print(f"❌ [MemoryRouter] STM signal write failed: {e}")

    def _write_to_ram(self, signal):
        try:
            oc_snaps = signal.get("oc_snapshots") or {}
            tick_trail = signal.get("tick_trail") or []
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO ram_snapshots (
                        marketId, selectionId, odds, tick_pattern, direction_bias,
                        anchor_odd, range_low, range_high, position_ratio,
                        volatility, tick_trail, oc_snapshots, position,
                        minutes_to_post, tier, last_updated, snapshot
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
                    json.dumps(tick_trail),
                    json.dumps(oc_snaps),
                    signal.get("position"),
                    signal.get("minutes_to_post"),
                    signal.get("tier"),
                    datetime.utcnow().isoformat(),
                    json.dumps(signal)
                ))
                conn.commit()
        except Exception as e:
            print(f"❌ [MemoryRouter] RAM signal write failed: {e}")

memory_router = MemoryRouter()
