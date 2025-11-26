import time
import sqlite3
from datetime import datetime
from config_paths import DB_PATH
from upgrade_import_patch import get_session_token
from utils.api_tools import fetch_live_odds
from db.signal_memory_db_writer import save_snapshot_to_ram
from signal_memory_engine.ram_reader import get_static_snapshot


def monitor_in_play_trends(self):
    self.session_token = get_session_token()

    while True:
        try:
            for market_id, runners in list(self.live_markets.items()):
                for selection_id in runners:
                    odds = fetch_live_odds(self.session_token, market_id, selection_id).get("lay")
                    if not odds:
                        continue

                    with sqlite3.connect(DB_PATH) as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO inplay_ticks (market_id, selection_id, timestamp, lay_odds)
                            VALUES (?, ?, ?, ?)
                        """, (market_id, selection_id, datetime.utcnow().isoformat(), odds))
                        conn.commit()

                    minutes = self.get_minutes_to_post(market_id)
                    if minutes > 0 or minutes < -15:
                        continue

                    self.in_play_tick_tracker[market_id][selection_id].append(odds)
                    self.live_inplay_odds[market_id][selection_id].append(odds)

                    key = (market_id, selection_id)
                    snapshot = get_static_snapshot().get(key, {}).get("snapshot", {})
                    snapshot.setdefault("oc_snapshots", {})
                    snapshot["oc_snapshots"].setdefault("OC7", {"odds_series": []})
                    snapshot["oc_snapshots"]["OC7"]["odds_series"].append(odds)
                    save_snapshot_to_ram(market_id, selection_id, snapshot)

                    if odds <= 1.5:
                        self.record_signal_outcome(
                            market_id=market_id,
                            selection_id=selection_id,
                            result="won",
                            explanation="In-play odds collapsed to ≤ 1.5"
                        )
        except Exception as e:
            print(f"⚠️ In-play monitor error: {e}")

        time.sleep(2)
