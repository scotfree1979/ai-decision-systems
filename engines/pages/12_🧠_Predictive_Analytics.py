# 12_🧠_Predictive_Analytics.py – Advanced Forecasts and Risk Indicators
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import plotly.express as px

DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db"

st.set_page_config(page_title="Predictive Analytics", layout="wide")
st.title("🧠 Predictive Analytics")

@st.cache_data
def load_bet_data():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("""
            SELECT strategy_name, bot_name, marketStartTime, pnl, stake, time_signal, meta_json
            FROM bets
            WHERE marketStartTime IS NOT NULL
        """, conn)
    df['marketStartTime'] = pd.to_datetime(df['marketStartTime'])
    df['date'] = df['marketStartTime'].dt.date
    return df

df = load_bet_data()

# Section: Signal Volume Forecast
st.subheader("🌀 Signal Forecast")
signal_counts = df.groupby(['date', 'time_signal']).size().reset_index(name='count')
signal_pivot = signal_counts.pivot(index='date', columns='time_signal', values='count').fillna(0)
st.line_chart(signal_pivot)

# Section: Strategy Signal Patterns
st.subheader("🔄 Strategy Activity Trends")
strategy_trend = df.groupby(['date', 'strategy_name']).size().reset_index(name='signals')
fig = px.line(strategy_trend, x='date', y='signals', color='strategy_name', title='Signal Frequency by Strategy')
st.plotly_chart(fig, use_container_width=True)

# Section: Risk Window Toggle
st.subheader("📊 Focus Risk Window")
risk_window = st.slider("Select risk window in minutes to off", 0, 100, (0, 20))
risk_df = df[df['time_signal'].isin([f'time_{risk_window[0]}', f'time_{risk_window[1]}'])]
risk_summary = risk_df.groupby('strategy_name').agg(
    signals=('strategy_name', 'count'),
    total_stake=('stake', 'sum'),
    avg_pnl=('pnl', 'mean')
).reset_index()
st.dataframe(risk_summary, use_container_width=True)

# Section: Volatility Insight via meta_json
st.subheader("🔫 Volatility Indicators")
def extract_volatility(meta):
    import json
    try:
        data = json.loads(meta) if meta else {}
        return data.get("volatility", {}).get("volatility")
    except:
        return None

df['vol_level'] = df['meta_json'].apply(extract_volatility)
vol_summary = df.groupby(['strategy_name', 'vol_level']).size().unstack(fill_value=0)
st.bar_chart(vol_summary)

st.markdown("---")
st.caption("🔮 This module evolves as new bets and signals are created. Forecasts adapt with each run.")
