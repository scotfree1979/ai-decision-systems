# 🎩 OGHeadTrader Integration & Strategic Logic

def og_head_trader_strategy(market_data, historical_performance):
    """
    Strategic function for OGHeadTrader to inform optimal ladder placements and suggest improvements.

    Parameters:
    - market_data: dict with current market odds.
    - historical_performance: dict with historical bot performance data.

    Returns:
    - Dict containing ladder placement suggestions and strategic insights.
    """
    strategy_insights = {}

    # Evaluate top 2 runners
    top_runners = sorted(market_data.items(), key=lambda x: x[1])[:2]

    for runner, odds in top_runners:
        historic_odds = historical_performance.get(runner, [])
        if historic_odds:
            avg_historic_odds = sum(historic_odds) / len(historic_odds)
            placement_suggestion = "tight" if odds < avg_historic_odds else "wide"
        else:
            placement_suggestion = "standard"

        strategy_insights[runner] = {
            'Current Odds': odds,
            'Suggested Ladder Placement': placement_suggestion
        }

    # Improvement insights based on recent performance
    recent_profit = sum(historical_performance.get('recent_profits', []))
    strategy_insights['Improvement Suggestion'] = (
        'Increase aggressiveness' if recent_profit > 50 else 'Maintain conservative approach'
    )

    return strategy_insights

# Example usage:
# market_data_example = {'Horse A': 2.5, 'Horse B': 3.0, 'Horse C': 4.0}
# historical_example = {'Horse A': [2.8, 2.6], 'Horse B': [3.1, 3.0], 'recent_profits': [20, 30, 10]}
# trader_strategy = og_head_trader_strategy(market_data_example, historical_example)
# print(trader_strategy)
