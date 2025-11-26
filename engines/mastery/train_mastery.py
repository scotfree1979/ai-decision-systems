#!/usr/bin/env python3
"""
train_mastery.py — nightly policy model trainer
Reads v_mastery_training view and fits an action model.
"""
# --- ensure repo root is importable ---
import os, sys
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

import sqlite3, pandas as pd, json, os
from datetime import date
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder
import joblib
from engines.config_paths import autoscalp_db

def load_training_data():
    con = sqlite3.connect(autoscalp_db())
    df = pd.read_sql("SELECT * FROM v_mastery_training", con)
    con.close()
    return df

from engines.mastery.objectives import OBJECTIVES

def train_model(df):
    X = df[["pnl_total","liability_total","ratio","slope_ppm","tick_vel_3s_up","mto_minutes"]]
    y = df["action"]

    # compute reward weight
    df["reward_weight"] = df["reward"].clip(lower=-OBJECTIVES["loss_cap_per_market"],
                                            upper=OBJECTIVES["profit_target_per_market"])
    df["success"] = (df["reward"] > 0).astype(int)
    df["target_gap"] = abs(OBJECTIVES["profit_target_per_market"] - df["reward"])
    sample_weight = (df["success"] * (1 - df["target_gap"]/OBJECTIVES["profit_target_per_market"])) \
                    .clip(lower=0.1)

    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import LabelEncoder
    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    model = RandomForestClassifier(n_estimators=300, max_depth=8, random_state=42)
    model.fit(X, y_enc, sample_weight=sample_weight)
    return model, le


def save_model(model, le):
    os.makedirs("models", exist_ok=True)
    model_path = f"models/mastery_policy_{date.today()}.pkl"
    joblib.dump({"model": model, "encoder": le}, model_path)
    print(f"[train_mastery] ✅ model saved → {model_path}")
    return model_path

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery.py:register_model
# 📆 PATCHED: 2025-10-26Z — fix mastery_state insert (SQLite-safe next version)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def register_model(path):
    con = sqlite3.connect(autoscalp_db())
    meta = {"path": path, "trained_on": str(date.today())}
    cur = con.cursor()
    cur.execute("""
        INSERT INTO mastery_state(progress, thresholds_json, version, source)
        VALUES (
            (SELECT COALESCE(MAX(progress),0)+1 FROM mastery_state),
            ?,
            (SELECT COALESCE(MAX(version),0)+1 FROM mastery_state),
            'TRAINER'
        )
    """, (json.dumps(meta),))
    con.commit()
    con.close()
    print("[train_mastery] model registered in mastery_state.")
# === PATCH END ===


if __name__ == "__main__":
    df = load_training_data()
    if df.empty:
        print("[train_mastery] no data in v_mastery_training — skipping.")
        exit(0)
    model, le = train_model(df)
    path = save_model(model, le)
    register_model(path)
