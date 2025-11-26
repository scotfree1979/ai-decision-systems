#!/usr/bin/env python3
# ===============================================================
# GHOSTTRADER INTELLIGENCE DASHBOARD v7 — Single-Page Layout
# ===============================================================

import os
import sys
import sqlite3
import pandas as pd
import pytz
import streamlit as st
from datetime import datetime, timedelta
from streamlit_autorefresh import st_autorefresh

# ─────────────────────────────────────────────
# Ensure engines/ and repo root are importable
# ─────────────────────────────────────────────
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)
_engines = os.path.join(_root, "engines")
if _engines not in sys.path:
    sys.path.insert(0, _engines)

from engines import config_paths
config_paths.set_db_paths(mode="live", quiet=True)

# ─────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(page_title="📈 AutoScalp Intelligence Dashboard v7", layout="wide")
st_autorefresh(interval=15000, key="refresh_intel")  # refresh every 15 s

st.title("🧠 AutoScalp Intelligence Dashboard v7")




# ===============================================================
# 🧠 AUTOSCALP v7 INTELLIGENCE — METRIC DASHBOARD (FIXED)
# ===============================================================

import pandas as pd, sqlite3
from datetime import datetime, timedelta

AUTO = config_paths.autoscalp_db()
SETTLE = os.path.join(os.path.dirname(AUTO), "settlements.db")

st.markdown("## ⚡ Live System State")

# --- define safe date defaults (fallback if not yet defined) ---
if 'start_date' not in locals():
    start_date = datetime.utcnow().replace(hour=0, minute=0, second=0)
if 'end_date' not in locals():
    end_date = datetime.utcnow()

# --- helper functions ---
def safe_scalar(label, query, db=AUTO):
    """Executes SQL query safely and prints debug feedback."""
    try:
        with sqlite3.connect(db) as con:
            cur = con.cursor()
            val = cur.execute(query).fetchone()
            result = round(val[0], 2) if val and val[0] is not None else 0
            st.caption(f"✅ {label}: {result}")
            return result
    except Exception as e:
        st.error(f"❌ {label} failed: {e}")
        return 0

# --- connection test ---
st.markdown("### 🔍 Connection Verification")
for name, path in {"autoscalp_gui.db": AUTO, "settlements.db": SETTLE}.items():
    try:
        with sqlite3.connect(path) as con:
            count = con.execute("SELECT count(*) FROM sqlite_master;").fetchone()[0]
        st.success(f"Connected to {name} — {count} objects found.")
    except Exception as e:
        st.error(f"❌ {name} failed: {e}")

# ---------------------------------------------------------------
# ✅ LIVE METRIC PROBE (corrected for schema)
# ---------------------------------------------------------------
st.markdown("### 📊 Live Metric Probes")

live_metrics = {
    "Live P&L": "SELECT SUM(avg_cashout) FROM v7_cashout;",  # fixed column name
    "Potential P&L": "SELECT SUM(potential_cashout) FROM v7_cashout;",
    "Runners Active": "SELECT COUNT(*) FROM v_dashboard_liabilities;",
    "Total Liability": "SELECT SUM(liability) FROM v_dashboard_liabilities;",
    "Avg Liability/Market": """
        SELECT AVG(liability) FROM (
            SELECT marketId, SUM(liability) AS liability
            FROM v_dashboard_liabilities GROUP BY marketId
        );
    """,
    "Avg Confidence": "SELECT p1_mean FROM v_mastery_brain_global;",
    "Drifting Markets": "SELECT COUNT(*) FROM v7_intelligence WHERE drift_speed>1;",
    "Highest Liability": "SELECT MAX(liability) FROM v7_liability_risk;",
    "Active Strategies": "SELECT COUNT(DISTINCT strategy) FROM v_strategy_perf;",
}

live_results = {}
for label, q in live_metrics.items():
    live_results[label] = safe_scalar(label, q, db=AUTO)

st.markdown("### ✅ Summary (preview values)")
st.json(live_results)

# ---------------------------------------------------------------
# ✅ HISTORICAL METRIC PROBE (fixed DB targets + schema)
# ---------------------------------------------------------------
st.markdown("### 🧭 Historical Metric Probes")

date_start = start_date.strftime("%Y-%m-%d")
date_end = end_date.strftime("%Y-%m-%d")

hist_metrics = {
    # settlements.db → realised P&L per day
    "Total Realised P&L": f"""
        SELECT ROUND(SUM(net),2)
        FROM v_settle_day
        WHERE day BETWEEN '{date_start}' AND '{date_end}';
    """,
    "Avg Daily P&L": f"""
        SELECT ROUND(AVG(net),2)
        FROM v_settle_day
        WHERE day BETWEEN '{date_start}' AND '{date_end}';
    """,

    # autoscalp_gui.db → performance signals
    "Win %": """
        SELECT ROUND(AVG(success)*100,2)
        FROM v_mastery_training_unified;
    """,
    "Avg Confidence": """
        SELECT ROUND(AVG(p1_mean),3)
        FROM v_mastery_brain_global;
    """,
    "Strategies Used": """
        SELECT COUNT(DISTINCT strategy)
        FROM v_strategy_perf;
    """
}

hist_results = {}
for label, q in hist_metrics.items():
    # use SETTLE DB for v_settle_day queries, AUTO for everything else
    target_db = SETTLE if "v_settle_day" in q else AUTO
    hist_results[label] = safe_scalar(label, q, db=target_db)

st.markdown("### ✅ Historical Summary (preview values)")
st.json(hist_results)

st.json(hist_results)

# ===============================================================
# 🔌 HELPER TO LOAD ANY VIEW
# ===============================================================
@st.cache_data
def load_view(name, db="data/autoscalp_gui.db", sql=None):
    try:
        con = sqlite3.connect(db)
        df = pd.read_sql_query(sql or f"SELECT * FROM {name}", con)
        con.close()
        return df
    except Exception as e:
        st.error(f"{name}: {e}")
        return pd.DataFrame()

# Paths
AUTO = config_paths.autoscalp_db()
BETS = config_paths.bets_db()
SETTLE = os.path.join(os.path.dirname(AUTO), "settlements.db")

# ===============================================================
# 💰 ZONE 1 — OVERVIEW
# ===============================================================
st.divider()
st.subheader("💰 Zone 1 — Overview")

c1, c2 = st.columns(2)
with c1:
    st.markdown("**v_settle_day — Daily P&L (Last 14 Days)**")
    df = load_view("v_settle_day", SETTLE,
                   "SELECT day, ROUND(SUM(net),2) AS pnl, COUNT(*) AS mkts "
                   "FROM v_settle_day GROUP BY day ORDER BY day DESC LIMIT 14;")
    st.dataframe(df, use_container_width=True)
with c2:
    st.markdown("**v7_cashout — Live Cashout Spread**")
    st.dataframe(load_view("v7_cashout", AUTO,
                   "SELECT marketId,potential_cashout,avg_cashout,worst_cashout,spread_now "
                   "FROM v7_cashout LIMIT 50;"), use_container_width=True)

# ===============================================================
# ⚙️ ZONE 2 — LIABILITIES
# ===============================================================
st.divider()
st.subheader("⚙️ Zone 2 — Liabilities")

st.dataframe(load_view("v_dashboard_liabilities", AUTO,
    "SELECT marketId,liability,markets FROM v_dashboard_liabilities ORDER BY liability DESC LIMIT 25;"),
    use_container_width=True)

st.dataframe(load_view("v7_liability_risk", AUTO,
    "SELECT marketId,selectionId,liability,win_pct FROM v7_liability_risk ORDER BY liability DESC LIMIT 50;"),
    use_container_width=True)

# ===============================================================
# 🧩 ZONE 3 — STRATEGY PERFORMANCE
# ===============================================================
st.divider()
st.subheader("🧩 Zone 3 — Strategy Performance")

st.dataframe(load_view("v_strategy_perf", AUTO,
    "SELECT strategy,pnl_today,matched_liab,unmatched_liab,open_parents,last_trade_ts "
    "FROM v_strategy_perf ORDER BY pnl_today DESC;"), use_container_width=True)

# ===============================================================
# 🧠 ZONE 4 — MASTERY / BRAIN
# ===============================================================
st.divider()
st.subheader("🧠 Zone 4 — Mastery / Brain Metrics")

col1, col2 = st.columns(2)
with col1:
    st.dataframe(load_view("v_mastery_brain_global", AUTO,
        "SELECT samples,total_pnl,p1_mean AS avg_confidence FROM v_mastery_brain_global;"),
        use_container_width=True)
with col2:
    st.dataframe(load_view("v_mastery_brain_macro", AUTO,
        "SELECT bucket,samples,total_pnl,p1_mean AS avg_confidence FROM v_mastery_brain_macro ORDER BY total_pnl DESC;"),
        use_container_width=True)

# ===============================================================
# 🏇 ZONE 5 — RUNNER / MARKET INTELLIGENCE
# ===============================================================
st.divider()
st.subheader("🏇 Zone 5 — Runner & Market Intelligence")

st.dataframe(load_view("v_runner_context_form", AUTO,
    "SELECT runner_name,fav_rank_bin,odds_band,runs,wins,win_rate,avg_entry_odds "
    "FROM v_runner_context_form ORDER BY win_rate DESC LIMIT 50;"), use_container_width=True)

c1, c2 = st.columns(2)
with c1:
    st.dataframe(load_view("v_runner_form_groups", AUTO,
        "SELECT form_class,odds_band,horses,avg_win_rate,avg_odds FROM v_runner_form_groups;"),
        use_container_width=True)
with c2:
    st.dataframe(load_view("v_trading_zone_groups", AUTO,
        "SELECT trading_zone,strength_label,horses,avg_win_rate,avg_odds FROM v_trading_zone_groups;"),
        use_container_width=True)

# ===============================================================
# 🧬 ZONE 6 — TRAINING / LEARNING
# ===============================================================
st.divider()
st.subheader("🧬 Zone 6 — Training / Learning Diagnostics")

st.dataframe(load_view("v_mastery_training_unified", AUTO,
    """
    SELECT 
        letter,
        pnl,
        success,
        drift_speed,
        form_win_rate,
        band_rel_vol,
        band_bias,
        band_stability
    FROM v_mastery_training_unified
    LIMIT 100;
    """),
    use_container_width=True)


st.dataframe(load_view("v7_timing_features_fixed", AUTO,
    "SELECT marketId,selectionId,pre_avg_odds,inplay_avg_odds,drift_speed,drift_ratio FROM v7_timing_features_fixed LIMIT 50;"),
    use_container_width=True)

st.dataframe(load_view("v7_intelligence", AUTO,
    "SELECT marketId,selectionId,pnl,success,drift_speed,momentum_class,potential_cashout,avg_cashout,worst_cashout FROM v7_intelligence LIMIT 50;"),
    use_container_width=True)

# ===============================================================
# 💼 ZONE 7 — VERIFIED SETTLEMENTS
# ===============================================================
st.divider()
st.subheader("💼 Zone 7 — Verified Settlements")

st.dataframe(load_view("v_settle_day", SETTLE,
    "SELECT day,ROUND(SUM(net),2) AS total_pnl,COUNT(*) AS markets FROM v_settle_day GROUP BY day ORDER BY day DESC LIMIT 14;"),
    use_container_width=True)

st.success("✅ Dashboard ready — all zones rendered.")
