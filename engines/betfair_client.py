# engines/adapters/betfair_client.py
def _headers(self) -> dict:
    from engines.session_secrets import load_betfair_creds
    app_key, tok = load_betfair_creds()
    if not tok: raise RuntimeError("No session token (run Step 1).")
    if not app_key: raise RuntimeError("No app key (run Step 1).")
    return {
        "X-Application": app_key,
        "X-Authentication": tok,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
