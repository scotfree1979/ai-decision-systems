# 11_🔍_Risk_Liability_Management.py – Live Exposure, Overrisk Flags, Bot Balances
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime

DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db"

st.set_page_config(page_title="Risk & Liability", layout="wide")
st.title("🔍 Risk & Liability Management")

# Load live exposure data
@st.cache_data
def fetch_current_exposure():
    with sqlite3.connect(DB_PATH) as conn:
        query = """
        SELECT bot_name, strategy_name, marketId, selectionId, odds, stake, liability, status, timestamp
        FROM bets
        WHERE status IN ('approved', 'placed', 'matched')
        """
        return pd.read_sql_query(query, conn)

# Load current bankroll
@st.cache_data

def fetch_budget():
    try:
        from daily_config import AVAILABLE_BUDGET
        return AVAILABLE_BUDGET
    except Exception:
        return 800.0

exposure_df = fetch_current_exposure()
current_budget = fetch_budget()

if exposure_df.empty:
    st.info("No active positions or live exposure data found.")
else:
    # Format
    exposure_df['timestamp'] = pd.to_datetime(exposure_df['timestamp'])
    exposure_df = exposure_df.sort_values(by='timestamp', ascending=False)

    # Summary Metrics
    total_liability = exposure_df['liability'].sum()
    avg_liability = exposure_df.groupby('bot_name')['liability'].sum().reset_index()
    overrisk_bots = avg_liability[avg_liability['liability'] > (0.5 * current_budget)]

    col1, col2 = st.columns(2)
    col1.metric("📈 Total Open Liability", f"{total_liability:.2f}")
    col2.metric("🧡 Remaining Budget", f"{current_budget - total_liability:.2f}")

    st.subheader("🪖 Detailed Exposure Table")
    st.dataframe(exposure_df, use_container_width=True)

    st.subheader("⚠️ Bots Over 50% Exposure Threshold")
    if overrisk_bots.empty:
        st.success("All bots within safe exposure limits.")
    else:
        st.warning(f"{len(overrisk_bots)} bot(s) exceeding 50% budget threshold")
        st.dataframe(overrisk_bots, use_container_width=True)

    # Visual: Liability by Bot
    st.subheader("📊 Liability Distribution by Bot")
    chart_data = avg_liability.sort_values('liability', ascending=False)
    st.bar_chart(chart_data.set_index('bot_name'))

    st.markdown("---")
    st.caption("🔍 Monitors approved, placed, and matched bets for real-time risk visibility. Updated each refresh.")
