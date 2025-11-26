import requests
import json
import inspect
import logging
import time

# Logger setup explicitly
logger = logging.getLogger("APIHijackLogger")
logger.setLevel(logging.INFO)
handler = logging.FileHandler("api_hijack_clean.log")  # changed log file to a clean one
formatter = logging.Formatter("%(asctime)s - %(message)s")
handler.setFormatter(formatter)
logger.handlers = [handler]  # clear existing handlers and set explicitly

BETFAIR_API_URL = "https://api.betfair.com/exchange/betting/json-rpc/v1"


def hijack_call(method_name, payload, headers, retries=3, delay=2):
    caller = inspect.stack()[1].filename
    logger.info(f"API call: {method_name} | From: {caller}")
    print(f"Calling Method: {method_name}")

    for attempt in range(retries):
        try:
            response = requests.post(BETFAIR_API_URL, headers=headers, data=json.dumps(payload), timeout=10)
            print(f"Response Status: {response.status_code}")

            if response.status_code == 200:
                try:
                    return response.json()
                except json.JSONDecodeError as e:
                    print(f"JSON Decode Error: {str(e)}")
                    return {"error": "JSON Decode Error", "details": str(e)}
            else:
                print(f"HTTP Error: {response.status_code} - {response.text}")

                if response.status_code == 400:
                    print("\U0001F510 Session token expired or invalid. Please enter a new one.")
                    new_token = input("Enter new Betfair session token: ").strip()

                    # Update daily_config.py
                    with open("daily_config.py", "r") as file:
                        lines = file.readlines()
                    with open("daily_config.py", "w") as file:
                        for line in lines:
                            if line.startswith("SESSION_TOKEN ="):
                                file.write(f'SESSION_TOKEN = "{new_token}"\n')
                            else:
                                file.write(line)

                    logger.info("✅ Session token updated successfully in daily_config.py")

                    # Update headers and retry the request
                    headers["X-Authentication"] = new_token
                    return hijack_call(method_name, payload, headers)

                return {"error": "HTTP Error", "status": response.status_code, "details": response.text}

        except requests.exceptions.RequestException as e:
            logger.warning(f"🔌 [API Hijack] Attempt {attempt + 1}/{retries} failed: {e}")
            print(f"Retrying... ({attempt + 1}/{retries})")
            time.sleep(delay)

    logger.error("❌ [API Hijack] All retry attempts failed. Giving up.")
    return {"error": "ConnectionError", "details": "All retry attempts failed"}
