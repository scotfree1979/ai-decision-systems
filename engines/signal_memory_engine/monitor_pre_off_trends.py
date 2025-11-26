import time
from upgrade_import_patch import get_session_token
from utils.api_tools import fetch_live_odds


def monitor_pre_off_trends(self):
    self.session_token = get_session_token()

    while True:
        try:
            for market_id, runners in self.in_play_tick_tracker.items():
                for selection_id in runners:
                    odds = fetch_live_odds(self.session_token, market_id, selection_id).get("lay")
                    if odds and odds <= 1.5:
                        self.record_signal_outcome(
                            market_id=market_id,
                            selection_id=selection_id,
                            result="won",
                            explanation="Detected in-play collapse to ≤ 1.5"
                        )
        except Exception as e:
            print(f"⚠️ Pre-off monitor error: {e}")

        time.sleep(2)
