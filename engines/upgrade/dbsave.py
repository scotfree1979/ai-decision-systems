# utils/db_utils.py

import sqlite3
from datetime import datetime
import logging

def save_bet_to_db(market_id, selection_id, odds, stake, side, ref, bet_id):
    try:
        with sqlite3.connect("scalper.db") as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO bets (marketId, selectionId, odds, stake, side, customerOrderRef, betfair_bet_id, placed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                market_id, selection_id, odds, stake, side, ref, bet_id, datetime.utcnow().isoformat()
            ))
            conn.commit()
    except Exception as e:
        logging.warning(f"⚠️ Failed to save bet to DB: {e}")
