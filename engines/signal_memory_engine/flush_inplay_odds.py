import sqlite3
import json
import time
from datetime import datetime
from config_paths import DB_PATH


def flush_inplay_odds_to_db(self):
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
