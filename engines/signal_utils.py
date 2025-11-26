# ✅ signal_utils.py

# Contains shared signal detection logic to avoid circular imports.

def implied_probability(odds):
    if odds == 0:
        return 0
    return 1 / odds

def percentage_change(anchor_odds, current_odds):
    anchor_prob = implied_probability(anchor_odds)
    current_prob = implied_probability(current_odds)
    if anchor_prob == 0:
        return 0
    return ((current_prob - anchor_prob) / anchor_prob) * 100

def detect_signal(anchor_odds, current_odds, runner_position=None, volume_spike=False, odds_discrepancy=False):
    change = percentage_change(anchor_odds, current_odds)
    if change >= 7.5:
        return 'Market Steamer Signal'
    elif change <= -7.5:
        return 'Market Drifter Signal'
    if volume_spike:
        return 'Volume Surge Signal'
    if odds_discrepancy:
        return 'Market Inefficiency Signal'
    return None
