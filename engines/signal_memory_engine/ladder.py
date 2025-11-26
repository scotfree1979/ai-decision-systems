import time
import uuid
from collections import deque
from db.signal_memory_db_writer import save_snapshot_to_ram
from signal_memory_engine.ram_reader import get_static_snapshot
from signal_memory_engine.utils.stake import get_adaptive_stake
from signal_memory_engine.blueprint_partial import match_blueprint_partial
from signal_memory_engine.playbook import start_playbook
from price_math import get_tick_size
from signal_memory_engine.utils.tick_math import get_tick_difference



def _attempt_next_ladder_rung(self, key, allow_partial=True):
    market_id, selection_id = key
    ladder = self.active_ladders.get(key)
    if not ladder:
        print(f"❌ No ladder config found for {key}")
        return
    if ladder["rung"] > 0 and not self._both_sides_matched(key):
        print(f"⛔ Ladder halted – waiting for both sides to match before rung {ladder['rung'] + 1} on {key}")
        return

    direction = ladder["direction"]
    current_odds = ladder["current_odds"]
    tick_size = ladder["tick_size"]
    rung = ladder["rung"]
    max_rungs = ladder.get("max_rungs", 3)
    stake = ladder["stake"]

    if rung >= max_rungs:
        print(f"✅ Ladder complete for {key} → Rungs: {rung}/{max_rungs}")
        ladder["status"] = "complete"
        return

    next_odds = current_odds - tick_size if direction == "lay_to_back" else current_odds + tick_size
    side = "BACK" if direction == "lay_to_back" else "LAY"

    print(f"🎯 Ladder rung {rung+1} for {key}: {side} @ {next_odds:.2f} | Stake: £{stake:.2f}")

    customer_order_ref = f"ladder_{uuid.uuid4().hex[:6]}"
    response = self.place_ladder_bet(market_id, selection_id, side, next_odds, stake, customer_order_ref)

    ladder["rung"] += 1
    ladder["current_odds"] = next_odds
    ladder.setdefault("rung_log", []).append({
        "rung": ladder["rung"],
        "odds": next_odds,
        "side": side,
        "status": "placed" if response else "failed",
        "timestamp": time.time()
    })

    if allow_partial:
        time.sleep(ladder.get("ladder_interval", 1))
        self._attempt_next_ladder_rung(key, allow_partial=True)


def place_ladder_bet(self, market_id, selection_id, side, odds, stake, customer_order_ref):
    from utils.api_tools import place_bet
    try:
        response = place_bet(
            market_id=market_id,
            selection_id=selection_id,
            side=side,
            odds=odds,
            stake=stake,
            customer_order_ref=customer_order_ref
        )
        print(f"✅ Ladder bet placed: {side} {selection_id} @ {odds} for £{stake}")
        return response
    except Exception as e:
        print(f"❌ Failed to place ladder bet: {e}")
        return None


def should_continue_ladder(self, key):
    ladder = self.active_ladders.get(key, {})
    snapshot = get_static_snapshot().get(key, {}).get("snapshot", {})
    odds = snapshot.get("tick_trail", [])[-1] if snapshot.get("tick_trail") else None
    direction = ladder.get("direction")

    if not ladder or ladder.get("status") != "active" or not odds:
        return False

    if ladder.get("rung", 0) >= ladder.get("max_rungs", 3):
        print(f"❌ Ladder halted — max rungs hit for {key}")
        return False

    if odds > ladder.get("range_high", 999) or odds > ladder.get("tier_max", 999):
        print(f"❌ Ladder halted — odds {odds} beyond boundary {ladder.get('range_high', 999)}")
        return False

    if self.detect_trend_reversal(key, direction, odds):
        print(f"❌ Ladder halted — trend reversal detected for {key}")
        return False

    if ladder.get("rung_log"):
        last = ladder["rung_log"][-1]
        if time.time() - last.get("timestamp", 0) > 10:
            print(f"❌ Ladder halted — stale rung >10s ago for {key}")
            return False

    return True


def detect_trend_reversal(self, key, direction, odds):
    snapshot = get_static_snapshot().get(key, {}).get("snapshot", {})
    anchor = snapshot.get("anchor_odd")
    if not anchor or not odds:
        return False

    ticks_moved = get_tick_difference(anchor, odds)

    if direction == "lay_to_back" and odds > anchor and ticks_moved >= 3:
        return True
    if direction == "back_to_lay" and odds < anchor and ticks_moved >= 3:
        return True
    return False

import sqlite3
from config_paths import DB_PATH
from price_math import get_tick_size

def check_and_register_new_ladders(self):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT customerOrderRef, marketId, selectionId, odds, stake, side
            FROM bets
            WHERE status = 'matched'
        """)
        rows = cursor.fetchall()
        conn.close()
    except Exception as e:
        print(f"❌ Ladder DB check failed: {e}")
        return

    for ref, market_id, selection_id, odds, stake, side in rows:
        key = (market_id, selection_id)
        if key not in self.active_ladders:
            direction = "lay_to_back" if side.upper() == "LAY" else "back_to_lay"
            self.active_ladders[key] = {
                "rung": 1,
                "current_odds": odds,
                "tick_size": get_tick_size(odds),
                "direction": direction,
                "stake": stake,
                "status": "active"
            }
            print(f"📦 Ladder activated: {ref} {selection_id} @ {odds} [{direction}]")
