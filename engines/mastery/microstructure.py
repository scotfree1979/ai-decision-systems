from __future__ import annotations
from typing import Any, Dict
import sqlite3
from engines.config_paths import autoscalp_db, q_retry as _q

def _clamp(x: float, lo: float, hi: float) -> float:
    return hi if x > hi else lo if x < lo else x

def summarize_levels(levels: list[int] | None) -> Dict[str, float]:
    """Optional helper if order-book levels [L0, L+1, L+2] are provided."""
    if not levels:
        return {"depth_total": 0.0, "depth_slope": 0.0}
    L = [max(0, int(v)) for v in levels[:3]]
    depth_total = float(sum(L))
    depth_slope = float(L[0] - (L[2] if len(L) > 2 else L[-1]))
    return {"depth_total": depth_total, "depth_slope": depth_slope}

def posterior_mean(alpha: float, beta: float, default: float = 0.5) -> float:
    s = alpha + beta
    return default if s <= 0 else alpha / s

# --- PATCH START: moderate p_fill scaling and slippage ------------------------
def compute_p_fill(ctx: Dict[str, Any], post: Dict[str, Any]) -> float:
    base = posterior_mean(
        float(post.get("p_fill", {}).get("alpha", 30.0)),
        float(post.get("p_fill", {}).get("beta", 20.0)),
        default=0.6,
    )
    depth_total = float(ctx.get("depth_total", 0.0))
    if "levels" in ctx and not depth_total:
        depth_total = summarize_levels(ctx.get("levels"))["depth_total"]

    stake = float(ctx.get("stake", 2.0))
    matched_per_min = float(ctx.get("matched_per_min", 100.0))
    sigma = float(ctx.get("sigma", 1.0))

    # Softer depth response; compare against stake-scaled baseline
    depth_baseline = max(120.0, 80.0 * stake)
    depth_factor   = _clamp((depth_total / depth_baseline) ** 0.5, 0.3, 1.2)
    matched_factor = _clamp(matched_per_min / 120.0, 0.3, 1.1)
    sigma_factor   = _clamp(1.0 / (1.0 + 0.7 * sigma), 0.4, 1.0)

    p = base * depth_factor * matched_factor * sigma_factor
    return _clamp(p, 0.05, 0.92)

def expected_slippage_ticks(p_fill: float, sigma: float) -> float:
    # Non-zero floor; grows as fill confidence drops and sigma rises
    raw = (1.0 - p_fill) * (0.8 + 0.5 * float(sigma))
    return _clamp(max(0.03, raw), 0.03, 3.0)
# --- PATCH END ----------------------------------------------------------------
# === PATCH START ===
# 📍 TARGET: engines/mastery/microstructure.py:gather_v7_signals
# 📆 PATCHED: 2025-11-07Z — read-only GUI DB + persist signals in mastery_v7.db
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3
import time
from engines.config_paths import autoscalp_db, mastery_v7_db

def gather_v7_signals(marketId: str, selectionId: str) -> dict:
    """Read live v7 metrics (read-only) and persist snapshot into mastery_v7.db."""
    t0 = time.time()
    print(f"[MICRO] ⏳ starting gather for mid={marketId} sid={selectionId}")

    # --- read-only connection to GUI DB (never writes) ---
    ro_path = f"file:{autoscalp_db()}?mode=ro"
    con = sqlite3.connect(ro_path, uri=True)
    con.row_factory = sqlite3.Row

    # --- default signal dict ---
    sig = {
        "marketId": marketId,
        "selectionId": selectionId,
        "slope_ppm": 0.0,
        "tick_vel_3s_up": 0.0,
        "momentum_class": None,
        "drift_speed": 0.0,
        "inplay_progress": 0.0,
        "expected_race_mins": None,
        "pos_inplay": None,
        "confidence": 0.0,
        "anchor_odd": None,
        "scope_zone": "UNKNOWN",
        "is_active": False,
        "is_passive": False,
        "is_ignored": False,
    }

    # ✅ Lazy import to avoid circular reference (Scope)
    try:
        from engines.decision_engine import orchestrator
        scope = orchestrator.get_scope_snapshot()
        zone = (scope.get(marketId, {}) or {}).get("zone", "UNKNOWN")
        sig["scope_zone"] = zone
        sig["is_active"]  = zone == "ACTIVE"
        sig["is_passive"] = zone == "PASSIVE"
        sig["is_ignored"] = zone == "IGNORED"
    except Exception as e:
        print(f"[MICRO] warn: could not attach scope for mid={marketId} — {e}")

    try:
        print("[MICRO] checking v7_intelligence view …")
        has_v7 = bool(con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v7_intelligence'"
        ).fetchone())
        print(f"[MICRO] v7_intelligence exists? {has_v7}")

        if has_v7:
            row_v7 = con.execute("""
                SELECT slope_ppm, tick_vel_3s_up, momentum_class
                  FROM v7_intelligence
                 WHERE marketId=? AND selectionId=? LIMIT 1
            """, (marketId, selectionId)).fetchone()
            if row_v7:
                sig.update({k: row_v7[k] for k in row_v7.keys() if k in sig})

            row_time = con.execute("""
                SELECT drift_speed, inplay_progress, expected_race_mins
                  FROM v7_timing_features_fixed
                 WHERE marketId=? AND selectionId=? LIMIT 1
            """, (marketId, selectionId)).fetchone()
            if row_time:
                sig.update({k: row_time[k] for k in row_time.keys() if k in sig})

            row_conf = con.execute("""
                SELECT confidence
                  FROM v_mastery_v7
                 WHERE marketId=? AND selectionId=? LIMIT 1
            """, (marketId, selectionId)).fetchone()
            if row_conf:
                sig.update({k: row_conf[k] for k in row_conf.keys() if k in sig})

    except Exception as e:
        print(f"[MICRO] ❌ gather error mid={marketId} sid={selectionId} → {e}")
    finally:
        con.close()

    # --- persist signal into mastery_v7.db ---
    try:
        con_m = sqlite3.connect(mastery_v7_db())
        cur = con_m.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS micro_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                marketId TEXT NOT NULL,
                selectionId TEXT NOT NULL,
                drift_speed REAL,
                slope_ppm REAL,
                momentum_class TEXT,
                inplay_progress REAL,
                expected_race_mins REAL,
                scope_zone TEXT,
                confidence REAL,
                created_at TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        cur.execute("""
            INSERT INTO micro_signals
            (marketId, selectionId, drift_speed, slope_ppm, momentum_class,
             inplay_progress, expected_race_mins, scope_zone, confidence)
            VALUES (:marketId, :selectionId, :drift_speed, :slope_ppm, :momentum_class,
                    :inplay_progress, :expected_race_mins, :scope_zone, :confidence)
        """, sig)
        con_m.commit(); con_m.close()
    except Exception as e:
        print(f"[MICRO] warn: could not persist to mastery_v7.db — {e}")

    print(f"[MICRO] ✅ done gather in {time.time()-t0:.2f}s")
    return sig
# === PATCH END ===



