#!/usr/bin/env python3
# engines/config_core_values.py
"""
Central definition of system-wide trading objectives.
Used by Mastery, Overwatcher, Feedback Engine, and Goal Adapter.
These are your “North Star” metrics — the model continuously learns toward them.
"""

from datetime import datetime, timezone

# ─────────────────────────────────────────────────────────────
# 🎯 CORE VALUES — tune these to redefine global objectives
# ─────────────────────────────────────────────────────────────

CORE_VALUES = {
    # Execution quality targets
    "target_matched_ratio": 1.00,     # 100% matched (all orders filled)
    "target_win_rate":       0.80,    # 80% or higher win rate (settled markets)

    # Profit and loss targets (per race)
    "target_profit_per_race": 32.0,   # average profit target per race (GBP)
    "max_loss_per_race":     -90.0,   # absolute loss cap per race (GBP)

    # Envelope thresholds for Overwatcher guardian
    "greenup_trigger":        32.0,   # P&L where greenup activates
    "stoploss_trigger":      -90.0,   # P&L where stoploss activates
    "micro_lay_threshold":    16.0,   # under-target trigger for top-up

    # Model scaling (optional, for reward shaping)
    "goal_alignment_weight":  0.5,    # reward scaling toward goal alignment
    "profit_curve_floor":  -5.0,    # start of mild loss region
    "profit_curve_ceiling": 32.0,   # full-credit profit threshold

}

# ─────────────────────────────────────────────────────────────
# Derived helpers (for logging / reporting)
# ─────────────────────────────────────────────────────────────
def summary() -> str:
    """Return human-readable summary for logs and dashboards."""
    return (
        f"[CORE VALUES] {datetime.now(timezone.utc).isoformat()}Z\n"
        f"• Matched target     : {CORE_VALUES['target_matched_ratio']*100:.0f}%\n"
        f"• Win rate target     : {CORE_VALUES['target_win_rate']*100:.0f}%\n"
        f"• Profit per race     : £{CORE_VALUES['target_profit_per_race']:.2f}\n"
        f"• Max loss per race   : £{CORE_VALUES['max_loss_per_race']:.2f}\n"
        f"• Guardian thresholds : +{CORE_VALUES['greenup_trigger']} / {CORE_VALUES['stoploss_trigger']}\n"
    )

# === PATCH START ===
# 📍 TARGET: engines/config_core_values.py:get_core_values
# 📆 PATCHED: 2025-11-06Z — expose profit-curve bounds for goal_adapter
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def get_core_values() -> dict:
    """
    Return a normalized copy of the CORE_VALUES dictionary
    with consistent key aliases for downstream modules.
    """
    return {
        "matched_target":    CORE_VALUES["target_matched_ratio"],
        "win_rate_target":   CORE_VALUES["target_win_rate"],
        "profit_target":     CORE_VALUES["target_profit_per_race"],
        "max_loss":          CORE_VALUES["max_loss_per_race"],
        "greenup_trigger":   CORE_VALUES["greenup_trigger"],
        "stoploss_trigger":  CORE_VALUES["stoploss_trigger"],
        "micro_lay_threshold": CORE_VALUES["micro_lay_threshold"],
        "goal_weight":       CORE_VALUES["goal_alignment_weight"],
        # ➕ newly exposed for goal_adapter’s open-ended profit curve
        "profit_curve_floor":   CORE_VALUES["profit_curve_floor"],
        "profit_curve_ceiling": CORE_VALUES["profit_curve_ceiling"],
    }
# === PATCH END ===



if __name__ == "__main__":
    print(summary())
