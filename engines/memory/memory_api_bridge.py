import requests
import json

# Betfair API credentials
BETFAIR_API_ENDPOINT = "https://api.betfair.com/exchange/betting/json-rpc/v1"
APP_KEY = "CZHojduNWa3kxWIn"
SESSION_TOKEN = "X/OMZKbok+9jUV28sdnt5vBLm4tPGOfJgbIJ2Snyprk="

headers = {
    'X-Application': APP_KEY,
    'X-Authentication': SESSION_TOKEN,
    'Content-Type': 'application/json'
}

# Confirmed market IDs from losing markets (shortened example, use your full list)
market_ids = ["1.243518", "1.243521"]  # Ensure these are correctly formatted and complete

payload = {
    "jsonrpc": "2.0",
    "method": "SportsAPING/v1.0/listMarketCatalogue",
    "params": {
        "filter": {"marketIds": market_ids},
        "maxResults": "100",
        "marketProjection": [
            "EVENT",
            "MARKET_START_TIME",
            "RUNNER_METADATA"
        ]
    },
    "id": 1
}

response = requests.post(BETFAIR_API_ENDPOINT, headers=headers, json=payload)

if response.status_code == 200:
    data = response.json()
    print(json.dumps(data, indent=4))  # clearly inspect Betfair's raw response
else:
    print(f"❌ API request failed: {response.status_code}, {response.text}")
