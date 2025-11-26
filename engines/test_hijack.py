from core.api_hijacker import list_market_catalogue

params = {
    "filter": {},
    "max_results": 5,
    "market_projection": ["COMPETITION", "EVENT", "EVENT_TYPE"]
}

result = list_market_catalogue(params)
print(result)
