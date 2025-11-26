
from token_manager import BetfairTokenManager
from api_wrapper import call_betfair_api
import threading
import time

USERNAME = "your_username"
PASSWORD = "your_password"
APP_KEY = "your_app_key"
CERT_PATH = "client-2048.crt"
KEY_PATH = "client-2048.key"

token_manager = BetfairTokenManager(USERNAME, PASSWORD, APP_KEY, CERT_PATH, KEY_PATH)

def keep_token_fresh(manager):
    while True:
        time.sleep(60 * 60)
        try:
            manager.get_token()
            print("🟢 Token is valid.")
        except:
            print("🔴 Failed to refresh token.")

threading.Thread(target=keep_token_fresh, args=(token_manager,), daemon=True).start()

ENDPOINT = "https://api.betfair.com/exchange/betting/json-rpc/v1"
PAYLOAD = [{
    "jsonrpc": "2.0",
    "method": "SportsAPING/v1.0/listEventTypes",
    "params": {},
    "id": 1
}]

response = call_betfair_api(token_manager, ENDPOINT, PAYLOAD)
print(response)
