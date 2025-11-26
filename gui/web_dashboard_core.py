#!/usr/bin/env python3
"""
web_dashboard_core.py — AutoScalp LIVE Web Dashboard
───────────────────────────────────────────────────────
Streamlit version of the Tkinter dashboard with
dark theme, neon-green highlights, and LIVE data from
autoscalp_gui.db + bets.db + settlements.db.
"""

import os, sys
# --- ensure repo root is importable ---
_here = os.path.dirname(os.path.abspath(__file__))          # .../gui
_root = os.path.abspath(os.path.join(_here, ".."))          # .../analytics_beta_dev
if _root not in sys.path:
    sys.path.insert(0, _root)


import os
import sqlite3
import pandas as pd
import streamlit as st
from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────
# Paths (reuse existing config_paths if available)
# ─────────────────────────────────────────────────────────────
from engines import config_paths as cp
cp.set_db_paths(mode="live", quiet=True)

AUTO_DB = cp.autoscalp_db()
BETS_DB = cp.bets_db()
SETTLE_DB = os.path.join(os.path.dirname(AUTO_DB), "settlements.db")

# ─────────────────────────────────────────────────────────────
# Theme
# ─────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="AutoScalp LIVE Dashboard",
    page_icon="⚡",
    layout="wide",
)

# Dark neon style
st.markdown("""
<style>
    body, .stApp { background-color: #0e1117; color: #f0f0f0; }
    .metric-label, .stMetricLabel { color: #00ff88 !important; }
    div[data-testid="stMetricValue"] { color: #00ff88 !important; }
    h1, h2, h3, h4 { color: #00ff88 !important; }
</style>
""", unsafe_allow_html=True)

# ===========================================================
# 🔗 Button to open GhostTrader (old analytics dashboard)
# ===========================================================
import streamlit as st

ghost_page_url = "/ghosttrader"  # we'll alias this below

# ===========================================================
# 🔗 Button to open GhostTrader (Analytics) dashboard
# ===========================================================
if st.button("📊 Open Dashboard Analytics"):
    st.switch_page("pages/10_📈_GhostTrader_Analytics.py")

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────
def _connect():
    con = sqlite3.connect(AUTO_DB)
    con.row_factory = sqlite3.Row
    con.execute(f"ATTACH DATABASE '{BETS_DB}' AS betsdb;")
    con.execute(f"ATTACH DATABASE '{SETTLE_DB}' AS setdb;")
    return con

def fmt_money(x):
    try: return f"£{float(x):,.2f}"
    except: return "£0.00"

# ─────────────────────────────────────────────────────────────
# KPI Section
# ─────────────────────────────────────────────────────────────
st.title("⚡ AutoScalp LIVE Dashboard")
st.subheader("🧠 Key Performance Indicators (24 h)")

con = _connect()
row = con.execute("""
    SELECT
        ROUND(SUM(profit),2) AS total_pnl,
        ROUND(SUM(CASE WHEN date(datetime(replace(settledDate,'Z','+00:00'))) = date('now','utc')
                       THEN profit ELSE 0 END),2) AS today_pnl,
        ROUND(AVG(CASE WHEN profit>0 THEN 1.0 ELSE 0.0 END)*100,1) AS win_rate,
        ROUND(AVG(CASE WHEN profit>0 THEN profit ELSE NULL END),2) AS avg_win,
        ROUND(AVG(CASE WHEN profit<0 THEN profit ELSE NULL END),2) AS avg_loss
    FROM setdb.bf_cleared_orders;
""").fetchone()

cols = st.columns(5)
cols[0].metric("Total P&L", fmt_money(row["total_pnl"]))
cols[1].metric("Today P&L", fmt_money(row["today_pnl"]))
cols[2].metric("Win %", f"{row['win_rate'] or 0:.1f}%")
cols[3].metric("Avg Win", fmt_money(row["avg_win"]))
cols[4].metric("Avg Loss", fmt_money(row["avg_loss"]))

# ─────────────────────────────────────────────────────────────
# Recent Settlements
# ─────────────────────────────────────────────────────────────
st.subheader("🏁 Recent Settlements")
settled = pd.read_sql("""
    SELECT marketId, SUM(profit) AS pnl, COUNT(*) AS bets
      FROM setdb.bf_cleared_orders
     WHERE datetime(replace(settledDate,'Z','+00:00')) >= datetime('now','-2 day')
     GROUP BY marketId
     ORDER BY MAX(settledDate) DESC LIMIT 10;
""", con)
st.dataframe(settled, use_container_width=True)

# ─────────────────────────────────────────────────────────────
# Upcoming / In-Play Markets
# ─────────────────────────────────────────────────────────────
st.subheader("⏱️ Upcoming & In-Play Markets")
markets = pd.read_sql("""
    SELECT event_name, market_name, marketStartTime,
           CASE
             WHEN datetime(replace(marketStartTime,'T',' ')) > datetime('now','utc')
                  THEN 'Upcoming'
             WHEN datetime(replace(marketStartTime,'T',' ')) >= datetime('now','utc','-15 minute')
                  THEN 'In-Play'
             ELSE 'Settled'
           END AS status
      FROM betsdb.bets
     WHERE datetime(replace(marketStartTime,'T',' ')) >= datetime('now','utc','-1 day')
     ORDER BY datetime(replace(marketStartTime,'T',' ')) ASC
     LIMIT 20;
""", con)
st.dataframe(markets, use_container_width=True)

con.close()
st.caption(f"Last updated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
