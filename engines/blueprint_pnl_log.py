# ✅ Step: Create blueprint_pnl_log table + match logic

import sqlite3
from datetime import datetime
import csv
import os

from config_paths import DB_PATH

PNL_TABLE = """
CREATE TABLE IF NOT EXISTS blueprint_pnl_log (
    bet_id TEXT PRIMARY KEY,
    horse_name TEXT,
    settled_date TEXT,
    odds_type TEXT,
    odds REAL,
    stake REAL,
    liability REAL,
    profit_loss REAL,
    status TEXT
);
"""


def create_pnl_table():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(PNL_TABLE)
        conn.commit()
        print("✅ Created/verified blueprint_pnl_log table.")


def insert_pnl_record(row):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO blueprint_pnl_log (
                    bet_id, horse_name, settled_date, odds_type, odds,
                    stake, liability, profit_loss, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                row["bet_id"],
                row["horse_name"],
                row["settled_date"],
                row["type"],
                float(row["odds"]) if row["odds"] not in [None, "", " -- "] else 0.0,
                float(row["stake"]) if row["stake"] not in [None, "", " -- "] else 0.0,
                float(row["liability"]) if row["liability"] not in [None, "", " -- "] else 0.0,
                float(row["profit_loss"]) if row["profit_loss"] not in [None, "", " -- "] else 0.0,
                row["status"]
            ))
            conn.commit()
    except Exception as e:
        print(f"❌ Failed to insert PnL row: {e}")



# ✅ Main Loader

def load_pnl_csv(filepath):
    if not os.path.exists(filepath):
        print(f"❌ CSV not found: {filepath}")
        return

    with open(filepath, newline='') as csvfile:
        reader = csv.DictReader(csvfile)
        for row in reader:
            try:
                desc = row.get("Description", "")
                name = desc.split("-Win")[0].split()[-1] if "-Win" in desc else desc.split()[-1]
                bet_id = desc.split("Betfair Bet ID")[-1].strip().replace(":", "") if "Betfair Bet ID" in desc else "?"

                data = {
                    "bet_id": bet_id,
                    "horse_name": name,
                    "settled_date": datetime.strptime(row["Settled"], "%d-%b-%y %H:%M:%S").date().isoformat(),
                    "type": row["Type"],
                    "odds": row["Odds"],
                    "stake": row["Stake (£)"],
                    "liability": row["Liability (£)"],
                    "profit_loss": row["Profit/Loss"],
                    "status": row["Status"]
                }
                insert_pnl_record(data)
            except Exception as e:
                print(f"⚠️ Skipped row due to error: {e}")


if __name__ == "__main__":
    create_pnl_table()
    load_pnl_csv("ExchangeBets_Settled (4).csv")
