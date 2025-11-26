# 15_🔍_Signal_Diagnostics.py – Deep Dive on Signal Activity
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db"

st.set_page_config(page_title="Signal Diagnostics", layout="wide")
st.title("🔍 Signal Diagnostics")

@st.cache_data
def load_signals():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("""
            SELECT customerOrderRef, strategy_name, signal_type, bot_name, status, time_signal,
                   marketId, selectionId, odds, stake, placed_at, matched_timestamp,
                   pnl, anchor_odd, marketStartTime
            FROM bets
            WHERE signal_type IS NOT NULL
            ORDER BY placed_at DESC
        """, conn)
    return df

df = load_signals()

# Filter tools
st.sidebar.header("Filter Diagnostics")
selected_status = st.sidebar.multiselect("Select Status", options=df['status'].unique(), default=df['status'].unique())
selected_time = st.sidebar.multiselect("Select Time Signal", options=df['time_signal'].unique(), default=df['time_signal'].unique())
selected_bot = st.sidebar.multiselect("Bot", options=df['bot_name'].unique(), default=df['bot_name'].unique())

df_filtered = df[
    (df['status'].isin(selected_status)) &
    (df['time_signal'].isin(selected_time)) &
    (df['bot_name'].isin(selected_bot))
]

st.subheader("🔢 Signal Breakdown Table")
st.dataframe(df_filtered, use_container_width=True)

# Signal Type Frequency
st.subheader("📊 Signal Type Frequency")
signal_freq = df_filtered['signal_type'].value_counts().reset_index()
signal_freq.columns = ['Signal Type', 'Count']
st.bar_chart(signal_freq.set_index('Signal Type'))

# Strategy-Level Profitability
st.subheader("📊 Strategy-Level PnL")
if not df_filtered.empty:
    strategy_pnl = df_filtered.groupby('strategy_name')['pnl'].sum().reset_index()
    strategy_pnl = strategy_pnl.sort_values(by='pnl', ascending=False)
    st.dataframe(strategy_pnl, use_container_width=True)

# Most Recent Signals
st.subheader("🔍 Most Recent Signals")
recent_df = df_filtered[['customerOrderRef', 'strategy_name', 'signal_type', 'status', 'odds', 'stake', 'pnl', 'marketStartTime']].head(25)
st.dataframe(recent_df, use_container_width=True)

# Footer
st.markdown("---")
st.caption("🔍 This module tracks all signal traffic across bots and strategies in real-time.")
