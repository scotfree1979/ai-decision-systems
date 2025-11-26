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
# 📆 PATCHED: 2025-11-06Z — restore signal persistence (no zero-fill overwrite)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def gather_v7_signals(marketId: str, selectionId: str) -> dict:
    """
    Hybrid V7 + Legacy signal gatherer (persistent-signal variant).
    • Keeps current schema-safe queries.
    • Preserves non-zero signal values instead of resetting them to 0.0 each tick.
    • Prevents total drift/momentum wipe-out that stops bets.
    """
    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
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
    }

    # --- detect if v7_intelligence exists ------------------------------------
    has_v7 = _q(con,
        "SELECT 1 FROM sqlite_master WHERE type='view' AND name='v7_intelligence'"
    ).fetchone() is not None

    # --- prefer V7 intelligence but merge with legacy when incomplete ----------
    if has_v7:
        row_v7 = _q(con, """
            SELECT slope_ppm, tick_vel_3s_up, momentum_class
              FROM v7_intelligence
             WHERE marketId=? AND selectionId=? LIMIT 1
        """, (marketId, selectionId)).fetchone()
        if row_v7:
            sig.update({k: row_v7[k] for k in row_v7.keys() if k in sig})

        # timing → drift_speed, inplay_progress, expected_race_mins
        row_time = _q(con, """
            SELECT drift_speed, inplay_progress, expected_race_mins
              FROM v7_timing_features_fixed
             WHERE marketId=? AND selectionId=? LIMIT 1
        """, (marketId, selectionId)).fetchone()
        if row_time:
            sig.update({k: row_time[k] for k in row_time.keys() if k in sig})

        # inferred positions
        row_inf = _q(con, """
            SELECT pos_50 AS pos_inplay, delta_50 AS drift_ratio
              FROM v7_intelligence_inferred
             WHERE marketId=? AND selectionId=? LIMIT 1
        """, (marketId, selectionId)).fetchone()
        if row_inf:
            sig.update({k: row_inf[k] for k in row_inf.keys() if k in sig})

        # mastery confidence
        row_conf = _q(con, """
            SELECT confidence
              FROM v_mastery_v7
             WHERE marketId=? AND selectionId=? LIMIT 1
        """, (marketId, selectionId)).fetchone()
        if row_conf:
            sig.update({k: row_conf[k] for k in row_conf.keys() if k in sig})



        # ✅ ensure drift_speed always numeric
        try:
            sig["drift_speed"] = float(
                sig.get("drift_speed") or sig.get("tick_vel_3s_up") or 0.0
            )
        except Exception:
            sig["drift_speed"] = 0.0

    # --- fallback to legacy trend_features or DriftSpeed_PatchView -----------
    else:
        has_bridge = _q(con,
            "SELECT 1 FROM sqlite_master WHERE type='view' AND name='DriftSpeed_PatchView'"
        ).fetchone() is not None

        if has_bridge:
            row = _q(con, """
                SELECT slope_ppm, drift_speed, momentum_class
                  FROM DriftSpeed_PatchView
                 WHERE marketId=? AND selectionId=? LIMIT 1
            """, (marketId, selectionId)).fetchone()
        else:
            row = _q(con, """
                SELECT slope_ppm, tick_vel_3s_up, momentum_class
                  FROM trend_features
                 WHERE marketId=? AND selectionId=? LIMIT 1
            """, (marketId, selectionId)).fetchone()

        if row:
            sig.update({k: row[k] for k in row.keys() if k in sig})
            if "tick_vel_3s_up" in row and not sig.get("drift_speed"):
                sig["drift_speed"] = row["tick_vel_3s_up"]

        # --- attach drift_speed from v7_timing_features_fixed (schema-verified) ----
        try:
            row_drift = _q(con, """
                SELECT drift_speed
                  FROM v7_timing_features_fixed
                 WHERE marketId=? AND selectionId=? LIMIT 1
            """, (marketId, selectionId)).fetchone()
            if row_drift and row_drift["drift_speed"] is not None:
                sig["drift_speed"] = float(row_drift["drift_speed"])
        except Exception as e:
            print(f"[MICRO][drift_speed] warn mid={marketId} sid={selectionId}: {e}")

    # ✅ only fill missing keys (no overwrite of existing signals)
    for k in ("slope_ppm","tick_vel_3s_up","drift_speed","inplay_progress","expected_race_mins"):
        if sig.get(k) is None:
            sig[k] = 0.0

    # ✅ baseline confidence for live scalper if missing
    if sig.get("confidence") in (None, 0, 0.0):
        sig["confidence"] = 0.5

    # --- anchor odds (bets) ---------------------------------------------------
    try:
        row = _q(con, """
            SELECT anchor_odd FROM bets
             WHERE marketId=? AND selectionId=? LIMIT 1
        """, (marketId, selectionId)).fetchone()
        if row and row["anchor_odd"] is not None:
            sig["anchor_odd"] = row["anchor_odd"]
    except Exception:
        pass

    con.close()
    return sig
# === PATCH END ===


