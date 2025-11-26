# dashboard_terminal.py (Detailed Version)
import time
import threading
import os
import sqlite3
import requests
import json
from datetime import datetime, timedelta

DATABASE_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'
BETFAIR_ENDPOINT = "https://api.betfair.com/exchange/betting/rest/v1.0/"
APP_KEY = "CZHojduNWa3kxWIn"
alerts = []  # global alerts queue

def clear_terminal():
    os.system('cls' if os.name == 'nt' else 'clear')

def fetch_total_liability():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(liability) FROM bets WHERE status='matched' AND result='pending'")
        result = cursor.fetchone()[0]
        return result if result else 0.0

def fetch_pl():
    with sqlite3.connect(DATABASE_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT SUM(stake * (odds - 1)) FROM bets WHERE status='matched' AND result='won'")
        result = cursor.fetchone()[0]
        return result if result else 0.0

def display_dashboard():
    clear_terminal()
    print("=" * 80)
    print(f"{'Real-Time Scalper Dashboard':^80}")
    print(f"Timestamp: {datetime.now().strftime('%Y-%m-%d %H:%M:%S'):^80}")
    print("=" * 80)

    liability = fetch_total_liability()
    bankroll = 696.87

    print("\n📌 BOT PERFORMANCE")
    print("-" * 80)
    print(f"| {'Bot Name':<20} | {'Trades':<7} | {'Matched':<8} | {'Unmatched':<9} | {'Current P/L (£)':<15}|")
    print("-" * 80)
    print(f"| {'OG - Head Trader':<20} | {'0':<7} | {'0':<8} | {'0':<9} | {fetch_pl():<15.2f}|")
    print("-" * 80)

    print("\n🐎 TOP 11 HORSES BY POTENTIAL PROFIT")
    print("-" * 80)
    print(f"| {'Race':<14} | {'Horse Name':<20} | {'Lay Odds':<9} | {'Back Odds':<10} | {'Profit (£)':<11} | {'Time':<4}|")
    print("-" * 80)

    exposure = liability
    print("\n⚠️ IMPACT OF PRESSING [C]: Immediate P/L = +£{:.2f} | Liability Reduction: £{:.2f}".format(fetch_pl(), liability))
    print(f"\n💰 Bankroll: £{bankroll:.2f} | Total Liability: £{liability:.2f} | Exposure: £{exposure:.2f}")
    print("=" * 80)

    print("\n📢 RECENT ALERTS")
    print("-" * 80)
    for alert in alerts[-5:]:
        print(f"- {alert}")
    print("-" * 80)

def dashboard_loop(refresh_interval=30):
    while True:
        display_dashboard()
        time.sleep(refresh_interval)

def command_interface():
    while True:
        cmd = input("\nEnter Command: ").strip().lower()
        if cmd == 'h':
            print("Hedging functionality activated.")
        elif cmd == 'c':
            print("Closing all positions.")
        elif cmd == 'q':
            print("Exiting dashboard...")
            os._exit(0)
        else:
            print("Invalid command. Please enter 'H', 'C', or 'Q'.")

def main():
    threading.Thread(target=dashboard_loop, daemon=True).start()
    command_interface()

if __name__ == '__main__':
    main()
