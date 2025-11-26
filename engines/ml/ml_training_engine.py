#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ml_training_engine.py — Playbooks → ML → Mastery integration

Stage-2 of AutoScalp Playbooks:
Trains a logistic regression model to predict trade success (win/loss)
and updates mastery_posteriors with predicted confidences.

Usage:
    python3 engines/ml/ml_training_engine.py --days 60 --model logit
"""

from __future__ import annotations
import os, sys, json, sqlite3, argparse, datetime
import pandas as pd
from typing import Dict, Any
from river import linear_model, optim, metrics, preprocessing

# ───────────────────────────────────────────────────────────────
# DB helpers
# ───────────────────────────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
DATA_DIR = os.path.join(ROOT, "data")
AUTO_DB = os.path.join(DATA_DIR, "autoscalp_gui.db")

def connect_db() -> sqlite3.Connection:
    con = sqlite3.connect(AUTO_DB)
    con.row_factory = sqlite3.Row
    return con

# ───────────────────────────────────────────────────────────────
# Schema guards
# ───────────────────────────────────────────────────────────────
def ensure_ml_tables(con: sqlite3.Connection) -> None:
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ml_models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trained_at TEXT,
            model_type TEXT,
            features_json TEXT,
            metrics_json TEXT,
            weights_json TEXT
        );
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ml_features_daily (
            day TEXT,
            feature_json TEXT,
            created_at TEXT DEFAULT (datetime('now','utc'))
        );
    """)
    # add columns to mastery_posteriors if missing
    cols = [r[1] for r in cur.execute("PRAGMA table_info(mastery_posteriors);")]
    if "confidence" not in cols:
        cur.execute("ALTER TABLE mastery_posteriors ADD COLUMN confidence REAL DEFAULT 0.5;")
    if "expected_value" not in cols:
        cur.execute("ALTER TABLE mastery_posteriors ADD COLUMN expected_value REAL DEFAULT 0.0;")
    con.commit()

# ───────────────────────────────────────────────────────────────
# Feature builder
# ───────────────────────────────────────────────────────────────
# === PATCH START: schema-correct Playbooks column map =========================
# 📍 TARGET: engines/ml/ml_training_engine.py
# 🔎 SEARCH: def build_features
# 📆 PATCHED: 2025-10-17T08:15Z — align columns with playbooks_settled schema
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def build_features(con: sqlite3.Connection, days:int=60) -> pd.DataFrame:
    since = (datetime.datetime.utcnow() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")

    # Fallback-safe: use odds_ticks if target_ticks doesn’t exist
    play_cols = [r[1] for r in con.execute("PRAGMA table_info(playbooks_settled);")]
    tick_col = "target_ticks" if "target_ticks" in play_cols else "odds_ticks"

    q = f"""
        SELECT
            p.day,
            p.marketId,
            p.selectionId,
            p.letter,
            p.class_band,
            p.{tick_col} AS target_ticks,
            p.odds_ticks,
            p.net_pl,
            p.confidence,
            p.hedge_ratio,
            p.green_up_pnl,
            p.liability_released,
            p.outcome_bin,
            CASE WHEN p.profit>0 THEN 1 ELSE 0 END AS success,
            m.net_pnl AS bin_net_pnl,
            m.priority,
            m.weight_applied
        FROM playbooks_settled p
        LEFT JOIN mastery_posteriors m
               ON p.letter = m.letter
              AND p.class_band = m.band
        WHERE date(p.day) >= date(?)
    """
    df = pd.read_sql_query(q, con, params=[since])
# === PATCH END =================================================================


    # clean / feature selection
    df.fillna(0, inplace=True)
    features = [
        "target_ticks","odds_ticks","confidence",
        "hedge_ratio","green_up_pnl","liability_released",
        "bin_net_pnl","priority","weight_applied"
    ]
    X = df[features]
    y = df["success"].astype(int)
    print(f"[ml] Dataset built: {len(df):,} rows, {len(features)} features (since {since})")
    return X, y, df, features

# ───────────────────────────────────────────────────────────────
# Model training
# ───────────────────────────────────────────────────────────────
def train_model(X: pd.DataFrame, y: pd.Series, model_type:str="logit") -> Dict[str,Any]:
    if model_type.lower() in ("logit","logistic","lr"):
        model = preprocessing.StandardScaler() | linear_model.LogisticRegression(
            optimizer=optim.SGD(0.01), l2=0.001
        )
    else:
        raise ValueError(f"Unsupported model type: {model_type}")

    metric_acc = metrics.Accuracy()
    metric_auc = metrics.ROCAUC()

    for xi, yi in zip(X.to_dict(orient="records"), y.tolist()):
        y_pred = model.predict_one(xi)
        model.learn_one(xi, yi)
        metric_acc.update(yi, y_pred)
        metric_auc.update(yi, y_pred)

    acc = metric_acc.get()
    auc = metric_auc.get()
    print(f"[ml] Trained {model_type} model — Accuracy={acc:.3f}  ROC-AUC={auc:.3f}")
    return {"model":model, "accuracy":acc, "auc":auc}

# ───────────────────────────────────────────────────────────────
# Save model + metrics
# ───────────────────────────────────────────────────────────────
def save_model(con: sqlite3.Connection, model_dict:Dict[str,Any],
               features:list[str]) -> str:
    model = model_dict["model"]
    weights = {k: float(v) for k,v in getattr(model["LogisticRegression"], "weights", {}).items()}
    metrics_json = json.dumps({
        "accuracy": model_dict["accuracy"],
        "roc_auc": model_dict["auc"]
    })
    weights_json = json.dumps(weights)
    features_json = json.dumps(features)
    ts = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

    cur = con.cursor()
    cur.execute("""
        INSERT INTO ml_models(trained_at, model_type, features_json, metrics_json, weights_json)
        VALUES(?,?,?,?,?)
    """, (ts, "LogisticRegression", features_json, metrics_json, weights_json))
    con.commit()

    out_dir = os.path.join(DATA_DIR, "ml_models")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"weights_{ts.replace(':','')}.json")
    with open(path, "w") as f: json.dump(weights, f, indent=2)
    print(f"[ml] Model weights saved → {path}")
    return path

# ───────────────────────────────────────────────────────────────
# Apply predictions to mastery_posteriors
# ───────────────────────────────────────────────────────────────
# === PATCH START: auto-seed posteriors for missing bins ========================
# 📍 TARGET: engines/ml/ml_training_engine.py:update_confidences
# 📆 PATCHED: 2025-10-17T08:50Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def update_confidences(con: sqlite3.Connection, model, df: pd.DataFrame, features:list[str]) -> int:
    df["pred_conf"] = df[features].to_dict(orient="records")
    df["pred_conf"] = df["pred_conf"].apply(lambda x: float(model.predict_proba_one(x).get(True, 0.5)))
    cur = con.cursor()

    # ensure columns exist
    cols = [r[1] for r in cur.execute("PRAGMA table_info(mastery_posteriors);")]
    if "confidence" not in cols:
        cur.execute("ALTER TABLE mastery_posteriors ADD COLUMN confidence REAL DEFAULT 0.5;")
    if "expected_value" not in cols:
        cur.execute("ALTER TABLE mastery_posteriors ADD COLUMN expected_value REAL DEFAULT 0.0;")

    updated = 0
    for _, r in df.iterrows():
        letter = str(r["letter"] or "").strip()
        band = str(r["class_band"] or "").strip()

        # auto-seed bin if missing
        cur.execute("""
            INSERT INTO mastery_posteriors(bin_key, letter, band, target_ticks, net_pnl, confidence, expected_value)
            VALUES(?, ?, ?, 1, 0.0, 0.5, 0.0)
            ON CONFLICT(bin_key) DO NOTHING
        """, (f"{letter}|{band}", letter, band))

        # update confidence values
        cur.execute("""
            UPDATE mastery_posteriors
               SET confidence=?,
                   expected_value=?
             WHERE letter=? AND band=?
        """, (
            r["pred_conf"],
            r["pred_conf"] * float(r["net_pl"]),
            letter,
            band,
        ))
        updated += cur.rowcount

    con.commit()
    print(f"[ml] Updated {updated:,} mastery_posteriors.confidence values (auto-seeded where missing)")
    return updated
# === PATCH END =================================================================

# ───────────────────────────────────────────────────────────────
# Main entry
# ───────────────────────────────────────────────────────────────
def main(argv=None):
    ap = argparse.ArgumentParser(description="AutoScalp ML Trainer")
    ap.add_argument("--days", type=int, default=60, help="Training window in days")
    ap.add_argument("--model", type=str, default="logit", help="Model type (logit)")
    args = ap.parse_args(argv)

    con = connect_db()
    ensure_ml_tables(con)
    X, y, df, features = build_features(con, args.days)
    model_dict = train_model(X, y, args.model)
    save_model(con, model_dict, features)
    update_confidences(con, model_dict["model"], df, features)
    con.close()
    print("[ml] Training complete.")

if __name__ == "__main__":
    main()
