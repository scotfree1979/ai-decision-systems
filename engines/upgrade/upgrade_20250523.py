# Cleaned upgrade_20250523.py to retain only required utilities under upgrade_20250526 control
import os
import sys
import logging
from datetime import datetime
from queue import Queue, Empty
import threading
import time
import traceback
import sqlite3

from signal_monitor import process_signal, evaluate_signal, bot_signal_queues, signal_queue

from config_paths import DB_PATH
SIGNALS_DB_PATH = os.path.abspath(DB_PATH)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    force=True
)
logging.info(f"📂 [Init] DB path resolved to: {SIGNALS_DB_PATH}")

# -------------- Clean Retained Core Utilities ----------------

def launch_protocol():
    with signal_queue.mutex:
        signal_queue.queue.clear()
    for q in bot_signal_queues.values():
        with q.mutex:
            q.queue.clear()
    logging.info("✅ System boot sequence complete.")

def log_system_initialization():
    for msg in [
        "✅ Market Monitor Initialized",
        "✅ Signal Monitor Active",
        "✅ Risk Manager Engaged",
        "✅ Budget Manager Online",
        "✅ Bot Queues Live",
        "✅ Thread Launch Confirmed"]:
        logging.info(msg)

# -------------- Removed Upgrade Monitor Logic (Now Controlled by Upgrade 26) ----------------

# -------------- Optional Utility - Queue Monitor (can be retained if desired) ----------------

def queue_monitor():
    logging.info("🧪 [QueueMonitor] Thread active. Waiting for signals...")
    logging.info(f"📌 [DEBUG] signal_queue memory ID (queue_monitor): {id(signal_queue)}")

    while True:
        try:
            signal = signal_queue.get(timeout=5)
            logging.info(f"📥 [QueueMonitor] Signal received from queue: {signal}")

            logging.info(f"🔀 [QueueMonitor] Passing signal to Signal Monitor: {signal}")
            process_signal(signal)
            logging.info(f"🔎 [QueueMonitor] Signal after enrichment: {signal}")
            logging.info("🧠 [QueueMonitor] Evaluating signal...")
            if evaluate_signal(signal):
                logging.info("✅ [QueueMonitor] Signal approved by Risk Manager")
                if signal.get('stake', 0) <= 800.0:
                    logging.info("💰 [QueueMonitor] Budget check passed, routing to bot...")
                    bot = signal.get('bot_name', 'OGHeadTrader')
                    bot_signal_queues[bot].put(signal)
                    logging.info(f"📦 [QueueMonitor] Signal routed to {bot}")
                else:
                    logging.info("🔐 [QueueMonitor] Budget check failed")
            else:
                logging.info("🚫 [QueueMonitor] Risk check failed")

        except Empty:
            continue
        except Exception as e:
            logging.error(f"❌ [QueueMonitor] Error while processing signal: {e}")
            traceback.print_exc()

if __name__ == "__main__":
    launch_protocol()
    threading.Thread(target=queue_monitor, name="queue_monitor", daemon=True).start()
    logging.info("✅ QueueMonitor thread launched.")
