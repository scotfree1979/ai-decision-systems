###1 IMPORTS + GLOBALS

"""
All dependencies, globals, trackers, and init variables
"""

import time
import logging
import sqlite3
from datetime import datetime, timezone, timedelta

from og_brain_logic import brain_memory
from config_paths import DB_PATH
from utils.api_tools import fetch_live_odds
from upgrade_import_patch import bot_signal_queues, get_session_token
from volatility_check import get_volatility_meta, get_recent_odds
from price_math import get_tick_size
from known_blueprints import KNOWN_BLUEPRINT_PATTERNS

# ✅ INJECT RUNNER TRADES FROM BLUEPRINT JSON

import os
import json
from datetime import datetime, timedelta

runner_trades = []
# Example default stake mapping
initial_stakes = {}
# ✅ Base stake map (static for now, dynamic later via playbooks)
initial_stakes = {}
for signal in runner_trades:
    sid = signal.get("selectionId") or signal.get("selection_id")
    if sid and sid not in initial_stakes:
        initial_stakes[sid] = 10.0

# Look for today's or yesterday's blueprint file
base_filename = "blueprint_signals_{}.json"
today = datetime.utcnow().date()
filenames = [
    base_filename.format(today.isoformat()),
    base_filename.format((today - timedelta(days=1)).isoformat())
]

for f in filenames:
    if os.path.exists(f):
        try:
            with open(f, "r") as infile:
                runner_trades = json.load(infile)
            print(f"📦 Loaded {len(runner_trades)} blueprint signals from {f}")
            break
        except Exception as e:
            print(f"⚠️ Failed to load runner_trades from {f}: {e}")

if not runner_trades:
    print("📭 No blueprint_signals.json found or empty. Blueprint matching disabled.")


breakout_tracker = {}
trailing_stop_tracker = {}
last_odds_map = {}
live_runner_map = {}

# ✅ Signal evaluation counters (to be placed near the top of evaluate_signal function)
signal_counters = {
    "signals_fired": 0,
    "nearly_valid": 0,
    "close_match": 0,
    "volatile": 0,
    "blueprint": 0,
    "breakout_only": 0,
    "not_valid": 0,
    "too_low": 0,
    "nosignaltype": 0,
    "bad_pattern": 0,
    "stake_missing": 0,
    "stoprisk": 0
}

_letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_suffix_index = 0


###2 HELPERS + UTILITY

# ✅ Unique Suffix Generator

def _next_suffix():
    global _suffix_index
    if _suffix_index == 0:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                conn.row_factory = sqlite3.Row
                cursor = conn.execute("SELECT customer_order_ref FROM scalping_bets WHERE customer_order_ref LIKE '__%'")
                rows = cursor.fetchall()
                max_found = 0
                for row in rows:
                    try:
                        prefix = row["customer_order_ref"].split("_")[0]
                        a, b, num = prefix[0], prefix[1], prefix[2:]
                        base = (ord(a) - 65) * 26 * 1000 + (ord(b) - 65) * 1000 + int(num)
                        max_found = max(max_found, base)
                    except:
                        continue
                _suffix_index = max_found + 1
        except Exception as e:
            logging.warning(f"⚠️ Failed to scan for existing customer_order_ref: {e}")
            _suffix_index = 1

    code = _suffix_index
    _suffix_index += 1

    a_index = (code // 1000) // 26
    b_index = (code // 1000) % 26

    if a_index >= len(_letters) or b_index >= len(_letters):
        raise ValueError("💥 _next_suffix() exhausted code space — increase letter base or reset")

    return f"{_letters[a_index]}{_letters[b_index]}{code % 1000:03}"

# ✅ Tick pattern detection for active signals

def get_tick_pattern_for_signal(selection_id):
    ticks = last_odds_map.get(selection_id, [])
    return classify_tick_pattern(ticks)

# ⟳ Signal Decision Helper

def determine_signal_type_and_trigger(signal):
    ratio = signal.get("position_ratio", 0)
    tick_pattern = signal.get("tick_pattern", "")
    confidence = signal.get("confidence", 0)
    blueprint_match = signal.get("blueprint_match", False)

    if signal.get("would_hit_stop"):
        return "stop_risk"

    if signal.get("is_breakout"):
        return "breakout"

    if signal.get("range_breakout"):
        return "range_expansion"

    if tick_pattern == "late_swing":
        return "volatile_move"

    if blueprint_match:
        return "entry_from_blueprint"

    if tick_pattern == "came_in" and ratio <= 0.30:
        return "entry"

    if tick_pattern == "drifted" and ratio >= 0.70:
        return "exit"

    return None

# 🔍 Tick Pattern Classification

def classify_tick_pattern(ticks):
    if len(ticks) < 6:
        return "flat"
    deltas = [ticks[i+1] - ticks[i] for i in range(len(ticks)-1)]
    if all(d > 0 for d in deltas):
        return "drifted"
    elif all(d < 0 for d in deltas):
        return "came_in"
    elif any(abs(d) > 1 for d in deltas):
        return "late_swing"
    return "flat"

# 🧮 Classify runner's bracket position

def classify_market_position(horses, selection_id):
    if not horses or not selection_id:
        return "field"
    sorted_runners = sorted(
        [h for h in horses if isinstance(h.get("odds"), (float, int))],
        key=lambda h: h["odds"]
    )
    for i, h in enumerate(sorted_runners):
        if h["selectionId"] == selection_id:
            if i == 0:
                return "favourite"
            elif i == 1:
                return "second_fav"
            elif i == 2:
                return "third_fav"
            else:
                return "field"
    return "field"


###3 BLUEPRINT MEMORY + MATCHING

# 🧠 Blueprint Memory and Matching

blueprint_tracker = {}
KNOWN_BLUEPRINT_PATTERNS = {
    "steam→→back-to-lay": ["steam", "", "back-to-lay"],
    "drift→→lay-to-back": ["drift", "", "lay-to-back"]
}

def track_and_match_blueprint(selection_id, tick_pattern, blueprint_move=None, trade_type=None, oc_band=None):
    """
    Tracks and evaluates blueprint pattern matches using full key structure.
    Falls back to classic tick pattern match if full key not matched.
    """
    recent = blueprint_tracker.get(selection_id, [])
    recent.append(tick_pattern)
    blueprint_tracker[selection_id] = recent[-30:]

    # Construct full enriched key if context exists
    if blueprint_move and trade_type:
        enriched_key = f"{blueprint_move}→{oc_band or ''}→{trade_type}"
        if enriched_key in KNOWN_BLUEPRINT_PATTERNS:
            return enriched_key, 0.15

    # Fallback pattern match based on tick pattern sequence
    for name, pattern in KNOWN_BLUEPRINT_PATTERNS.items():
        if len(recent) >= len(pattern) and recent[-len(pattern):] == pattern:
            return name, 0.10

    return None, 0.0



###4 RANGE + TICK LOGIC

# 🧠 Range Builder – Multi-Layered with Fallbacks
def get_enriched_range(market_id, selection_id, odds_list=None):
    import sqlite3
    from config_paths import DB_PATH

    range_low = None
    range_high = None
    source = "unknown"
    confidence_score = 0.0

    # ✅ Layer 1: Use last 30-min tick history if available
    if odds_list and len(odds_list) >= 6:
        range_low = min(odds_list)
        range_high = max(odds_list)
        source = "tick_history"
        confidence_score = 0.65

    # ✅ Layer 2: Use anchor + OC1–OC6 fallback even if anchor_odd is missing
    if not range_low or not range_high:
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT anchor_odd, odds_check_1, odds_check_2, odds_check_3, odds_check_4, odds_check_5, odds_check_6
                    FROM bets WHERE marketId = ? AND selectionId = ?
                """, (market_id, selection_id))
                row = cursor.fetchone()
                if row:
                    odds_points = [x for x in row if isinstance(x, (int, float))]
                    if len(odds_points) >= 2:
                        range_low = min(odds_points)
                        range_high = max(odds_points)
                        source = "anchor+oc"
                        confidence_score = 0.6
                    else:
                        print(f"⚠️ Not enough numeric range points for {selection_id} in {market_id}: {row}")
        except Exception as e:
            print(f"⚠️ Range fallback DB error for {selection_id}: {e}")

    return {
        "range_low": range_low,
        "range_high": range_high,
        "range_breakout": False,
        "confidence_score": round(min(confidence_score, 1.0), 3),
        "range_source": source
    }

###5a EVALUATE SIGNAL LOOP – BUILD rows + live_runner_map FIRST

# 🧠 Pull eligible rows with anchor_odd and build live_runner_map
rows = []
live_runner_map = {}

try:
    now = datetime.now(timezone.utc)
    window_start = now + timedelta(minutes=-15)
    window_end = now + timedelta(minutes=90)

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT DISTINCT marketId, selectionId, marketStartTime FROM bets
            WHERE anchor_odd IS NOT NULL
        """)
        for market_id, selection_id, start_str in cursor.fetchall():
            if not start_str:
                continue
            try:
                start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                if window_start <= start_dt <= window_end:
                    rows.append((market_id, selection_id))

                    # Fetch odds for each valid runner
                    odds_data = fetch_live_odds(get_session_token(), market_id, selection_id)
                    if not odds_data:
                        continue

                    runner = {
                        "selectionId": selection_id,
                        "odds": odds_data.get("lay")
                    }

                    if market_id not in live_runner_map:
                        live_runner_map[market_id] = []
                    live_runner_map[market_id].append(runner)

            except Exception:
                continue
except Exception as e:
    print(f"❌ Failed to prefetch eligible rows or odds data: {e}")
    rows = []

###5b EVALUATE SIGNAL LOOP
start_times = {}
# ✅ Unified Evaluate Signal
def evaluate_signal(horses=live_runner_map):
    signal_evaluated = False
    signal = None
    import sqlite3
    from volatility_check import get_volatility_meta, get_recent_odds
    from price_math import get_tick_size
    from datetime import datetime, timezone, timedelta
    import json

    def finalise_signal(signal):
        if not signal or not isinstance(signal, dict):
            print("⚠️ Skipping finalise_signal — no valid signal provided.")
            return
        try:
            print(f"Ꮪ Finalise: status={signal.get('status')} | stake={signal.get('stake')} | odds={signal.get('odds')} | bot={signal.get('bot_name')}")
            print_signal_analysis(signal)
            if signal.get("status") == "approved":
                signal["session_token"] = get_session_token()
                execute_signal(signal)
        except Exception as e:
            print(f"❌ FinaliseSignal execution error: {e}")

    # MAIN PROCESSING LOOP (process all runners)
    for market_id, selection_id in rows:
        try:
            odds_data = fetch_live_odds(get_session_token(), market_id, selection_id)
            if not odds_data:
                continue

            runner = {
                "selectionId": selection_id,
                "odds": odds_data.get("lay")
            }

            if market_id not in live_runner_map:
                live_runner_map[market_id] = []
            live_runner_map[market_id].append(runner)

            # ✅ Build the signal object
            signal = {
                "selectionId": selection_id,
                "marketId": market_id,
                "odds": runner["odds"],
                "status": "pending"
            }

            base_stake = initial_stakes.get(selection_id, 10.0) if initial_stakes else 10.0

            # 👇 Add safety check before any get() usage
            if not signal or not isinstance(signal, dict):
                print("⚠️ Skipping signal construction — invalid structure.")
                continue

            if signal.get("would_hit_stop"):
                print("🛑 Signal blocked due to stop risk flag.")
                continue

        except Exception as e:
            print(f"❌ EvaluateSignal loop error: {e}")
            continue


    try:
        now = datetime.now(timezone.utc)
        window_start = now + timedelta(minutes=-15)
        window_end = now + timedelta(minutes=90)

        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT DISTINCT marketId, selectionId, marketStartTime FROM bets
                WHERE anchor_odd IS NOT NULL
            """)
          
            for market_id, selection_id, start_str in cursor.fetchall():
                if not start_str:
                    try:
                        with sqlite3.connect(DB_PATH) as conn2:
                            c2 = conn2.cursor()
                            c2.execute("""
                                SELECT meta_json FROM bets
                                WHERE marketId = ? AND selectionId = ?
                            """, (market_id, selection_id))
                            row = c2.fetchone()
                            if row and row[0]:
                                try:
                                    meta = json.loads(row[0])
                                    start_time = meta.get("marketStartTime")
                                    if start_time:
                                        c2.execute("""
                                            UPDATE bets SET marketStartTime = ?
                                            WHERE marketId = ? AND selectionId = ?
                                        """, (start_time, market_id, selection_id))
                                        conn2.commit()
                                        start_str = start_time
                                except Exception:
                                    continue
                    except Exception:
                        continue
                try:
                    if start_str:
                        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                        if window_start <= start_dt <= window_end:
                            rows.append((market_id, selection_id))
                            start_times[market_id] = start_dt
                except:
                    continue
    except Exception as e:
        print(f"❌ Failed to scan bets: {e}")
        return

    from collections import defaultdict
    market_summary = defaultdict(lambda: {
        "evaluated": 0,
        "entry": 0,
        "exit": 0,
        "volatile": 0,
        "none": 0,
        "blueprints": 0,
        "conf_total": 0,
        "conf_count": 0,
        "low_conf": 0,
        "has_api_range": 0,
        "has_drift": 0,
        "has_full_odds": 0,
        "range_low": [],
        "range_high": [],
        "fav_range": "",
        "second_range": "",
        "third_range": "",
        "fourth_range": "",
        "field_avg_range": ""
    })

    for market_id, selection_id in rows:
        try:
            start_dt = start_times.get(market_id)
            if not start_dt:
                continue

            minutes_to_off = int((start_dt - datetime.now(timezone.utc)).total_seconds() / 60)
            if minutes_to_off < -15 or minutes_to_off > 90:
                continue

         
            odds_data = fetch_live_odds(get_session_token(), market_id, selection_id)
            current_odds = odds_data.get("lay") if odds_data else None
            if not current_odds:
                continue

            if selection_id not in last_odds_map:
                last_odds_map[selection_id] = []
            last_odds_map[selection_id].append(current_odds)
            last_odds_map[selection_id] = last_odds_map[selection_id][-600:]
            odds_list = last_odds_map[selection_id]

            volatility_meta = get_volatility_meta(market_id, selection_id, odds_list)
            range_low = volatility_meta.get("range_low")
            range_high = volatility_meta.get("range_high")
            current_odds = odds_list[-1]

            if not range_low or not range_high:
                anchor = volatility_meta.get("anchor_odd")
                oc1 = volatility_meta.get("odds_check_1")
                oc2 = volatility_meta.get("odds_check_2")
                oc3 = volatility_meta.get("odds_check_3")

                odds_points = [anchor, oc1, oc2, oc3]
                odds_points = [o for o in odds_points if isinstance(o, (int, float))]

                if len(odds_points) >= 2:
                    range_low = min(odds_points)
                    range_high = max(odds_points)
                elif anchor and current_odds:
                    range_low = min(anchor, current_odds)
                    range_high = max(anchor, current_odds)
                else:
                    continue


            if range_high == range_low:
                range_low -= 0.01
                range_high += 0.01

            ratio = round((current_odds - range_low) / (range_high - range_low), 3)

            signal = {
                "selectionId": selection_id,
                "marketId": market_id,
                "odds": current_odds,
                "anchor_odd": volatility_meta.get("anchor_odd", None),
                "spread": volatility_meta.get("spread", 0),
                "confidence": 0.0,
                "drift": volatility_meta.get("drift", 0),
                "avg_volatility": volatility_meta.get("avg_volatility", 0),
                "volatile": volatility_meta.get("volatile", False),
                "tick_size": get_tick_size(current_odds),
                "bet_type": "SCALP",
                "time_signal": datetime.utcnow().isoformat(),
                "runnerName": None
            }

            # ✅ Inject blueprint-based signal override
            # ✅ Inject blueprint-based signal override
            for trade in runner_trades:
                if trade["market_id"] == market_id and trade["runner"].endswith(str(selection_id)):
                    signal["is_breakout"] = trade.get("is_breakout", False)
                    signal["would_hit_stop"] = trade.get("would_hit_stop", False)
                    signal["entry_exit_ratio"] = f"{trade.get('entry_tick_ratio')}→{trade.get('exit_tick_ratio')}"
 
                    signal["blueprint_match"] = True
                    signal["blueprint_move"] = trade["move"]
                    signal["scalp_direction"] = trade["trade_type"]
                    signal["tick_pattern"] = trade["move"]
                    signal["blueprint_source"] = trade["source"]

                    # Confidence from source quality
                    if trade["source"] == "full_odds_check":
                        signal["confidence"] += 0.07
                    elif trade["source"] == "partial_odds_check":
                        signal["confidence"] += 0.04
                    elif trade["source"] == "anchor+odds_only":
                        signal["confidence"] += 0.02

                    # Confidence based on signal quality
                    if signal["is_breakout"]:
                        signal["confidence"] += 0.05
                    if signal["would_hit_stop"]:
                        signal["confidence"] -= 0.08

                break


            signal["last_odds_list"] = odds_list
            signal["range_low"] = range_low
            signal["range_high"] = range_high
            signal["position_ratio"] = ratio
            signal["tick_pattern"] = get_tick_pattern_for_signal(selection_id)
            signal["signal_type"] = determine_signal_type_and_trigger(signal)
            blueprint_match, match_boost = track_and_match_blueprint(
                selection_id,
                signal.get("tick_pattern"),
                signal.get("blueprint_move"),
                signal.get("scalp_direction"),
                signal.get("oc_band")
            )
            signal["confidence"] += match_boost
            if blueprint_match:
                signal["blueprint_match"] = blueprint_match

            tick_size = get_tick_size(current_odds)
            tick_range = round((range_high - range_low) / tick_size)
            horses = live_runner_map.get(market_id, [])
            market_bracket = classify_market_position(horses, selection_id)

            signal["market_bracket"] = market_bracket
            signal["tick_range"] = tick_range

            # 🔁 Signal counter updates
            if signal.get("would_hit_stop"):
                signal_counters["stoprisk"] += 1

            signal_type = signal.get("signal_type")
            confidence = signal.get("confidence", 0.0)

            if signal_type in ["entry", "exit", "breakout", "range_expansion", "entry_from_blueprint"] and confidence >= 0.65:
                signal_counters["signals_fired"] += 1

            if signal_type in ["entry", "exit"] and 0.60 <= confidence < 0.65:
                signal_counters["nearly_valid"] += 1

            if signal_type in ["breakout", "range_expansion", "entry_from_blueprint"] and confidence >= 0.65:
                signal_counters["close_match"] += 1

            if signal_type == "volatile_move":
                signal_counters["volatile"] += 1

            if signal.get("blueprint_match"):
                signal_counters["blueprint"] += 1

            if signal_type in ["breakout", "range_expansion"] and confidence < 0.65:
                signal_counters["breakout_only"] += 1

            if signal_type is None and confidence >= 0.65:
                signal_counters["not_valid"] += 1

            if confidence < 0.65:
                signal_counters["too_low"] += 1

            if signal_type is None:
                signal_counters["nosignaltype"] += 1

            if signal.get("tick_pattern") == "flat":
                signal_counters["bad_pattern"] += 1

            if "stake" not in signal or not signal.get("stake"):
                signal_counters["stake_missing"] += 1


            # 🔁 Re-evaluate signal type after all boosts applied
            signal["signal_type"] = determine_signal_type_and_trigger(signal)

            if market_bracket == "favourite":
                if tick_range <= 5:
                    signal["confidence"] = max(signal["confidence"], 0.72)

                elif tick_range <= 7:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                elif tick_range <= 9:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                else:
                    signal["confidence"] = max(signal["confidence"], 0.72)

            elif market_bracket == "second_fav":
                if tick_range <= 6:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                elif tick_range <= 9:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                else:
                    signal["confidence"] = max(signal["confidence"], 0.72)

            elif market_bracket == "third_fav":
                if tick_range <= 8:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                elif tick_range <= 12:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                else:
                    signal["confidence"] = max(signal["confidence"], 0.72)

            else:  # field
                if tick_range <= 10:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                elif tick_range <= 14:
                    signal["confidence"] = max(signal["confidence"], 0.72)
                else:
                    signal["confidence"] = max(signal["confidence"], 0.72)

            mkt = market_summary[market_id]
            mkt["evaluated"] += 1
            mkt["conf_total"] += signal["confidence"]
            mkt["conf_count"] += 1
            mkt["range_low"].append(range_low)
            mkt["range_high"].append(range_high)
            if signal["confidence"] < 0.7:
                mkt["low_conf"] += 1
            if match_boost:
                mkt["blueprints"] += 1
            if signal["signal_type"] == "entry":
                mkt["entry"] += 1
            elif signal["signal_type"] == "exit":
                mkt["exit"] += 1
            elif signal["signal_type"] == "volatile_move":
                mkt["volatile"] += 1
            else:
                mkt["none"] += 1

            # 🧹 Bracket-specific range tracking for summary output
            range_str = f"{range_low} → {range_high}"
            if market_bracket == "favourite" and not mkt["fav_range"]:
                mkt["fav_range"] = range_str
            elif market_bracket == "second_fav" and not mkt["second_range"]:
                mkt["second_range"] = range_str
            elif market_bracket == "third_fav" and not mkt["third_range"]:
                mkt["third_range"] = range_str
            elif market_bracket == "fourth_fav" and not mkt["fourth_range"]:
                mkt["fourth_range"] = range_str

            else:
                if "field_ranges" not in mkt:
                    mkt["field_ranges"] = []
                mkt["field_ranges"].append((range_low, range_high))
                lows = [r[0] for r in mkt["field_ranges"] if r[0]]
                highs = [r[1] for r in mkt["field_ranges"] if r[1]]
                if lows and highs:
                    mkt["field_avg_range"] = f"{round(sum(lows)/len(lows), 2)} → {round(sum(highs)/len(highs), 2)}"

            if signal["signal_type"] in ["entry", "exit", "range_expansion", "breakout", "entry_from_blueprint"]:
                base_stake = initial_stakes.get(selection_id, 10.0) if initial_stakes else 10.0

            if signal["signal_type"] in ["breakout", "range_expansion"] and signal.get("blueprint_match"):
                stake_mode = "aggressive"
                stake = base_stake * 2.0
            elif signal["signal_type"] in ["entry", "exit"] and signal.get("confidence", 0) >= 0.8:
                stake_mode = "smart"
                stake = base_stake * 1.5
            else:
                stake_mode = "cautious"
                stake = base_stake

            signal["stake"] = round(stake, 2)
            signal["stake_mode"] = stake_mode
            signal["status"] = "approved"
            signal["session_token"] = get_session_token()
            signal_evaluated = True
            finalise_signal(signal)

        except Exception as e:
            print(f"❌ Signal processing error for {selection_id} in {market_id}: {e}")
            continue

    for market_id, mkt in market_summary.items():
        avg_conf = round(mkt["conf_total"] / mkt["conf_count"], 2) if mkt["conf_count"] else 0.0
        range_low = min(mkt["range_low"]) if mkt["range_low"] else "–"
        range_high = max(mkt["range_high"]) if mkt["range_high"] else "–"
        print(f"\n    📁 Market: {market_id}")
        print(f"        • Runners Evaluated:   {mkt['evaluated']}")
        print(f"        • Entry Signals:       {mkt['entry']} ✅")
        print(f"        • Exit Signals:        {mkt['exit']} ❌")
        print(f"        • Volatile Moves:      {mkt['volatile']} ⚡")
        print(f"        • Neutral/None:        {mkt['none']} 🟡")
        print(f"        • Blueprint Matches:   {mkt['blueprints']} 📘")
        if mkt['blueprints']:
            print(f"        • Pattern Influence:     Boosted confidence from matching historical trades")
        print(f"        • Avg Confidence:      {avg_conf}")
        print(f"        • Tick Range (Fav):     {mkt['fav_range']}")
        print(f"        • Tick Range (2nd):     {mkt['second_range']}")
        print(f"        • Tick Range (3rd):     {mkt['third_range']}")
        print(f"        • Tick Range (4th):     {mkt['fourth_range']}")
        print(f"        • Tick Range (Field):   {mkt['field_avg_range']}")
        print(f"        • Tick Range (Raw):     {range_low} → {range_high}")
        print(f"        • API Range Count:     {mkt['has_api_range']}")
        print(f"        • Drift Signals:       {mkt['has_drift']}")
        print(f"        • Full Odds Tracked:   {mkt['has_full_odds']}")
        print(f"        • Low Confidence:      {mkt['low_conf']}")

        # 🔁 Final fallback only after trying all runners
        if not signal_evaluated:
            if 'signal' not in locals() or not signal:
                print("💜 Every ting Blessed... Waiting for Markets! No signal object available.")
            else:
                print("📊 No trades placed this loop. Here's the diagnostic breakdown:")
                print("\n📊 Signal Summary This Loop:")
                print(f"📈 Total Signals Qualified (High Confidence):    {signal_counters.get('signals_fired', 0)}")
                print(f"🟢 Nearly Valid Entry/Exit Trades (< 0.65):       {signal_counters.get('nearly_valid', 0)}")
                print(f"🟠 Close Matches (Blueprint/Breakout):            {signal_counters.get('close_match', 0)}")
                print(f"🔵 Volatile Move Candidates:                      {signal_counters.get('volatile', 0)}")
                print(f"📘 Blueprint Matches:                             {signal_counters.get('blueprint', 0)}")
                print(f"🟤 Range/Breakout Only (Low Confidence):           {signal_counters.get('breakout_only', 0)}")
                print(f"🟡 Not Valid (No Signal Type, Decent Confidence):  {signal_counters.get('not_valid', 0)}")
                print(f"⚪ Confidence Too Low:                            {signal_counters.get('too_low', 0)}")
                print("\n🚫 No Trades Placed This Loop — Diagnostic Breakdown:")
                print(f"• ❌ NoSignalType (type was None):                {signal_counters.get('nosignaltype', 0)}")
                print(f"• ❌ BelowMinConfidence (< 0.65):                 {signal_counters.get('too_low', 0)}")
                print(f"• ❌ InvalidTickPattern (e.g. flat):              {signal_counters.get('bad_pattern', 0)}")
                print(f"• ❌ StakeNotAssigned (missed in logic):          {signal_counters.get('stake_missing', 0)}")
                print(f"• ❌ StopRiskFlagged (blocked intentionally):     {signal_counters.get('stoprisk', 0)}")



###6 FINALISE SIGNAL + ACTIVE PATH

# 🔁 Active Signal Path ... [updated]
        print(f"🚨 Entered active path for {signal.get('selectionId')} in {signal.get('marketId')}")
        log_signal_to_memory(signal)
        signal["market_profile"] = detect_market_profile(horses or [])
        strategy = "SCALP"
        blueprint = KNOWN_BLUEPRINT_PATTERNS.get(strategy)
        signal["strategy"] = strategy

        odds = signal.get("odds")
        if not isinstance(odds, (int, float)):
            print(f"❌ Rejected – Invalid odds: {odds} [{signal.get('selectionId')} in {signal.get('marketId')}]")
            return None

        selection_id = signal.get("selectionId")
        market_id = signal.get("marketId")

        if selection_id and isinstance(odds, (int, float)):
            prev = last_odds_map.get(selection_id, [])
            updated = (prev + [odds])[-6:]
            last_odds_map[selection_id] = updated
            signal["last_odds_list"] = updated

        # ⏳ Use OC-based range builder
        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT anchor_odd, odds_check_1, odds_check_2, odds_check_3, odds_check_4, odds_check_5, odds_check_6
                    FROM bets
                    WHERE marketId = ? AND selectionId = ?
                """, (market_id, selection_id))
                row = cursor.fetchone()
                if row:
                    odds_points = [o for o in row if isinstance(o, (int, float))]
                    if len(odds_points) >= 2:
                        signal["range_low"] = min(odds_points)
                        signal["range_high"] = max(odds_points)
                        signal["anchor_odd"] = row[0]
                        signal["confidence"] = 0.75 + (0.05 if len(odds_points) >= 4 else 0.0)
        except Exception as e:
            print(f"⚠️ DB range builder failed: {e}")
            return None

        range_low = signal.get("range_low")
        range_high = signal.get("range_high")
        if not (range_low and range_high):
            print(f"❌ Rejected – Missing range values. Range low: {range_low}, Range high: {range_high} [{selection_id} in {market_id}]")
            return None

        try:
            position_ratio = round((odds - range_low) / (range_high - range_low), 3)
        except Exception as e:
            print(f"❌ Crash while calculating ratio for [{selection_id}] in [{market_id}]: {e}")
            return None

        signal["position_ratio"] = position_ratio
        signal["tick_pattern"] = get_tick_pattern_for_signal(selection_id)
        signal["signal_type"] = determine_signal_type_and_trigger(signal)

        blueprint_match, match_boost = track_and_match_blueprint(selection_id, signal["tick_pattern"])
        if blueprint_match:
            signal["blueprint_match"] = blueprint_match
            signal["confidence"] += match_boost
            print(f"📘 Blueprint matched: {blueprint_match} (+{match_boost})")

        print(f"📊 Tick Pattern: {signal['tick_pattern']}, Type: {signal['signal_type']}, Confidence: {signal['confidence']}, Ratio: {position_ratio}")

        if not signal["signal_type"]:
            print("🟡 No valid trade setup – skipping execution.")
            return None

        print(f"🟢 TRIGGER: {signal['signal_type']} | Pattern: {signal['tick_pattern']} | Trend from anchor: {signal.get('anchor_odd')} → {odds} | Confidence: {round(signal['confidence'], 2)}")

        signal["status"] = "approved"

        base_stake = initial_stakes.get(selection_id, 10.0) if initial_stakes else 10.0
        confidence = signal.get("confidence", 0.85)
        range_breakout = signal.get("range_breakout", False)
        has_last_odds = len(signal.get("last_odds_list", [])) >= 6
        has_anchor = signal.get("anchor_odd") is not None

        if range_breakout:
            stake_mode = "aggressive"
            stake = base_stake * 2.0
        elif confidence >= 0.9 and has_anchor and has_last_odds:
            stake_mode = "smart"
            stake = base_stake * 1.5
        else:
            stake_mode = "cautious"
            stake = base_stake

        signal["stake_mode"] = stake_mode
        signal["stake"] = round(stake, 2)
        signal["bot_name"] = "OGHeadTrader"

        # 🔁 Ensure correct signal payload
        signal["scalp_direction"] = signal.get("scalp_direction") or "lay_to_back"
        signal["scalp_ticks"] = signal.get("scalp_ticks") or 2

        return signal

###7 TRADE EXECUTION LOGIC

# ✅ Step 2 – Finalise Signal Refactor

def execute_signal(signal):
    from price_math import get_tick_size
    from bet_core import place_bet, check_bet_matched, cancel_bet
    from brain_headtrader import get_enriched_range, get_tick_pattern_for_signal, determine_signal_type_and_trigger, track_and_match_blueprint, classify_market_position

    print(f"\nᾚa Finalise: status={signal.get('status')} | stake={signal.get('stake')} | odds={signal.get('odds')} | bot={signal.get('bot_name')}")

    odds = signal.get("odds")
    selection_id = signal.get("selectionId")
    market_id = signal.get("marketId")

    if not signal.get("range_low") or not signal.get("range_high"):
        enriched = get_enriched_range(market_id, selection_id, signal.get("last_odds_list"))
        signal.update(enriched)

    range_low = signal.get("range_low")
    range_high = signal.get("range_high")

    if range_low is None or range_high is None:
        print("❌ Missing range bounds – cannot finalise signal.")
        return

    signal["tick_pattern"] = get_tick_pattern_for_signal(selection_id)
    signal["signal_type"] = determine_signal_type_and_trigger(signal)
    blueprint_match, match_boost = track_and_match_blueprint(selection_id, signal["tick_pattern"])
    if blueprint_match:
        signal["blueprint_match"] = blueprint_match
        signal["confidence"] = signal.get("confidence", 0.7) + match_boost
        print(f"📘 Blueprint matched: {blueprint_match} (+{match_boost})")

    tick_size = get_tick_size(odds)
    midpoint = (range_high + range_low) / 2
    direction = "lay_to_back" if odds >= midpoint else "back_to_lay"
    signal["tick_size"] = tick_size
    signal["scalp_direction"] = direction
    ticks = signal.get("scalp_ticks", 2)
    signal["scalp_ticks"] = ticks

    if "confidence" not in signal or not isinstance(signal["confidence"], (int, float)):
        signal["confidence"] = 0.75

    horses = signal.get("horses") or []
    signal["market_bracket"] = classify_market_position(horses, selection_id)

    print(f"🧮 Range midpoint: {midpoint:.2f} | Odds: {odds} | Direction: {direction} | Ticks: {ticks}")
    print(f"📊 Tick Pattern: {signal['tick_pattern']}, Type: {signal['signal_type']}, Confidence: {signal['confidence']}, Ratio: {signal.get('position_ratio', '-')}")

    if signal.get("status") == "approved" and odds <= 20:
        signal["session_token"] = get_session_token()
        print(f"[DEBUG] Placing bet with token: {signal.get('session_token')}")

        entry_side = "LAY" if direction == "lay_to_back" else "BACK"
        entry_odds = odds
        stake = signal.get("stake", 10.0)
        session_token = signal.get("session_token")

        entry_bet_id = place_bet(session_token, market_id, selection_id, stake, entry_odds, entry_side)
        if not entry_bet_id:
            print("❌ Entry bet failed. Aborting scalp.")
            return

        matched = False
        for _ in range(10):
            time.sleep(3)
            if check_bet_matched(session_token, entry_bet_id):
                matched = True
                break

        if not matched:
            cancel_bet(session_token, entry_bet_id)
            print("⚠️ Entry bet unmatched after timeout. Cancelled.")
            return

        if direction == "lay_to_back":
            hedge_odds = round(odds - ticks * tick_size, 2)
            hedge_side = "BACK"
        else:
            hedge_odds = round(odds + ticks * tick_size, 2)
            hedge_side = "LAY"

        exit_bet_id = place_bet(session_token, market_id, selection_id, stake, hedge_odds, hedge_side)
        if exit_bet_id:
            print(f"✅ Hedge bet placed @ {hedge_odds} ({hedge_side})")
        else:
            print("❌ Hedge bet placement failed.")
    else:
        print(f"⏸️ Signal not placed – status={signal.get('status')} or odds too high: {odds}")

def update_trailing_stop(selection_id, latest_odds):
    info = trailing_stop_tracker.get(selection_id)
    if not info:
        return

    direction = info["direction"]
    tick_size = info["tick_size"]
    new_peak = info["current_peak"]

    if direction == "lay_to_back":
        if latest_odds < new_peak:
            new_peak = latest_odds
            info["current_peak"] = new_peak
            info["stop_odds"] = round(new_peak + (info["ticks"] * tick_size), 2)
    else:
        if latest_odds > new_peak:
            new_peak = latest_odds
            info["current_peak"] = new_peak
            info["stop_odds"] = round(new_peak - (info["ticks"] * tick_size), 2)

    if (direction == "lay_to_back" and latest_odds >= info["stop_odds"]) or \
       (direction == "back_to_lay" and latest_odds <= info["stop_odds"]):
        print(f"🔻 Trailing stop hit at {latest_odds} for {selection_id}. Executing exit.")
        exit_signal = {
            "selectionId": selection_id,
            "marketId": "?",
            "odds": latest_odds,
            "stake": info["stake"],
            "bet_type": "BASIC",
            "direction": "exit",
            "status": "approved",
            "scalp_direction": direction
        }
        place_lay_strategy(exit_signal)
        trailing_stop_tracker.pop(selection_id, None)

def log_signal_to_memory(signal):
    signal_entry = {
        "timestamp": time.time(),
        "signal": signal,
        "status": "received"
    }
    brain_memory["daily_signals"].append(signal_entry)
    print(f"📥 [Brain] Logged signal for {signal.get('selectionId')} in {signal.get('marketId')}")
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS approved_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    marketId TEXT,
                    selectionId TEXT,
                    runnerName TEXT,
                    odds REAL,
                    anchor_odd REAL,
                    drift REAL,
                    confidence REAL,
                    avg_volatility REAL,
                    volatile INTEGER,
                    spread REAL,
                    tick_size REAL,
                    strategy_name TEXT,
                    bet_type TEXT,
                    time_signal TEXT,
                    customer_order_ref TEXT,
                    status TEXT,
                    created_at TEXT,
                    blueprint_match INTEGER,
                    blueprint_move TEXT,
                    blueprint_source TEXT,
                    result TEXT,
                    pnl REAL,
                    won BOOLEAN
                )
            """)
            cursor.execute("""
                INSERT INTO approved_signals (
                    marketId, selectionId, runnerName, odds, anchor_odd, drift, confidence,
                    avg_volatility, volatile, spread, tick_size, strategy_name, bet_type, time_signal,
                    customer_order_ref, status, created_at,
                    blueprint_match, blueprint_move, blueprint_source, result, pnl, won
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.get("marketId"),
                signal.get("selectionId"),
                signal.get("runnerName"),
                signal.get("odds"),
                signal.get("anchor_odd"),
                signal.get("drift"),
                signal.get("confidence"),
                signal.get("avg_volatility"),
                int(signal.get("volatile", False)),
                signal.get("spread"),
                signal.get("tick_size"),
                signal.get("strategy_name"),
                signal.get("bet_type"),
                signal.get("time_signal"),
                signal.get("customerOrderRef"),
                signal.get("status"),
                datetime.utcnow().isoformat(),
                signal.get("blueprint_match", 0),
                signal.get("blueprint_move", ""),
                signal.get("blueprint_source", ""),
                signal.get("result", ""),
                signal.get("pnl", 0.0),
                int(signal.get("won", 0))
            ))
            conn.commit()
            print(f"🧾 Signal saved to DB for {signal.get('selectionId')} in {signal.get('marketId')}")
    except Exception as e:
        logging.warning(f"⚠️ Failed to log signal to DB: {e}")

def print_signal_analysis(signal):
    print("\n📊 SIGNAL ANALYSIS")
    print(f"🏇 Runner:         {signal.get('runnerName')}")
    print(f"🎯 Market ID:      {signal.get('marketId')}")
    print(f"🆔 Selection ID:   {signal.get('selectionId')}")
    print(f"💸 Odds:           {signal.get('odds')}")
    print(f"🪙 Anchor Odds:    {signal.get('anchor_odd')}")
    print(f"📉 Drift:          {signal.get('drift')}%")
    print(f"📏 Spread:         {signal.get('spread')}")
    low = signal.get('range_low')
    high = signal.get('range_high')
    tick_size = signal.get('tick_size', 0.01)
    if low and high and isinstance(low, (int, float)) and isinstance(high, (int, float)):
        ticks = round((high - low) / tick_size)
        print(f"📐 Range:          {low} → {high} ({ticks} ticks)")
    else:
        print("📐 Range:          –")
    print(f"📊 Tick Pattern:   {signal.get('tick_pattern')}")
    print(f"📈 Position Ratio: {round(signal.get('position_ratio', 0), 3)}")
    print(f"🔥 Confidence:     {round(signal.get('confidence', 0) * 100)}%")
    print(f"⚡ Volatility:     {signal.get('avg_volatility', '–')} (Volatile: {signal.get('volatile')})")
    if signal.get("blueprint_match"):
        print(f"📘 Blueprint:      ✅ ({signal.get('blueprint_move')} | {signal.get('blueprint_source')})")
    else:
        print("📘 Blueprint:      None")
    print(f"🧠 Signal Type:    {signal.get('signal_type')}")
    print(f"✅ Status:         {signal.get('status')}")
    if "result" in signal:
        print(f"🏆 Result:         {signal.get('result', '–')} | P&L: {signal.get('pnl', 0.0)} | Won: {signal.get('won')}")

def detect_market_profile(horses):
    if not horses or len(horses) < 4:
        return "unknown"
    odds = sorted([
        h["odds"] for h in horses if isinstance(h.get("odds"), (int, float))
    ])
    if len(odds) < 2:
        return "unknown"
    if odds[0] < 2.2 and odds[1] > 4.5:
        return "short_price_fav"
    elif odds[0] < 3.5 and odds[1] < 5.0:
        return "dual_fav"
    elif odds[0] > 6.0:
        return "wide_open"
    else:
        return "balanced"

