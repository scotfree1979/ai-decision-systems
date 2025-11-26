# 2_🤖_Bot_Performance.py – Merged Bot Metrics & 30-Day Breakdown
import streamlit as st
import pandas as pd
import sqlite3
import plotly.express as px
import threading
from datetime import datetime
import sys
sys.path.append("/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/engines")
from daily_config import AVAILABLE_BUDGET, STAKE_MULTIPLIER

DB_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

st.set_page_config(page_title="Bot Performance", layout="wide")
st.title("🤖 Bot Performance Dashboard")

# Load recent bets
@st.cache_data
def fetch_bot_data():
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("""
            SELECT * FROM bets 
            WHERE placed_at >= date('now', '-30 day')
        """, conn)

df = fetch_bot_data()

# Bot Leaderboard
st.subheader("🏆 Bot Leaderboard")
if not df.empty:
    df['pnl'] = pd.to_numeric(df['pnl'], errors='coerce').fillna(0)
    df['stake'] = pd.to_numeric(df['stake'], errors='coerce').fillna(0)
    df['odds'] = pd.to_numeric(df['odds'], errors='coerce').fillna(0)

    leaderboard = df.groupby('bot_name').agg(
        total_bets=('customerOrderRef', 'count'),
        total_stake=('stake', 'sum'),
        total_pnl=('pnl', 'sum'),
        avg_odds=('odds', 'mean'),
        wins=('status', lambda x: (x == 'won').sum())
    ).reset_index()
    leaderboard['strike_rate'] = (leaderboard['wins'] / leaderboard['total_bets']) * 100
    leaderboard = leaderboard.sort_values(by='total_pnl', ascending=False)
    leaderboard.rename(columns={
        'bot_name': 'Bot',
        'total_bets': 'Total Bets',
        'total_stake': 'Total Stake (£)',
        'total_pnl': 'Total P&L (£)',
        'avg_odds': 'Avg Odds',
        'strike_rate': 'Strike Rate (%)'
    }, inplace=True)

    st.dataframe(leaderboard[['Bot', 'Total Bets', 'Total Stake (£)', 'Total P&L (£)', 'Strike Rate (%)', 'Avg Odds']], use_container_width=True)

# Total Profit by Bot
st.subheader("💰 Total Profit by Bot")
if 'bot_name' in df.columns and 'pnl' in df.columns:
    pnl_summary = df.groupby('bot_name')['pnl'].sum().reset_index().sort_values(by='pnl', ascending=False)
    pnl_summary.rename(columns={'bot_name': 'Bot', 'pnl': 'Total P&L (£)'}, inplace=True)
    fig = px.bar(pnl_summary, x='Bot', y='Total P&L (£)', color='Total P&L (£)', title='Total P&L by Bot')
    st.plotly_chart(fig, use_container_width=True)

# Strike Rate by Bot
st.subheader("🎯 Strike Rates by Bot")
if 'bot_name' in df.columns and 'status' in df.columns:
    wins = df[df['status'] == 'won'].groupby('bot_name').size().reset_index(name='Wins')
    total = df.groupby('bot_name').size().reset_index(name='Total')
    strike = pd.merge(total, wins, on='bot_name', how='left').fillna(0)
    strike['Strike Rate (%)'] = (strike['Wins'] / strike['Total']) * 100
    strike.rename(columns={'bot_name': 'Bot'}, inplace=True)
    fig_strike = px.bar(strike, x='Bot', y='Strike Rate (%)', color='Strike Rate (%)',
                        title='Strike Rate (%) by Bot', color_continuous_scale='Viridis')
    st.plotly_chart(fig_strike, use_container_width=True)

# Trades Executed
st.subheader("📊 Total Trades Executed")
trade_counts = df.groupby('bot_name').size().reset_index(name='Trades')
trade_counts.rename(columns={'bot_name': 'Bot'}, inplace=True)
fig_trades = px.bar(trade_counts, x='Bot', y='Trades', title='Total Trades by Bot', color='Trades')
st.plotly_chart(fig_trades, use_container_width=True)

# Live Config View
st.markdown("---")
st.markdown("### 🧾 Live System Settings from daily_config")
st.code(f"Bankroll: £{AVAILABLE_BUDGET} | Stake Multiplier: x{STAKE_MULTIPLIER}", language="text")
