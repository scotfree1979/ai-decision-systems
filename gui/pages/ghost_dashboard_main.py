# gui/pages/10_📈_GhostTrader_Analytics.py
"""Wrapper that loads the existing GhostTrader dashboard."""

import runpy
import os
import sys
import streamlit as st

# ⬅️ Back button to return to the main AutoScalp page
if st.button("⬅️ Go Back to AutoScalp", key="ghost_wrapper_back"):

    st.switch_page("web_dashboard_core.py")

# ─────────────────────────────────────────────
# Fix Python path so 'engines' imports work
# ─────────────────────────────────────────────
# /Users/malachikelly/Dev/analytics_beta_dev/gui/pages → go up two levels
repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../"))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)

# Now run the GhostTrader main dashboard
ghost_path = os.path.join(repo_root, "engines", "ghost_dashboard_main.py")
runpy.run_path(ghost_path, run_name="__main__")

