


import time
import threading
from collections import defaultdict
from db.signal_memory_db_writer import save_snapshot_to_ram
from config_paths import DB_PATH
import sqlite3
from datetime import datetime
import json
from upgrade_import_patch import get_session_token
from utils.api_tools import fetch_live_odds


def maybe_reset_for_new_day(self):
    """
    Initializes session if a new day is detected. Resets session state and reloads live memory.
    """
    while not hasattr(self, "session_token") or self.session_token is None:
        print("⏳ Waiting for session token...")
        time.sleep(1)

    today = datetime.utcnow().date()
    if not hasattr(self, 'session_date'):
        self.session_date = today

    if today != self.session_date:
        self.flush_inplay_odds_to_db()
        print(f"🔄 New day detected ({today}). Resetting session state.")
        self.start_new_session(balance=self.current_balance)
        self.session_date = today
    else:
        print("📆 Same day. Continuing with existing session.")

    if not hasattr(self, "_debug_logger"):
        self.setup_daily_logger()

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
    self.SEGMENT_DB = self.extract_blueprint_segments()

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

    self.load_pattern_pnl_from_playbooks()

    from signal_memory.hooks.monitoring import (
        monitor_passive_trends,
        monitor_pre_off_trends,
        monitor_in_play_trends,
        evaluate_market_loop,
        generate_signal_status_report,
        update_tradable_markets,
        monitor_live_for_partial_blueprints,
        evaluate_active_runners,
        conflict_debug_watchdog
    )

    threading.Thread(target=monitor_passive_trends, args=(self,), daemon=True).start()
    threading.Thread(target=monitor_pre_off_trends, args=(self,), daemon=True).start()
    threading.Thread(target=monitor_in_play_trends, args=(self,), daemon=True).start()

    print("📦 Waiting 5s before initial OC snapshot load...")
    time.sleep(5)
    print("📦 Initialising session with OC snapshot load...")
    self.load_oc_snapshots_from_db()

    threading.Thread(target=evaluate_market_loop, args=(self,), daemon=True).start()
    threading.Thread(target=generate_signal_status_report, args=(self,), daemon=True).start()
    threading.Thread(target=update_tradable_markets, args=(self,), daemon=True).start()
    threading.Thread(target=monitor_live_for_partial_blueprints, args=(self,), daemon=True).start()
    threading.Thread(target=evaluate_active_runners, args=(self,), daemon=True).start()
    threading.Thread(target=conflict_debug_watchdog, args=(self,), daemon=True).start()


def flush_inplay_odds_to_db(self):
    from datetime import datetime
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        now = datetime.utcnow().isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")

        for market_id, runners in self.live_inplay_odds.items():
            for selection_id, odds_series in runners.items():
                if not odds_series:
                    continue
                cursor.execute("""
                    INSERT INTO inplay_odds_log (market_id, selection_id, date, start_time, end_time, odds_series)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    market_id, selection_id, today, now, now, json.dumps(odds_series)
                ))
        conn.commit()
    print(f"✅ Flushed in-play odds to DB.")


