def estimate_liability(odds, stake, side):
    """
    Calculates liability based on Betfair side and odds.
    """
    if side == "LAY" and isinstance(odds, (float, int)):
        return round((odds - 1) * stake, 2)
    elif side == "BACK":
        return round(stake, 2)
    return 0.0
