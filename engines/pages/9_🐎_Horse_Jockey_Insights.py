# 9_🐎_Horse_Jockey_Insights.py – Momentum Signals by Horse, Jockey, Trainer
import streamlit as st
import pandas as pd
import sqlite3
import json
from datetime import datetime, timedelta

DB_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db"

st.set_page_config(page_title="Horse & Jockey Insights", layout="wide")
st.title("🐎 Horse & Jockey Momentum Dashboard")

# Load relevant runner data with meta_json
@st.cache_data
def load_bets():
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("""
            SELECT date, horse_name, strategy_name, bot_name, pnl, meta_json
            FROM bets
            WHERE date >= date('now', '-60 day')
        """, conn)
        df['meta'] = df['meta_json'].apply(lambda x: json.loads(x) if x else {})
        return df

bets = load_bets()
bets['date'] = pd.to_datetime(bets['date'])
bets['won'] = bets['pnl'] > 0

# Extract jockey, trainer, form
bets['jockey'] = bets['meta'].apply(lambda m: m.get('jockey', 'Unknown'))
bets['trainer'] = bets['meta'].apply(lambda m: m.get('trainer', 'Unknown'))
bets['form'] = bets['meta'].apply(lambda m: m.get('form', ''))

latest_date = bets['date'].max()
today_bets = bets[bets['date'] == latest_date]
past_bets = bets[bets['date'] < latest_date]

# 🐎 Horses in today's races with momentum form (last 1-4 wins)
def check_winning_streak(form):
    return sum([1 for ch in form[:4] if ch == '1'])

form_scores = past_bets[past_bets['won']].groupby('horse_name')['form'].last().dropna().apply(check_winning_streak)
today_horses = today_bets[['horse_name']].drop_duplicates()
today_horses = today_horses.merge(form_scores.rename('recent_wins'), on='horse_name', how='left').fillna(0)
today_horses = today_horses[today_horses['recent_wins'] >= 1].sort_values('recent_wins', ascending=False)

# 🧑‍✈️ Jockeys with winning history
jockey_winners = past_bets[past_bets['won']].groupby('jockey').size().reset_index(name='wins')
today_jockeys = today_bets[['jockey']].drop_duplicates()
today_jockeys = today_jockeys.merge(jockey_winners, on='jockey', how='left').fillna(0)
today_jockeys = today_jockeys[today_jockeys['wins'] > 1].sort_values('wins', ascending=False)

# 🏠 Trainers with winning history
trainer_winners = past_bets[past_bets['won']].groupby('trainer').size().reset_index(name='wins')
today_trainers = today_bets[['trainer']].drop_duplicates()
today_trainers = today_trainers.merge(trainer_winners, on='trainer', how='left').fillna(0)
today_trainers = today_trainers[today_trainers['wins'] > 2].sort_values('wins', ascending=False)

st.subheader("🐎 Horse Form Momentum (Last 1-4 Races)")
st.dataframe(today_horses, use_container_width=True)

st.subheader("🧑‍✈️ Jockey Hot List")
st.dataframe(today_jockeys, use_container_width=True)

st.subheader("🏠 Trainer Hot List")
st.dataframe(today_trainers, use_container_width=True)

st.markdown("---")
st.caption("⚠️ Based on meta.json form data and win signals over past 60 days. Updated each session.")
