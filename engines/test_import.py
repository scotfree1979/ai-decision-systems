from risk_manager import RiskManager

risk_manager = RiskManager(
    max_liability_per_race=40,
    max_daily_loss_per_bot=100,
    global_max_exposure=500,
    max_concurrent_races=4,
    stop_loss_threshold=60,
    profit_lock_threshold=40
)

signal = {
    'stake': 10,
    'odds': 3.5,
    'confidence': 0.9,
    'type': 'Market Drifter Pre-off'
}

risk_manager.evaluate_signal(signal)
