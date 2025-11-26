# 5_🚨_Critical_Alerts.py – System Health & Error Monitor
import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
import threading

DB_PATH = '/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/data/bets.db'

st.set_page_config(page_title="Critical Alerts", layout="wide")
st.title("🚨 Critical System Alerts")

# ✅ Load recent system errors
def load_recent_errors():
    now = datetime.utcnow()
    cutoff = now - timedelta(minutes=30)
    with sqlite3.connect(DB_PATH) as conn:
        query = f'''
        SELECT timestamp, marketId, customerOrderRef, signal_type, bot_name, status
        FROM bets
        WHERE status IN ('rejected', 'error', 'timeout', 'skipped')
        AND datetime(timestamp) > "{cutoff.isoformat()}"
        ORDER BY timestamp DESC
        '''
        return pd.read_sql(query, conn)

# ✅ Evaluate active threads by label
EXPECTED_THREADS = [
    "DBWriter",
    "MetaInjector",
    "RunMonitor",
    "SignalMonitor",
    "RiskManager",
    "BudgetManager",
    "OGBrain",
    "OG Head Trader",
    "OG Pre-off",
    "OG In-Play"
]

def evaluate_threads():
    thread_name_map = {
        "Thread-1": "DBWriter",
        "Thread-2": "MetaInjector",
        "Thread-3": "RunMonitor",
        "Thread-4": "SignalMonitor",
        "Thread-5": "RiskManager",
        "Thread-6": "BudgetManager",
        "Thread-7": "OGBrain",
        "Thread-8": "OG Head Trader",
        "Thread-9": "OG Pre-off",
        "Thread-10": "OG In-Play"
    }
    raw_threads = [t.name for t in threading.enumerate() if not t.name.startswith("QUpgradeBot")]
    translated = [thread_name_map.get(t, t) for t in raw_threads]
    missing = [t for t in EXPECTED_THREADS if t not in translated]
    return EXPECTED_THREADS, translated, missing

# ---------------------------- Display ----------------------------

st.markdown("""
### 🏁 Recent Errors
""")
errors = load_recent_errors()
if errors.empty:
    st.success("✅ No critical errors found in the last 30 minutes.")
else:
    errors['timestamp'] = pd.to_datetime(errors['timestamp']).dt.strftime('%Y-%m-%d %H:%M:%S')
    st.dataframe(errors, use_container_width=True)

# ---------------------------- Threads ----------------------------

st.markdown("""
### ⚙️ Thread Health
""")
expected, active, missing = evaluate_threads()
col1, col2 = st.columns(2)
col1.metric("Expected Threads", len(expected))
col2.metric("Active Threads", len(active))

if missing:
    st.error(f"❌ Missing threads: {', '.join(missing)}")
else:
    st.success("✅ All critical system threads are online.")

with st.expander("🔍 Show all active thread names"):
    st.write(sorted(active))

# Footer
st.markdown('---')
st.caption('🔄 Dashboard refreshes every 60 seconds. Monitor for system liveness and thread health.')
