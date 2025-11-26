# engines/mastery/objectives.py
"""
Defines global profit/loss and win-rate objectives for AutoScalp.
These values are read by Overwatcher, Mastery, and Trainer to align goals.
"""

OBJECTIVES = {
    "profit_target_per_market": 32.0,   # £ target win
    "loss_cap_per_market": 90.0,        # £ max loss
    "min_win_rate": 0.80,               # 80% minimum success
}

def goal_summary():
    g = OBJECTIVES
    return (
        f"🎯  Goal Orientation:\n"
        f"   Profit Target: £{g['profit_target_per_market']}\n"
        f"   Max Loss: £{g['loss_cap_per_market']}\n"
        f"   Min Win Rate: {g['min_win_rate']*100:.0f}%\n"
    )
