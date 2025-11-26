import logging
import threading
import time
import requests
import json
from datetime import datetime, timedelta
from upgrade_import_patch import *
from og_bot_family_launcher import launch_bot_family

# ✅ Threaded imports split across upgrades
from upgrade.upgrade_20250525 import (
    fetch_market_data,
    run_monitor,
    signal_monitor,
    market_monitor_signals,
    launch_protocol
)
from upgrade.upgrade_20250526 import (
    risk_manager,
    budget_manager,
    bet_settlement_monitor
)

# ✅ Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
logging.info("\U0001F680 Starting Betfair Automation System")

# ✅ Keep session alive and fetch budget
keep_alive()
fetch_budget()

# ✅ Launch bots only when valid markets exist
url = "https://api.betfair.com/exchange/betting/json-rpc/v1"
response = requests.post(
    url,
    headers={
        "X-Application": APP_KEY,
        "X-Authentication": SESSION_TOKEN,
        "Content-Type": "application/json"
    },
    data=json.dumps([
        {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listMarketCatalogue",
            "params": {
                "filter": {
                    "eventTypeIds": ["7"],
                    "marketStartTime": {
                        "from": datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z"),
                        "to": (datetime.utcnow() + timedelta(days=2)).strftime("%Y-%m-%dT00:00:00Z")
                    }
                },
                "maxResults": "30"
            },
            "id": 1
        }
    ])
)
markets = response.json()[0].get("result", []) if response.status_code == 200 else None
if not markets:
    logging.warning("⚠️ Market data fetch failed, bot launch halted.")
else:
    launch_bot_family(
        signal_queues=bot_signal_queues,
        headers={
            "X-Application": APP_KEY,
            "X-Authentication": SESSION_TOKEN,
            "Content-Type": "application/json"
        },
        risk_mgr=risk_manager,
        bot_assignments={
            "OGHeadTrader": "HeadTraderBot",
            "OGPreoff": "PreOffBot",
            "OGInPlay": "OGInPlayBot"
        },
        bot_budget_limits={},
        toggles={},
        budget_mgr=budget_manager
    )

# ✅ Launch protocol from upgrade 25
threading.Thread(target=launch_protocol, daemon=True).start()

# ✅ Session + Evaluation Flow
threading.Thread(target=run_monitor, daemon=True).start()
threading.Thread(target=market_monitor_signals, daemon=True).start()
threading.Thread(target=signal_monitor, daemon=True).start()
threading.Thread(target=risk_manager, daemon=True).start()
threading.Thread(target=budget_manager, daemon=True).start()
threading.Thread(target=bet_settlement_monitor, daemon=True).start()

# ♻️ Continuous loop
try:
    while True:
        keep_alive()
        time.sleep(60)
except KeyboardInterrupt:
    logging.warning("🛑 Shutdown signal received. Exiting...")
