# 📦 Quick Market Fetch Debugger
# Run this script standalone to confirm Betfair is returning markets for today

import requests
import json
from datetime import datetime, timedelta

APP_KEY = "CZHojduNWa3kxWIn"
SESSION_TOKEN = input("🔐 Enter your Betfair session token: ").strip()

headers = {
    "X-Application": APP_KEY,
    "X-Authentication": SESSION_TOKEN,
    "Content-Type": "application/json"
}

from_time = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
to_time = from_time + timedelta(hours=23, minutes=59)

print(f"📅 Requesting WIN markets from {from_time} to {to_time} (UTC)")

payload = [
    {
        "jsonrpc": "2.0",
        "method": "SportsAPING/v1.0/listMarketCatalogue",
        "params": {
            "filter": {
                "eventTypeIds": ["7"],
                "marketCountries": ["GB", "IE"],
                "marketTypeCodes": ["WIN"],
                "marketStartTime": {
                    "from": from_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "to": to_time.strftime("%Y-%m-%dT%H:%M:%SZ")
                }
            },
            "marketProjection": ["RUNNER_DESCRIPTION", "MARKET_START_TIME", "EVENT"],
            "sort": "FIRST_TO_START",
            "maxResults": "100"
        },
        "id": 1
    }
]

response = requests.post(
    url="https://api.betfair.com/exchange/betting/json-rpc/v1",
    headers=headers,
    data=json.dumps(payload)
)

if response.status_code != 200:
    print(f"❌ Request failed: {response.status_code} – {response.text}")
else:
    try:
        data = response.json()
        results = data[0].get("result", [])
        print(f"✅ Total WIN markets fetched: {len(results)}")
        for m in results[:10]:
            event = m.get("event", {})
            start = m.get("marketStartTime", "?")
            venue = event.get("venue", "Unknown")
            print(f"🟢 Market: {venue} – Starts at {start}")
    except Exception as e:
        print(f"❌ Failed to parse response: {e}")
        print(response.text)
