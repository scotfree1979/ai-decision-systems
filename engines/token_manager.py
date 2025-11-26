import requests
import time
import threading

class BetfairTokenManager:
    def __init__(self, username, password, app_key, cert_path, key_path):
        self.username = username
        self.password = password
        self.app_key = app_key
        self.cert_path = cert_path
        self.key_path = key_path
        self.session_token = None
        self.last_refresh = 0
        self.lock = threading.Lock()
        self.block_calls = False

    def login(self):
        url = "https://identitysso.betfair.com/api/certlogin"
        try:
            response = requests.post(
                url,
                cert=(self.cert_path, self.key_path),
                data={
                    "username": self.username,
                    "password": self.password
                },
                headers={
                    "X-Application": self.app_key,
                    "Content-Type": "application/x-www-form-urlencoded"
                }
            )
            response.raise_for_status()
            data = response.json()
            if data.get("loginStatus") == "SUCCESS":
                self.session_token = data["sessionToken"]
                self.last_refresh = time.time()
                print("✅ Session token refreshed.")
                return self.session_token
            else:
                raise Exception(f"❌ Login failed: {data}")
        except Exception as e:
            print(f"❌ Token login error: {e}")
            raise

    def get_token(self):
        with self.lock:
            if not self.session_token or self._token_expired():
                return self.login()
            return self.session_token

    def _token_expired(self):
        return time.time() - self.last_refresh > 11.5 * 3600

    def invalidate_token(self):
        with self.lock:
            self.session_token = None

    def toggle_block(self, state: bool):
        self.block_calls = state
        print("🚫 API calls blocked." if state else "✅ API calls unblocked.")
