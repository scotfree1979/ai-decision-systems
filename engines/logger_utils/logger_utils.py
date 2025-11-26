import hashlib
import sqlite3
from datetime import datetime

# 📌 Customer Order Reference

def generate_customer_order_ref(marketId, selectionId, date=None):
    if not date:
        date = datetime.utcnow().date().isoformat()
    unique_str = f"{marketId}_{selectionId}_{date}"
    return hashlib.md5(unique_str.encode()).hexdigest()[:32]

# 📌 Odds & Tick Logic

def calculate_tick(odds):
    if odds < 2.0:
        return 0.01
    elif odds < 3.0:
        return 0.02
    elif odds < 4.0:
        return 0.05
    elif odds < 6.0:
        return 0.1
    elif odds < 10.0:
        return 0.2
    elif odds < 20.0:
        return 0.5
    elif odds < 30.0:
        return 1.0
    elif odds < 50.0:
        return 2.0
    elif odds < 100.0:
        return 5.0
    else:
        return 10.0

def round_to_valid_odds(odds):
    tick = calculate_tick(odds)
    rounded_odds = round(round(odds / tick) * tick, 2)
    return float(f"{rounded_odds:.2f}")

# 📌 Bot Budget Status

def get_bot_status_from_db(bot_name, db_path):
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT matched_liability, daily_pnl FROM budgets WHERE bot_name=?", (bot_name,))
        result = cursor.fetchone()
        return result if result else (0, 0)

# 📌 Meta Parsing for ICE/FIRE

def parse_meta_flags(signal):
    meta = signal.get("meta", {})
    return meta.get("ice", False), meta.get("fire", False)
