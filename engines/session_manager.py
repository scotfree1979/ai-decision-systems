# session_manager.py (Enhanced Robust Automated Session Token Management)

import requests
import time
import threading

BETFAIR_LOGIN_URL = "https://identitysso-cert.betfair.com/api/login"
USERNAME = "quickwallets@gmail.com"
PASSWORD = "MR5049mr!!!"
APP_KEY = "CZHojduNWa3kxWIn"

headers = {"X-Application": APP_KEY, "Content-Type": "application/json"}

def refresh_session_token(interval=900):
    global headers
    payload = {"username": USERNAME, "password": PASSWORD}

    while True:
        try:
            response = requests.post(BETFAIR_LOGIN_URL, data=payload)
            login_response = response.json()

            if login_response.get('status') == 'SUCCESS':
                headers["X-Authentication"] = login_response['token']
                print(f"🔑 Session Token Refreshed Successfully")
            else:
                error_info = login_response.get('error', 'No detailed error provided')
                print(f"🚫 Session Token Refresh Failed: {error_info}")

        except requests.RequestException as e:
            print(f"🚫 Network Error during Session Token Refresh: {e}")
        except ValueError as e:
            print(f"🚫 JSON Decoding Error during Session Token Refresh: {e}")
        except Exception as e:
            print(f"🚫 Unexpected Error during Session Token Refresh: {e}")

        time.sleep(interval)

# Start automated token refresh
threading.Thread(target=refresh_session_token, daemon=True).start()
