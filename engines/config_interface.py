# config_interface.py

import time
import os

# Default Configuration
settings = {
    'stake_size': 2.0,
    'target_horses': 3,
    'profit_goal_percent': 25,
    'min_liquidity': 10000,
    'scalp_window_start': 20,
    'scalp_window_end': 5,
    'bot_running': False
}

# Bet Data Structure
bet_data_columns = [
    'bet_id', 'marketId', 'horse_name', 'odds', 'stake', 'result', 'timestamp',
    'error_code', 'bet_type', 'status', 'strategy_name', 'liability',
    'risk_profile', 'confidence_level', 'race_liability'
]

# Display Current Settings
def display_settings():
    print("\n--- Current Scalping Bot Settings ---")
    for key, value in settings.items():
        print(f"{key.replace('_', ' ').title()}: {value}")

# Update Settings Function
def update_settings():
    print("\n--- Update Scalping Bot Settings ---")
    for key in settings:
        if key != 'bot_running':
            new_val = input(f"Enter new value for {key.replace('_', ' ').title()} (Current: {settings[key]}): ")
            if new_val:
                try:
                    if '.' in new_val:
                        settings[key] = float(new_val)
                    else:
                        settings[key] = int(new_val)
                    print(f"{key.replace('_', ' ').title()} updated to {settings[key]}")
                except ValueError:
                    print("Invalid input. Value not changed.")

# Control Bot Functions
def start_bot():
    if not settings['bot_running']:
        settings['bot_running'] = True
        print("\n🚀 Scalping Bot Started.")
    else:
        print("\n⚠️ Bot is already running.")

def stop_bot():
    if settings['bot_running']:
        settings['bot_running'] = False
        print("\n🛑 Scalping Bot Stopped.")
    else:
        print("\n⚠️ Bot is not currently running.")

def refresh_bot():
    print("\n🔄 Refreshing bot...")
    stop_bot()
    time.sleep(1)
    start_bot()

# Main Interface Loop
def main_interface():
    while True:
        display_settings()
        print("\nCommands:")
        print("[1] Update Settings")
        print("[2] Start Bot")
        print("[3] Stop Bot")
        print("[4] Refresh Bot")
        print("[5] Exit")

        choice = input("\nEnter command number: ")

        if choice == '1':
            update_settings()
        elif choice == '2':
            start_bot()
        elif choice == '3':
            stop_bot()
        elif choice == '4':
            refresh_bot()
        elif choice == '5':
            print("\nExiting Configuration Interface. Goodbye!")
            break
        else:
            print("\nInvalid command. Please enter a valid number.")

        time.sleep(1)
        os.system('clear' if os.name == 'posix' else 'cls')

# Entry Point
if __name__ == '__main__':
    print("🚀 Betfair Scalping Bot Configuration & Control Interface 🚀")
    main_interface()
