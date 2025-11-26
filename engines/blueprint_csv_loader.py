# blueprint_csv_loader.py

"""
🔍 CSV Loader for Blueprint Bet Matching
Links settled Betfair bets from exported CSV into blueprint signal pipeline.
Enhances trend confidence scoring using real P&L results.
"""

import csv
import sqlite3
from datetime import datetime
from config_paths import DB_PATH
from collections import defaultdict

def get_matched_bet_ids_for_runner(runner_name, settled_date):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT bet_id, profit_loss, status FROM blueprint_pnl_log
                WHERE horse_name = ? AND settled_date = ?
            """, (runner_name, settled_date))
            rows = cursor.fetchall()
            return [{
                "bet_id": row[0],
                "profit_loss": row[1],
                "status": row[2]
            } for row in rows]
    except Exception as e:
        print(f"❌ Error fetching matched bets for {runner_name} on {settled_date}: {e}")
        return []


# Format: 04-Jul-25 18:03:45
CSV_DATETIME_FORMAT = "%d-%b-%y %H:%M:%S"

# Load settled bets from CSV

def load_settled_bets(csv_path):
    matched_bets = defaultdict(list)
    
    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    settled_date = datetime.strptime(row["Settled"], CSV_DATETIME_FORMAT).date()
                    placed_time = datetime.strptime(row["Placed"], CSV_DATETIME_FORMAT).time()
                    runner_name = row["Description"].split("-")[0].split()[-1].strip()
                    profit_loss = float(row["Profit/Loss"].replace("\u00a3", "").strip())
                    
                    matched_bets[(settled_date, runner_name)].append({
                        "profit": profit_loss,
                        "type": row["Type"],
                        "odds": float(row["Odds"]),
                        "stake": float(row["Stake (£)"]),
                        "placed_time": placed_time,
                        "description": row["Description"],
                        "status": row["Status"],
                    })
                except Exception as e:
                    print(f"⚠️ CSV row error: {e} | Row: {row}")
    
    except Exception as e:
        print(f"❌ Failed to read CSV: {e}")

    return matched_bets


def commit_bets_to_db(matched_bets):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            for (date_key, runner_name), bets in matched_bets.items():
                for bet in bets:
                    cursor.execute("""
                        INSERT INTO blueprint_pnl_log (
                            bet_date, runner_name, profit, bet_type, odds, stake, placed_time, description, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        str(date_key),
                        runner_name,
                        bet["profit"],
                        bet["type"],
                        bet["odds"],
                        bet["stake"],
                        bet["placed_time"].strftime("%H:%M:%S"),
                        bet["description"],
                        bet["status"]
                    ))
            conn.commit()
        print(f"✅ Committed {sum(len(b) for b in matched_bets.values())} CSV bets to blueprint_pnl_log")

    except Exception as e:
        print(f"❌ Failed to commit bets to DB: {e}")


def load_and_commit_csv(csv_path):
    print(f"📥 Loading CSV: {csv_path}")
    matched_bets = load_settled_bets(csv_path)
    if matched_bets:
        commit_bets_to_db(matched_bets)
    else:
        print("⚠️ No bets matched from CSV")

import json
import os

def get_blueprint_meta(pattern_key):
    try:
        with open("blueprint_meta_summary.json", "r") as f:
            meta_summary = json.load(f)
            if pattern_key in meta_summary:
                meta = meta_summary[pattern_key]
                return meta.get("win_rate", 0), meta.get("avg_confidence", 0)
    except Exception as e:
        print(f"⚠️ Failed to load blueprint meta: {e}")
    return 0, 0

