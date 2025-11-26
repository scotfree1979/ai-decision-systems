# engines/mastery/policy_lookup.py
from __future__ import annotations
import os, math, sqlite3
from typing import Dict, Optional

from engines.config_paths import connect_db

DEFAULT_PRIORS = {
    "p1": {"alpha": 20.0, "beta": 20.0},
    "p2": {"alpha": 15.0, "beta": 25.0},
    "p3": {"alpha": 10.0, "beta": 30.0},
    "p_fill": {"alpha": 30.0, "beta": 20.0},
    "mae": {"mean": 1.5, "std": 0.7, "exp_stop_ticks": 1.0},
    "costs": {"fees_ticks": 0.10},
    "sizing": {"base_stake": 2.00},
    "stops": {"hard_stop_ticks": 2, "timeout_sec": 45},
}

def get_bin_key(distance_band: str, code: str, tto_window: str,
                day_of_week: str, surface_type: str, fav_rank_bin: str) -> str:
    return f"{distance_band}|{code}|{tto_window}|{day_of_week}|{surface_type}|{fav_rank_bin}"

# ---- helpers ----------------------------------------------------------------

def _current_source_upper() -> str:
    try:
        from engines.upgrade_import_patch import get_mode  # type: ignore
        m = (get_mode() or os.environ.get("AUTOSCALP_MODE") or "TEST").upper()
    except Exception:
        m = (os.environ.get("AUTOSCALP_MODE") or "TEST").upper()
    return "TEST" if m not in ("TEST", "LEARNING", "LIVE") else m

def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return bool(con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

def _has_col(con: sqlite3.Connection, table: str, col: str) -> bool:
    return col in [r[1] for r in con.execute(f"PRAGMA table_info({table})")]

def _combine_welford(n1: int, mean1: float, m21: float, n2: int, mean2: float, m22: float) -> tuple[int,float,float]:
    """Combine two (n,mean,M2) accumulators (Welford)."""
    if n2 <= 0:
        return n1, mean1, m21
    if n1 <= 0:
        return n2, mean2, m22
    n = n1 + n2
    delta = mean2 - mean1
    mean = mean1 + delta * (n2 / n)
    m2 = m21 + m22 + delta * delta * (n1 * n2 / n)
    return n, mean, m2

def _read_priors(conn: sqlite3.Connection, bin_key: str) -> Dict:
    row = conn.execute(
        "SELECT prior_p1_alpha, prior_p1_beta, prior_p2_alpha, prior_p2_beta, prior_p3_alpha, prior_p3_beta,"
        " prior_fill_alpha, prior_fill_beta, prior_mae_mean, prior_mae_std "
        "FROM mastery_priors WHERE bin_key=?", (bin_key,)
    ).fetchone()
    if not row:
        return DEFAULT_PRIORS.copy()
    out = {
        "p1": {"alpha": float(row[0] or 0.0), "beta": float(row[1] or 0.0)},
        "p2": {"alpha": float(row[2] or 0.0), "beta": float(row[3] or 0.0)},
        "p3": {"alpha": float(row[4] or 0.0), "beta": float(row[5] or 0.0)},
        "p_fill": {"alpha": float(row[6] or 0.0), "beta": float(row[7] or 0.0)},
        "mae": {
            "mean": float(row[8] or DEFAULT_PRIORS["mae"]["mean"]),
            "std":  float(row[9] or DEFAULT_PRIORS["mae"]["std"]),
            "exp_stop_ticks": max(1.0, float(row[9] or DEFAULT_PRIORS["mae"]["std"]))  # keep your prior convention
        },
        "costs":  DEFAULT_PRIORS["costs"].copy(),
        "sizing": DEFAULT_PRIORS["sizing"].copy(),
        "stops":  DEFAULT_PRIORS["stops"].copy(),
    }
    return out

def _sources_for(mode_upper: str, cascade: bool) -> list[str]:
    if not cascade:
        return [mode_upper]
    if mode_upper == "TEST":
        return ["TEST"]
    if mode_upper == "LEARNING":
        return ["TEST", "LEARNING"]
    # LIVE
    return ["TEST", "LEARNING", "LIVE"]

# ---- patched loader ---------------------------------------------------------

def load_posteriors(conn: sqlite3.Connection, bin_key: str,
                    source: Optional[str] = None, cascade: bool = True) -> dict:
    """
    Return a dict shaped like DEFAULT_PRIORS, but with PRIORS + (aggregated) POSTERIORS.
    - If mastery_posteriors has a 'source' column, sum rows across the cascade:
        TEST → [TEST]; LEARNING → [TEST,LEARNING]; LIVE → [TEST,LEARNING,LIVE]
    - If no 'source' column or table not present, we simply return PRIORS (back-compat),
      or PRIORS + the single shared POSTERIOR row (if present).
    """
    mode = (source or _current_source_upper()).upper()
    pri = _read_priors(conn, bin_key)

    if not _table_exists(conn, "mastery_posteriors"):
        return pri  # no learned signal yet

    has_source = _has_col(conn, "mastery_posteriors", "source")
    conn.row_factory = sqlite3.Row

    # Accumulators for pN, fill, MAE
    agg = {
        "p1_alpha": 0.0, "p1_beta": 0.0,
        "p2_alpha": 0.0, "p2_beta": 0.0,
        "p3_alpha": 0.0, "p3_beta": 0.0,
        "fill_alpha": 0.0, "fill_beta": 0.0,
        "mae_n": 0, "mae_mean": 0.0, "mae_m2": 0.0,
    }

    if has_source:
        srcs = _sources_for(mode, cascade)
        rows = conn.execute(
            f"SELECT p1_alpha,p1_beta,p2_alpha,p2_beta,p3_alpha,p3_beta,"
            f"       fill_alpha,fill_beta,mae_n,mae_mean,mae_m2 "
            f"FROM mastery_posteriors WHERE bin_key=? AND source IN ({','.join('?'*len(srcs))})",
            (bin_key, *srcs)
        ).fetchall()
    else:
        # Single shared row (your current schema): treat it as the already-aggregated posterior.
        rows = conn.execute(
            "SELECT p1_alpha,p1_beta,p2_alpha,p2_beta,p3_alpha,p3_beta,fill_alpha,fill_beta,mae_n,mae_mean,mae_m2 "
            "FROM mastery_posteriors WHERE bin_key=?",
            (bin_key,)
        ).fetchall()

    # Sum Beta counts; combine MAE by Welford
    for r in rows or []:
        agg["p1_alpha"] += float(r["p1_alpha"] or 0.0); agg["p1_beta"] += float(r["p1_beta"] or 0.0)
        agg["p2_alpha"] += float(r["p2_alpha"] or 0.0); agg["p2_beta"] += float(r["p2_beta"] or 0.0)
        agg["p3_alpha"] += float(r["p3_alpha"] or 0.0); agg["p3_beta"] += float(r["p3_beta"] or 0.0)
        agg["fill_alpha"] += float(r["fill_alpha"] or 0.0); agg["fill_beta"] += float(r["fill_beta"] or 0.0)
        n, mean, m2 = _combine_welford(
            int(agg["mae_n"]), float(agg["mae_mean"]), float(agg["mae_m2"]),
            int(r["mae_n"] or 0), float(r["mae_mean"] or 0.0), float(r["mae_m2"] or 0.0)
        )
        agg["mae_n"], agg["mae_mean"], agg["mae_m2"] = n, mean, m2

    # Produce the same shape as DEFAULT_PRIORS, but with counts = priors + posteriors
    out = {
        "p1":    {"alpha": float(pri["p1"]["alpha"]) + agg["p1_alpha"],
                  "beta":  float(pri["p1"]["beta"])  + agg["p1_beta"]},
        "p2":    {"alpha": float(pri["p2"]["alpha"]) + agg["p2_alpha"],
                  "beta":  float(pri["p2"]["beta"])  + agg["p2_beta"]},
        "p3":    {"alpha": float(pri["p3"]["alpha"]) + agg["p3_alpha"],
                  "beta":  float(pri["p3"]["beta"])  + agg["p3_beta"]},
        "p_fill":{"alpha": float(pri["p_fill"]["alpha"]) + agg["fill_alpha"],
                  "beta":  float(pri["p_fill"]["beta"])  + agg["fill_beta"]},
        "mae":   {
            # If we have any MAE data, use the learned mean/std; otherwise keep priors
            "mean": float(agg["mae_mean"]) if agg["mae_n"] > 0 else float(pri["mae"]["mean"]),
            "std":  (math.sqrt(agg["mae_m2"] / max(1, agg["mae_n"] - 1))
                     if agg["mae_n"] > 1 else float(pri["mae"]["std"])),
            "exp_stop_ticks": float(pri["mae"]["exp_stop_ticks"]),  # keep your prior-driven exp stop
        },
        "costs":  pri["costs"].copy(),
        "sizing": pri["sizing"].copy(),
        "stops":  pri["stops"].copy(),
    }
    return out

