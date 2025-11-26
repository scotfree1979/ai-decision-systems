# 3_\ud83d\udcc9_Bankroll_Summary.py – Merged Live + Historical Bankroll Tracking
import streamlit as st
import sqlite3
import pandas as pd
import plotly.express as px
import sys

sys.path.append("/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/engines")
from daily_config import fetch_available_budget, STAKE_MULTIPLIER

DB_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

st.set_page_config(page_title="Bankroll Summary", layout="wide")
st.title("📉 Bankroll Summary")

# Runtime Profile (Live Snapshot)
head_trader_profile = {
    'bankroll': fetch_available_budget(),
    'stake_multiplier': STAKE_MULTIPLIER,
    'current_exposure': 0.0  # Placeholder until live exposure system is active
}

col1, col2, col3 = st.columns(3)
col1.metric("💰 Current Bankroll (£)", f"£{head_trader_profile['bankroll']:.2f}")
col2.metric("📉 Current Exposure (£)", f"£{head_trader_profile['current_exposure']:.2f}")
col3.metric("🕐️ Stake Multiplier", f"x{head_trader_profile['stake_multiplier']:.2f}")

# Strategy Exposure Breakdown
st.subheader("📈 Bankroll Over Time")
with sqlite3.connect(DB_PATH) as conn:
    hist_df = pd.read_sql_query("SELECT placed_at AS timestamp, pnl FROM bets ORDER BY placed_at ASC", conn)

if not hist_df.empty:
    hist_df['timestamp'] = pd.to_datetime(hist_df['timestamp'])
    hist_df['cumulative_bankroll'] = hist_df['pnl'].cumsum()

    fig_bankroll = px.line(hist_df, x='timestamp', y='cumulative_bankroll', title='Bankroll Progression', markers=True)
    st.plotly_chart(fig_bankroll, use_container_width=True)

    st.subheader("🗖 Daily Bankroll Changes")
    hist_df['date'] = hist_df['timestamp'].dt.date
    daily_changes = hist_df.groupby('date')['pnl'].sum().reset_index()
    fig_daily = px.bar(daily_changes, x='date', y='pnl', title='Daily Bankroll Changes',
                       labels={'pnl': 'Daily P/L (£)'}, color='pnl', color_continuous_scale='RdYlGn')
    st.plotly_chart(fig_daily, use_container_width=True)

    st.subheader("🕒 Recent Bankroll Activity")
    recent = hist_df.tail(10).copy()
    recent['timestamp'] = recent['timestamp'].dt.strftime('%Y-%m-%d %H:%M:%S')
    st.dataframe(recent[['timestamp', 'pnl', 'cumulative_bankroll']], use_container_width=True)
else:
    st.info("No historical bankroll data found.")

# Footer
st.markdown('---')
st.caption('🔄 Dashboard refreshes every 30 seconds. Data sourced from historical database and runtime profile.')
