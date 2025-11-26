# 4_🕒_Recent_Trades.py – Cleaned and Functional
import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta

st.set_page_config(page_title="Recent Trades", layout="wide")
st.title("🕒 Recent Trades")

DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db"

# Set 5AM UTC as daily reset anchor
now = datetime.utcnow()
today_5am = now.replace(hour=5, minute=0, second=0, microsecond=0)
if now < today_5am:
    today_5am = today_5am - timedelta(days=1)

# Load today's trades from 5AM onwards
def load_historical_trades():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            query = f'''
                SELECT placed_at, market_name, event_name, strategy_name, stake, odds, status, pnl
                FROM bets
                WHERE placed_at >= "{today_5am.isoformat()}"
                ORDER BY placed_at DESC
                LIMIT 50
            '''
            return pd.read_sql(query, conn)
    except Exception as e:
        st.error(f"❌ Failed to load recent trades: {e}")
        return pd.DataFrame()

historical_trades_df = load_historical_trades()

if historical_trades_df.empty:
    st.info("No trades placed yet today.")
else:
    # Format for display
    historical_trades_df['placed_at'] = pd.to_datetime(historical_trades_df['placed_at'])
    historical_trades_df['placed_at'] = historical_trades_df['placed_at'].dt.strftime("%H:%M:%S")

    historical_trades_df = historical_trades_df.rename(columns={
        "placed_at": "Placed At",
        "market_name": "Market",
        "event_name": "Venue",
        "strategy_name": "Strategy",
        "stake": "Stake",
        "odds": "Odds",
        "status": "Status",
        "pnl": "P&L"
    })
    st.dataframe(historical_trades_df, use_container_width=True)
