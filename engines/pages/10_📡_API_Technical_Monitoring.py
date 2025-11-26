# 10_📡_API_Technical_Monitoring.py – API Load, Batching, Hijack Tracking
import streamlit as st
import pandas as pd
import sqlite3
from datetime import datetime
import os

LOG_PATH = "/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/logs/api_hijack_clean.log"

st.set_page_config(page_title="API Technical Monitoring", layout="wide")
st.title("📡 API Technical Monitoring")

# Load logs from file
@st.cache_data
def fetch_api_logs():
    if not os.path.exists(LOG_PATH):
        return pd.DataFrame(columns=['timestamp', 'endpoint', 'status', 'duration', 'session_id'])

    with open(LOG_PATH, "r") as f:
        lines = f.readlines()

    records = []
    for line in lines:
        try:
            parts = line.strip().split("|")
            if len(parts) == 5:
                timestamp, endpoint, status, duration, session_id = parts
                records.append({
                    "timestamp": timestamp,
                    "endpoint": endpoint,
                    "status": status,
                    "duration": float(duration),
                    "session_id": session_id
                })
        except Exception:
            continue

    return pd.DataFrame(records)

df = fetch_api_logs()

if df.empty:
    st.warning("⚠️ No API logs found. Check log path or data capture.")
else:
    df['timestamp'] = pd.to_datetime(df['timestamp'])
    df = df.sort_values('timestamp', ascending=False)

    st.subheader("🔄 Most Recent API Calls")
    st.dataframe(df.head(50), use_container_width=True)

    st.subheader("📊 Endpoint Usage Summary")
    endpoint_summary = df['endpoint'].value_counts().reset_index()
    endpoint_summary.columns = ['Endpoint', 'Calls']
    st.bar_chart(endpoint_summary.set_index('Endpoint'))

    st.subheader("🕓 Call Volume Over Time")
    df['hour'] = df['timestamp'].dt.strftime('%H:00')
    hourly_summary = df.groupby('hour').size().reset_index(name='calls')
    st.line_chart(hourly_summary.set_index('hour'))

    st.subheader("🚨 Hijack or Failure Alerts")
    hijack_df = df[df['status'].isin(['401', '403'])]
    if not hijack_df.empty:
        st.error(f"❌ {len(hijack_df)} security issues or hijacks detected")
        st.dataframe(hijack_df, use_container_width=True)
    else:
        st.success("✅ No hijack events detected in recent logs")

    st.markdown("---")
    st.caption("🔍 This data is pulled from `api_hijack_clean.log` and updated live.")
