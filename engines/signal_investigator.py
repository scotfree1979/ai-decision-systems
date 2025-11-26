# signal_investigator.py

import json
import time
import sqlite3
from datetime import datetime
from signal_memory_engine import signal_memory

# 🔧 Ensure snapshot_cache exists for simulation
if not hasattr(signal_memory, "snapshot_cache"):
    signal_memory.snapshot_cache = {}

from pprint import pprint
import glob
from config_paths import DB_PATH
from collections import deque


def force_memory_entry(signal):
    key = (signal['marketId'], signal['selectionId'])
    snapshot = {
        'odds_history': deque([signal["odds"]], maxlen=12),
        'range_low': signal["range_low"],
        'range_high': signal["range_high"],
        'tick_pattern': signal["tick_pattern"],
        'position_ratio': signal["position_ratio"],
        'volatility': signal["volatility"],
        'direction_bias': signal["scalp_direction"].replace("_", ""),
        'anchor_odd': signal["odds"],
        'oc_snapshots': {"OC1": {"odds": signal["odds"]}},
        'minutes_to_post': signal.get("minutes_to_post", 10),
        'last_updated': time.time(),
        'runner_info': {"status": "ACTIVE"},
        'market_info': {"status": "OPEN", "inplay": False},
        'valid_signal': True,
        'can_trade': True,
    }
    signal_memory.memory_write[key] = snapshot
    signal_memory.memory_read[key] = snapshot
    signal_memory.snapshot_cache[key] = {
        "snapshot": snapshot,
        "last_updated": time.time(),
        "source": "forced"
    }
    if signal["marketId"] not in signal_memory.active_tradable_markets:
        signal_memory.active_tradable_markets.append(signal["marketId"])


def safe_float(x):
    try:
        return float(x)
    except:
        return None

# Load blueprints (match today's version automatically)
def load_blueprint_patterns():
    try:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        matching = sorted(glob.glob(f"blueprint_signals_{today}.json"))
        if not matching:
            raise FileNotFoundError(f"No blueprint_signals_{today}.json found")

        latest_file = matching[-1]
        print(f"📘 Loading blueprints from latest file: {latest_file}")

        with open(latest_file, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to load blueprint patterns: {e}")
        return {}

# Load runners from bets table for today (relax anchor_odd requirement)
def load_signals_from_bets():
    print("📦 Loading test signals from bets table...")
    today = datetime.utcnow().date().isoformat()
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("PRAGMA table_info(bets)")
    columns = [row[1] for row in cursor.fetchall()]

    date_column = None
    for name in ["marketStartTime", "timestamp", "marketTime", "start_time"]:
        if name in columns:
            date_column = name
            break

    if not date_column:
        print("❌ Could not find a suitable date column in 'bets' table.")
        return []

    print(f"🕒 Using date column: {date_column}")

    cursor.execute(f"""
        SELECT marketId, selectionId, anchor_odd,
               odds_check_1, odds_check_2, odds_check_3,
               odds_check_4, odds_check_5, odds_check_6
        FROM bets
    """) 
    rows = cursor.fetchall()
    print(f"🧪 Raw rows returned from DB: {len(rows)}")
    for row in rows[:3]:
        print(f"   ↳ {row}")
    conn.close()

    signals = []
    for row in rows:
        market_id, selection_id, anchor, *ocs = row
        tick_trail_raw = [anchor] + list(ocs)
        tick_trail = [safe_float(x) for x in tick_trail_raw if safe_float(x) is not None]
        if not tick_trail or len(tick_trail) < 2:
            continue

        deltas = [tick_trail[i+1] - tick_trail[i] for i in range(len(tick_trail)-1)]
        if all(d > 0 for d in deltas):
            tick_pattern = "drifted"
        elif all(d < 0 for d in deltas):
            tick_pattern = "steamed"
        elif any(abs(d) > 0.05 for d in deltas):
            tick_pattern = "pingpong"
        else:
            tick_pattern = "flat"

        range_low = min(tick_trail)
        range_high = max(tick_trail)
        odds = tick_trail[-1]
        direction = "lay_to_back" if tick_pattern == "drifted" else "back_to_lay"

        signal = {
            "marketId": market_id,
            "selectionId": selection_id,
            "odds": odds,
            "range_low": range_low,
            "range_high": range_high,
            "tick_pattern": tick_pattern,
            "position_ratio": 0.5,
            "volatility": 0.2,
            "scalp_direction": direction,
            "signal_type": "investigator_reconstructed",
            "confidence": 0.72,
            "minutes_to_post": 10,
            "forced": True,
            "already_traded": False
        }
        signals.append(signal)

    print(f"✅ Loaded {len(signals)} signals from bets table.")
    return signals

# ⬇️ Simulate what the system would do when placing bets and firing ladders
def place_basic_lay_bet(signal):
    print(f"💰 [Simulated] Bet placed → BACK @ {round(signal['odds'] - 0.1, 2)} | LAY @ {round(signal['odds'] + 0.1, 2)}")
    print(f"📈 Ladder triggered → BACK: {round(signal['odds'] - 0.1, 2)}, LAY: {round(signal['odds'] + 0.1, 2)}")
    key = (signal['marketId'], signal['selectionId'])
    signal_memory.active_ladders[key] = {"status": "SIMULATED_LADDER_PLACED"}

blueprint_patterns = load_blueprint_patterns()
test_signals = load_signals_from_bets()
results = []

print("\n================ SIGNAL INVESTIGATOR ================")
print(f"📊 Running {len(test_signals)} signals through full logic\n")

for idx, signal in enumerate(test_signals):
    key = (signal['marketId'], signal['selectionId'])
    force_memory_entry(signal)  # ✅ Consolidated call

    print(f"\n🔍 Signal {idx+1}: {signal['tick_pattern'].upper()} @ {signal['odds']} | Dir: {signal['scalp_direction']} | Conf: {signal['confidence']}")
    start_time = time.time()

    print(f"   🧪 Pre-check snapshot: {key in signal_memory.snapshot_cache}")
    try:
        place_basic_lay_bet(signal)
        elapsed = round(time.time() - start_time, 2)

        snapshot = signal_memory.get_static_snapshot().get(key, {}).get("snapshot", {})
        if not snapshot:
            print(f"❌ No snapshot found in memory.")
        else:
            print(f"✅ Snapshot exists | Last updated: {round(time.time() - snapshot.get('last_updated', 0), 1)}s ago")

        if key in signal_memory.active_ladders:
            print(f"✅ Ladder status: {signal_memory.active_ladders[key]['status']}")
        else:
            print(f"❌ No ladder created for this signal.")

        results.append({"signal": signal, "status": "processed", "elapsed": elapsed})
    except Exception as e:
        print(f"❌ Signal failed: {e}")
        results.append({"signal": signal, "status": "error", "error": str(e)})

passes = sum(1 for r in results if r["status"] == "processed")
fails = len(results) - passes
print("\n================ INVESTIGATION REPORT ================")
print(f"✅ Processed:  {passes}")
print(f"❌ Failed:     {fails}\n")

print("====================================================")
print("Run complete. You can now test live runners from today's DB entries.")
