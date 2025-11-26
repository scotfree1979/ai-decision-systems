def get_current_liability(open_bets, market_id, selection_id):
    key = (market_id, selection_id)
    total = 0.0
    for bet_type in open_bets.get(key, []):
        if bet_type == "entry" or bet_type == "hedge":
            total += 10.0  # default stake assumption
    return total
