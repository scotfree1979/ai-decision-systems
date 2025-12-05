#!/usr/bin/env python3
"""
goal_adapter — bridges core values ↔ mastery learning signals.
Evaluated once per feedback cycle so the model trains toward live goals.
"""

from engines.config_core_values import CORE_VALUES
from datetime import datetime, timezone
import math
import os
import sqlite3

# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/goal_adapter.py
# 📆 PATCHED: 2025-12-04 — introduce DAL-safe read helpers
# =======================================================================

from engines.config_paths import auto_conn

def _safe_read(sql: str, params=()):
    """
    Execute SQL safely using DALReadProxy.
    Always returns list-of-tuples (never raises, never returns None).
    """
    try:
        con = auto_conn(rw=False)
        fut = con.execute(sql, params or ())
        rows = fut.fetchall()
        return rows or []
    except Exception:
        return []

# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/goal_adapter.py:_trade_stats
# 📆 PATCHED: 2025-12-12 — DAL-safe + MATCHED + correct dashboard column
# =======================================================================

def _trade_stats(period_days=None):
    """
    Returns dict(total, good, bad, win_rate, matched_ratio)
    Reads exclusively through DAL read proxies.
    """
    where = ""
    params = ()

    if period_days is not None:
        if period_days == 0:
            where = "AND date(opened_at)=date('now','utc')"
        else:
            where = "AND opened_at >= datetime('now','utc', ?)"
            params = (f"-{period_days} day",)

    # total parents
    row = _safe_read_one(f"""
        SELECT COUNT(*)
          FROM orders
         WHERE role='PARENT'
           {where}
    """, params)
    total = row[0] if row else 0

    # GOOD: matched hedge children (H)
    row = _safe_read_one(f"""
        SELECT COUNT(DISTINCT p.id)
          FROM orders p
          JOIN orders c ON c.hedge_of = p.id
         WHERE p.role='PARENT'
           AND c.role='CHILD'
           AND c.source='H'
           AND UPPER(c.entry_status)='MATCHED'
           {where.replace('opened_at','p.opened_at')}
    """, params)
    good = row[0] if row else 0
    bad = total - good

    # --- Win rate via v_dashboard_cashout (DAL-safe) ---------------------
    rowset = _safe_read(f"""
        SELECT marketId,
               SUM(COALESCE(total,0)) AS t
          FROM v_dashboard_cashout
         WHERE date(day) >= CASE
                 WHEN ? IS NULL THEN date(day)
                 WHEN ?='0' THEN date('now','utc')
                 ELSE date('now','utc', ?)
             END
         GROUP BY marketId
    """, (
        None if period_days is None else str(period_days),
        "0" if period_days == 0 else None,
        f"-{period_days} day" if period_days not in (None,0) else None
    ))

    if rowset:
        wins = sum(1 for _, t in rowset if float(t or 0.0) > 0.0)
        total_mkts = len(rowset)
        win_rate = wins / total_mkts if total_mkts else 0.0
    else:
        win_rate = 0.0

    matched_ratio = good / total if total > 0 else 0.0

    return dict(
        total=total,
        good=good,
        bad=bad,
        win_rate=win_rate,
        matched_ratio=matched_ratio,
    )

# === PATCH END =========================================================

def _safe_read_one(sql: str, params=()):
    rows = _safe_read(sql, params)
    return rows[0] if rows else None

# === PATCH END =========================================================


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

# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/goal_adapter.py:print_trade_outcome_summary
# =======================================================================

# === PATCH START =======================================================
def print_trade_outcome_summary():
    """
    Print ALL TIME, TODAY, 7-DAY, 30-DAY stats.
    """
    stats_all  = _trade_stats(None)
    stats_day  = _trade_stats(0)
    stats_7    = _trade_stats(7)
    stats_30   = _trade_stats(30)

    def fmt(name, s):
        print(f"\n[{name}]")
        print(f"  Total parents : {s['total']}")
        print(f"  Good (hedged) : {s['good']}")
        print(f"  Bad           : {s['bad']}")
        print(f"  Win rate      : {s['win_rate']:.3f}")
        print(f"  Matched ratio : {s['matched_ratio']:.3f}")

    print_trade_outcome_summary.__wrapped__ = True  # marker

    print("\n=== TRADE OUTCOME SUMMARY ===")
    fmt("ALL TIME", stats_all)
    fmt("TODAY", stats_day)
    fmt("LAST 7 DAYS", stats_7)
    fmt("LAST 30 DAYS", stats_30)
    print("================================\n")
# === PATCH END =========================================================

# === PATCH START =======================================================
def evaluate_progress(live_pnl, win_rate, matched_ratio):
    """
    New correct goal-alignment:
      • Profit alignment (vs profit target)
      • Loss control (vs max loss)
      • Win rate alignment
      • Matched ratio alignment
    Each component 0–1, then averaged.
    """
    g = current_goals()

    # Profit score (open-ended)
    if live_pnl >= g["target_profit"]:
        profit_score = 1.0
    else:
        profit_score = max(0.0, live_pnl / g["target_profit"])

    # Loss control score
    if live_pnl >= 0:
        loss_score = 1.0
    else:
        loss_score = max(0.0, 1 - abs(live_pnl) / abs(g["max_loss"]))

    # Win rate score
    win_score = min(1.0, win_rate / g["win_rate"])

    # Matched ratio score
    match_score = min(1.0, matched_ratio / g["matched_ratio"])

    return round((profit_score + loss_score + win_score + match_score) / 4.0, 3)
# === PATCH END =========================================================
# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/goal_adapter.py:_trade_good_bad_counts
# 📆 PATCHED: 2025-12-12 — DAL-safe + MATCHED logic
# =======================================================================

def _trade_good_bad_counts():
    """Return (good, bad, total) for today."""
    row = _safe_read_one("""
        SELECT COUNT(*)
          FROM orders
         WHERE role='PARENT'
           AND date(opened_at)=date('now','utc')
    """)
    total = row[0] if row else 0

    # good = matched hedge children
    row = _safe_read_one("""
        SELECT COUNT(DISTINCT p.id)
          FROM orders p
          JOIN orders c ON c.hedge_of = p.id
         WHERE p.role='PARENT'
           AND date(p.opened_at)=date('now','utc')
           AND c.role='CHILD'
           AND c.source='H'
           AND UPPER(c.entry_status)='MATCHED'
    """)
    good = row[0] if row else 0

    bad = total - good
    return good, bad, total

# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/goal_adapter.py:_win_rate_today
# 📆 PATCHED: 2025-12-12 — DAL-only, dashboard-only (correct schema)
# =======================================================================

def _win_rate_today():
    """
    Win rate today = % of markets with positive PnL.
    Uses ONLY v_dashboard_cashout, which we verified by live test.
    This avoids relying on unknown settlement schema.
    """
    from engines.config_paths import open_auto_db, q_retry as _q

    try:
        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        rows = _q(con, """
            SELECT marketId, SUM(COALESCE(total,0)) AS t
              FROM v_dashboard_cashout
             WHERE date(day)=date('now','utc')
             GROUP BY marketId
        """).fetchall()

        if not rows:
            return 0.0

        wins = sum(1 for r in rows if float(r["t"] or 0.0) > 0.0)
        total = len(rows)
        return wins / total if total else 0.0

    except Exception as e:
        print(f"[mastery] goal_adapter pnl warn: {e}")
        return 0.0

# === PATCH END =========================================================




# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/goal_adapter.py:_matched_ratio_today
# 📆 PATCHED: 2025-12-12 — DAL-safe + MATCHED logic
# =======================================================================

def _matched_ratio_today():
    row = _safe_read_one("""
        SELECT COUNT(*)
          FROM orders
         WHERE role='PARENT'
           AND date(opened_at)=date('now','utc')
    """)
    total = row[0] if row else 0

    row = _safe_read_one("""
        SELECT COUNT(DISTINCT p.id)
          FROM orders p
          JOIN orders c ON c.hedge_of=p.id
         WHERE p.role='PARENT'
           AND date(p.opened_at)=date('now','utc')
           AND c.role='CHILD'
           AND c.source='H'
           AND UPPER(c.entry_status)='MATCHED'
    """)
    good = row[0] if row else 0

    if total == 0:
        return 0.0

    ratio = good / total
    return ratio if ratio > 0 else 0.05

# === PATCH END =========================================================


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

