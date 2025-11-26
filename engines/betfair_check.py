# ✅ betfair_check.py – Live Backdoor Entry + Launch Trigger (Fixed Risk & Budget Injection)

# -----------------------------
# ✅ betfair_check.py – Live Backdoor Entry + Launch Trigger (Clock Integrated)
# -----------------------------

# -----------------------------
# ✅ Imports
# -----------------------------
import logging
import sys
import threading
import time
import os
import json
import requests
from datetime import datetime, timedelta
from queue import Queue

# -----------------------------
# ✅ Project Imports
# -----------------------------
from daily_config import TODAY_DATE, fetch_available_budget
from upgrade_import_patch import APP_KEY, set_session_token
from get_markets import get_markets_and_insert
from og_bot_family_launcher import launch_bot_family

from signal_monitor import bot_signal_queues

# -----------------------------
# ✅ Get Session Token Input First (Corrected)
# -----------------------------
import os  # Must be before os.environ access

token = ""
while not token:
    token = input("🔐 Enter your Betfair session token: ").strip()

# ✅ Set in environment BEFORE any imports that rely on it
os.environ["SESSION_TOKEN"] = token

# ✅ Set in config + patch (if used)
import upgrade_import_patch

set_session_token(token)
upgrade_import_patch.SESSION_TOKEN = token  # Optional fallback link

# -----------------------------
# ✅ Session Maintenance + Budget
# -----------------------------
def keep_alive():
    try:
        response = requests.post(
            "https://identitysso.betfair.com/api/keepAlive",
            headers={
                "X-Authentication": token,
                "X-Application": APP_KEY
            }
        )
        if response.status_code == 200:
            logging.info(f"✅ [Session] Session keep-alive successful at {datetime.utcnow()}")
        else:
            logging.warning("⚠️ [Session] Keep-alive failed.")
    except Exception as e:
        logging.error(f"❌ [Session] Error keeping session alive: {e}")

AVAILABLE_BUDGET = fetch_available_budget()

# -----------------------------
# ✅ TEMP CLEANUP FOR DEV TESTING
# -----------------------------
def clear_bets_table():
    import sqlite3
    from config_paths import DB_PATH
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM bets")

# -----------------------------
# ✅ Queue Inspector Utility
# -----------------------------
def inspect_bot_queues():
    for bot, q in bot_signal_queues.items():
        logging.info(f"🔍 Queue status → {bot}: {q.qsize()} item(s) waiting")

# =============================================
# ✅ betfair_check.py – Main Launcher Runtime (Updated)
# =============================================

from upgrade.upgrade_20250602_flow_01 import (
    validate_bets_table,
    run_monitor,
    run_clock_only,
    market_monitor_signals,
    signal_monitor,
    risk_manager,
    budget_manager
)
from og_brain_logic import place_bets_from_brain
from database_hijack_monitor import launch_db_writer
from enrich_with_betfair_meta import enrich_with_betfair_meta, get_betfair_runner_meta
from upgrade_import_patch import (
    run_monitor_ready,
    market_monitor_ready,
    signal_monitor_ready,
    risk_system_ready,
    bot_signal_queues
)
from learning_optimizer import LearningOptimizer

import upgrade_import_patch
upgrade_import_patch.bot_signal_queues = bot_signal_queues

# -----------------------------
# ✅ BOT LAUNCH – Connects to patch queues
# -----------------------------
def start_bot_queues():
    print("⟲ Bot queue processors launched")

    def process_bot_queue(bot_name):
        try:
            logging.info(f"🔵 {bot_name} bot loop starting...")
            while True:
                try:
                    signal = bot_signal_queues[bot_name].get(timeout=10)
                    logging.info(f"🌟 {bot_name} processing signal: {signal['customerOrderRef']}")

                    from bet_placer import (
                        place_basic_lay_bet,
                        place_ladder_lay_bet,
                        place_greenup_exit,
                        place_inplay_lay_bet,
                        place_scalp_trade
                    )

                    bet_type = signal.get("bet_type")

                    if bet_type == "BASIC":
                        place_basic_lay_bet(signal)
                    elif bet_type == "LADDER":
                        place_ladder_lay_bet(signal)
                    elif bet_type == "GREENUP":
                        place_greenup_exit(signal)
                    elif bet_type == "INPLAY":
                        place_inplay_lay_bet(signal)
                    elif bet_type == "SCALP":
                        place_scalp_trade(signal)
                    else:
                        logging.warning(f"⚠️ Unknown bet type: {bet_type} for ref {signal['customerOrderRef']}")

                    bot_signal_queues[bot_name].task_done()
                    time.sleep(0.1)

                except Exception as inner:
                    logging.exception(f"❌ {bot_name} inner loop error:")
                    time.sleep(1)
        except Exception as e:
            logging.exception(f"❌ {bot_name} bot thread startup failed:")

    for bot_name in bot_signal_queues:
        threading.Thread(target=process_bot_queue, args=(bot_name,), name=bot_name, daemon=True).start()

def launch_upgrade_clock_controller():
    threading.Thread(target=run_clock_only, daemon=True).start()

def launch_learning_optimizer():
    try:
        optimizer = LearningOptimizer()
        threading.Thread(target=optimizer.run, name="LearningOptimizer", daemon=True).start()
        logging.info("🧠 [LearningOptimizer] Thread started")
    except Exception as e:
        logging.error(f"❌ Failed to launch Learning Optimizer: {e}")

def launch_protocol():
    logging.info("🧠 [launch_protocol] Default launch protocol triggered.")

    launch_db_writer()
    logging.info("🧵 [DB Hijack Monitor Started]")

    launch_bot_family(bot_signal_queues=bot_signal_queues)
    logging.info("🤖 [Bot Family Launched]")

    launch_upgrade_clock_controller()
    logging.info("🕒 [Live Clock Countdown Started]")

    def wrapped_run_monitor():
        logging.info("✅ [Run Monitor] Launch confirmed")
        run_monitor_ready.set()
        run_monitor()
        logging.info("✅ [Run Monitor] First pass complete")

    def wrapped_market_monitor():
        run_monitor_ready.wait()
        logging.info("✅ [Market Monitor] Launch confirmed")
        market_monitor_signals()
        logging.info("✅ [Market Monitor] First pass complete")
        market_monitor_ready.set()

    def wrapped_signal_monitor():
        def _inner():
            logging.info("🧪 [Wrapped Signal Monitor] Inner function executing")
            market_monitor_ready.wait()
            logging.info("✅ [Signal Monitor] Launch confirmed")
            signal_monitor()
            logging.info("✅ [Signal Monitor] First pass complete")

        logging.info("🧪 [Wrapped Signal Monitor] Thread created, launching inner...")
        threading.Thread(target=_inner, name="SignalMonitorInner", daemon=True).start()
        logging.info("✅ [Signal Monitor] First pass complete")

    def wrapped_risk_manager():
        signal_monitor_ready.wait()
        threading.Thread(target=risk_manager, name="RiskManager", daemon=True).start()
        threading.Thread(target=budget_manager, name="BudgetManager", daemon=True).start()
        logging.info("✅ [Risk & Budget Managers] Launched")

    def wrapped_brain():
        risk_system_ready.wait()
        threading.Thread(target=place_bets_from_brain, name="OGBrain", daemon=True).start()
        logging.info("🧠 [OG Brain Logic Started]")

    threading.Thread(target=wrapped_run_monitor, name="RunMonitor", daemon=True).start()
    threading.Thread(target=wrapped_market_monitor, name="MarketMonitor", daemon=True).start()
    threading.Thread(target=wrapped_signal_monitor, name="SignalMonitor", daemon=True).start()
    threading.Thread(target=wrapped_risk_manager, name="RiskManagerBlock", daemon=True).start()
    threading.Thread(target=wrapped_brain, name="BrainBlock", daemon=True).start()
    threading.Thread(target=launch_learning_optimizer, name="LearningOptimizerBlock", daemon=True).start()
    threading.Thread(target=start_bot_queues, name="StartBotQueues", daemon=True).start()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s', force=True)
    logging.info("🚀 Starting Betfair Automation System")

    keep_alive()

    try:
        validate_bets_table()
        logging.info("✅ Bets table validated.")

        logging.info("🚀 Launching upgrade v2 protocol inline...")
        launch_protocol()

        logging.info("🔎 Fetching GB/IE markets for today/tomorrow with 7+ runners...")
        markets = get_markets_and_insert()
        logging.info(f"📆 Market fetch returned {len(markets)} entries")

        if not markets:
            logging.warning("⚠️ No valid GB/IE markets found with 7+ runners. Tool halted.")
            sys.exit(0)

        logging.info("♻️ Entering session keep-alive loop")
        while True:
            keep_alive()
            time.sleep(60)

    except Exception as e:
        logging.error(f"❌ Critical failure during main startup: {e}")
        sys.exit(1)

    except KeyboardInterrupt:
        logging.warning("⛔ Shutdown signal received. Exiting...")
