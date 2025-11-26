import time
import os
import logging
import sqlite3
from datetime import datetime
from collections import defaultdict
from config_paths import DB_PATH


from signal_memory_engine.reporting import generate_signal_status_report
from signal_memory_engine.pnl import load_pattern_pnl_from_playbooks
from signal_memory_engine.flush_inplay_odds import flush_inplay_odds_to_db

def maybe_reset_for_new_day(self):
    start = time.time()
    while not hasattr(self, "session_token") or self.session_token is None:
        if time.time() - start > 10:
            raise RuntimeError("❌ Session token not injected into engine after 10s.")
        print("⏳ Waiting for session token...")
        time.sleep(1)


    today = datetime.utcnow().date()
    if not hasattr(self, 'session_date'):
        self.session_date = today

    if today != self.session_date:
        flush_inplay_odds_to_db(self)
        print(f"🔄 New day detected ({today}). Resetting session state.")
        start_new_session(self, balance=self.current_balance)
        self.session_date = today
    else:
        print("📆 Same day. Continuing with existing session.")
        # ✅ Clean boot of OC Snapshot Recorder
        

    if not hasattr(self, "_debug_logger"):
        setup_daily_logger(self)

    if self.session_token:
        print(f"🔐 Session token injected: {self.session_token[:6]}...")
    else:
        print("⚠️ Warning: No session token available yet.")

    self.session_active = True
    self.session_date = datetime.utcnow().date()
    self.starting_balance = 600.0
    self.current_balance = 600.0
    self.halt_triggered = False
    self.trade_log = []
    self.clean_exit_log = []
    self.current_stake = 10.0
    self.open_bets = defaultdict(list)
    self.unmatched_signals = {}

    self.live_playbooks = defaultdict(lambda: {
        "start_oc": None,
        "end_oc": None,
        "opened_at": None,
        "closed_at": None,
        "pnl": 0.0,
        "status": "open",
        "steps": []
    })

    self.live_pattern_pnl = defaultdict(lambda: {
        "trades": 0,
        "net_pnl": 0.0
    })

    load_pattern_pnl_from_playbooks(self)
    print("📈 OC Snapshot Recorder launched and session is clean.")


def start_new_session(self, balance=500.0):
    self.session_active = True
    self.starting_balance = balance
    self.current_balance = balance
    self.halt_triggered = False
    self.trade_log = []
    self.clean_exit_log = []
    self.current_stake = 10.0
    self.open_bets.clear()
    self.unmatched_signals.clear()

def setup_daily_logger(self):
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    part = 1

    while True:
        log_file = os.path.join(log_dir, f"{today_str}_part{part}.log")
        if not os.path.exists(log_file):
            break
        if os.path.getsize(log_file) < 100_000:
            break
        part += 1

    logger = logging.getLogger("SignalDebugLogger")
    logger.setLevel(logging.INFO)

    if logger.hasHandlers():
        logger.handlers.clear()

    handler = logging.FileHandler(log_file, mode='a')
    formatter = logging.Formatter('%(asctime)s | %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    self._debug_logger = logger
    self._debug_log_path = log_file
    logger.info(f"🟢 Logging started: {log_file}")




