# 7_⚙️_Strategy_Analytics.py – Strategy Performance Trends & Drawdowns
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
from datetime import datetime

DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db"

st.set_page_config(page_title="Strategy Analytics", layout="wide")
st.title("⚙️ Strategy Analytics & Trends")

# Load recent strategy data
@st.cache_data

def load_strategy_data():
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("""
            SELECT strategy_name, pnl, stake, odds, placed_at, status
            FROM bets
            WHERE placed_at IS NOT NULL AND strategy_name IS NOT NULL
        """, conn)

df = load_strategy_data()

if not df.empty:
    df['pnl'] = pd.to_numeric(df['pnl'], errors='coerce').fillna(0)
    df['stake'] = pd.to_numeric(df['stake'], errors='coerce').fillna(0)
    df['odds'] = pd.to_numeric(df['odds'], errors='coerce').fillna(0)
    df['placed_at'] = pd.to_datetime(df['placed_at'], errors='coerce')
    df.dropna(subset=['placed_at'], inplace=True)
    df['date'] = df['placed_at'].dt.date

    st.subheader("🌟 Strategy Leaderboard")
    leaderboard = df.groupby('strategy_name').agg(
        total_bets=('strategy_name', 'count'),
        total_pnl=('pnl', 'sum'),
        avg_odds=('odds', 'mean'),
        total_stake=('stake', 'sum')
    ).reset_index().sort_values(by='total_pnl', ascending=False)
    leaderboard.rename(columns={
        'strategy_name': 'Strategy',
        'total_bets': 'Total Bets',
        'total_pnl': 'Total P&L (£)',
        'avg_odds': 'Avg Odds',
        'total_stake': 'Total Stake (£)'
    }, inplace=True)

    st.dataframe(leaderboard, use_container_width=True)

    st.subheader("🔢 Strategy Drawdown Tracker")
    pnl_by_day = df.groupby(['date', 'strategy_name'])['pnl'].sum().reset_index()
    for strat in pnl_by_day['strategy_name'].unique():
        subset = pnl_by_day[pnl_by_day['strategy_name'] == strat].copy()
        subset['cumulative'] = subset['pnl'].cumsum()
        subset['drawdown'] = subset['cumulative'].cummax() - subset['cumulative']
        max_drawdown = subset['drawdown'].max()
        st.markdown(f"**{strat}** – Max Drawdown: £{max_drawdown:.2f}")
        fig_dd = px.line(subset, x='date', y='drawdown', title=f'Drawdown Trend: {strat}')
        st.plotly_chart(fig_dd, use_container_width=True)
else:
    st.info("No strategy-level data available yet. Try again once bets are placed.")
