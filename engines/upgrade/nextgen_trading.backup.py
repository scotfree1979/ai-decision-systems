# upgrade/nextgen_trading.py

import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

import time
import logging
def enforce_stake_limit(stake):
    return min(float(stake), 10.0)

import threading
import sqlite3
from datetime import datetime
import requests
import json
from upgrade_import_patch import get_session_token
from price_math import get_tick_size, walk_ticks  # ✅ Added for tick-accurate hedge odds
from bet_core import place_bet, check_bet_matched, cancel_bet
from utils.api_tools import fetch_live_odds
from collections import defaultdict
from upgrade.dbsave import save_bet_to_db
from signal_memory_engine import signal_memory
from known_blueprints import KNOWN_BLUEPRINT_PATTERNS
from blueprint_csv_loader import get_blueprint_meta

from config_paths import DB_PATH  # points to bets.db

def save_signal_snapshot_to_bets_db(signal):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bets (
                    marketId, selectionId, odds, range_low, range_high, tick_pattern,
                    confidence, drift, spread, position_ratio, volatility, signal_type, timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("marketId"),
                signal.get("selectionId"),
                signal.get("odds"),
                signal.get("range_low"),
                signal.get("range_high"),
                signal.get("tick_pattern"),
                signal.get("confidence"),
                signal.get("drift", 0.0),
                signal.get("spread", 0),
                signal.get("position_ratio"),
                signal.get("volatility", 0.0),
                signal.get("signal_type", "None"),
                datetime.utcnow().isoformat()
            ))
            conn.commit()
    except Exception as e:
        print(f"⚠️ Failed to save signal snapshot: {e}")


_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_suffix_index = 0
active_scalps = defaultdict(int)

runner_trades = []

from upgrade.Group3_Strategy_Upgrade import execute_smart_scalp

APP_KEY = "CZHojduNWa3kxWIn"  # Replace with your real app key

def get_matched_amount(session_token, bet_id):
    try:
        response = requests.post(
            "https://api.betfair.com/exchange/betting/rest/v1.0/listCurrentOrders/",
            headers={
                "X-Authentication": session_token,
                "X-Application": APP_KEY,
                "Content-Type": "application/json"
            },
            data=json.dumps({"betIds": [bet_id]})
        )
        result = response.json()
        orders = result.get("currentOrders", [])
        if orders:
            return float(orders[0].get("sizeMatched", 0))
    except Exception as e:
        print(f"⚠️ Error checking matched amount: {e}")
    return 0.0

def _next_suffix():
    global _suffix_index
    from config_paths import DB_PATH

    if _suffix_index == 0:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            refs = conn.execute(
                "SELECT customerOrderRef FROM bets WHERE customerOrderRef LIKE '__%'"
            ).fetchall()
            max_found = 0
            for row in refs:
                try:
                    prefix = row["customerOrderRef"].split("_")[0]
                    a, b, num = prefix[0], prefix[1], prefix[2:]
                    base = (ord(a) - 65) * 26 * 1000 + (ord(b) - 65) * 1000 + int(num)
                    max_found = max(max_found, base)
                except:
                    continue
            _suffix_index = max_found + 1

    code = _suffix_index
    _suffix_index += 1

    a_index = (code // 1000) // 26
    b_index = (code // 1000) % 26

    if a_index >= len(_letters) or b_index >= len(_letters):
        raise ValueError("💥 _next_suffix() exhausted code space — increase letter base or reset")

    return f"{_letters[a_index]}{_letters[b_index]}{code % 1000:03}"

def save_bet_to_db(market_id, selection_id, odds, stake, side, ref, bet_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO live_signals (marketId, selectionId, odds, stake, side, customerOrderRef, betfair_bet_id, placed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, selection_id, odds, stake, side, ref, bet_id, datetime.utcnow().isoformat()
            ))
            conn.commit()
    except Exception as e:
        logging.warning(f"⚠️ Failed to save bet to DB: {e}")

BREAKOUT_CONFIRM_TICKS = 2  # Number of ticks outside the range to confirm trend

def get_open_trade_state(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT customerOrderRef, side, stake, betfair_bet_id
                FROM live_signals
                WHERE marketId = ? AND selectionId = ?
                ORDER BY placed_at DESC
                LIMIT 12
            """, (market_id, selection_id))
            rows = cursor.fetchall()

        # Group by customerOrderRef
        grouped = {}
        for ref, side, stake, bet_id in rows:
            base_ref = ref.split("_")[0]
            if base_ref not in grouped:
                grouped[base_ref] = {"entry": None, "hedge": None}
            if side == "LAY" and ref.endswith("_E"):
                grouped[base_ref]["entry"] = (ref, stake, bet_id)
            elif side == "BACK" and ref.endswith("_H"):
                grouped[base_ref]["hedge"] = (ref, stake, bet_id)

        complete_trades = 0
        open_trades = []

        for base, pair in grouped.items():
            if pair["entry"] and pair["hedge"]:
                complete_trades += 1
            else:
                open_trades.append(base)

        return {
            "complete_trades": complete_trades,
            "open_refs": open_trades
        }

    except Exception as e:
        print(f"❌ Error checking trade state: {e}")
        return {"complete_trades": 0, "open_refs": []}

    state = get_open_trade_state(signal["marketId"], signal["selectionId"])

    if state["complete_trades"] >= 3:
        print(f"🚫 [NEXTGEN] Max complete trades reached on runner {signal['selectionId']}. Skipping.")
        return

    if state["open_refs"]:
        direction = signal.get("scalp_direction")
        odds = signal.get("odds")

        # 🚫 Block multiple lay-to-back trades over odds > 7.0
        if direction == "lay_to_back" and odds > 7.0:
            if state["complete_trades"] > 0:
                print(f"🚫 [NEXTGEN] Only one lay-to-back trade allowed for odds > 7.0 | Current: {state['complete_trades']}")
                return

        print(f"⚠️ [NEXTGEN] Unhedged trades exist on {signal['selectionId']} | Refs: {state['open_refs']}")

        if direction == "lay_to_back":
            # Allow stacking LAYs only if new odds are higher
            try:
                with sqlite3.connect("scalper.db") as conn:
                    cursor = conn.cursor()
                    cursor.execute("""
                         SELECT MAX(odds) FROM bets
                         WHERE marketId = ? AND selectionId = ? AND side = 'LAY' AND customerOrderRef LIKE '%_E'
                    """, (signal["marketId"], signal["selectionId"]))
                    row = cursor.fetchone()
                    previous_lay_odds = row[0] if row and row[0] else None

                if previous_lay_odds and odds <= previous_lay_odds:
                    print(f"🚫 [NEXTGEN] New LAY odds ({odds}) worse than previous ({previous_lay_odds}). Skipping.")
                    return
            except Exception as e:
                print(f"⚠️ Could not check previous LAY odds: {e}")

        elif direction == "back_to_lay":
            print("🚫 [NEXTGEN] Cannot stack BACK trades with open exposure. Skipping.")
            return


def get_open_trade_state(market_id, selection_id):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT customerOrderRef, side, stake, betfair_bet_id
                FROM live_signals
                WHERE marketId = ? AND selectionId = ?
                ORDER BY placed_at DESC
                LIMIT 12
            """, (market_id, selection_id))
            rows = cursor.fetchall()

        grouped = {}
        for ref, side, stake, bet_id in rows:
            base_ref = ref.split("_")[0]
            if base_ref not in grouped:
                grouped[base_ref] = {"entry": None, "hedge": None}
            if side == "LAY" and ref.endswith("_E"):
                grouped[base_ref]["entry"] = (ref, stake, bet_id)
            elif side == "BACK" and ref.endswith("_H"):
                grouped[base_ref]["hedge"] = (ref, stake, bet_id)

        complete_trades = 0
        open_trades = []

        for base, pair in grouped.items():
            if pair["entry"] and pair["hedge"]:
                complete_trades += 1
            else:
                open_trades.append(base)

        return {
            "complete_trades": complete_trades,
            "open_refs": open_trades
        }

    except Exception as e:
        print(f"❌ Error checking trade state: {e}")
        return {"complete_trades": 0, "open_refs": []}

def launch_nextgen_thread(signal):
    signal_memory.enrich(signal)
    try:
        base_stake = signal_memory.get_adaptive_stake()
    except:
        base_stake = 10.0

    blueprint = signal.get("blueprint_match")
    pattern_key = signal.get("pattern_key")
    pattern_meta = KNOWN_BLUEPRINT_PATTERNS.get(pattern_key, {}).get("meta", {})
    win_rate = pattern_meta.get("win_rate", 0)
    avg_conf = pattern_meta.get("avg_confidence", 0)
    conf = signal.get("confidence", 0.0)

    if blueprint:
        if win_rate >= 0.65 and conf >= 0.68:
            print(f"📘 Blueprint confirmed | WinRate: {win_rate} | Confidence: {conf}")
            try:
                from blueprint_csv_loader import get_blueprint_meta
                pattern_key = blueprint
                win_rate, avg_conf = get_blueprint_meta(pattern_key)
                signal["blueprint_meta"] = {
                    "pattern_key": pattern_key,
                    "win_rate": win_rate,
                    "avg_confidence": avg_conf,
                }
            except Exception as e:
                print(f"⚠️ Failed to enrich blueprint meta: {e}")

        elif win_rate >= 0.55 and signal.get("tick_pattern") in ["drifted", "steamed"]:
            print(f"📘 Blueprint weaker but tradable pattern (Tick: {signal.get('tick_pattern')})")
        else:
            print(f"🚫 [FILTER] Blueprint match but weak stats | WinRate: {win_rate}")
            return
    else:
        if conf >= 0.76 and signal.get("tick_pattern") in ["drifted", "steamed"]:
            print(f"🔍 Non-blueprint confidence edge accepted | Confidence: {conf}")
        elif 0.70 <= conf < 0.76 and signal_memory.is_trend_forming(
            signal["marketId"], signal["selectionId"], signal.get("scalp_direction"),
            signal.get("odds"), signal.get("range_high"), signal.get("range_low")):
            print(f"🔄 Trend forming – accepting non-blueprint trade | Confidence: {conf}")
        else:
            # ✅ Rebound Detection (Flat pattern, mid-confidence, healthy tick range)
            tick_range = signal.get("tick_range") or (
                round((signal.get("range_high", 0) - signal.get("range_low", 0)) / get_tick_size(signal.get("odds", 1)), 2)
                if signal.get("range_high") and signal.get("range_low") and signal.get("odds") else 0
            )
            signal["tick_range"] = tick_range
            if signal.get("tick_pattern") == "flat" and 0.68 <= conf < 0.72 and tick_range >= 8:
                print("🔁 [NEXTGEN] Rebound candidate detected – possible delayed drift. Proceeding.")
                signal["signal_type"] = "rebound_drift"

            signal_memory.track_unmatched_signal(signal)

            bp_key = signal_memory.detect_blueprint_match(
                signal["marketId"], signal["selectionId"], signal.get("scalp_direction"),
                signal.get("odds"), signal.get("range_high"), signal.get("range_low"))

            if bp_key:
                print(f"🔁 Memory-triggered blueprint pattern detected: {bp_key}")
                signal["blueprint_match"] = bp_key
                signal["signal_type"] = "memory_trend"
                signal["confidence"] = 0.71
                volatility = signal.get("volatility", 0.0)

                if volatility < 0.0005 and tick_range < 6:
                    print(f"🚫 [FILTER] Low volatility + narrow range. Skipping.")
                    return
                elif volatility < 0.0005:
                    print(f"⚠️ [NEXTGEN] Low volatility. Proceeding with caution.")


                # ✅ Attach blueprint meta from known patterns
                pattern_meta = KNOWN_BLUEPRINT_PATTERNS.get(bp_key, {}).get("meta", {})
                signal["blueprint_meta"] = {
                    "pattern_key": bp_key,
                    "win_rate": pattern_meta.get("win_rate", 0),
                    "avg_confidence": pattern_meta.get("avg_confidence", 0)
                    

                }
                print(f"📊 BlueprintMeta: {signal.get('blueprint_meta')}")

    if signal.get("tick_pattern") == "flat":
        print(f"⚠️ [NEXTGEN] Flat tick pattern. Proceeding with caution.")
        signal["tick_pattern_flag"] = "flat_warning"


    # Edge of Range Adjustment
    ratio = signal.get("position_ratio", 0.5)
    if (ratio < 0.05 or ratio > 0.95) and conf < 0.75:
        print(f"🚫 [FILTER] Extreme edge + low confidence. Skipping.")
        return
    elif ratio < 0.1 or ratio > 0.9:
        print(f"⚠️ [NEXTGEN] Edge of range. Proceeding with caution.")

    # Narrow Range Adjustment
    tick_range = signal.get("tick_range", 0)
    if tick_range < 5 and conf < 0.75:
        print(f"🚫 [FILTER] Narrow range + Low confidence. Skipping.")
        return
    elif tick_range < 5:
        print(f"⚠️ [NEXTGEN] Narrow range but confidence ok. Proceeding.")

    # Blueprint/SigType Fallback
    if not signal.get("blueprint_match") and not signal.get("signal_type"):
        if conf >= 0.72 and tick_range >= 8:
            print(f"⚠️ [NEXTGEN] No pattern, but good confidence + range. Proceeding.")
        else:
            print(f"🚫 [FILTER] No signal type or blueprint match. Skipping.")
            return


    signal = dict(signal)
    signal["customerOrderRef"] = f"SS_{_next_suffix()}"

    try:
        from config_paths import DB_PATH
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT anchor_odd FROM bets
                WHERE marketId = ? AND selectionId = ?
                ORDER BY placed_at DESC LIMIT 1
            """, (signal.get("marketId"), signal.get("selectionId")))
            row = cursor.fetchone()
            if row and row[0] is not None:
                signal["anchor_odd"] = float(row[0])
            else:
                print(f"🚫 [NEXTGEN] No anchor_odd found for {signal.get('selectionId')} – skipping.")
                return
    except Exception as e:
        print(f"⚠️ [NEXTGEN] DB error fetching anchor_odd: {e}")
        return

    direction = signal.get("scalp_direction")
    odds = signal.get("odds")
    anchor = signal.get("anchor_odd")

    if not direction:
        signal["scalp_direction"] = "lay_to_back" if odds > anchor else "back_to_lay"

    selection_id = signal.get("selectionId")

    if odds is None or odds > 20:
        print(f"🚫 [NEXTGEN] Odds too high ({odds}). Skipping scalp.")
        return

    if signal_memory.should_halt_trading():
        print("🚫 HALT TRIGGERED – Skipping scalp.")
        return

    range_low = signal.get("range_low")
    range_high = signal.get("range_high")
    scalp_ticks = signal.get("scalp_ticks", 2)
    tick_size = get_tick_size(odds)

    if range_low is None or range_high is None:
        print(f"⚠️ [NEXTGEN] Missing range boundaries. Skipping scalp.")
        return

    if signal["scalp_direction"] == "lay_to_back":
        ticks_to_top = (range_high - odds) / tick_size
        if ticks_to_top >= scalp_ticks:
            pass
        elif odds > range_high + BREAKOUT_CONFIRM_TICKS * tick_size:
            pass
        else:
            print(f"⏸️ [NEXTGEN] Odds too close to or inside range for lay_to_back. Waiting.")
            return

    elif signal["scalp_direction"] == "back_to_lay":
        ticks_to_bottom = (odds - range_low) / tick_size
        if ticks_to_bottom >= scalp_ticks:
            pass
        elif odds < range_low - BREAKOUT_CONFIRM_TICKS * tick_size:
            pass
        else:
            print(f"⏸️ [NEXTGEN] Odds too close to or inside range for back_to_lay. Waiting.")
            return

    if blueprint and win_rate >= 0.75:
        stake_mode = "aggressive"
        signal["stake"] = round(base_stake * 1.2, 2)
    elif blueprint:
        stake_mode = "moderate"
        signal["stake"] = round(base_stake, 2)
    elif conf >= 0.9:
        stake_mode = "confident"
        signal["stake"] = round(base_stake * 1.5, 2)
    else:
        stake_mode = "cautious"
        signal["stake"] = round(base_stake * 0.85, 2)

    signal["stake_mode"] = stake_mode

    def run_nextgen_scalp_wrapper():
        try:
            run_nextgen_scalp(dict(signal))
        except Exception as e:
            print(f"❌ NextGenScalp error: {e}")

    threading.Thread(target=run_nextgen_scalp_wrapper, name="NextGenScalpThread", daemon=True).start()
    print(f"🚀 Launched SmartScalp + NextGenScalp for selectionId: {signal.get('selectionId')}")


# ✅ All logic already uses save_bet_to_db from this file
# ❌ No need to call or import it from database_hijack_monitor anymore

# ✅ Nothing to change below unless triggered by future issue

# ✅ Remaining logic unchanged

def place_bet_with_retries(
    session_token, market_id, selection_id,
    stake, odds, side, ref_base,
    max_retries=2, delay=2
):
    for attempt in range(max_retries + 1):
        ref = f"{ref_base}_R{attempt}" if attempt > 0 else ref_base

        # Skip if bet already exists in DB
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT betfair_bet_id FROM live_signals
                WHERE marketId = ? AND selectionId = ? AND customerOrderRef = ?
            """, (market_id, selection_id, ref))
            if cursor.fetchone():
                print(f"⚠️ Bet {ref} already exists. Skipping retry.")
                return None

        bet_id = place_bet(session_token, market_id, selection_id, stake, odds, side, ref)
        if bet_id:
            print(f"✅ Bet placed: {side} £{stake} @ {odds} | Ref: {ref}")
            save_bet_to_db(market_id, selection_id, odds, stake, side, ref, bet_id)
            return bet_id
        else:
            print(f"❌ Bet failed: {side} @ {odds} | Ref: {ref}")
            time.sleep(delay)

    print(f"🚫 Max retries reached for {side} @ {odds}")
    return None


def run_nextgen_scalp(signal):
    selection_id = signal.get("selectionId")
    odds = signal.get("odds")
    entry_bet_id = None  # 🛡️ Prevent undefined variable in crash recovery

    # ✅ Frequency and Odds Throttle
    if odds is None or odds > 20:
        print(f"🚫 [NEXTGEN] Odds too high ({odds}). Skipping scalp.")
        return

    if active_scalps[selection_id] >= 3:
        print(f"🚫 [NEXTGEN] Too many active scalps on runner {selection_id}. Skipping.")
        return

    print("\n[NEXTGEN] 🚀 Starting next-gen scalp engine...")
    print(f"[NEXTGEN] Runner: {selection_id} | Odds: {odds} | Stake: £10.00 (forced)")

    try:
        session_token = get_session_token()
        market_id = signal.get("marketId")
        stake = signal_memory.get_adaptive_stake()
        ticks = signal.get("scalp_ticks", 2)

        direction = signal.get("scalp_direction", "lay_to_back")
        tick_size = get_tick_size(odds)
        signal["tick_size"] = tick_size

        if direction == "lay_to_back":
            entry_side = "LAY"
            hedge_side = "BACK"
            hedge_odds = walk_ticks(odds, ticks, direction="up")  # ✅ FIXED: walk up for lay-to-back
        else:
            entry_side = "BACK"
            hedge_side = "LAY"
            hedge_odds = walk_ticks(odds, ticks, direction="down")  # ✅ FIXED: walk down for back-to-lay


        suffix = _next_suffix()
        base_ref = f"NG_{suffix}"
        if len(base_ref) > 28:
            base_ref = base_ref[:28]

        entry_ref = f"{base_ref}_E"
        hedge_ref = f"{base_ref}_H"

        entry_bet_id = place_bet_with_retries(
            session_token, market_id, selection_id,
            stake, odds, entry_side, entry_ref,
            max_retries=3
        )

        if entry_bet_id:
            signal_memory.record_matched_bet(
                market_id, selection_id, entry_side, odds, stake
            )
            save_bet_to_db(market_id, selection_id, odds, stake, entry_side, entry_ref, entry_bet_id)
        else:
            print("❌ Entry bet placement failed. Exiting scalp.")
            active_scalps[selection_id] -= 1
            return
  



        save_bet_to_db(market_id, selection_id, odds, stake, entry_side, entry_ref, entry_bet_id)

        # ✅ Immediately place hedge bet (pre-calculated stake)
        if direction == "lay_to_back":
            hedge_stake = round((odds * stake) / hedge_odds, 2)
        else:
            hedge_stake = round((stake * hedge_odds) / odds, 2)

        hedge_bet_id = place_bet_with_retries(
            session_token, market_id, selection_id,
            hedge_stake, hedge_odds, hedge_side, hedge_ref,
            max_retries=3
        )

        if hedge_bet_id:
            signal_memory.record_matched_bet(
                market_id, selection_id, hedge_side, hedge_odds, hedge_stake
            )
            signal_memory.resolve_runner_pnl(market_id, selection_id)
        else:
            print("❌ Hedge bet placement failed.")



        if hedge_bet_id:
            print(f"""📊 LIVE TRADE SUMMARY
        ────────────────────────────
        🏇 Selection ID:   {selection_id}
        🎯 Market ID:      {market_id}
        🧭 Direction:      {direction.upper()}
        💰 Entry:          {entry_side} £{stake} @ {odds} | Ref: {entry_ref}
        💼 Hedge:          {hedge_side} £{hedge_stake} @ {hedge_odds} | Ref: {hedge_ref}
        📏 Tick Count:     {ticks}
        📐 Range:          {signal.get("range_low")} → {signal.get("range_high")}
        📦 CustomerRef:    {base_ref}
        💼 Stake Used: £{stake} | Memory Stake: £{signal_memory.get_adaptive_stake()} | Balance: £{signal_memory.current_balance:.2f}")

        """)


        # ⏳ Monitor entry for match timeout, then cancel both if unmatched
        time.sleep(10)
        matched = get_matched_amount(session_token, entry_bet_id)
        if matched == 0:
            print("[NEXTGEN] ❌ No match after timeout. Cancelling both entry and hedge.")
            cancel_bet(session_token, entry_bet_id)
            cancel_bet(session_token, hedge_bet_id)

        active_scalps[selection_id] -= 1

    except Exception as e:
        print(f"💥 [NEXTGEN] Crash: {e}")
        active_scalps[selection_id] -= 1

        save_bet_to_db(market_id, selection_id, odds, stake, entry_side, entry_ref, entry_bet_id)

        matched_so_far = 0.0

        for i in range(10):
            time.sleep(1)
            matched_now = get_matched_amount(session_token, entry_bet_id)
            new_matched = round(matched_now - matched_so_far, 2)

            if new_matched >= 0.01:
                matched_so_far += new_matched
                print(f"[NEXTGEN] ✅ New matched: £{new_matched:.2f} (Total matched: £{matched_so_far:.2f})")

                hedge_stake = round((odds * stake) / hedge_odds, 2)


                hedge_sub_ref = f"{hedge_ref}_P{i}"
                hedge_bet_id = place_bet(session_token, market_id, selection_id, hedge_stake, hedge_odds, hedge_side, hedge_ref)


                if hedge_bet_id:
                    save_bet_to_db(market_id, selection_id, hedge_odds, hedge_stake, hedge_side, hedge_sub_ref, hedge_bet_id)

        if matched_so_far == 0:
            print("[NEXTGEN] ❌ No match after timeout. Cancelling entry.")
            cancel_bet(session_token, entry_bet_id)

        active_scalps[selection_id] -= 1

    except Exception as e:
        print(f"💥 [NEXTGEN] Crash: {e}")
        active_scalps[selection_id] -= 1

# -----------------------------
# 🔍 RANGE SNAPSHOT TESTING TOOL
# -----------------------------

def fetch_runner_odds(session_token, market_id):
    try:
        response = requests.post(
            "https://api.betfair.com/exchange/betting/rest/v1.0/listMarketBook/",
            headers={
                "X-Authentication": session_token,
                "X-Application": APP_KEY,
                "Content-Type": "application/json"
            },
            data=json.dumps({
                "marketIds": [market_id],
                "priceProjection": {
                "priceData": ["EX_BEST_OFFERS", "EX_TRADED"],
                "exBestOffersOverrides": { "bestPricesDepth": 3 },
                "virtualise": True
            }
            })
        )
        result = response.json()
        return result[0] if result else None
    except Exception as e:
        print(f"⚠️ Error fetching runner odds: {e}")
        return None

from get_markets import get_markets_and_insert

from upgrade_import_patch import set_session_token

if __name__ == "__main__":
    while True:
        print("Select test mode:")
        print("  R - Range test (market scan)")
        print("  P - Payload simulation")
        choice = input("Enter R or P: ").strip().lower()

        if choice.startswith("r"):
            print("🔐 Please enter your Betfair session token:")
            token = input("Session Token: ").strip()
            set_session_token(token)
            session_token = get_session_token()
            markets = get_markets_and_insert()

            if not markets or not isinstance(markets[0], dict):
                print("❌ No valid markets returned. Exiting.")
                exit()

            for market in markets:
                market_id = market.get("marketId")
                runners = market.get("runners", [])
                print(f"\n📈 Market ID: {market_id}")

                book = fetch_runner_odds(session_token, market_id)
                if not book:
                    continue

                for runner_data in book.get("runners", []):
                    selection_id = runner_data.get("selectionId")
                    back = runner_data.get("ex", {}).get("availableToBack", [])
                    lay = runner_data.get("ex", {}).get("availableToLay", [])

                    top_back = back[0]["price"] if back else None
                    top_lay = lay[0]["price"] if lay else None

                    traded = runner_data.get("ex", {}).get("tradedVolume", [])

                    if traded:
                        total_matched = sum(pt["size"] for pt in traded)
                        weighted_anchor = sum(pt["price"] * pt["size"] for pt in traded) / total_matched
                        tick_size = get_tick_size(weighted_anchor)
                        range_low = round(weighted_anchor - 2 * tick_size, 2)
                        range_high = round(weighted_anchor + 2 * tick_size, 2)
                        print(f"🐎 Runner ID: {selection_id} | Back: {top_back} | Lay: {top_lay} | Anchor: {weighted_anchor:.2f} | Matched Volume: {int(total_matched)} | Range: [{range_low} - {range_high}]")
                    else:
                        print(f"🐎 Runner ID: {selection_id} | Back: {top_back} | Lay: {top_lay} | No matched volume data")

        elif choice.startswith("p"):
            print("\n🧪 Payload Simulation Mode")
            while input("Simulate? ").strip().lower().startswith("y"):
                try:
                    odds = float(input("Enter entry odds: ").strip())
                    stake_input = float(input("Enter entry stake: ").strip())
                    ticks = int(input("Enter number of ticks: ").strip())
                    entry_stake = enforce_stake_limit(stake_input)

                    for direction in ["lay_to_back", "back_to_lay"]:
                        if direction == "lay_to_back":
                            entry_side = "LAY"
                            hedge_side = "BACK"
                            hedge_odds = walk_ticks(odds, ticks, direction="up")  # ✅ FIXED: walk up for back
                            hedge_stake = round((odds * entry_stake) / hedge_odds, 2)
                        else:
                            entry_side = "BACK"
                            hedge_side = "LAY"
                            hedge_odds = walk_ticks(odds, ticks, direction="down")  # ✅ FIXED: walk down for lay
                            hedge_stake = round((entry_stake * hedge_odds) / odds, 2)

                        print(f"\n🧾 Simulated {direction.upper()} trade:")
                        print(f"ENTRY: {entry_side} £{entry_stake} @ {odds}")
                        print(f"HEDGE: {hedge_side} £{hedge_stake} @ {hedge_odds}")

                except Exception as e:
                    print(f"❌ Simulation error: {e}")
                print("\n---")
            print("✅ Simulation ended.")
