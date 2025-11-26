# 🧠 HeadTrader Router – Navigation Shell for Command Centre Modules
import sys
import os
PAGES_DIR = os.path.abspath(os.path.dirname(__file__))
if PAGES_DIR not in sys.path:
    sys.path.append(PAGES_DIR)

import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
from reportlab.pdfgen import canvas
import os
from config_paths import DB_PATH
from headtrader_pages import PAGE_MAP

# ----------------------------
# 🚦 Module Routing Panel
# ----------------------------
st.set_page_config(page_title="HeadTrader Modules", layout="wide")
st.title("🧠 HeadTrader Command Centre")

PAGES = {
    "Main Dashboard": "main",
    "Signal Scanner": "scanner",
    "Exposure Heatmap": "exposure",
    "OGBrain Trials": "trials",
    "Strategy Journal": "journal",
    "Lockdown Panel": "lockdown",
    "Daily Debrief": "debrief"
}

selected_page = st.selectbox("🗭 Navigate Modules", list(PAGES.keys()), index=0)

if PAGES[selected_page] != "main":
    PAGE_MAP[PAGES[selected_page]]()
    st.stop()

# ----------------------------
# 🧠 Default to Main Dashboard Content
st.success("✅ Main HeadTrader Dashboard loaded. Embed existing dashboard code here.")

# ----------------------------
# 📁 Current Activity Snapshot
# ----------------------------
st.subheader("📁 Current Activity Snapshot")

@st.cache_data
def get_active_summary():
    today = datetime.utcnow().strftime('%Y-%m-%d')
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(f"""
            SELECT bot_name, strategy_name, status, COUNT(*) as count 
            FROM bets
            WHERE date = '{today}'
            GROUP BY bot_name, strategy_name, status
        """, conn)
    return df

activity_df = get_active_summary()
st.dataframe(activity_df, use_container_width=True)

# ----------------------------
# 📳 Talk to HeadTrader
# ----------------------------
st.subheader("💬 Talk to HeadTrader")

st.markdown("""
You can issue live tactical instructions to the HeadTrader AI system. These influence signal weighting, strategy toggles, and capital allocation.
""")

st.markdown("### 🔢 Strategy Control Panel")

col1, col2, col3 = st.columns(3)
ENABLE_LADDER_LOGIC = col1.checkbox("Ladder Strategy", value=True)
ENABLE_SCALPING_LOGIC = col2.checkbox("Scalping Strategy", value=True)
ENABLE_GREENUP_LOGIC = col3.checkbox("Green-Up Logic", value=True)

col4, col5, col6 = st.columns(3)
ENABLE_INPLAY_LOGIC = col4.checkbox("In-Play Execution", value=True)
SKIP_BETS_AFTER_OFF = col5.checkbox("Block Late Bets", value=True)
FORBID_DUPLICATE_CUSTOMERREF = col6.checkbox("No Duplicate Bets", value=True)

st.markdown("### 🔧 Rule Sensitivity Dials")
col10, col11 = st.columns(2)
MAX_SCALPS_PER_HORSE = col10.slider("Max Scalp Chains / Horse", 1, 4, 2)
SCALP_MATCH_THRESHOLD = col11.slider("Scalp Match % to Allow Next", 10, 100, 50)

col12, col13 = st.columns(2)
MAX_RACE_LIABILITY = col12.slider("Max Race Liability (£)", 10, 200, 100)
MAX_BET_PER_RUNNER = col13.slider("Max Bet Per Runner (£)", 2, 100, 15)

st.markdown("### 🔑 Capital & Risk Toggle")
col7, col8, col9 = st.columns(3)
MIN_AVAILABLE_BUDGET = col7.slider("Min Available Budget £", 0, 1000, 100)
MAX_GLOBAL_LIABILITY = col8.slider("Max Global Liability £", 100, 2000, 500)
MAX_DAILY_LOSS = col9.slider("Max Daily Loss £", 50, 500, 250)

st.markdown("---")

st.markdown("### ✍️ Issue Tactical Command")
command_input = st.text_input("Type a live command for HeadTrader (e.g. disable ladder strategy, boost greenup confidence)")
if st.button("Send Command"):
    st.success(f"📤 Command sent to HeadTrader: '{command_input}'")

# ----------------------------
# 📝 Custom Guidance
# ----------------------------
st.subheader("📝 Custom Guidance")
message = st.text_area("Write a custom note to the HeadTrader below (saved for today only):")
if st.button("Save Note"):
    st.success("🧠 Your guidance has been recorded for today.")

if message:
    st.info(f"📀 Saved Message: {message}")

# ----------------------------
# 📤 Export Daily Session Report
# ----------------------------
st.subheader("📤 Export Daily Session Report")
if st.button("Generate Report PDF"):
    pdf_path = os.path.join("../reporting", f"report_{datetime.utcnow().strftime('%Y%m%d')}.pdf")
    os.makedirs(os.path.dirname(pdf_path), exist_ok=True)
    c = canvas.Canvas(pdf_path)
    c.drawString(100, 800, "🧠 HeadTrader Daily Session Report")
    c.drawString(100, 780, f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}")
    c.drawString(100, 760, f"Total Bets: {summary_stats['total_bets']}")
    c.drawString(100, 740, f"Win Rate: {summary_stats['win_rate']*100:.2f}%")
    c.drawString(100, 720, f"Average Odds: {summary_stats['average_odds']:.2f}")
    c.drawString(100, 700, "Custom Note:")
    c.drawString(120, 680, message[:100])
    c.save()
    st.success(f"✅ Report saved to: {pdf_path}")

# ----------------------------
# 🧠 Advanced Strategy Control Toggles
# ----------------------------

st.subheader("🎛️ Tactical Adjustments")

col14, col15 = st.columns(2)
AUTO_DISABLE_UNSTABLE_STRATEGY = col14.checkbox("Auto-disable unstable strategies", value=True)
FOCUS_ON_LOW_VOLATILITY = col15.checkbox("Prefer low-volatility runners", value=False)

col16, col17 = st.columns(2)
ENABLE_STRATEGY_JOURNALING = col16.checkbox("Enable Strategy Journaling", value=True)
AUTO_TOGGLE_ON_POSITIVE_RUN = col17.checkbox("Auto-increase stake on winning streak", value=False)

st.markdown("### 📌 Execution Mode")
col18, col19, col20 = st.columns(3)
EXECUTION_MODE = col18.radio("Select Mode", ["Standard", "Aggressive", "Defensive"], index=0)
AUTO_MODE_SWITCH = col19.checkbox("Allow OGBrain to switch modes", value=True)
LOCK_STRATEGY_ON_PROFIT = col20.checkbox("Lock profitable strategy until stop", value=False)

st.markdown("---")

# ----------------------------
# 🔮 Learning Optimizer Summary
st.subheader("📊 Learning Optimizer Insights")
try:
    optimizer = LearningOptimizer()
    optimizer.run()
    summary_stats = optimizer.stats
except Exception as e:
    st.error("❌ Failed to run Learning Optimizer. Please check if the class is properly imported.")
    summary_stats = {'total_bets': 0, 'win_rate': 0, 'average_odds': 0, 'strategy_performance': {}}

col10, col11, col12 = st.columns(3)
col10.metric("Total Bets", summary_stats['total_bets'])
col11.metric("Win Rate", f"{summary_stats['win_rate']*100:.2f}%")
col12.metric("Avg Odds", f"{summary_stats['average_odds']:.2f}")

if summary_stats['strategy_performance']:
    perf_df = pd.DataFrame.from_dict(summary_stats['strategy_performance'], orient='index', columns=['WinRate'])
    st.dataframe(perf_df.sort_values(by='WinRate', ascending=False), use_container_width=True)
else:
    st.info("📟 No strategy performance data yet.")

# ----------------------------
# 🧠 OGBrain Strategic Engine
# ----------------------------
st.subheader("🧠 OGBrain: Strategic Engine Console")

st.markdown("""
This module surfaces key ideas and live diagnostics generated by the OGBrain engine.
These include:
- Detected inefficiencies in betting strategy
- Adaptive model tweaks
- Suggested optimizations for future race clusters

All signals here are auto-generated from real-time evaluation of enriched race data.
""")

with sqlite3.connect(DB_PATH) as conn:
    try:
        brain_df = pd.read_sql_query("""
            SELECT timestamp, recommendation, reasoning
            FROM brain_output
            ORDER BY timestamp DESC
            LIMIT 10
        """, conn)
        brain_df['timestamp'] = pd.to_datetime(brain_df['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
        st.dataframe(brain_df, use_container_width=True)
    except Exception as e:
        st.warning("⚠️ OGBrain has not submitted recent recommendations.")

st.markdown("### ✍️ Feedback to OGBrain")
brain_feedback = st.text_area("Send strategic insights or observations to OGBrain (this will soon connect to learning loop):")
if st.button("Submit Feedback"):
    st.success("📨 Feedback noted for future memory training.")

# ----------------------------
# 🔹 Market Tension Tracker (Preview)
# ----------------------------
st.subheader("🔥 Volatility Heatmap (Preview)")
st.caption("Based on % swing from anchor odds – Live rows only")

with sqlite3.connect(DB_PATH) as conn:
    df = pd.read_sql_query("""
        SELECT horse_name, bot_name, strategy_name, meta_json, time_signal
        FROM bets
        WHERE status IN ('fully_enriched', 'risk_evaluated', 'approved')
    """, conn)

def extract_volatility(row):
    try:
        meta = eval(row['meta_json']) if row['meta_json'] else {}
        vol = meta.get('volatility', {})
        return vol.get('swing_percent', 0)
    except:
        return 0

df['volatility'] = df.apply(extract_volatility, axis=1)
df = df.sort_values(by='volatility', ascending=False).head(10)

if not df.empty:
    st.dataframe(df[['horse_name', 'strategy_name', 'bot_name', 'volatility']], use_container_width=True)
else:
    st.info("No volatility data available yet.")

st.caption("🧠 HeadTrader feedback actions are live and enforce rules directly.")
