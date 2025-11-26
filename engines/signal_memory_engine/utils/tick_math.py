def get_tick_difference(start_odds, current_odds):
    try:
        diff = abs(float(current_odds) - float(start_odds))
        if start_odds < 2.0:
            return round(diff / 0.01)
        elif start_odds < 3.0:
            return round(diff / 0.02)
        elif start_odds < 4.0:
            return round(diff / 0.05)
        elif start_odds < 6.0:
            return round(diff / 0.1)
        elif start_odds < 10.0:
            return round(diff / 0.2)
        elif start_odds < 20.0:
            return round(diff / 0.5)
        elif start_odds < 30.0:
            return round(diff / 1.0)
        elif start_odds < 50.0:
            return round(diff / 2.0)
        elif start_odds < 100.0:
            return round(diff / 5.0)
        else:
            return round(diff / 10.0)
    except:
        return 0
