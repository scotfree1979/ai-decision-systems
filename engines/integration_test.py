import sys
sys.path.append('/Volumes/Sonnics/Race Tool - Sandbox/analytics_beta/engines')

from risk_manager import RiskManager
from market_monitor_signals import run_monitor

def integration_test():
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

    if risk_manager.evaluate_signal(signal):
        print("✅ Integration Test Passed: Risk Manager correctly approved signal.")
    else:
        print("🚨 Integration Test Failed: Risk Manager rejected signal incorrectly.")

if __name__ == "__main__":
    integration_test()
