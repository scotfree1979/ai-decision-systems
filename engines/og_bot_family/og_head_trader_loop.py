import sqlite3
import pandas as pd
from datetime import datetime

# Database connection
DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/engines/bets.db"
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Fetch recent bets data
trades = pd.read_sql("SELECT horse, odds, result FROM bets ORDER BY timestamp DESC", conn)
print(trades.head())

# Close connection
conn.close()
