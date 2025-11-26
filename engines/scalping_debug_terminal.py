# ===== Scalping Engine Script (scalping_engine.py) =====

from quick_lay_debugging import run_scalping_engine

if __name__ == '__main__':
    session_token = input("🔐 Enter your Betfair session token: ")
    headers = {
        "X-Application": "CZHojduNWa3kxWIn",
        "X-Authentication": session_token,
        "content-type": "application/json"
    }
    active_bets = []  # Placeholder for active bets list

    run_scalping_engine(headers, active_bets)