# session_alerts.py - Explicitly Manages Timed Alerts for Trading Sessions
import time
from datetime import datetime, timedelta

def session_alerts(race_start_time):
    """
    Explicitly handles session alerts:
    - 20 mins before: initial ladder trades
    - 10 mins before: business as usual
    - 5 mins before: final checks
    """
    alerts_triggered = set()

    while True:
        now = datetime.now()
        time_until_race = (race_start_time - now).total_seconds() / 60  # in minutes

        if time_until_race <= 20 and "initial_trades" not in alerts_triggered:
            print("🚦 [20-Minute Alert] Starting initial ladder trades.")
            trigger_initial_ladder_trades()
            alerts_triggered.add("initial_trades")

        if time_until_race <= 10 and "business_as_usual" not in alerts_triggered:
            print("🟢 [10-Minute Alert] Business as usual pre-off trades.")
            trigger_preoff_trading_cycle()
            alerts_triggered.add("business_as_usual")

        if time_until_race <= 5 and "final_checks" not in alerts_triggered:
            print("🔴 [5-Minute Alert] Running final risk and exposure checks.")
            run_final_preoff_checks()
            alerts_triggered.add("final_checks")

        if time_until_race <= 0:
            print("🏁 [Race Started] Trading window closed.")
            break

        time.sleep(10)  # checks every 10 seconds

# Explicit placeholder functions (to be imported from their modules):
def trigger_initial_ladder_trades():
    from og_preoff_scalping import place_initial_ladder_trades
    place_initial_ladder_trades()

def trigger_preoff_trading_cycle():
    from og_preoff_scalping import execute_preoff_cycle
    execute_preoff_cycle()

def run_final_preoff_checks():
    from risk_manager_rules import execute_final_checks
    execute_final_checks()
