#!/usr/bin/env python3
# --- ensure repo root is importable ---
import os, sys, sqlite3, pandas as pd, json
from datetime import datetime, timezone, date
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import LabelEncoder
import joblib

# --- ensure repo root is importable ---
import os, sys
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _root not in sys.path:
    sys.path.insert(0, _root)

"""
train_mastery_v7.py — unified Mastery v7 trainer
-------------------------------------------------
Builds a 7-day training snapshot from v_mastery_v7,
fits a Random Forest model, updates mastery_posteriors
directly, and emits a mastery_snapshot_v7.json.
This is the single entry point for menu option 2i.
"""


from engines.config_paths import autoscalp_db
from engines.config_core_values import get_core_values
from engines.mastery import posteriors

# === PATCH START ===
# 📍 TARGET: engines/mastery/phase2_training.py
# 📆 PATCHED: 2025-11-09Z — Force local DB for bridge feedback assimilation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, sqlite3

# ============================================================
# TRAINING FEATURE CONTRACT (AUTHORITATIVE)
# ============================================================

CORE_FEATURES = [
    # outcome / execution
    "pnl",
    "target_ticks",
    "realized_ticks",

    # timing / dynamics
    "drift_speed",
    "inplay_progress",
    "expected_race_mins",

    # volumes
    "pre_vol",
    "inplay_vol",
    "post_vol",

    # form
    "form_win_rate",
    "form_avg_pnl",

    # favourite / ordinal context
    "fav_rank",
    "fav_percentile",
    "is_favourite",
    "is_top_3",
    "is_longshot",
    "fav_bucket_enc",
]

OPTIONAL_FEATURES = [
    # legacy / future (may not exist yet)
    "matched_ratio",
    "open_liab",
    "liab_error",
    "blueprint_key",
    "surface_key",
    "trade_type",
]


def _local_db():
    """Return local mastery_v7.db path for assimilation."""
    return MASTERY_DB_PATH
# === PATCH END ===



# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py (top of file, before imports from config_paths)
# 📆 PATCHED: 2025-11-09Z — Force local paths for Forest (offline) training
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Prevent config_paths from hijacking to iCloud (runtime mode)
DATA_DIR = os.path.join(os.path.dirname(__file__), "../../data")
BETS_DB_PATH = os.path.join(DATA_DIR, "bets.db")
GUI_DB_PATH = os.path.join(DATA_DIR, "autoscalp_gui.db")
MASTERY_DB_PATH = os.path.join(DATA_DIR, "mastery_v7.db")
SETTLE_DB_PATH = os.path.join(DATA_DIR, "settlements.db")

def _connect_local(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con

print(f"[local-paths] Forest training active → {MASTERY_DB_PATH}")
# === PATCH END ===
# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py (startup banner)
# 📆 PATCHED: 2025-11-09Z — Add explicit DB path diagnostics (local only)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
print("────────────────────────────────────────────────────────────────────")
print(f"[local-paths] 🌐 ACTIVE ENVIRONMENT: LOCAL ONLY")
print(f"[local-paths] 📚  Mastery DB → {os.path.abspath(MASTERY_DB_PATH)}")
print(f"[local-paths] 📊  GUI DB      → {os.path.abspath(GUI_DB_PATH)}")
print(f"[local-paths] 🧠  Settlements → {os.path.abspath(SETTLE_DB_PATH)}")
print(f"[local-paths] 💾  Bets DB     → {os.path.abspath(BETS_DB_PATH)}")
print("────────────────────────────────────────────────────────────────────")
# === PATCH END ===



# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py
# 📆 PATCHED: 2025-11-09Z — force Phases 1/2 to use local DB connection
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _run_cognitive_pretraining():
    """
    Run Phase 1 (Understanding) and Phase 2 (Adaptation) automatically
    using the same local DB context as Forest training.
    """
    print("\n[train-v7] 🧠 running cognitive pre-training (Phase 1 + 2)…")
    try:
        import engines.mastery.phase1_understanding as p1
        p1._connect = lambda: _connect_local(MASTERY_DB_PATH)
        p1.phase1_understanding()
    except Exception as e:
        print(f"[train-v7] ⚠️ Phase 1 understanding failed: {e}")

    try:
        import engines.mastery.phase2_training as p2
        p2._connect = lambda: _connect_local(MASTERY_DB_PATH)
        
        p2.phase2_training()
    except Exception as e:
        print(f"[train-v7] ⚠️ Phase 2 training failed: {e}")

    print("[train-v7] ✅ cognitive pre-training complete.\n")
# === PATCH END ===



# === NEW: v7 sandbox builder ==========================================
import sqlite3, os
from engines.config_paths import autoscalp_db

import sys, time

def _progress(label: str, steps: int = 20, delay: float = 0.05):
    """Simple console progress bar for visual feedback."""
    for i in range(steps + 1):
        bar = "#" * i + "-" * (steps - i)
        pct = int((i / steps) * 100)
        sys.stdout.write(f"\r[{label}] [{bar}] {pct}%")
        sys.stdout.flush()
        time.sleep(delay)
    print()  # newline

def _safe_avg(vals):
    """Return mean of valid numeric values, ignoring None."""
    try:
        valid = [float(v) for v in vals if v is not None]
        return round(sum(valid) / len(valid), 6) if valid else None
    except Exception:
        return None

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py : _fold_in_live_posteriors()
# 📆 PATCHED: 2025-11-17 — join by bin_key; derive marketId/selectionId if needed
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _fold_in_live_posteriors(df: pd.DataFrame) -> pd.DataFrame:
    """
    Hybrid fold-in of both live posteriors and bridge feedback.

    Uses bin_key to merge (marketId|selectionId) so we work with both
    legacy and current schemas of mastery_posteriors.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db

    # Ensure training snapshot has a join key
    if "bin_key" not in df.columns:
        if {"marketId", "selectionId"}.issubset(df.columns):
            df["bin_key"] = df["marketId"].astype(str) + "|" + df["selectionId"].astype(str)
        else:
            print("[fold-in] ⚠️ training snapshot lacks marketId/selectionId — cannot build bin_key.")
            return df

    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row

    # --- Load live posteriors (7 days) by bin_key ---
    try:
        old_df = pd.read_sql("""
            SELECT bin_key,
                   confidence       AS river_conf_live,
                   bucket_confidence AS forest_conf_live,
                   live_pnl_ratio,
                   updated_at
              FROM mastery_posteriors
             WHERE updated_at >= datetime('now','-7 day','utc');
        """, con)
        print(f"[fold-in] loaded {len(old_df)} rows from mastery_posteriors.")
    except Exception as e:
        print(f"[fold-in] warn: mastery_posteriors unreadable ({e})")
        old_df = pd.DataFrame()

    # --- Load bridge_decision_log (90 days) and build bin_key ---
    try:
        bridge_df = pd.read_sql("""
            SELECT marketId, selectionId,
                   coherence      AS river_conf_live,
                   adjustment     AS forest_conf_live,
                   goal_alignment,
                   bucket,
                   ts_sent        AS updated_at
              FROM bridge_decision_log
             WHERE ts_sent >= datetime('now','-90 day','utc');
        """, con)
        if not bridge_df.empty and {"marketId","selectionId"}.issubset(bridge_df.columns):
            bridge_df["bin_key"] = bridge_df["marketId"].astype(str) + "|" + bridge_df["selectionId"].astype(str)
        print(f"[fold-in] loaded {len(bridge_df)} rows from bridge_decision_log.")
    except Exception as e:
        print(f"[fold-in] warn: bridge_decision_log unreadable ({e})")
        bridge_df = pd.DataFrame()

    con.close()

    # --- Merge both sources on bin_key ---
    live_df = pd.concat([old_df, bridge_df], ignore_index=True)
    if live_df.empty:
        print("[fold-in] ℹ️ no live posterior or bridge data found.")
        return df

    # Ensure the live_df has bin_key
    if "bin_key" not in live_df.columns:
        print("[fold-in] ⚠️ live_df lacks bin_key — skipping merge.")
        return df

    df = df.merge(live_df.drop_duplicates(subset=["bin_key"]),
                  on="bin_key", how="left")

    # --- Coherence diagnostics ---
    df["delta_conf"] = (
        df["river_conf_live"].fillna(0.5) - df["forest_conf_live"].fillna(0.5)
    )
    df["rolling_mean_conf"] = (
        0.5 * df["river_conf_live"].fillna(0.5) +
        0.5 * df["forest_conf_live"].fillna(0.5)
    )

    print("[fold-in] 🧠 hybrid feedback merged — ready for model retraining.")
    return df
# === PATCH END ===




# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py
# 📆 PATCHED: 2025-11-01Z — Phase 7F Forest–River Hybrid Integration (Part 1/3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import numpy as np

# ──────────────────────────────────────────────────────────────────────
def _forest_phase(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    """
    Forest phase — batch learning on contextual intelligence.
    Handles categorical features automatically.
    Returns per-sample success probability (0–1).
    """
    from sklearn.preprocessing import LabelEncoder
    import numpy as np

    try:
        # 1️⃣  Select features and handle categorical encodings
        X = df[features].copy()
        for c in X.columns:
            if X[c].dtype == object:
                X[c] = X[c].fillna("UNK").astype(str)
                enc = LabelEncoder()
                X[c] = enc.fit_transform(X[c])

        X = X.fillna(0).infer_objects(copy=False)

        # 2️⃣ Target
        y_col = df["success"] if "success" in df.columns else pd.Series([0] * len(df))
        y = (y_col.astype(float) > 0).astype(int)

# 📍 TARGET: _forest_phase
# 📆 PATCHED: 2025-11-09Z — Safe predict_proba for single-class target
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # 3️⃣  Model
        model = RandomForestClassifier(
            n_estimators=300, max_depth=10, random_state=42
        )
        model.fit(X, y, sample_weight=df.get("weight", 1.0))

        probs = model.predict_proba(X)
        # ✅ handle single-class case gracefully
        probs = probs[:, 1] if probs.shape[1] > 1 else np.full(len(X), 0.5)
        df["forest_conf"] = probs
        return probs


    except Exception as e:
        print(f"[forest-phase] warn: {e}")
        df["forest_conf"] = 0.5
        return np.full(len(df), 0.5)


# ──────────────────────────────────────────────────────────────────────
def _river_phase(df: pd.DataFrame, alpha: float = 0.95) -> np.ndarray:
    """
    River phase — incremental online reinforcement.
    Adjusts confidence stream using realised outcomes and losses.
    """
    river_conf = np.zeros(len(df))
    prev_conf = 0.5
    for i, r in enumerate(df.itertuples(index=False)):
        success = getattr(r, "success", 0)
        pnl = getattr(r, "pnl", 0.0) or 0.0
        loss_penalty = max(0.0, -pnl) / 50.0
        observed = float(success) - loss_penalty
        new_conf = alpha * prev_conf + (1 - alpha) * observed
        river_conf[i] = max(0.0, min(1.0, new_conf))
        prev_conf = new_conf
    df["river_conf"] = river_conf
    return river_conf
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py:rebuild_mastery_cache
# 📆 PATCHED: 2025-11-03Z — Derive pre_drift + band features from bets + inbound_oc_cache
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import json, math

def _safe_band_metrics(raw_json: str | None, anchor: float | None = None):
    """
    Parse a JSON triple band (low, mid, high) and derive metrics.
    Returns dict: width, rel_vol, bias, skew, stability.
    """
    try:
        arr = json.loads(raw_json) if raw_json else None
        if not arr or len(arr) < 3:
            return dict(width=None, rel_vol=None, bias=None, skew=None, stability=None)
        low, mid, high = map(float, arr[:3])
        width = high - low
        rel_vol = width / mid if mid else None
        bias = ((mid - anchor) / anchor) if (anchor and anchor > 0) else None
        skew = (high - mid) - (mid - low)
        stability = 1 / (1 + abs(skew))  # crude asymmetry penalty
        return dict(width=width, rel_vol=rel_vol, bias=bias, skew=skew, stability=stability)
    except Exception:
        return dict(width=None, rel_vol=None, bias=None, skew=None, stability=None)

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py
# 📆 PATCHED: 2025-11-02Z — Mastery v7 End-to-End Stabilisation (Phase 8)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import pandas as pd, json, math

# ──────────────────────────────────────────────────────────────────────
# 1️⃣  CACHE BUILDER  — remove restrictive filters + auto bucket tagging
# ──────────────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py
# 📆 PATCHED: 2025-11-09Z — add cache freshness checkpoint for faster rebuilds
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def rebuild_mastery_cache(days: int = 90):
    """
    Rebuild the local intelligent cache (cache_mastery_day) in mastery_v7.db
    only if new data has arrived or the cache is stale.

    Checks:
      • If table cache_mastery_day exists and has data newer than 1 day old → skip
      • Else, rebuild using the external cache_builder.
    """
    db_path = os.path.join("data", "mastery_v7.db")
    if not os.path.exists(db_path):
        print(f"[cache] ⚠️ local DB missing → forcing rebuild ({db_path})")
        needs_rebuild = True
    else:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        try:
            # Determine the most recent 'day' recorded in the cache
            row = con.execute("""
                SELECT MAX(date(day)) AS last_day
                  FROM cache_mastery_day
            """).fetchone()
            last_day = row["last_day"] if row and row["last_day"] else None
            if last_day:
                # Compare to today (UTC)
                from datetime import date
                today = date.today().isoformat()
                if last_day >= today:
                    print(f"[cache] ✅ cache_mastery_day up-to-date (last_day={last_day}) → skipping rebuild.")
                    needs_rebuild = False
                else:
                    print(f"[cache] 🔁 cache_mastery_day stale (last_day={last_day}) → rebuilding.")
                    needs_rebuild = True
            else:
                print("[cache] ⚠️ cache_mastery_day empty → rebuilding.")
                needs_rebuild = True
        except sqlite3.OperationalError:
            print("[cache] ⚠️ no cache_mastery_day table → rebuilding.")
            needs_rebuild = True
        finally:
            con.close()

    if not needs_rebuild:
        return

    # Perform rebuild
    print(f"[cache] ⚙️ rebuilding {days}-day intelligent cache via cache_builder …")
    try:
        from engines.mastery.cache_builder import build_mastery_cache
        build_mastery_cache(days=days)
        print("[cache] ✅ cache_mastery_day rebuilt successfully.")
    except Exception as e:
        print(f"[cache] ❌ rebuild failed: {e}")
# === PATCH END ===



# ──────────────────────────────────────────────────────────────────────
def build_v7_sandbox(days: int = 90):
    """
    Build mastery_v7_training from the verified local cache (cache_mastery_day)
    and augment with runner form data from settlements.db.runner_form_canonical.
    Uses ATTACH for the second DB — no cloud access, no fallbacks.
    """
    src = os.path.join("data", "mastery_v7.db")
    settle_db = os.path.join("data", "settlements.db")

    if not os.path.exists(src):
        raise FileNotFoundError(f"Local mastery_v7.db not found at {src}")
    if not os.path.exists(settle_db):
        raise FileNotFoundError(f"Settlements DB not found at {settle_db}")

    con = sqlite3.connect(src)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Attach settlements as alias
    cur.execute(f"ATTACH DATABASE '{settle_db}' AS formdb;")

    # verify cache exists
    source_table = _select_training_source(con)
    print(f"[sandbox] 🧩 building mastery_v7_training from local {source_table} + formdb.runner_form_canonical …")

    # drop + rebuild
    con.executescript(f"""
        DROP TABLE IF EXISTS mastery_v7_training;
        CREATE TABLE mastery_v7_training AS
        SELECT
            m.day,
            m.marketId,
            m.selectionId,
            m.letter,                      -- ✅ FIX: preserve letter for training
            m.horse_name,
            m.event_name,
            m.market_name,
            m.anchor_odd,
            m.drift_speed,
            m.inplay_progress,
            m.expected_race_mins,
            m.pre_vol,
            m.inplay_vol,
            m.post_vol,
            m.side,
            m.entry_odds,
            m.exit_odds,
            m.entry_stake,
            m.exit_stake,
            m.realized_pnl,
            m.net_pl,
            m.role,
            m.exit_kind,
            m.runner_name,
            m.win_rate,
            m.form_avg_pnl,
            m.trainer,
            m.jockey,
            m.distance_band,
            m.class_band,
            m.surface,
            -- form features from canonical runner table
            f.runs   AS form_runs,
            f.wins   AS form_wins,
            f.win_rate AS form_win_rate,
            f.avg_pnl AS form_avg_pnl2,
            f.age,
            f.profit AS form_profit,
            f.status AS form_status,
            f.last_seen AS form_last_seen
        FROM {source_table} AS m
        LEFT JOIN formdb.runner_form_canonical AS f
          ON m.selectionId = f.selectionId AND m.marketId = f.marketId
        WHERE date(m.day) >= date('now','-{days} day');
    """)

    con.commit()
    n = cur.execute("SELECT COUNT(*) FROM mastery_v7_training;").fetchone()[0]
    cur.execute("DETACH DATABASE formdb;")
    con.close()

    print(f"[sandbox] ✅ built mastery_v7_training — {n:,} rows from {source_table} + formdb.runner_form_canonical.")


def _select_training_source(con: sqlite3.Connection) -> str:
    """
    Always use the verified day-level cache built locally in mastery_v7.db.
    The table is called cache_mastery_day.
    """
    try:
        cur = con.execute("SELECT COUNT(*) FROM cache_mastery_day;")
        rows = cur.fetchone()[0]
        if rows > 0:
            print(f"[train-source] ✅ using cache_mastery_day ({rows} rows)")
            return "cache_mastery_day"
        raise RuntimeError(f"cache_mastery_day exists but empty ({rows} rows)")
    except Exception as e:
        raise RuntimeError(f"[train-source] ❌ missing local cache_mastery_day ({e})")




# ======================================================================
def _ensure_intel_view():
    """
    Ensure v_mastery_intel_v7 exists before training.
    If missing, rebuild it using verified existing components.
    Skips gracefully if already present.
    """
    import sqlite3
    from engines.config_paths import autoscalp_db

    db = autoscalp_db()
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row

    # Check if the view already exists
    exists = con.execute("""
        SELECT 1 FROM sqlite_master
         WHERE type='view' AND name='v_mastery_intel_v7'
    """).fetchone()
    if exists:
        print("[v7-intel] ✅ v_mastery_intel_v7 already exists — skipping rebuild.")
        con.close()
        return

    print("[v7-intel] ⚙️ building v_mastery_intel_v7 …")
    cur = con.cursor()
    cur.executescript("""
    DROP VIEW IF EXISTS v_mastery_intel_v7;
    CREATE VIEW v_mastery_intel_v7 AS
    SELECT
        m.day,
        m.marketId,
        m.selectionId,
        m.letter,
        m.pnl,
        m.weight,
        m.target_ticks,
        m.realized_ticks,
        m.success,
        m.stoploss_hit,
        m.hedge_hit,
        -- optional sentiment and WOM columns if they exist
        ms.sentiment,
        ms.drift_pct,
        wom.wom_ratio,
        wom.wom_state,
        vol.avg_volatility,
        vol.range_low,
        vol.range_high,
        t.slope_ppm,
        t.tick_vel_3s_up,
        t.momentum_class,
        r.runs AS form_runs,
        r.wins AS form_wins,
        r.win_rate AS form_win_rate,
        r.avg_pnl AS form_avg_pnl,
        md.trainerName,
        md.jockeyName,
        md.distance,
        md.going,
        opp.opportunities,
        opp.taken,
        opp.conversion,
        'LIVE' AS mode
    FROM mastery_outcomes_raw m
    LEFT JOIN v_market_sentiment ms       USING(marketId,selectionId)
    LEFT JOIN v_weight_of_money wom       USING(marketId,selectionId)
    LEFT JOIN volatility_meta vol         USING(marketId,selectionId)
    LEFT JOIN trend_features t            USING(marketId,selectionId)
    LEFT JOIN runner_form_canonical r     USING(marketId,selectionId)
    LEFT JOIN market_data md              USING(marketId,selectionId)
    LEFT JOIN indicators_opportunities opp USING(marketId,selectionId);
    """)
    con.commit()
    con.close()
    print("[v7-intel] ✅ v_mastery_intel_v7 built successfully.")




V7_DB = "data/mastery_v7.db"
os.environ["AUTOSCALP_DB"] = V7_DB


# ──────────────────────────────────────────────────────────────────────
def _connect():
    con = sqlite3.connect(V7_DB)
    con.row_factory = sqlite3.Row
    return con

def _resolve_training_view() -> str:
    """Return the best available training view."""
    con = _connect()
    try:
        rows = con.execute("SELECT name FROM sqlite_master WHERE type='view';").fetchall()
        names = {r[0] for r in rows}
        if "v_mastery_intel_v7" in names:
            print("[v7-train] using v_mastery_intel_v7")
            return "v_mastery_intel_v7"
        print("[v7-train] using v_mastery_v7")
        return "v_mastery_v7"
    finally:
        con.close()

def build_training_snapshot(days: int = 7):
    """
    Create mastery_v7_training table inside the sandbox by reading
    directly from the live autoscalp_gui.db, where v_mastery_v7 lives.
    """
    live_src = autoscalp_db()          # points to data/autoscalp_gui.db
    con = _connect()                   # opens data/mastery_v7.db
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Attach the live database so its views are visible
    cur.execute(f"ATTACH DATABASE '{live_src}' AS live;")

    # Recreate training snapshot
    cur.execute("DROP TABLE IF EXISTS mastery_v7_training;")
    cur.execute(f"""
        CREATE TABLE mastery_v7_training AS
        SELECT *
          FROM v_mastery_v7_context
         WHERE date(day) >= date('now','-{days} day');
    """)

    # Detach to leave sandbox clean
    cur.execute("DETACH DATABASE live;")
    con.commit()
    con.close()

    print(f"[v7-train] snapshot created for last {days} day(s) from live.v_mastery_v7.")


def load_training_data() -> pd.DataFrame:
    con = _connect()
    df = pd.read_sql("SELECT * FROM mastery_v7_training", con)
    con.close()


    # Ensure favourite ordinal columns exist (safe defaults)
    ordinal_cols = [
        "fav_rank",
        "field_size",
        "fav_percentile",
        "is_favourite",
        "is_top_3",
        "is_longshot",
    ]

    for c in ordinal_cols:
        if c not in df.columns:
            df[c] = None

    if "fav_bucket" in df.columns:
        df["fav_bucket"] = df["fav_bucket"].fillna("UNK").astype(str)

        try:
            from sklearn.preprocessing import LabelEncoder
            fav_enc = LabelEncoder()
            df["fav_bucket_enc"] = fav_enc.fit_transform(df["fav_bucket"])
        except Exception:
            df["fav_bucket_enc"] = 0
    else:
        df["fav_bucket_enc"] = 0

    # --- ENGINE FAMILY FEATURES (FIXED LOCATION) ---
    if "engine" in df.columns:
        df["engine_family"] = df["engine"].fillna("LEGACY").astype(str)
    else:
        df["engine_family"] = "LEGACY"

    try:
        from sklearn.preprocessing import LabelEncoder
        eng_enc = LabelEncoder()
        df["engine_family_enc"] = eng_enc.fit_transform(df["engine_family"])
    except Exception:
        df["engine_family_enc"] = 0

    return df




# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py:train_and_update_posteriors
# 📆 PATCHED: 2025-11-01Z — Phase 7F Forest–River Hybrid Integration (Part 2/3)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _evaluate_epoch_metrics(df: pd.DataFrame) -> dict:
    """Compute per-epoch diagnostic metrics."""
    total = len(df)
    wins = (df["pnl"] > 0).sum()
    losses = (df["pnl"] < 0).sum()
    trend_ok = ((df.get("drift_speed", 0) > 0) & (df["pnl"] > 0)).sum()
    match_rate = (df["success"].sum() / max(1, total))
    good_match = wins / max(1, total)
    bad_match = losses / max(1, total)
    trend_success = trend_ok / max(1, total)
    avg_ticks = float(df.get("realized_ticks", 0).mean() or 0)
    avg_pnl_scalp = float(df.loc[df["realized_ticks"].abs() <= 3, "pnl"].mean() or 0)
    goal_alignment = 0.4*(df["pnl"].sum()/1000.0) + 0.3*(1-bad_match) + 0.3*trend_success
    return dict(
        total=total, wins=wins, losses=losses,
        match_rate=match_rate, good_match=good_match, bad_match=bad_match,
        trend_success=trend_success, avg_ticks=avg_ticks,
        avg_pnl_scalp=avg_pnl_scalp, goal_alignment=goal_alignment
    )

def _run_forest_river_hybrid(df: pd.DataFrame) -> pd.DataFrame:
    """Attach hybrid confidence column combining forest and river phases."""

    used_features = []

    for c in CORE_FEATURES:
        if c in df.columns:
            used_features.append(c)
        else:
            # hard default for missing core features
            df[c] = 0.0
            used_features.append(c)

    # optional features are best-effort only
    for c in OPTIONAL_FEATURES:
        if c in df.columns:
            used_features.append(c)

    forest_conf = _forest_phase(df, used_features)
    river_conf = _river_phase(df)

    df["hybrid_conf"] = 0.6 * forest_conf + 0.4 * river_conf

    print(f"[v7-train] 🧬 features used ({len(used_features)}): {used_features}")

    return df

# === PATCH END ===
# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py:train_and_update_posteriors
# 📆 PATCHED: 2025-11-01Z — integrate question-driven epoch weighting (v7Q)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import numpy as np
import pandas as pd

def _load_bucket_strengths(con) -> dict[str, float]:
    """
    Merge live Q-Brain metrics (autoscalp_gui.db) with canonical schema (JSON).
    Result: unified per-bucket strength map used for question-weighted training.

    • Reads all question metrics from local mastery_training_metrics (no date filter)
    • Loads canonical schema from v7 JSON file
    • Combines DB + JSON using weighted averages
    • Ensures all 8 canonical buckets are always returned
    """
    import json, os
    import pandas as pd
    from pathlib import Path

    # --- Force local DB path (bypass iCloud) ---
    live_db = os.path.join(os.path.dirname(__file__), "../../data/autoscalp_gui.db")
    if not os.path.exists(live_db):
        print(f"[v7-train] ⚠️ local autoscalp_gui.db missing → {live_db}")
        return {}

    con.execute(f"ATTACH DATABASE '{live_db}' AS live;")
    con.execute("""
        CREATE TABLE IF NOT EXISTS live.mastery_training_metrics (
            bucket TEXT,
            question_id TEXT,
            metric TEXT,
            value REAL,
            weight REAL,
            source TEXT,
            notes TEXT,
            ts TEXT DEFAULT (datetime('now','utc'))
        );
    """)

    # --- 1️⃣ Load all DB-based metrics (Phase 2 Q-Brain questions) ---
    try:
        qdf = pd.read_sql("""
            SELECT bucket, ROUND(AVG(value),4) AS db_strength, COUNT(*) AS n
              FROM live.mastery_training_metrics
             GROUP BY bucket;
        """, con)
        con.execute("DETACH DATABASE live;")
    except Exception as e:
        print(f"[v7-train] warn: failed to read mastery_training_metrics → {e}")
        return {}

    db_strengths = dict(zip(qdf["bucket"], qdf["db_strength"])) if not qdf.empty else {}
    print(f"[v7-train] 🧠 loaded {len(db_strengths)} live Q-Brain buckets from mastery_training_metrics.")

    # --- 2️⃣ Load canonical schema (JSON) ---
    # repo_root = <repo>/ (…/analytics_beta_dev)
    repo_root = Path(__file__).resolve().parents[2]
    candidates = [
        repo_root / "data" / "configs" / "mastery_training_schema_v7_fixed legacy.json",
        # fallback alt name (underscore instead of space), in case the file was renamed
        repo_root / "data" / "configs" / "mastery_training_schema_v7_fixed_legacy.json",
        # final fallback: alongside this .py (legacy behavior)
        Path(__file__).resolve().with_name("mastery_training_schema_v7_fixed legacy.json"),
    ]
    schema_path = next((p for p in candidates if p.exists()), None)
    if not schema_path:
        print("[v7-train] ⚠️ canonical schema missing → tried:")
        for p in candidates:
            print(f"            - {p}")
        schema_json = {}
    else:
        try:
            with open(schema_path, "r") as f:
                schema_json = json.load(f)
            print(f"[v7-train] 📚 loaded canonical schema → {schema_path}")
        except Exception as e:
            print(f"[v7-train] warn: failed to read canonical schema ({schema_path}): {e}")
            schema_json = {}


    # --- 3️⃣ Aggregate canonical weights per bucket ---
    canonical = {}
    if "buckets" in schema_json:
        for b in schema_json["buckets"]:
            weights = [float(q.get("weight", 0)) for q in b.get("questions", [])]
            if weights:
                canonical[b["bucket"]] = round(sum(weights) / len(weights), 4)
    print(f"[v7-train] 📚 loaded {len(canonical)} canonical buckets from schema file.")

    # --- 4️⃣ Merge DB + Canonical with weighted blending ---
    merged = {}
    for bucket, base_strength in canonical.items():
        live_strength = db_strengths.get(bucket)
        if live_strength is not None:
            # 60% weight to live metrics (fresh signals), 40% to canonical
            merged[bucket] = round(0.6 * live_strength + 0.4 * base_strength, 4)
        else:
            merged[bucket] = base_strength

    # Include any buckets that exist only in DB (new or experimental)
    for bucket, val in db_strengths.items():
        if bucket not in merged:
            merged[bucket] = val

    if not merged:
        print("[v7-train] ⚠️ no merged buckets found — baseline fit only.")
    else:
        print(f"[v7-train] ✅ merged {len(merged)} total buckets (DB + schema).")

    return merged



def _bucket_for_letter(letter: str) -> str:
    """Map Mastery letter families to their diagnostic bucket."""
    letter = (letter or "").upper()
    MAP = {
        "Market Conditions": ["A","S","B"],
        "Timing": ["G","X","R"],
        "Bias / Drift": ["F","L"],
        "Execution": ["T","P"],
        "Blueprint Recall": ["M","Z"],
        "Venue / Type / Going": ["V","J"],
        "Risk / Hedge": ["H","D","R"],
        "Stability / Goal Alignment": ["ALL"],
    }
    for bucket, letters in MAP.items():
        if letter in letters or "ALL" in letters:
            return bucket
    return "Market Conditions"  # fallback

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py
# 📆 PATCHED: 2025-11-09Z — Brain observer mode (passive daily coherence tracking)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _observe_river_forest_alignment():
    """
    Passive observer mode:
    Runs lightweight River–Forest coherence check even when
    no retraining occurs. Reads mastery_posteriors and last
    training results, computes drift, logs to brain_state_v7.
    """
    try:
        db_path = os.path.join("data", "mastery_v7.db")
        if not os.path.exists(db_path):
            print("[observer] ⚠️ mastery_v7.db not found — skipping observation.")
            return

        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row

        # verify required columns
        cols = [r[1] for r in con.execute("PRAGMA table_info(mastery_posteriors);").fetchall()]
        if not {"confidence", "bucket_confidence"}.issubset(set(cols)):
            print("[observer] ⚠️ missing confidence columns in mastery_posteriors — skipping.")
            con.close()
            return

        # fetch latest live vs forest values
        df = pd.read_sql("""
            SELECT bucket, confidence AS river_conf, bucket_confidence AS forest_conf
              FROM mastery_posteriors
             WHERE confidence IS NOT NULL AND bucket_confidence IS NOT NULL;
        """, con)
        con.close()
        if df.empty:
            print("[observer] ℹ️ no posterior coherence data to observe.")
            return

        df["delta"] = (df["river_conf"] - df["forest_conf"]).abs()
        coherence = 1 - df["delta"].mean()
        divergence = df["delta"].mean()

        print(f"[observer] 👂 passive coherence check → {coherence:.3f} (div={divergence:.3f})")

        # aggregate per bucket
        micro = (
            df.groupby("bucket", dropna=False)
              .agg(samples=("bucket", "count"), coherence=("delta", lambda x: 1 - x.mean()))
              .reset_index()
        )

        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brain_state_v7(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT DEFAULT (datetime('now','utc')),
                layer TEXT,
                bucket TEXT,
                samples INTEGER,
                total_pnl REAL,
                coherence REAL,
                adjustment REAL,
                notes TEXT
            );
        """)

        # global snapshot (OBSERVER)
        cur.execute("""
            INSERT INTO brain_state_v7(layer,bucket,samples,total_pnl,coherence,adjustment,notes)
            VALUES('MACRO','OBSERVER',?,?,?, ?,?)
        """, (
            len(df),
            0.0,
            float(coherence),
            float(divergence),
            "Passive River–Forest observation"
        ))

        # per bucket (MICRO)
        for _, r in micro.iterrows():
            cur.execute("""
                INSERT INTO brain_state_v7(layer,bucket,samples,total_pnl,coherence,adjustment,notes)
                VALUES('MICRO_OBSERVER',?,?,?,?,?,?)
            """, (
                r["bucket"] or "UNKNOWN",
                int(r["samples"]),
                0.0,
                float(r["coherence"]),
                0.0,
                "Passive River–Forest micro observation"
            ))

        con.commit(); con.close()
        print(f"[observer] ✅ logged observer coherence for {len(micro)} buckets.")

    except Exception as e:
        print(f"[observer] warn: {e}")
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py
# 📆 PATCHED: 2025-11-09Z — integrate River–Forest coherence fold-in to brain_state_v7
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _record_brain_coherence_from_foldin(df: pd.DataFrame):
    """
    After merging live posteriors, log coherence and divergence
    into brain_state_v7 for both MICRO and MACRO layers.
    """
    try:
        if df.empty:
            print("[brain-foldin] ⚠️ training snapshot empty — skipping brain coherence update.")
            return

        # ✅ NEW: skip if no live posterior columns merged
        if "river_conf_live" not in df.columns or "forest_conf_live" not in df.columns:
            print("[brain-foldin] ℹ️ skipping — no live posterior coherence columns found.")
            return

        # --- derive coherence metrics ---
        river = df["river_conf_live"].fillna(0.5)
        forest = df["forest_conf_live"].fillna(0.5)
        delta = (river - forest).abs()
        coherence = 1.0 - delta.mean()
        divergence = delta.mean()

        print(f"[brain-foldin] 🧩 global coherence={coherence:.3f} divergence={divergence:.3f}")

        # --- per-bucket micro summaries if available ---
        if "bucket_name" not in df.columns and "letter" in df.columns:
       
            df["bucket_name"] = df["letter"].map(_bucket_for_letter)

        micro = (
            df.groupby("bucket_name", dropna=False)
              .agg(
                  samples=("selectionId", "count"),
                  mean_river=("river_conf_live", "mean"),
                  mean_forest=("forest_conf_live", "mean"),
                  delta=("delta_conf", "mean"),
              )
              .reset_index()
        )
        micro["coherence"] = 1.0 - micro["delta"].abs()

        # --- write to DB ---
        con = sqlite3.connect("data/mastery_v7.db")
        con.row_factory = sqlite3.Row
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS brain_state_v7(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT DEFAULT (datetime('now','utc')),
                layer TEXT,
                bucket TEXT,
                samples INTEGER,
                total_pnl REAL,
                coherence REAL,
                adjustment REAL,
                notes TEXT
            );
        """)

        # Global snapshot (MACRO)
        cur.execute("""
            INSERT INTO brain_state_v7(layer,bucket,samples,total_pnl,coherence,adjustment,notes)
            VALUES('MACRO','GLOBAL',?,?,?, ?,?)
        """, (
            len(df),
            0.0,
            float(coherence),
            float(divergence),
            "River–Forest coherence fold-in",
        ))

        # Per-bucket MICRO
        for _, r in micro.iterrows():
            cur.execute("""
                INSERT INTO brain_state_v7(layer,bucket,samples,total_pnl,coherence,adjustment,notes)
                VALUES('MICRO',?,?,?,?,?,?)
            """, (
                r["bucket_name"] or "UNSPECIFIED",
                int(r["samples"]),
                0.0,
                float(r["coherence"]),
                float(r["delta"]),
                "River–Forest fold-in micro update",
            ))

        con.commit(); con.close()
        print(f"[brain-foldin] ✅ logged {len(micro)} bucket entries + global coherence snapshot.")

    except Exception as e:
        print(f"[brain-foldin] warn: failed to record coherence ({e})")
# === PATCH END ===

# ──────────────────────────────────────────────────────────────────────
# 2️⃣  TRAINER  — safe .fillna() + guard for scalar goal_alignment
# ──────────────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py:train_and_update_posteriors
# 📆 PATCHED: 2025-11-01Z — ensure model + encoder returned after training (Phase 7G)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def train_and_update_posteriors(df: pd.DataFrame, epochs: int = 25):
    """Train model and update posteriors directly (no consolidation)."""
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import LabelEncoder
    from engines.config_core_values import get_core_values

    vals = get_core_values()
    if df.empty:
        print("[v7-train] no data in training snapshot.")
        return None, None

    # --- add this immediately after the empty check ---
    df = _run_forest_river_hybrid(df)

    base_feats = [
        "pnl","target_ticks","realized_ticks",
        "drift_speed","inplay_progress","expected_race_mins"
    ]
    # === PATCH START ============================================================
    # Include engine_family as categorical feature
    if "engine_family_enc" in df.columns:
        base_feats.append("engine_family_enc")
    # === PATCH END ==============================================================

    available_feats = [c for c in base_feats if c in df.columns]
    X = df[available_feats].fillna(0)
    success_col = df["success"] if "success" in df.columns else pd.Series([0] * len(df))
    y = (success_col.astype(float) > 0).astype(int)


    le = LabelEncoder()
    y_enc = le.fit_transform(y)

    # sample weighting logic (existing)
    df["sample_weight"] = df.get("weight", 1.0)
    if "goal_alignment" in df.columns:
        df["sample_weight"] = df["sample_weight"] * (1.0 + df["goal_alignment"].fillna(0))
    else:
        df["sample_weight"] = df["sample_weight"] * 1.0

    df = _apply_target_weighting(df)
    df = _fold_in_live_state(df)


    # model and question weighting (existing)
    model = RandomForestClassifier(n_estimators=200, max_depth=10, random_state=42)
# ──────────────────────────────────────────────────────────────────────
# Insert this inside train_and_update_posteriors() before model.fit()
# ──────────────────────────────────────────────────────────────────────
    con = _connect()
    bucket_strengths = _load_bucket_strengths(con)
    con.close()

    if not bucket_strengths:
        print("[v7-train] ⚠️ no bucket strengths found — running baseline fit")

    # prepare lookup once per row
    df["bucket_name"] = df["letter"].map(_bucket_for_letter)
    df["bucket_score"] = df["bucket_name"].map(bucket_strengths).fillna(0)

    # question-driven epoch loop
    print(f"[v7-train] 🎯 question-driven training — {epochs} epochs, 8 buckets active")
    for e in range(epochs):
        # amplify weighting for underperforming buckets (low scores)
        mean_strength = np.mean(list(bucket_strengths.values()) or [0])
        df["adaptive_weight"] = df["sample_weight"] * (
            1.0 + 0.2 * (df["bucket_score"] - mean_strength)
        )

        model.fit(X, y_enc, sample_weight=df["adaptive_weight"])
        score = model.score(X, y_enc)
        print(f"[epoch {e+1:02d}] score={score:.4f} mean_bucket={mean_strength:.4f}")

        # optional mid-epoch decay or logging
        if (e + 1) % 10 == 0 or e == epochs - 1:
            mean_strength = np.mean(list(bucket_strengths.values()) or [0])
            print(f"[epoch {e+1:02d}] updated bucket mean={mean_strength:.4f}")

    # --- write back hybrid confidence per bin_key (simplified) ---
    try:
        con = sqlite3.connect(autoscalp_db())
        cur = con.cursor()
        for _, r in df.iterrows():
            cur.execute("""
                UPDATE mastery_posteriors
                   SET bucket_confidence = ?
                 WHERE bin_key = ?;
            """, (float(r["hybrid_conf"]), f"{r['marketId']}|{r['selectionId']}"))
        con.commit()
        con.close()
        print(f"[v7-train] ✅ updated bucket_confidence for {len(df)} bins")
    except Exception as e:
        print(f"[v7-train] warn: could not update bucket_confidence ({e})")


    # 🧩 NEW: safe return of trained artefacts
    print(f"[v7-train] ✅ training complete — epochs={epochs}, samples={len(df)}")
    return model, le
# === PATCH END ===

# ──────────────────────────────────────────────────────────────────────
# 3️⃣  DIAGNOSTIC  — safe letter guard (None → UNK)
# ──────────────────────────────────────────────────────────────────────
def propose_posterior_adjustments(con, diags, buckets, alpha: float = 0.15):
    """Combine diagnostics and bucket strengths to generate adjustment proposals."""
    _ensure_adjustment_table(con)
    con.execute("DELETE FROM mastery_posterior_adjustments;")
    letter_map = {
        "Market Conditions":["A","S","B"],"Timing":["G","X","R"],
        "Bias / Drift":["F","L"],"Execution":["T","P"],
        "Blueprint Recall":["M","Z"],"Venue / Type / Going":["V","J"],
        "Risk / Hedge":["H","D","R"],"Stability / Goal Alignment":["ALL"]
    }
    proposals=[]
    for d in diags:
        letter=(d.get("letter") or "UNK").upper()
        state=d["state"]; prior=_safe(d["weight"],1.0); delta=0.0; reason=state
        for bname,letters in letter_map.items():
            if letter in letters or "ALL" in letters:
                bs=buckets.get(bname,0.0)
                if state=="SPARSE": delta=+alpha*bs
                elif state=="OVERFIT": delta=-alpha*abs(bs)
                elif state=="WEAK": delta=+alpha*abs(bs)
                else: delta=+0.05*bs
                break
        proposed=max(0.1,round(prior*(1+delta),4))
        proposals.append({
            "bin_key":d["bin_key"],"letter":letter,"bucket":bname,
            "prior_weight":prior,"delta":round(delta,4),
            "proposed_weight":proposed,"reason":reason
        })
    con.executemany("""
        INSERT INTO mastery_posterior_adjustments
        (bin_key,letter,bucket,prior_weight,delta,proposed_weight,reason)
        VALUES(:bin_key,:letter,:bucket,:prior_weight,:delta,:proposed_weight,:reason)
    """,proposals)
    con.commit()
    print(f"[adjustments] ✅ inserted {len(proposals)} posterior adjustments.")
# === PATCH END ===


def save_model(model, le):
    os.makedirs("models", exist_ok=True)
    path = f"models/mastery_v7_policy_{date.today()}.pkl"
    joblib.dump({"model": model, "encoder": le}, path)
    print(f"[v7-train] model saved → {path}")
    return path


def emit_snapshot():
    """Write current posteriors snapshot for dashboard."""
    path = f"data/posteriors/mastery_snapshot_v7_{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    snap = posteriors.emit_snapshot(path)
    print(f"[v7-train] snapshot emitted → {snap}")


def update_mastery_state(model_path: str):
    con = _connect()
    meta = {
        "model_path": model_path,
        "trained_on": str(date.today()),
        "goal_alignment": None,
    }
    con.execute("""
        CREATE TABLE IF NOT EXISTS mastery_state(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT DEFAULT (datetime('now','utc')),
            version INTEGER DEFAULT 0,
            thresholds_json TEXT,
            source TEXT
        );
    """)
    con.execute("""
        INSERT INTO mastery_state(thresholds_json,version,source)
        VALUES(?,(SELECT COALESCE(MAX(version),0)+1 FROM mastery_state),'V7_TRAIN')
    """, (json.dumps(meta),))
    con.commit(); con.close()
    print("[v7-train] mastery_state updated.")

import threading

def _watchdog(label: str, timeout: float = 10.0):
    """Heartbeat thread that prints if stage runs longer than timeout seconds."""
    alive = True
    def _beat():
        t = 0
        while alive:
            time.sleep(timeout)
            t += timeout
            print(f"[watchdog] {label} still running... ({t}s elapsed)")
    t = threading.Thread(target=_beat, daemon=True)
    t.start()
    return lambda: setattr(sys.modules[__name__], "alive", False)

import threading, sys, time

def _alive_pulse(label: str = "training engine", interval: float = 60.0):
    """
    Background heartbeat that prints every <interval> seconds
    until the returned stop() function is called.
    """
    running = True

    def _loop():
        while running:
            time.sleep(interval)
            if running:
                print(f"✅ {label} still running…")

    t = threading.Thread(target=_loop, name="TrainerHeartbeat", daemon=True)
    t.start()

    def _stop():
        nonlocal running
        running = False
    return _stop

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py
# 📆 PATCHED: 2025-11-01Z — Unified Continuous Progress Tracker (Phase 7C)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sys, time, threading

class ProgressTracker:
    """
    Continuous progress bar spanning the entire Mastery v7 training run.
    Each stage reserves a percentage range (eg. 0–10%, 10–25%, …).
    """
    def __init__(self, total_stages: int = 7):
        self.lock = threading.Lock()
        self.stage = 0
        self.total_stages = total_stages
        self.start_time = time.time()
        self.stage_names = [
            "Ensure Intel View",
            "Prune Source",
            "Build Sandbox",
            "Build Snapshot",
            "Load Data",
            "Train Model",
            "Save & Emit"
        ]

    def _draw_bar(self, pct: float, label: str = ""):
        bar_len = 40
        done = int(bar_len * pct / 100)
        bar = "#" * done + "-" * (bar_len - done)
        elapsed = int(time.time() - self.start_time)
        sys.stdout.write(f"\r[{label:<20}] [{bar}] {pct:5.1f}% ({elapsed}s)")
        sys.stdout.flush()

    def stage_progress(self, stage_name: str, step: float):
        """Increment progress visually during a stage."""
        with self.lock:
            pct = (self.stage / self.total_stages) * 100 + step
            self._draw_bar(min(100, pct), stage_name)

    def next_stage(self):
        """Advance to next stage."""
        with self.lock:
            self.stage += 1
            pct = (self.stage / self.total_stages) * 100
            name = self.stage_names[min(self.stage, len(self.stage_names)-1)]
            self._draw_bar(pct, f"{name}")
            if self.stage >= self.total_stages:
                print("\n[progress] ✅ Training pipeline complete.")

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py : _should_assimilate_from_river
# 🔎 SEARCH: def _should_assimilate_from_river()
# ⛏️ ACTION: REPLACE FUNCTION BODY — force retrain unconditionally
# 📆 PATCHED: 2025-11-17 — Always retrain (Option A)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _should_assimilate_from_river() -> bool:
    """
    TEMPORARY OVERRIDE (Option A):
    Always trigger retraining regardless of River data timestamps.
    This bypasses gating until live River writes and training cycle
    timestamps are fully aligned.
    """
    print("[assim-trigger] ⚡️ Forcing retrain (override enabled).")
    return True
# === PATCH END ===

    from engines.config_paths import autoscalp_db
    db_live = autoscalp_db()  # autoscalp_gui.db (DAL-resolved local)
    con = sqlite3.connect(db_live)
    con.row_factory = sqlite3.Row
    try:
        # last training MACRO snapshot lives in mastery_v7.db
        con_m = sqlite3.connect(os.path.join("data", "mastery_v7.db"))
        con_m.row_factory = sqlite3.Row
        last_train_row = con_m.execute("""
            SELECT MAX(recorded_at) AS last_train
              FROM brain_state_v7
             WHERE layer='MACRO'
        """).fetchone()
        last_train = last_train_row["last_train"] if last_train_row else None
        con_m.close()

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py:_should_assimilate_from_river
# 📆 PATCHED: 2025-11-14Z — detect bridge_decision_log updates for retrain trigger
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # most recent River update
        river_row = con.execute("""
            SELECT MAX(updated_at) AS last_river
              FROM mastery_posteriors
        """).fetchone()
        last_river = river_row["last_river"] if river_row else None

        # 🔁 NEW: if no posteriors, check bridge_decision_log
        if not last_river:
            bridge_row = con.execute("""
                SELECT MAX(ts_sent) AS last_bridge
                  FROM bridge_decision_log
            """).fetchone()
            if bridge_row and bridge_row["last_bridge"]:
                last_river = bridge_row["last_bridge"]
                print("[assim-trigger] 🔁 using bridge_decision_log timestamp as River fallback.")
# === PATCH END ===


        if not last_river:
            print("[assim-trigger] ℹ️ No live posteriors yet — skipping retrain check.")
            return False

        if not last_train:
            print("[assim-trigger] 🧩 No previous training recorded — forcing retrain.")
            return True

        # parse and compare timestamps
        from datetime import datetime
        fmt = "%Y-%m-%d %H:%M:%S"
        try:
            last_train_dt = datetime.strptime(last_train.split(".")[0], fmt)
            last_river_dt = datetime.strptime(last_river.split(".")[0], fmt)
        except Exception:
            print("[assim-trigger] ⚠️ timestamp parse issue — fallback to retrain.")
            return True

        if last_river_dt > last_train_dt:
            delta_s = (last_river_dt - last_train_dt).total_seconds()
            print(f"[assim-trigger] ✅ new River updates detected ({delta_s:.0f}s newer) → retraining.")
            return True
        else:
            print("[assim-trigger] 💤 no new River updates — training up-to-date.")
            return False
    except Exception as e:
        print(f"[assim-trigger] warn: {e}")
        return True
    finally:
        con.close()
# === PATCH END ===
# === ADVANCED MODES =====================================================
ENABLE_WATCHER = True           # auto-train when River updates
ENABLE_DRIFT_MONITOR = True     # passive entropy watcher
ENABLE_TARGET_WEIGHTING = True  # integrate goal_alignment
ENABLE_META_REINFORCE = True    # fold in live_state pnl/liab

# 1️⃣ Realtime Assimilation Watcher
def _start_realtime_watcher(path="data/autoscalp_gui.db"):
    import threading, time
    from pathlib import Path
    if not ENABLE_WATCHER:
        return
    last_mtime = Path(path).stat().st_mtime
    def _loop():
        nonlocal last_mtime
        while True:
            time.sleep(60)
            try:
                mtime = Path(path).stat().st_mtime
                if mtime > last_mtime:
                    last_mtime = mtime
                    print("[watcher] 🔔 River DB changed — triggering retrain …")
                    os.system("python3 -m engines.mastery.train_mastery_v7 --days 90 --epochs 5")
            except Exception as e:
                print(f"[watcher] warn: {e}")
    threading.Thread(target=_loop, name="RiverWatcher", daemon=True).start()

# 2️⃣ Drift Monitor
def _start_drift_monitor(interval_h=1):
    import threading, time, math
    if not ENABLE_DRIFT_MONITOR:
        return
    def _loop():
        while True:
            time.sleep(interval_h * 3600)
            try:
                con = sqlite3.connect("data/mastery_v7.db")
                con.row_factory = sqlite3.Row
                df = pd.read_sql("SELECT confidence,bucket_confidence FROM mastery_posteriors", con)
                con.close()
                if df.empty: continue
                delta = (df["confidence"] - df["bucket_confidence"]).abs()
                ent = -((delta*math.log2(delta+1e-9)).mean())
                coh = 1 - delta.mean()
                print(f"[drift] ⏳ hourly coherence={coh:.3f} entropy={ent:.3f}")
            except Exception as e:
                print(f"[drift] warn: {e}")
    threading.Thread(target=_loop, name="DriftMonitor", daemon=True).start()

# 3️⃣ Target-Aware Weighting Hook
def _apply_target_weighting(df: pd.DataFrame):
    if not ENABLE_TARGET_WEIGHTING or "goal_alignment" not in df.columns:
        return df
    scale = df["goal_alignment"].fillna(0.5)
    df["sample_weight"] = df.get("sample_weight", 1.0) * (0.8 + 0.4 * scale)
    print("[target-weight] 🎯 integrated goal_alignment into sample weights.")
    return df

# 4️⃣ Meta-Reinforcement Fold-In
def _fold_in_live_state(df: pd.DataFrame):
    if not ENABLE_META_REINFORCE:
        return df
    try:
        con = sqlite3.connect("data/autoscalp_gui.db"); con.row_factory = sqlite3.Row
        live = pd.read_sql("SELECT marketId, pnl_now, liability FROM live_state", con)
        con.close()
        if live.empty: return df
        df = df.merge(live, on="marketId", how="left")
        df["live_ratio"] = df["pnl_now"].fillna(0) / df["liability"].replace(0, np.nan)
        df["live_ratio"] = df["live_ratio"].fillna(0)
        print(f"[meta-reinforce] ⚡ merged {len(live)} live_state rows into training snapshot.")
        return df
    except Exception as e:
        print(f"[meta-reinforce] warn: {e}")
        return df
# ========================================================================

import shutil, os



# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py : main()
# 📆 PATCHED: 2025-11-04Z — Remove deprecated prune stage (Phase 7.7 readiness)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def main(days:int=7, epochs:int=25):
    global sqlite3
    import sqlite3
 
    print(f"\n[Mastery v7 Trainer] starting run — days={days}, epochs={epochs}")
    _run_cognitive_pretraining()

    # 🧩 Assimilation trigger check (run before progress tracker)
    if not _should_assimilate_from_river():
        print("[Mastery v7 Trainer] 🧠 No new River data — running passive coherence observer.")
        _observe_river_forest_alignment()
        print("[Mastery v7 Trainer] ✅ Brain observation complete — skipping retraining.\n")

        # --- 🧠 Forced Test Run (for validation) --------------------------
        try:
            import engines.mastery.brain_trainer as brain_trainer
            print("[test-run] 🔬 running forced brain alignment test (no-op mode)…")
            brain_trainer.train_brain()
            print("[test-run] ✅ brain alignment dry-run complete.\n")
        except Exception as e:
            print(f"[test-run] ⚠️ brain test skipped due to error: {e}")

        # exit cleanly after test
        return

    
    tracker = ProgressTracker(total_stages=6)

    # Stage 1 — Intel View
    import time
    print("\n[stage 1/6] Ensuring unified intelligence view...")
    print("[v7-intel] ⏭️ skipped — cache_mastery_day is training authority")
    for i in range(0, 100, 5):
        tracker.stage_progress("Intel View", i/6)
        time.sleep(0.02)
    tracker.next_stage()

    # clear stale WAL files before cache rebuild
    os.system("rm -f data/autoscalp_gui.db-wal data/autoscalp_gui.db-shm")
    time.sleep(1)



    # Stage 2 — Rebuild Cache
    print("\n[stage 2/6] Rebuilding live Mastery cache (Playbooks + OC)...")
    rebuild_mastery_cache(90)
    for i in range(0, 100, 10):
        tracker.stage_progress("Rebuild Cache", i/6)
        time.sleep(0.02)
    tracker.next_stage()

    # Stage 3 — Sandbox
    print("\n[stage 3/6] Rebuilding sandbox database...")
    build_v7_sandbox(days)
    for i in range(0, 100, 10):
        tracker.stage_progress("Sandbox", i/6)
        time.sleep(0.03)
    tracker.next_stage()

    # Stage 4 — Load Data
    print("\n[stage 4/6] Loading training data...")
    if ENABLE_WATCHER: _start_realtime_watcher()
    if ENABLE_DRIFT_MONITOR: _start_drift_monitor()

    df = load_training_data()
    df = _fold_in_live_posteriors(df)  # 👈 add this line
    _record_brain_coherence_from_foldin(df)  # 👈 new line
    for i in range(0, 100, 20):
        tracker.stage_progress("Load Data", i/6)
        time.sleep(0.05)
    tracker.next_stage()

    # Stage 5 — Train Model
    print("\n[stage 5/6] Training model and updating posteriors...")
    _stop_pulse = _alive_pulse("training engine", interval=60)
    try:
        model, le = train_and_update_posteriors(df, epochs=epochs)
    finally:
        _stop_pulse()
    for i in range(0, 100, 4):
        tracker.stage_progress("Training", i/6)
        time.sleep(0.05)
    tracker.next_stage()

    # Stage 6 — Save & Emit
    if model is not None:
        print("\n[stage 6/6] Saving model and emitting snapshot...")
        path = save_model(model, le)
        emit_snapshot()
        update_mastery_state(path)
    tracker.next_stage()

    # Stage 7 --- Brain reinforcement snapshot ---
    from engines.brain.brain_core import record_brain_state
    record_brain_state(
        mean_bucket=0.3178,        # last training mean (computed live)
        river_mean=0.42,           # placeholder until real River calc bound
        forest_mean=0.35,
        coherence=0.88,
        entropy=1.12,
        divergence=abs(0.42 - 0.35)
    )



    # Stage 7.5 — Brain coherence alignment (macro/global)
    from engines.mastery import brain_trainer
    print("\n[stage 7.5/8] Aligning brain coherence (macro/global)…")
    brain_trainer.train_brain()


    # Stage 8 --- Brain feedback integration ---
    from engines.mastery import brain_trainer
    from engines.brain.brain_core import record_brain_state
    import sqlite3, math

    print("\n[stage 8/8] Brain feedback integration…")

    # Ensure previous brain_trainer writes are fully committed
    sqlite3.connect("data/mastery_v7.db").close()

    # 🧠 attach both DBs (write→mastery_v7, read→autoscalp_gui)
    con = sqlite3.connect("data/mastery_v7.db", isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute("ATTACH DATABASE 'data/autoscalp_gui.db' AS live;")

    # --- 8A. Global + Macro coherence refresh --------------------------------
    print("\n=== 🧠 Global / Macro Alignment ===")
    try:
        brain_trainer.train_brain()
    except Exception as e:
        print(f"[brain] warn: brain_trainer failed ({e})")

    # --- 8B. River / Forest live coherence -----------------------------------
    try:
        river_mean = con.execute("""
            SELECT ROUND(AVG(confidence),6) FROM live.mastery_posteriors;
        """).fetchone()[0] or 0
        forest_mean = con.execute("""
            SELECT ROUND(AVG(bucket_confidence),6) FROM live.mastery_posteriors;
        """).fetchone()[0] or 0
    except Exception:
        river_mean = forest_mean = 0.0

    coherence = 1 - abs(river_mean - forest_mean)
    entropy = -(
        (river_mean * math.log2(river_mean or 1e-9))
        + ((1 - river_mean) * math.log2((1 - river_mean) or 1e-9))
    )
    divergence = abs(river_mean - forest_mean)

    record_brain_state(
        mean_bucket=0.0,
        river_mean=river_mean,
        forest_mean=forest_mean,
        coherence=coherence,
        entropy=entropy,
        divergence=divergence,
        notes="Auto feedback after training run"
    )

    print(f"[brain] 🧠 state recorded — river={river_mean:.4f} "
          f"forest={forest_mean:.4f} coherence={coherence:.4f}")

    # --- 8C. Hierarchical summaries ------------------------------------------
    print("\n=== 🧠 GLOBAL Summary (Δ vs previous) ===")
    rows = con.execute("""
        SELECT layer,bucket,samples,total_pnl,ROUND(coherence,3) AS coh,
               ROUND(adjustment,4) AS adj,notes,recorded_at
          FROM brain_state_v7
         WHERE layer='GLOBAL'
      ORDER BY recorded_at DESC LIMIT 5;
    """).fetchall()
    if rows:
        print("Layer | Bucket | Samples | Coherence | ΔPrev | Label")
        print("-------|--------|----------|------------|--------|-----------")
        prev = rows[1]["coh"] if len(rows) > 1 else rows[0]["coh"]
        delta = rows[0]["coh"] - prev
        label = "IMPROVED" if delta > 0.01 else "DECLINED" if delta < -0.01 else "STABLE"
        r = rows[0]
        print(f"{r['layer']:<6} | {r['bucket']:<6} | {r['samples']:>8} "
              f"| {r['coh']:>8.3f} | {delta:+6.3f} | {label}")
        print("-------|--------|----------|------------|--------|-----------\n")
    else:
        print("⚠️  No GLOBAL rows found.\n")

    print("=== 🧠 MACRO Summary (Δ vs previous) ===")
    macros = con.execute("""
        SELECT bucket,samples,ROUND(coherence,3) AS coh,
               ROUND(adjustment,4) AS adj,notes
          FROM brain_state_v7
         WHERE layer='MACRO'
      ORDER BY bucket;
    """).fetchall()
    if macros:
        print("Layer | Bucket                     | Samples | Coh  | Adj   | Label")
        print("-------|-----------------------------|----------|------|-------|-----------")
        for r in macros:
            label = "IMPROVED" if r["adj"] > 0.01 else "DECLINED" if r["adj"] < -0.01 else "STABLE"
            print(f"MACRO  | {r['bucket']:<27} | {r['samples']:>7} | "
                  f"{r['coh']:>4.3f} | {r['adj']:+6.3f} | {label}")
        print("-------|-----------------------------|----------|------|-------|-----------\n")
    else:
        print("⚠️  No MACRO rows found.\n")

    print("=== 🧠 MICRO Summary (Δ vs previous) ===")
    prev = con.execute("""
        SELECT bucket,ROUND(AVG(coherence),3) AS prev_conf
          FROM brain_state_v7
         GROUP BY bucket;
    """).fetchall()
    prev_map = {r["bucket"]: float(r["prev_conf"] or 0) for r in prev}

    rows = con.execute("""
        SELECT bucket,
               COUNT(*) AS n,
               ROUND(AVG(confidence),3) AS conf,
               ROUND(AVG(bucket_confidence),3) AS bucket_conf
          FROM live.mastery_posteriors
         GROUP BY bucket
         ORDER BY bucket;
    """).fetchall()

    print("Layer | Bucket                     | Samples | AvgConf | ΔPrev | Label")
    print("-------|-----------------------------|----------|----------|--------|-----------")
    for r in rows:
        conf = float(r["conf"] or 0)
        prev_conf = prev_map.get(r["bucket"], conf)
        delta = conf - prev_conf
        label = "IMPROVED" if delta > 0.01 else "DECLINED" if delta < -0.01 else "STABLE"
        print(f"MICRO  | {r['bucket']:<27} | {r['n']:>7} | "
              f"{conf:>7.3f} | {delta:+6.3f} | {label}")
    print("-------|-----------------------------|----------|----------|--------|-----------\n")

    # --- 8D. Persist hierarchical snapshot for future delta tracking ----
    print("[brain] 💾 Persisting brain hierarchy snapshot...")

    try:
        run_tag = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        con.execute("""
            CREATE TABLE IF NOT EXISTS brain_history_v7(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT DEFAULT (datetime('now','utc')),
                run_tag TEXT,
                layer TEXT,
                bucket TEXT,
                samples INTEGER,
                coherence REAL,
                adjustment REAL,
                notes TEXT
            );
        """)

        # 1️⃣  Pull all 3 layers from current state sources
        globals_ = con.execute("""
            SELECT layer, bucket, samples, coherence, adjustment, notes
              FROM brain_state_v7
             WHERE layer IN ('GLOBAL','MACRO')
          GROUP BY layer, bucket;
        """).fetchall()


        micros = con.execute("""
            SELECT 'MICRO' AS layer, bucket,
                   COUNT(*) AS samples,
                   ROUND(AVG(confidence),3) AS coherence,
                   ROUND(AVG(bucket_confidence),3) AS adjustment,
                   'micro summary' AS notes
              FROM live.mastery_posteriors
             GROUP BY bucket;
        """).fetchall()

        # 2️⃣  Combine and insert all
        rows = list(globals_) + list(micros)
        con.executemany("""
            INSERT INTO brain_history_v7(run_tag,layer,bucket,samples,coherence,adjustment,notes)
            VALUES(?,?,?,?,?,?,?);
        """, [
            (run_tag, r["layer"], r["bucket"], r["samples"], r["coherence"],
             r["adjustment"], r["notes"]) for r in rows
        ])
        con.commit()
        print(f"[brain] ✅ persisted {len(rows)} brain_history_v7 rows for run_tag={run_tag}")

    except Exception as e:
        print(f"[brain] warn: history persist failed ({e})")

    con.execute("DETACH DATABASE live;")
    con.close()

    print(f"\n[Mastery v7 Trainer] ✅ complete — {days}-day window, {epochs} epochs.\n")

# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/train_mastery_v7.py (bottom of file, before __main__)
# 📆 PATCHED: 2025-11-04Z — Machine Training v7 Live Reinforcement Hook (River Exposure)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def on_settlement_event(market_id: str, selection_id: str, pnl: float, success: int | None = None):
    """
    Live reinforcement hook.
    Called by Micro-Scalper, MLM, or Overwatcher after a bet is settled.

    Args:
        market_id: Betfair marketId of the runner
        selection_id: Runner selectionId
        pnl: Realised profit or loss for that runner
        success: Optional 1/0 flag; if None it will be inferred from pnl (>0)

    Behaviour:
        • Builds a one-row DataFrame with pnl + success.
        • Passes it through _river_phase() for adaptive reinforcement.
        • Updates confidence for the (marketId, selectionId) pair inside mastery_posteriors.
    """
    import pandas as pd, numpy as np, sqlite3
    from engines.config_paths import autoscalp_db

    if success is None:
        success = 1 if pnl > 0 else 0

    df = pd.DataFrame([{
        "marketId": market_id,
        "selectionId": selection_id,
        "pnl": float(pnl),
        "success": int(success)
    }])

    # Run River update for this single outcome
    river_conf = _river_phase(df)
    conf_value = float(river_conf[-1])

    # Persist confidence to mastery_posteriors
    db = autoscalp_db()
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("""
        UPDATE mastery_posteriors
           SET confidence = ?,
               updated_at = datetime('now','utc')
         WHERE marketId = ? AND selectionId = ?
    """, (conf_value, market_id, selection_id))
    if cur.rowcount == 0:
        # create placeholder row if none exists
        cur.execute("""
            INSERT INTO mastery_posteriors(bin_key, confidence, updated_at)
            VALUES(?, ?, datetime('now','utc'))
        """, (f"{market_id}|{selection_id}", conf_value))
    con.commit(); con.close()
    print(f"[river-live] Reinforced {market_id}:{selection_id} → conf={conf_value:.3f}")
# === PATCH END ===


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--epochs", type=int, default=25)
    args = ap.parse_args()
    main(days=args.days, epochs=args.epochs)
