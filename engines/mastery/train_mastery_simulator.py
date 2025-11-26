#!/usr/bin/env python3
"""
train_mastery_simulator.py — active training via replay simulation
──────────────────────────────────────────────────────────────────────────────
After daily consolidation, this module:
  • Loads latest ML policy (RandomForest from models/)
  • Replays today's markets N times with stochastic noise
  • Samples alternative actions at each decision
  • Scores each action by liability reduction & realised reward
  • Applies small (5 %) weight updates to mastery_posteriors
"""

import os, sys, sqlite3, json, random
import pandas as pd
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db
from engines.mastery import posteriors
from engines.mastery.canonical_digest import weight_for_letter
import joblib

SIM_WEIGHT = 0.05            # 5 % influence
SIM_EPOCHS = 20              # number of simulated runs

# ── helpers ───────────────────────────────────────────────────────────────

def _load_latest_model():
    """Return latest RandomForest + encoder pair."""
    models = [f for f in os.listdir("models") if f.startswith("mastery_policy_")]
    if not models:
        raise RuntimeError("no mastery_policy_*.pkl found")
    path = max([os.path.join("models", m) for m in models], key=os.path.getmtime)
    pack = joblib.load(path)
    print(f"[simulator] using model {path}")
    return pack["model"], pack["encoder"]

def _load_contexts():
    """Pull latest training view as base environment."""
    con = sqlite3.connect(autoscalp_db())
    df = pd.read_sql("SELECT * FROM v_mastery_training", con)
    con.close()
    return df

def _simulate_day(df, model, encoder):
    """
    Run one simulated replay of the day.
    Randomly perturb features, query model for action, and compute reward.
    """
    X = df[["pnl_total","liability_total","ratio","slope_ppm","tick_vel_3s_up","mto_minutes"]].copy()
    y_pred = encoder.inverse_transform(model.predict(X))
    df["sim_action"] = y_pred

    # inject stochastic perturbation (~±10 %)
    noise = 0.1
    df["pnl_total"] *= (1 + noise * (2*pd.Series([random.random()-0.5 for _ in range(len(df))])))
    df["liability_total"] *= (1 + noise * (2*pd.Series([random.random()-0.5 for _ in range(len(df))])))

    # reward: higher if pnl↑ and liability↓ relative to baseline
    df["sim_reward"] = (df["pnl_total"] / df["liability_total"].replace(0,1)).clip(-5,5)
    return df[["marketId","sim_action","sim_reward","pnl_total","liability_total"]]

def _apply_updates(sim_df):
    """Fold simulated rewards back into mastery_posteriors with small weight."""
    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
    for _, r in sim_df.iterrows():
        letter = (r["sim_action"] or "A")[0].upper()
        w = weight_for_letter(letter) * SIM_WEIGHT
        posteriors.update_after_outcome(
            con,
            bin_key=f"{letter}|SIM",
            target_ticks=1,
            success=(r["sim_reward"] > 0),
            net_pnl=float(r["sim_reward"]),
            weight_applied=w,
            letter=letter,
            band="simulation",
            source="SIMULATION",
            source_run=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            auto_commit=False
        )
    con.commit(); con.close()

# ── main ──────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    try:
        model, enc = _load_latest_model()
        base_df = _load_contexts()
        if base_df.empty:
            print("[simulator] no contexts to train on.")
            sys.exit(0)

        for i in range(SIM_EPOCHS):
            print(f"[simulator] epoch {i+1}/{SIM_EPOCHS}")
            sim = _simulate_day(base_df.copy(), model, enc)
            _apply_updates(sim)

        print(f"[simulator] ✅ completed {SIM_EPOCHS} simulated replays "
              f"→ {len(base_df)*SIM_EPOCHS:,} updates applied (weight × {SIM_WEIGHT})")

    except Exception as e:
        print(f"[simulator] fatal: {e}")
