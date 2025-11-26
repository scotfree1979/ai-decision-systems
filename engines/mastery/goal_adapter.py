#!/usr/bin/env python3
"""
goal_adapter — bridges core values ↔ mastery learning signals.
Evaluated once per feedback cycle so the model trains toward live goals.
"""

from engines.config_core_values import CORE_VALUES
from datetime import datetime, timezone
import math
import os

def current_goals():
    """Return canonical live goal values."""
    return dict(
        target_profit = CORE_VALUES["target_profit_per_race"],
        max_loss      = CORE_VALUES["max_loss_per_race"],
        win_rate      = CORE_VALUES["target_win_rate"],
        matched_ratio = CORE_VALUES["target_matched_ratio"],
    )

# === PATCH START ===
# 📍 TARGET: engines/mastery/goal_adapter.py:evaluate_progress
# 📆 PATCHED: 2025-11-06Z — open-ended profit & graded-loss goal alignment
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import math

def _pnl_score(pnl: float, *, profit_target: float, max_loss: float) -> float:
    """
    Map per-race £PnL to a [0–1] quality score.
    • Profits above target saturate at 1.0
    • Small losses are lightly penalised
    • Large losses fall off steeply toward 0
    """
    if pnl >= profit_target:
        return 1.0
    if 0 < pnl < profit_target:
        return 0.5 + 0.5 * (pnl / profit_target)
    if -5 <= pnl <= 0:
        return 0.4 + 0.1 * (pnl / -5)          # gentle slope 0.4→0.3
    if pnl < -5:
        # exponential decay until max_loss (e.g. -90)
        return max(0.0, 0.3 * math.exp(pnl / abs(max_loss)))
    return 0.0

# === PATCH START ===
# 📍 TARGET: engines/mastery/goal_adapter.py
# 📆 PATCHED: 2025-11-06Z — add print-only trade outcome summary (H/S/M logic)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3
from engines.config_paths import autoscalp_db

def print_trade_outcome_summary():
    """
    Console-only summary showing number of good (H) and bad (S/M/no child) trades.
    Does not affect goal alignment calculations — purely informational.
    """
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row

    total = con.execute("""
        SELECT COUNT(*) FROM orders WHERE role='PARENT';
    """).fetchone()[0] or 0

    good = con.execute("""
        SELECT COUNT(DISTINCT p.id)
          FROM orders p
          JOIN orders c ON c.hedge_of = p.id
         WHERE p.role='PARENT'
           AND c.role='CHILD'
           AND c.source='H';
    """).fetchone()[0] or 0

    bad = con.execute("""
        SELECT COUNT(DISTINCT p.id)
          FROM orders p
          LEFT JOIN orders c ON c.hedge_of = p.id
         WHERE p.role='PARENT'
           AND (c.id IS NULL OR c.source IN ('S','M'));
    """).fetchone()[0] or 0

    con.close()

    pct_good = (good / total * 100) if total else 0
    pct_bad  = (bad / total * 100) if total else 0

    print("\n[goal_adapter] Trade Outcome Snapshot")
    print(f"  • Good trades  : {good:6d} ({pct_good:5.1f}%)  (hedged)")
    print(f"  • Bad trades   : {bad:6d} ({pct_bad:5.1f}%)  (S/M/no child)")
    print(f"  • Total trades : {total:6d}")
    print(f"  • Goal alignment proxy (good/total): {good/total if total else 0:5.3f}")
# === PATCH END ===



def evaluate_progress(live_pnl: float, win_rate: float, matched_ratio: float) -> float:
    """
    Compute a 0–1 progress score vs core goals.

    Open-ended upward reward:
      • profits above target continue to score 1.0
      • losses graded by size, tolerating small losses
    Weighted equally with win-rate and matched-ratio.
    """
    g = current_goals()

    pnl_component   = _pnl_score(live_pnl,
                                 profit_target=g["target_profit"],
                                 max_loss=g["max_loss"])
    win_component   = min(1.0, max(0.0, win_rate / g["win_rate"]))
    match_component = min(1.0, max(0.0, matched_ratio / g["matched_ratio"]))

    return round((pnl_component + win_component + match_component) / 3.0, 3)
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/mastery/goal_adapter.py
# 📆 PATCHED: 2025-11-17Z — correct PnL, win_rate, matched_ratio using existing dashboard + trade logic
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import sqlite3
from engines.config_paths import autoscalp_db
from gui.dashboard_data import _live_realized_today     # settlement-verified PnL

def _trade_good_bad_counts():
    """
    EXACT SAME LOGIC as print_trade_outcome_summary(), but scoped to TODAY.
    good  = parent with child.source='H'
    bad   = S/M/no child
    total = parents today
    """
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row

    # all parents today
    total = con.execute("""
        SELECT COUNT(*) FROM orders
         WHERE role='PARENT'
           AND date(opened_at)=date('now','utc')
    """).fetchone()[0] or 0

    # hedged (good)
    good = con.execute("""
        SELECT COUNT(DISTINCT p.id)
          FROM orders p
          JOIN orders c ON c.hedge_of=p.id
         WHERE p.role='PARENT'
           AND date(p.opened_at)=date('now','utc')
           AND c.role='CHILD'
           AND c.source='H'
           AND c.entry_status='MATCHED'
    """).fetchone()[0] or 0

    # everything else is bad
    bad = total - good

    con.close()
    return good, bad, total

def _win_rate_today():
    """
    Win rate today = % of markets with positive PnL.
    EXACTLY as per your definition.
    """
    from engines.config_paths import settlements_db, auto_conn, q_retry as _q

    # 1) Try settlement source first (canonical)
    try:
        con = sqlite3.connect(settlements_db())
        con.row_factory = sqlite3.Row
        rows = con.execute("""
            SELECT marketId, SUM(net) AS s
              FROM v_settle_mkt_day
             WHERE day=date('now','utc')
             GROUP BY marketId
        """).fetchall()
        con.close()
        if rows:
            wins = sum(1 for r in rows if float(r["s"] or 0.0) > 0)
            total = len(rows)
            return wins / total if total else 0.0
    except:
        pass

    # 2) Fallback: dashboard view
    try:
        con = auto_conn(); con.row_factory = sqlite3.Row
        rows = _q(con, """
            SELECT marketId, total
              FROM v_dashboard_cashout
             WHERE date(day)=date('now','utc')
        """).fetchall()
        con.close()
        if rows:
            wins = sum(1 for r in rows if float(r["total"] or 0.0) > 0)
            total = len(rows)
            return wins / total if total else 0.0
    except:
        pass

    return 0.0




# === PATCH START ===
# 📍 TARGET: engines/mastery/goal_adapter.py:_matched_ratio_today
# 📆 PATCHED: 2025-11-20 — fix correct CHILD-HEDGE detection + fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _matched_ratio_today():
    """
    matched_ratio = hedged_parents / total_parents_today
    Correct handling:
      • hedge = CHILD role with entry_status='MATCHED' AND source='H'
      • fallback: if system still warming up, allow partial progress
    """
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row

    total = con.execute("""
        SELECT COUNT(*) FROM orders
         WHERE role='PARENT'
           AND date(opened_at)=date('now','utc')
    """).fetchone()[0] or 0

    # hedged
    good = con.execute("""
        SELECT COUNT(DISTINCT p.id)
          FROM orders p
          JOIN orders c ON c.hedge_of=p.id
         WHERE p.role='PARENT'
           AND date(p.opened_at)=date('now','utc')
           AND c.role='CHILD'
           AND c.entry_status='MATCHED'
           AND c.source='H'
    """).fetchone()[0] or 0

    con.close()

    if total == 0:
        return 0.0

    # Warm-up handling: if many parents but exchange still syncing, cap at 0.05 baseline
    ratio = good / total
    return ratio if ratio > 0 else 0.05
# === PATCH END ===



# === PATCH START ===
# 📍 TARGET: engines/mastery/goal_adapter.py:compute_live_goals
# 📆 PATCHED: 2025-11-20 — correct PnL source and safe fallback
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from gui.dashboard_data import _live_realized_today

def compute_live_goals():
    """
    Returns live metrics:
      • PnL (settlement-verified)
      • win_rate (market-level)
      • matched_ratio (child hedges)
    """
    try:
        live_pnl = float(_live_realized_today() or 0.0)
    except Exception:
        live_pnl = 0.0

    win_rate      = _win_rate_today()
    matched_ratio = _matched_ratio_today()

    return live_pnl, win_rate, matched_ratio
# === PATCH END ===



# MODIFY as_feedback_dict to pull correct values
def as_feedback_dict(_: float = 0.0, __: float = 0.0, ___: float = 0.0):
    """
    Pulls REAL sources for PnL/win/match ratio.
    Ignores incoming args (kept for compatibility).
    """
    live_pnl, win_rate, matched_ratio = compute_live_goals()
    score = evaluate_progress(live_pnl, win_rate, matched_ratio)

    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "goal_profit": CORE_VALUES["target_profit_per_race"],
        "goal_loss":   CORE_VALUES["max_loss_per_race"],
        "goal_win":    CORE_VALUES["target_win_rate"],
        "goal_matched":CORE_VALUES["target_matched_ratio"],
        "live_pnl":    live_pnl,
        "win_rate":    win_rate,
        "matched_ratio": matched_ratio,
        "goal_alignment": score,
    }

# === PATCH END ===

