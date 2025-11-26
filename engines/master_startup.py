# master_startup.py (Fully Corrected Final Version)

import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import threading
import time
import datetime
from betfair_api import manual_session_token, BetfairAPI
from enhanced_real_time_bot_feed import log_session_management, log_bet_and_db_operations

APP_KEY = "CZHojduNWa3kxWIn"

def keep_alive_loop(api):
    while True:
        success = api.keep_alive()
        if success:
            log_session_management("session_alive_success")
        else:
            log_session_management("session_alive_fail", error_message="Keep-alive loop encountered an issue.")
        time.sleep(1800)  # every 30 minutes explicitly

def main():
    session_token = manual_session_token()
    log_session_management("session_start")

    api = BetfairAPI()

    keep_alive_thread = threading.Thread(target=keep_alive_loop, args=(api,), daemon=True)
    keep_alive_thread.start()

    while True:
        log_bet_and_db_operations("heartbeat")
        api.fetch_markets()
        api.send_batch()
        time.sleep(300)  # 5-minute loops explicitly

if __name__ == "__main__":
    main()