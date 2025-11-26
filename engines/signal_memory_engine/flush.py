import sqlite3
import json
from datetime import datetime
from config_paths import DB_PATH

def flush_inplay_odds_to_db(self):
    """
    🧹 Persist in-play odds from memory to DB at end of day.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            now = datetime.utcnow().isoformat()
            today = datetime.utcnow().strftime("%Y-%m-%d")

            for market_id, runners in self.live_inplay_odds.items():
                for selection_id, odds_series in runners.items():
                    if not odds_series:
                        continue
                    cursor.execute("""
                        INSERT INTO inplay_odds_log (
                            market_id, selection_id, date, start_time, end_time, odds_series
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        market_id,
                        selection_id,
                        today,
                        now,
                        now,
                        json.dumps(odds_series)
                    ))
            conn.commit()
        print("✅ Flushed in-play odds to DB.")
    except Exception as e:
        print(f"❌ Failed to flush in-play odds: {e}")
