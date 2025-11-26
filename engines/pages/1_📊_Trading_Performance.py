# 1_📊_Trading_Performance.py – 30-Day Strategic Drilldown
import sqlite3
import streamlit as st
from datetime import datetime, timedelta
import pandas as pd

DB_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

st.title("📊 30-Day Trading Performance")

# Pull 30 days of bets
def fetch_30_day_data():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("""
            SELECT * FROM bets 
            WHERE placed_at >= date('now', '-30 day')
              AND status IN ('matched', 'settled')
              AND pnl IS NOT NULL
        """, conn)
    df['placed_at'] = pd.to_datetime(df['placed_at'])
    return df

df = fetch_30_day_data()

# Daily P&L Trend
st.subheader("📅 Daily Profit & Loss")
if not df.empty:
    df['TradeDate'] = df['placed_at'].dt.date
    daily_pnl = df.groupby('TradeDate')['pnl'].sum()
    st.line_chart(daily_pnl)
else:
    st.warning("No matched or settled data found in the last 30 days.")

# Strategy Breakdown
st.subheader("🧠 Strategy Performance Summary")
if 'strategy_name' in df.columns:
    strat_df = df.groupby('strategy_name').agg(
        Bets=('stake', 'count'),
        WinRate=('status', lambda x: (x == 'won').mean() * 100),
        AvgStake=('stake', 'mean'),
        ROI=('pnl', lambda x: (x.sum() / df[df['strategy_name'] == x.name]['stake'].sum()) * 100),
        Profit=('pnl', 'sum')
    ).sort_values(by='Profit', ascending=False)
    st.dataframe(strat_df)
else:
    st.info("No 'strategy_name' field found in bets table.")

# Top and Bottom Horses
st.subheader("🏇 Top and Bottom Performing Horses")
if 'horse_name' in df.columns:
    runner_pnl = df.groupby('horse_name')['pnl'].sum().sort_values(ascending=False)
    col1, col2 = st.columns(2)
    col1.write("### 🟢 Top 10 Runners")
    col1.dataframe(runner_pnl.head(10))
    col2.write("### 🔴 Bottom 10 Runners")
    col2.dataframe(runner_pnl.tail(10))
else:
    st.info("No 'horse_name' field found in bets table.")

# Worst Drawdown Days
st.subheader("📉 Worst Drawdown Days")
daily_drawdown = df.groupby(df['placed_at'].dt.date)['pnl'].sum().sort_values()
st.table(daily_drawdown.head(5))
