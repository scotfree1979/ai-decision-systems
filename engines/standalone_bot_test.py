# ✅ standalone_bot_test.py – Simulates full bot launcher flow with test signals

import logging
import time
from queue import Queue
from datetime import datetime
from og_bot_family_launcher import launch_bot_family
from daily_config import SESSION_TOKEN

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# ✅ Shared Queues
signal_queue = Queue()
bot_signal_queues = {
    "OGHeadTrader": Queue(),
    "OGPreoff": Queue(),
    "OGInPlay": Queue()
}

# ✅ Manually define test runners
runners = [
    ("1.244598132", 29531321, "Fascinating Lips", "2025-06-09T14:30:00.000Z"),
    ("1.244598132", 47704046, "Irezumi", "2025-06-09T14:30:00.000Z"),
    ("1.244598132", 45676673, "Foinix", "2025-06-09T14:30:00.000Z"),
    ("1.244598132", 17970593, "Fullforward", "2025-06-09T14:30:00.000Z"),
    ("1.244598163", 12345678, "Example Horse A", "2025-06-09T14:50:00.000Z"),
    ("1.244598163", 22345678, "Example Horse B", "2025-06-09T14:50:00.000Z"),
    ("1.244598163", 32345678, "Example Horse C", "2025-06-09T14:50:00.000Z"),
    ("1.244598163", 42345678, "Example Horse D", "2025-06-09T14:50:00.000Z"),
    ("1.244598195", 98765432, "Sample Horse A", "2025-06-09T15:10:00.000Z"),
    ("1.244598195", 87654321, "Sample Horse B", "2025-06-09T15:10:00.000Z"),
    ("1.244598195", 76543210, "Sample Horse C", "2025-06-09T15:10:00.000Z"),
    ("1.244598195", 65432109, "Sample Horse D", "2025-06-09T15:10:00.000Z")
]

# ✅ Group by race (every 4 runners = 1 race)
race_groups = [runners[i:i + 4] for i in range(0, len(runners), 4)]
now = datetime.utcnow().isoformat()
signals_to_test = []

# ✅ Build test signals
for race in race_groups:
    if len(race) < 4:
        continue
    for idx, (market_id, selection_id, horse_name, market_start) in enumerate(race):
        ladder_odds = 3.0 if idx == 0 else 5.0
        signals_to_test.append({
            "status": "approved",
            "bet_type": "LADDER",
            "marketId": market_id,
            "selectionId": selection_id,
            "odds": ladder_odds,
            "stake": 5.0,
            "customerOrderRef": f"LDR_{selection_id}",
            "marketStartTime": market_start,
            "bot_name": "OGHeadTrader",
            "session_token": SESSION_TOKEN,
            "time_signal": "time_20"
        })

    # Scalp - second runner
    market_id, selection_id, horse_name, market_start = race[1]
    signals_to_test.append({
        "status": "approved",
        "bet_type": "SCALP",
        "marketId": market_id,
        "selectionId": selection_id,
        "odds": 5.0,
        "stake": 5.0,
        "customerOrderRef": f"SCP_{selection_id}",
        "marketStartTime": market_start,
        "bot_name": "OGPreoff",
        "session_token": SESSION_TOKEN,
        "time_signal": "time_10"
    })

    # GreenUp - third runner
    market_id, selection_id, horse_name, market_start = race[2]
    signals_to_test.append({
        "status": "approved",
        "bet_type": "GREENUP",
        "marketId": market_id,
        "selectionId": selection_id,
        "odds": 5.0,
        "stake": None,
        "customerOrderRef": f"GRN_{selection_id}",
        "marketStartTime": market_start,
        "bot_name": "OGHeadTrader",
        "session_token": SESSION_TOKEN,
        "time_signal": "time_5"
    })

# ✅ InPlay - 5 scattered runners
inplay_targets = runners[:5]
for market_id, selection_id, horse_name, market_start in inplay_targets:
    signals_to_test.append({
        "status": "approved",
        "bet_type": "INPLAY",
        "marketId": market_id,
        "selectionId": selection_id,
        "odds": 5.0,
        "stake": 2.0,
        "customerOrderRef": f"INP_{selection_id}",
        "marketStartTime": market_start,
        "bot_name": "OGInPlay",
        "session_token": SESSION_TOKEN,
        "time_signal": "time_0"
    })

# ✅ Queue signals for testing
for signal in signals_to_test:
    bot_queue = bot_signal_queues.get(signal["bot_name"])
    if bot_queue:
        bot_queue.put(signal)
        logging.info(f"📬 Queued signal: {signal['customerOrderRef']} → {signal['bot_name']}")
    else:
        logging.warning(f"❌ No bot queue found for: {signal['bot_name']}")

# ✅ Launch bots
launch_bot_family(
    headers={"X-Authentication": SESSION_TOKEN, "X-Application": "CZHojduNWa3kxWIn"},
    bot_assignments={},
    bot_budget_limits={},
    toggles={},
    bot_signal_queues=bot_signal_queues
)

# ⏱️ Let it run and observe output
logging.info("🧪 [Standalone Test] Bots launched with test signals. Watching for output...")
time.sleep(15)
logging.info("✅ [Standalone Test] Run complete. Check logs for bet execution summaries.")
