import sqlite3
import time
import json
from datetime import datetime
from config_paths import DB_PATH
from collections import defaultdict


def start_playbook(self, market_id, selection_id, pattern_key):
    group_id = str(uuid.uuid4())[:8]
    self.live_playbooks[(market_id, selection_id)] = {
        "playbook_group_id": group_id,
        "pattern_key": pattern_key,
        "opened_at": time.time(),
        "status": "open",
        "pnl": 0.0,
        "steps": [f"start:{pattern_key}"]
    }
    print(f"📘 New playbook started: {pattern_key} → ID {group_id}")


def finalise_playbook(self, market_id, selection_id, result, profit):
    key = (market_id, selection_id)
    if key not in self.live_playbooks:
        return

    playbook = self.live_playbooks[key]
    playbook["closed_at"] = time.time()
    playbook["status"] = result
    ladder_log = playbook.get("steps", [])
    for s in ladder_log:
        if s.startswith("ladder:"):
            try:
                rung_data = json.loads(s.replace("ladder:", ""))
                playbook["ladder_matched"] = sum(1 for r in rung_data if r.get("status") == "matched")
                playbook["ladder_total"] = len(rung_data)
            except:
                pass

    playbook["pnl"] = profit

    pattern = playbook.get("pattern_key")
    self.track_live_pattern_pnl(pattern, profit)

    print(f"📘 Playbook finished for {selection_id}: {pattern} | Result: {result} | PnL: £{profit:.2f}")


def save_playbook_to_db(self, market_id, selection_id, playbook):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS playbooks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    playbook_group_id TEXT,
                    marketId TEXT,
                    selectionId INTEGER,
                    pattern_key TEXT,
                    blueprint_match TEXT,
                    scalp_direction TEXT,
                    tick_pattern TEXT,
                    confidence REAL,
                    scalp_ticks INTEGER,
                    entry_odds REAL,
                    hedge_odds REAL,
                    entry_side TEXT,
                    hedge_side TEXT,
                    entry_stake REAL,
                    hedge_stake REAL,
                    entry_bet_id TEXT,
                    hedge_bet_id TEXT,
                    pnl REAL,
                    result TEXT,
                    opened_at TEXT,
                    closed_at TEXT,
                    customer_order_ref TEXT,
                    ladder_matched INTEGER,
                    ladder_total INTEGER
                )
            """)
            cursor.execute("""
                INSERT INTO playbooks (
                    playbook_group_id, marketId, selectionId, pattern_key, blueprint_match,
                    scalp_direction, tick_pattern, confidence, scalp_ticks,
                    entry_odds, hedge_odds, entry_side, hedge_side,
                    entry_stake, hedge_stake, entry_bet_id, hedge_bet_id,
                    pnl, result, opened_at, closed_at, customer_order_ref, ladder_matched, ladder_total
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                playbook.get("playbook_group_id"),
                market_id,
                selection_id,
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
        print(f"⚠️ Failed to save playbook: {e}")


def record_matched_bet(self, market_id, selection_id, odds, stake, side, profit=None, pattern_key=None, signal=None, bet_id=None):
    key = (market_id, selection_id)
    if key in self.live_playbooks:
        self.live_playbooks[key].update({
            "blueprint_match": signal.get("blueprint_match"),
            "scalp_direction": signal.get("scalp_direction"),
            "tick_pattern": signal.get("tick_pattern"),
            "confidence": signal.get("confidence"),
            "scalp_ticks": signal.get("scalp_ticks"),
            "signal_type": signal.get("signal_type")
        })

        if side == "entry":
            self.live_playbooks[key]["entry_odds"] = odds
            self.live_playbooks[key]["entry_stake"] = stake
            self.live_playbooks[key]["entry_side"] = side
            self.live_playbooks[key]["entry_bet_id"] = signal.get("entry_bet_id") if signal else None
        elif side == "hedge":
            self.live_playbooks[key]["hedge_odds"] = odds
            self.live_playbooks[key]["hedge_stake"] = stake
            self.live_playbooks[key]["hedge_side"] = side
            self.live_playbooks[key]["hedge_bet_id"] = signal.get("hedge_bet_id") if signal else None

        if signal and "customerOrderRef" in signal:
            self.live_playbooks[key]["customer_order_ref"] = signal.get("customerOrderRef")

        if bet_id:
            self.open_bets[key].append(side)
            print(f"✅ Recorded matched {side} bet for {key} → Bet ID: {bet_id}")
        else:
            print(f"⚠️ No {side}_bet_id provided — bet may not be active.")

    group_id = self.live_playbooks[key].get("playbook_group_id") if key in self.live_playbooks else None

    entry = {
        "timestamp": time.time(),
        "market_id": market_id,
        "selection_id": selection_id,
        "odds": odds,
        "stake": stake,
        "side": side,
        "profit": profit,
        "playbook_group_id": group_id
    }
    engine.counter["playbook_complete"] += 1

    self.trade_log.append(entry)

    if profit is not None:
        self.current_balance += profit
        if pattern_key:
            self.track_live_pattern_pnl(pattern_key, profit)
