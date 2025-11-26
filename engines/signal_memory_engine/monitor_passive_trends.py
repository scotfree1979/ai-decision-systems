import time
from upgrade_import_patch import get_session_token
from utils.api_tools import fetch_live_odds


def monitor_passive_trends(self):
    self.session_token = get_session_token()

    while True:
        try:
            for market_id, runners in list(self.live_markets.items()):
                for selection_id in runners:
                    odds = fetch_live_odds(self.session_token, market_id, selection_id).get("lay")
                    if odds:
                        self.passive_tick_tracker[market_id][selection_id].append(odds)
        except Exception as e:
            print(f"⚠️ Passive trend error: {e}")

        time.sleep(2)
