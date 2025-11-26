
import requests

def call_betfair_api(token_manager, endpoint, payload, timeout=5):
    if token_manager.block_calls:
        print("🚫 Blocked: API calls are currently paused.")
        return None

    for attempt in range(2):
        try:
            token = token_manager.get_token()
            headers = {
                "X-Application": token_manager.app_key,
                "X-Authentication": token,
                "Content-Type": "application/json"
            }

            response = requests.post(endpoint, headers=headers, json=payload, timeout=timeout)
            if "INVALID_SESSION_INFORMATION" in response.text:
                print("⚠️ Invalid session, refreshing token...")
                token_manager.invalidate_token()
                continue

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            print(f"❌ API request error: {e}")
            return None

    raise Exception("❌ Token refresh failed after retry.")
