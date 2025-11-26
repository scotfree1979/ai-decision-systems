# 6_📈_Market_Insights.py – Strategy Performance Intelligence
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime, timedelta

DB_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

st.set_page_config(page_title="Market Insights and Strategy Performance", layout="wide")
st.title("📈 Market Insights and Strategy Performance")

# ---------------------------- Load Data ----------------------------
@st.cache_data

def fetch_insights():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("SELECT * FROM bets WHERE strategy_name IS NOT NULL AND timestamp IS NOT NULL", conn)
        return df

# ---------------------------- Data Summary ----------------------------
df = fetch_insights()
if df.empty:
    st.warning("No signal data found.")
else:
    df['timestamp'] = pd.to_datetime(df['timestamp'], errors='coerce')
    df = df[df['timestamp'].notnull()]  # Drop rows with invalid timestamp
    df['date'] = df['timestamp'].dt.date
    df['pnl'] = pd.to_numeric(df['pnl'], errors='coerce').fillna(0)
    df['stake'] = pd.to_numeric(df['stake'], errors='coerce').fillna(0)
    df['odds'] = pd.to_numeric(df['odds'], errors='coerce').fillna(0)

    st.subheader("🏆 Strategy Leaderboard")
    leaderboard = df.groupby('strategy_name').agg(
        total_bets=('customerOrderRef', 'count'),
        total_stake=('stake', 'sum'),
        total_pnl=('pnl', 'sum'),
        avg_odds=('odds', 'mean'),
        wins=('status', lambda x: (x == 'won').sum())
    ).reset_index()
    leaderboard['strike_rate'] = (leaderboard['wins'] / leaderboard['total_bets']) * 100
    leaderboard = leaderboard.sort_values(by='total_pnl', ascending=False)

    st.dataframe(leaderboard[['strategy_name', 'total_bets', 'total_stake', 'total_pnl', 'strike_rate', 'avg_odds']], use_container_width=True)

    # Best and Worst Strategy
    best = leaderboard.iloc[0]
    worst = leaderboard.iloc[-1]

    st.success(f"✅ Best Performing Strategy: {best['strategy_name']} | £{best['total_pnl']:.2f} P&L")
    st.error(f"❌ Worst Performing Strategy: {worst['strategy_name']} | £{worst['total_pnl']:.2f} P&L")

    # Signal Heatmap
    st.subheader("🧭 Signal Heatmap by Time")
    heatmap_df = df.groupby(['strategy_name', 'time_signal']).size().unstack(fill_value=0)
    st.dataframe(heatmap_df, use_container_width=True)

    # Volatility Insights
    st.subheader("🌡️ Volatility Summary (Meta Tags)")
    df['meta_json'] = df['meta_json'].fillna('{}')
    df['meta'] = df['meta_json'].apply(lambda x: eval(x) if isinstance(x, str) else x)
    df['volatility'] = df['meta'].apply(lambda m: m.get('volatility', {}).get('volatility') if isinstance(m, dict) else None)
    vol_summary = df['volatility'].value_counts().rename_axis('volatility').reset_index(name='count')
    st.dataframe(vol_summary, use_container_width=True)

    # Meta Signal Tags (fire, ice, etc)
    st.subheader("🔥 Special Tags in Meta")
    tags = ['fire', 'ice', 'pace_collapse', 'fav_weakening']
    tag_data = []
    for tag in tags:
        tag_df = df[df['meta_json'].str.contains(f'"{tag}": true', na=False)]
        tag_data.append({
            'tag': tag,
            'signals': len(tag_df),
            'pnl': tag_df['pnl'].sum()
        })
    tag_df_final = pd.DataFrame(tag_data)
    st.dataframe(tag_df_final, use_container_width=True)

st.markdown("---")
st.caption("🧠 This page evaluates all strategies and signals for current trading intelligence. Auto-refresh every 60s.")
