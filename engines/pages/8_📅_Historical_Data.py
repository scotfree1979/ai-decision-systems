# 8_📅_Historical_Data.py – Daily + 30-Day P&L and Signal Breakdown
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta

DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db"

st.set_page_config(page_title="Historical Data", layout="wide")
st.title("📅 Historical Strategy Performance")

# Load bets from the last 30 days
@st.cache_data

def fetch_historical_bets():
    with sqlite3.connect(DB_PATH) as conn:
        query = """
        SELECT date, stake, pnl, status, bot_name, strategy_name, marketId
        FROM bets
        WHERE date >= date('now', '-30 day')
        """
        return pd.read_sql(query, conn)

# Calculate daily stats
@st.cache_data

def calculate_daily_stats(df):
    if df.empty:
        return pd.DataFrame()
    df['date'] = pd.to_datetime(df['date'])
    summary = df.groupby('date').agg(
        Races=('marketId', pd.Series.nunique),
        Wins=('status', lambda x: (x == 'won').sum()),
        Losses=('status', lambda x: (x == 'lost').sum()),
        Strike_Rate=('status', lambda x: round((x == 'won').sum() / max((x == 'won').sum() + (x == 'lost').sum(), 1) * 100, 2)),
        PnL=('pnl', 'sum')
    ).reset_index()
    summary['PnL'] = summary['PnL'].round(2)
    summary = summary.sort_values('date', ascending=False)
    return summary

# Load and process
bets_df = fetch_historical_bets()
daily_summary_df = calculate_daily_stats(bets_df)

# Toggle: Last X days
view_option = st.radio("View Range", options=["Last 1 Day", "Last 7 Days", "Last 30 Days"], horizontal=True)

if view_option == "Last 1 Day":
    view_df = daily_summary_df.head(1)
elif view_option == "Last 7 Days":
    view_df = daily_summary_df.head(7)
else:
    view_df = daily_summary_df

# Summary row
if not view_df.empty:
    total_races = int(view_df['Races'].sum())
    total_wins = int(view_df['Wins'].sum())
    strike_rate = round(total_wins / max(total_races, 1) * 100, 2)
    total_pnl = round(view_df['PnL'].sum(), 2)

    st.markdown("""
    ### 🔍 Summary
    """)
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Races", f"{total_races}")
    col2.metric("Total Wins", f"{total_wins}")
    col3.metric("Strike Rate", f"{strike_rate}%")
    col4.metric("Running P&L", f"£{total_pnl:.2f}")

# Charts
if not view_df.empty:
    st.markdown("""
    ### 📊 Daily Strike Rate
    """)
    st.line_chart(view_df.set_index('date')['Strike_Rate'])

    st.markdown("""
    ### 💹 Daily P&L
    """)
    st.bar_chart(view_df.set_index('date')['PnL'])

# Table
if not view_df.empty:
    st.markdown("""
    ### 📆 Daily Breakdown Table
    """)
    st.dataframe(view_df, use_container_width=True)
else:
    st.warning("No data available for this range.")

# Footer
st.markdown("---")
st.caption("🔄 Updates every day at 5am. Includes settled race stats only.")
