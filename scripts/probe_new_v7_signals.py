#!/usr/bin/env python3
"""
Check availability of new v7 columns/views for Race Shape, Timing, and In-Play inference.
"""

import sqlite3
from engines.config_paths import autoscalp_db, q_retry as _q

views = {
    "v7_race_position_summary": ["anchor_odd", "avg_drift", "race_pattern"],
    "v7_timing_features_fixed": ["pre_avg_odds", "drift_ratio"],
    "v7_intelligence_expanded": ["inplay_progress", "drift_speed", "expected_race_mins"],
    "v7_intelligence": ["slope_ppm", "tick_vel_3s_up"],
}

print("🔍 Verifying new v7 columns (schema-level probe)\n")
con = sqlite3.connect(autoscalp_db())
con.row_factory = sqlite3.Row

for view, cols in views.items():
    print(f"View: {view}")
    try:
        schema = [r["name"] for r in _q(con, f"PRAGMA table_info({view})").fetchall()]
        if not schema:
            print(f"  ❌ view not found")
            continue
        for c in cols:
            print(f"  {'✅' if c in schema else '⚠️'} {c}")
        # sanity check: quick count
        row = _q(con, f"SELECT COUNT(*) AS n FROM {view}").fetchone()
        print(f"  Rows: {row['n']}\n")
    except Exception as e:
        print(f"  ❌ error: {e}\n")

con.close()
print("✅ Probe finished — ready to integrate verified views.")
