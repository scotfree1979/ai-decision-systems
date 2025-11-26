# 📄 File: enhanced_real_time_bot_feed.py (Updated)

import logging
import traceback
from datetime import datetime

# Logging Configuration
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# ✅ [INIT] Log system startup or global processes
def log_system_initialization():
    messages = [
        "✅ Market Monitor Initialized",
        "✅ Signal Monitor Active",
        "✅ Risk Manager Engaged",
        "✅ Budget Manager Online",
        "✅ Bot Queues Live",
        "✅ Thread Launch Confirmed"
    ]
    for msg in messages:
        logging.info(msg)

# 🧠 Signal Lifecycle Tracking
def log_signal_processing(stage, signal):
    try:
        ref = signal.get("customerOrderRef", "UNKNOWN")
        msg = f"📡 [Signal:{stage.upper()}] Ref: {ref} | Market: {signal.get('marketId')} | Sel: {signal.get('selectionId')} | Strat: {signal.get('strategy_name')}"
        logging.info(msg)
    except Exception as e:
        logging.error(f"❌ Error logging signal stage '{stage}': {e}")

# 🧠 Risk & Budget Evaluation
def log_risk_management(stage, signal, decision):
    try:
        ref = signal.get("customerOrderRef", "UNKNOWN")
        verdict = "✅ PASSED" if decision else "❌ BLOCKED"
        msg = f"🔍 [Risk:{stage.upper()}] Ref: {ref} | Verdict: {verdict}"
        logging.info(msg)
    except Exception as e:
        logging.error(f"❌ Risk log failed: {e}")

def log_budget_check(signal, approved):
    try:
        ref = signal.get("customerOrderRef", "UNKNOWN")
        msg = f"💸 [Budget Check] Ref: {ref} | Status: {'✅ Approved' if approved else '❌ Denied'}"
        logging.info(msg)
    except Exception as e:
        logging.error(f"❌ Budget log failed: {e}")

# 🧾 Bet and DB Ops
def log_bet_and_db_operations(stage, signal, status, extra=""):
    try:
        ref = signal.get("customerOrderRef", "UNKNOWN")
        msg = f"🎯 [Bet:{stage.upper()}] Ref: {ref} | Status: {status} {extra}"
        logging.info(msg)
    except Exception as e:
        logging.error(f"❌ Bet/DB log failed: {e}")

# 🟢 Thread Tracking
def log_thread_start(thread_name):
    try:
        logging.info(f"🧵 [Thread Start] {thread_name} launched successfully")
    except Exception as e:
        logging.error(f"❌ Thread log failed: {e}")

# ⏱️ Timing Event

def log_time_based_event(event_label, signal):
    try:
        ref = signal.get("customerOrderRef", "UNKNOWN")
        msg = f"⏰ [Timer:{event_label}] Ref: {ref} | Market: {signal.get('marketId')} | Strat: {signal.get('strategy_name')}"
        logging.info(msg)
    except Exception as e:
        logging.error(f"❌ Time event log failed: {e}")

# 🚨 Error Reporter
def log_error(context, exception):
    try:
        logging.error(f"🛑 [{context}] {str(exception)}")
        traceback.print_exc()
    except:
        logging.error("❌ Failed to print traceback.")

# ⚠️ Warnings and Nulls
def log_warning(context, details):
    try:
        logging.warning(f"⚠️ [{context}] {details}")
    except:
        logging.warning("⚠️ Failed to log warning.")
