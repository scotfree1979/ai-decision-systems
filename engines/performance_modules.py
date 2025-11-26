# Performance Modules Implementation

import logging
import time
from datetime import datetime
from risk_manager import RiskManager
from bet_placer import place_hybrid_bet

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Module 1: Rapid In-Play Decision-Making Enhancements
def rapid_inplay_decision(runner_id, odds, marketId, risk_manager, bot_name, signal_type):
    start_time = time.time()
    logging.info(f"⏱️ Starting rapid decision-making for market {marketId}, runner {runner_id}")

    decision_made = place_hybrid_bet(runner_id, odds, marketId, risk_manager, bot_name, signal_type)

    elapsed_time = time.time() - start_time
    if elapsed_time > 1.0:
        logging.warning(f"⚠️ Decision time exceeded 1 second: {elapsed_time:.2f}s")
    else:
        logging.info(f"✅ Decision executed in {elapsed_time:.2f}s")

    return decision_made

# Module 2: Dynamic Market Conditions Adjustment
def dynamic_market_adjustment(runner_id, current_odds, volatility_index, marketId, risk_manager, bot_name, signal_type):
    logging.info(f"🔄 Adjusting for market volatility (index: {volatility_index}) for runner {runner_id}")

    if volatility_index > 7.0:  # High volatility
        adjusted_stake_multiplier = 0.5
    elif volatility_index > 4.0:  # Moderate volatility
        adjusted_stake_multiplier = 0.75
    else:  # Low volatility
        adjusted_stake_multiplier = 1.0

    logging.info(f"Adjusted stake multiplier: {adjusted_stake_multiplier}")

    # Temporarily adjust liability based on volatility
    original_liability = risk_manager.max_liability_per_race
    risk_manager.max_liability_per_race *= adjusted_stake_multiplier

    result = place_hybrid_bet(runner_id, current_odds, marketId, risk_manager, bot_name, signal_type)

    # Reset original liability
    risk_manager.max_liability_per_race = original_liability

    return result

# Module 3: Automatic Exposure Balancing
def automatic_exposure_balancing(risk_manager: RiskManager):
    logging.info("🔍 Checking automatic exposure balancing...")

    exposure = risk_manager.current_global_exposure
    threshold = risk_manager.global_max_exposure * 0.05

    if exposure > risk_manager.global_max_exposure + threshold:
        logging.warning(f"⚠️ Exposure too high: £{exposure:.2f}. Triggering global hedge.")
        risk_manager.trigger_global_hedge()
    elif exposure < risk_manager.global_max_exposure - threshold:
        logging.info(f"🔽 Exposure significantly below maximum. Opportunity to safely increase risk: £{exposure:.2f}")
    else:
        logging.info("✅ Exposure within acceptable range.")

# Quick Terminal Tests (for immediate verification)
if __name__ == "__main__":
    risk_manager = RiskManager(
        max_liability_per_race=50,
        max_daily_loss_per_bot=100,
        global_max_exposure=500,
        max_concurrent_races=3,
        stop_loss_threshold=100,
        profit_lock_threshold=200
    )

    test_runner_id = 215817
    test_marketId = "1.231313957"
    test_bot_name = "TestBot"
    test_signal_type = "Market Drifter In-play"

    # Rapid In-play Decision test
    rapid_inplay_decision(test_runner_id, 4.5, test_marketId, risk_manager, test_bot_name, test_signal_type)

    # Dynamic Market Conditions Adjustment test
    dynamic_market_adjustment(test_runner_id, 6.0, 5.5, test_marketId, risk_manager, test_bot_name, test_signal_type)

    # Automatic Exposure Balancing test
    automatic_exposure_balancing(risk_manager)
