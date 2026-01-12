#!/usr/bin/env python3
"""
check_v7_intelligence.py
------------------------
Authoritative V7 Intelligence inspection script.

• Enumerates all known V7 / Mastery / Brain / OC / Playbook views
• Executes COUNT(*) safely
• Reports:
    - OK (rows present)
    - EMPTY (0 rows)
    - ERROR (broken dependency)
• No writes
• No assumptions
"""

import sqlite3
from pathlib import Path

DBS = {
    "autoscalp_gui.db": Path("data/autoscalp_gui.db"),
    "mastery_v7.db": Path("data/mastery_v7.db"),
}

VIEWS = {
    "autoscalp_gui.db": [
        "letter_intelligence_today",
        "v7_cashout",
        "v7_intelligence",
        "v7_intelligence_expanded",
        "v7_liability_risk",
        "v7_oc_drift_unfolded",
        "v7_preoff_position",
        "v7_race_position_final",
        "v7_race_position_inferred",
        "v7_race_position_named",
        "v7_race_position_summary",
        "v7_shape_summary",
        "v7_timing_features_fixed",
        "v_blueprint_playbook_train",
        "v_dash_iq_mastery_effect",
        "v_dash_strategy_intel_letter",
        "v_mastery_brain_global",
        "v_mastery_brain_input",
        "v_mastery_brain_macro",
        "v_mastery_core_results",
        "v_mastery_intel_v7",
        "v_mastery_intelligence_runner",
        "v_mastery_live",
        "v_mastery_match_quality",
        "v_mastery_pattern_train",
        "v_mastery_training",
        "v_mastery_training_base",
        "v_mastery_training_input",
        "v_mastery_training_unified",
        "v_mastery_v7",
        "v_oc_timing",
        "v_orders_v7",
        "v_playbooks_all",
        "v_playbooks_norm",
        "v_timing_features_train_v7",
        "v_timing_features_v7",
    ],
    "mastery_v7.db": [
        "v7_mastery_race_clusters_new",
        "v7_mastery_race_summary_new",
        "v7_mastery_race_view_new",
        "v_mastery_v7_context",
        "v_mastery_v7_context_plus",
    ],
}

def check_db(db_name: str, db_path: Path, views: list[str]):
    print("=" * 80)
    print(f"DB: {db_name}  →  {db_path.resolve()}")
    print("=" * 80)

    if not db_path.exists():
        print("❌ DATABASE NOT FOUND\n")
        return

    con = sqlite3.connect(db_path)
    cur = con.cursor()

    for v in views:
        try:
            cur.execute(f"SELECT COUNT(*) FROM {v}")
            n = cur.fetchone()[0]
            if n > 0:
                print(f"{v:<45} rows={n:<10} OK")
            else:
                print(f"{v:<45} rows=0        ⚠️ EMPTY")
        except Exception as e:
            print(f"{v:<45} ❌ ERROR → {e}")

    con.close()
    print()

def main():
    for db, views in VIEWS.items():
        check_db(db, DBS[db], views)

    print("✔ View inspection complete.")

if __name__ == "__main__":
    main()
