# ✅ standalone_bot_test.py – Simulates full bot launcher flow with test signals

import logging
import time
from queue import Queue
from og_bot_family_launcher import launch_bot_family
from upgrade_import_patch import *
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')

# ✅ Shared Queues – exactly as used in live tool
bot_signal_queues = {
    "OGHeadTrader": Queue(),
    "OGPreoff": Queue(),
    "OGInPlay": Queue()
}

# ✅ Patch queues into upgrade import patch for downstream use
upgrade_import_patch.bot_signal_queues = bot_signal_queues

# ✅ Dummy managers to satisfy launcher injection
class DummyRiskManager:
    pass

class DummyBudgetManager:
    pass

# ✅ Test signal payloads (mimicking real post-risk-approved rows)
test_signals = [
    {
        "status": "approved",
        "bet_type": "BASIC",
        "marketId": "1.TESTMARKET",
        "selectionId": 123,
        "odds": 4.2,
        "stake": 2.0,
        "customerOrderRef": "TestRef_HT_001",
        "marketStartTime": datetime.utcnow().isoformat(),
        "time_signal": "time_10"
    },
    {
        "status": "approved",
        "bet_type": "LADDER",
        "marketId": "1.TESTMARKET",
        "selectionId": 124,
        "odds": 3.9,
        "stake": 2.0,
        "customerOrderRef": "TestRef_PO_001",
        "marketStartTime": datetime.utcnow().isoformat(),
        "time_signal": "time_10"
    },
    {
        "status": "approved",
        "bet_type": "INPLAY",
        "marketId": "1.TESTMARKET",
        "selectionId": 125,
        "odds": 5.0,
        "stake": 2.0,
        "customerOrderRef": "TestRef_IP_001",
        "marketStartTime": datetime.utcnow().isoformat(),
        "time_signal": "time_10"
    }
]

# ✅ Inject each test signal into its correct queue
bot_signal_queues["OGHeadTrader"].put(test_signals[0])
bot_signal_queues["OGPreoff"].put(test_signals[1])
bot_signal_queues["OGInPlay"].put(test_signals[2])

# ✅ Launch all bots through real launcher
launch_bot_family(
    headers={"X-Authentication": "TEST_TOKEN", "X-Application": "TEST_APP"},
    risk_mgr=DummyRiskManager(),
    bot_assignments={},
    bot_budget_limits={},
    toggles={},
    budget_mgr=DummyBudgetManager(),
    bot_signal_queues=bot_signal_queues
)

# Keep test running long enough to observe behavior
logging.info("🧪 [Standalone Test] Bots launched with test signals. Watching for output...")
time.sleep(10)
