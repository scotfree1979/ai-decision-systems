import sqlite3
from config_paths import DB_PATH
from upgrade_import_patch import get_session_token

def load_pattern_pnl_from_playbooks(self):
    """
    📦 Load historical playbook PnL from DB to restore memory across sessions.
    Builds self.live_pattern_pnl[pattern_key] with {trades, net_pnl}.
    """
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT pattern_key, COUNT(*), SUM(pnl)
                FROM playbooks
                WHERE result IN ('success', 'poor_edge')
                GROUP BY pattern_key
            """)
            rows = cursor.fetchall()

            for pattern_key, trade_count, net_pnl in rows:
                if not pattern_key:
                    continue
                self.live_pattern_pnl[pattern_key]["trades"] = trade_count
                self.live_pattern_pnl[pattern_key]["net_pnl"] = net_pnl or 0.0

        print(f"✅ Loaded historical PnL from playbooks ({len(rows)} patterns)")
    except Exception as e:
        print(f"⚠️ Failed to load PnL from playbooks: {e}")

def track_live_pattern_pnl(self, pattern_key, pnl):
    if not pattern_key:
        return
    self.live_pattern_pnl[pattern_key]["trades"] += 1
    self.live_pattern_pnl[pattern_key]["net_pnl"] += pnl

def refresh_pattern_pnl_from_api(self):
    try:
        session_token = get_session_token()
        headers = {
            'X-Application': "CZHojduNWa3kxWIn",
            'X-Authentication': session_token,
            'Content-Type': 'application/json'
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "SportsAPING/v1.0/listSettledBets",
            "params": {"betStatus": "SETTLED"},
            "id": 1
        }

        import requests
        response = requests.post("https://api.betfair.com/exchange/betting/json-rpc/v1", headers=headers, json=payload)
        data = response.json()
        settled = data.get('result', {}).get('bets', [])

        for bet in settled:
            try:
                pattern_key = bet.get("customerOrderRef", "").split("_")[0]
                profit = float(bet.get("profit", 0.0))
                if pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
                    self.live_pattern_pnl[pattern_key]["trades"] += 1
                    self.live_pattern_pnl[pattern_key]["net_pnl"] += profit
            except Exception:
                continue

        print(f"📡 Synced pattern PnL using API fallback")
    except Exception as e:
        print(f"⚠️ Failed to refresh pattern PnL from API: {e}")
