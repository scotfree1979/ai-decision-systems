# engines/live/bank_state.py
from __future__ import annotations
import sys, os, sqlite3
from datetime import datetime, timezone, timedelta


# --- allow standalone execution ---
if __name__ == "__main__" and __package__ is None:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

try:
    from engines.config_paths import autoscalp_db, q_retry as _q
except ImportError:
    sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))
    from engines.config_paths import autoscalp_db, q_retry as _q

def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# === PATCH START ===
# 📍 TARGET: engines/live/bank_state.py:_conn
# 🔎 SEARCH: def _conn(ro=False, rw=False)
# 📆 PATCHED: 2025-11-21

from engines.config_paths import auto_conn as _auto_conn

def _conn(ro=False, rw=False) -> sqlite3.Connection:
    """
    DAL-safe connector for autoscalp_gui.db.
    READ = LOCAL replica
    WRITE = CLOUD primary
    """
    # rw=True forces DAL writer → CLOUD; rw=False = reader → LOCAL
    con = _auto_conn(rw=bool(rw))
    con.row_factory = sqlite3.Row

    # Preserve previous safe pragmas (DAL will ignore any unsupported)
    try:
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass

    return con
# === PATCH END ===


def fetch_live_balance() -> float:
    """Fetch availableToBetBalance via Betfair API (requires SESSION_TOKEN)."""
    try:
        from engines.betfair_status import _keys
        app_key, tok = _keys()
        import requests, json
        headers = {
            "X-Application": app_key,
            "X-Authentication": tok,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        payload = [{
            "jsonrpc": "2.0",
            "method": "AccountAPING/v1.0/getAccountFunds",
            "params": {},
            "id": 1
        }]
        r = requests.post("https://api.betfair.com/exchange/account/json-rpc/v1",
                          headers=headers, data=json.dumps(payload), timeout=8)
        r.raise_for_status()
        j = r.json()
        if isinstance(j, list): j = j[0].get("result", j[0])
        return float(j.get("availableToBetBalance", 0.0))
    except Exception as e:
        print(f"[bank_state] fetch_live_balance fail: {e}")
        return 0.0



def _ensure_table(con: sqlite3.Connection) -> None:
    _q(con, """
        CREATE TABLE IF NOT EXISTS internal_bank(
            day TEXT PRIMARY KEY,
            start_balance REAL NOT NULL,
            current_balance REAL NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    con.commit()

# === PATCH START ===
# 📍 TARGET: engines/live/bank_state.py:_conn
# 📆 PATCHED: 2025-11-14Z — add busy_timeout + small retry for concurrent writers
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _conn(ro=False, rw=False) -> sqlite3.Connection:
    con = sqlite3.connect(autoscalp_db(), timeout=15.0, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=8000;")   # wait up to 8 s if another writer holds lock
    con.execute("PRAGMA journal_mode=WAL;")
    con.execute("PRAGMA synchronous=NORMAL;")
    return con
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/live/bank_state.py:init_today
# 📆 PATCHED: 2025-10-17T00:40Z — ensure internal_bank refresh from Betfair daily_config
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def init_today(start_balance: float | None = None) -> None:
    """
    Ensure today's internal_bank row exists and is seeded with the real Betfair balance.
    If balance from daily_config.fetch_available_budget() differs from DB, update it.
    """
    from engines.daily_config import fetch_available_budget
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = _conn()
    try:
        _ensure_table(con)

        live_balance = fetch_available_budget()
        print(f"[bank_state] fetched Betfair live balance: {live_balance:.2f}")

        row = _q(con, "SELECT * FROM internal_bank WHERE day=?", (today,)).fetchone()

        if not row:
            _q(con, """
                INSERT INTO internal_bank(day, start_balance, current_balance, updated_at)
                VALUES(?,?,?,?)
            """, (today, live_balance, live_balance, _now_utc_str()))
            print(f"[bank_state] created new internal_bank row {today}: {live_balance:.2f}")
        else:
            cur = float(row["current_balance"] or 0.0)
            if abs(cur - live_balance) > 0.01:
                _q(con, """
                    UPDATE internal_bank
                       SET start_balance=?, current_balance=?, updated_at=?
                     WHERE day=?
                """, (live_balance, live_balance, _now_utc_str(), today))
                print(f"[bank_state] refreshed internal_bank with live Betfair balance ({live_balance:.2f})")
        con.commit()

    finally:
        con.close()
# === PATCH END ===



# engines/live/bank_state.py

# --- globals ---
_balance_now: float = 0.0
_live_balance: float = 0.0


# === PATCH START ===
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: def reset_for_live():
# ⛏️ ACTION: inject auto-init of internal_bank via init_today()
# 📆 PATCHED: 2025-10-05T09:56Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def reset_for_live():
    """Seed live balance from API budget (or fallback) and ensure internal_bank is ready."""
    global _live_balance
    try:
        from engines.daily_config import fetch_available_budget
        amt = fetch_available_budget()
        _live_balance = float(amt or 0.0)
        print(f"[bank_state] Live balance initialised: {_live_balance:.2f}")
    except Exception as e:
        _live_balance = 500.0
        print(f"[bank_state] fallback balance applied: {_live_balance:.2f} (reason: {e})")

    # --- NEW: ensure internal_bank is seeded for today ---
    try:
        from engines.live import bank_state as _bs
        _bs.init_today(_live_balance or 600.0)
        print(f"[bank_state] ensured internal_bank row for today (start={_live_balance or 600.0:.2f})")
    except Exception as e:
        print(f"[bank_state] warn: could not seed internal_bank ({e})")
# === PATCH END ===



def get_live_balance() -> float:
    """Return the seeded live balance (API/fallback)."""
    return float(_live_balance or 0.0)


_balance = 0.0

def reset_for_replay(balance: float = 1000.0):
    global _balance
    _balance = float(balance)
    print(f"[bank_state] Replay balance initialised: {_balance:.2f}")

def get_balance() -> float:
    return _balance

def apply_settlement(pnl: float):
    global _balance
    _balance += float(pnl or 0.0)



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def apply_settlement(delta: float) -> None:
    """Apply profit/loss from a settled market to today's balance."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = _conn()
    try:
        _ensure_table(con)
        row = _q(con, "SELECT current_balance FROM internal_bank WHERE day=?", (today,)).fetchone()
        if not row:
            _q(con, "INSERT INTO internal_bank(day,start_balance,current_balance,updated_at) VALUES(?,?,?,?)",
               (today, 0.0, float(delta), _now_utc_str()))
        else:
            cur = float(row["current_balance"] or 0.0)
            new_bal = cur + float(delta or 0.0)
            _q(con, "UPDATE internal_bank SET current_balance=?, updated_at=? WHERE day=?",
               (new_bal, _now_utc_str(), today))
        con.commit()
    finally:
        con.close()

def get_balance() -> float:
    """Return today's current balance (0.0 if missing)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    con = _conn()
    try:
        row = _q(con, "SELECT current_balance FROM internal_bank WHERE day=?", (today,)).fetchone()
        return float(row["current_balance"]) if row and row["current_balance"] is not None else 0.0
    finally:
        con.close()

# --- CLI entrypoint ----------------------------------------------------------
# 📍 TARGET: engines/live/bank_state.py
# 🔎 SEARCH: if __name__ == "__main__":\n    if len\(sys\.argv\) == 2 and sys\.argv\[1\]\.startswith\("init="\):
# ⛏️ ACTION: extend CLI to support plain `init`
# 📆 PATCHED: 2025-10-01T00:20Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1].startswith("init="):
        start = float(sys.argv[1].split("=",1)[1])
        init_today(start)
        print(f"[BankState] Init today with start balance {start}")
    elif len(sys.argv) == 2 and sys.argv[1] == "init":
        init_today(None)  # fetch from daily_config
        print(f"[BankState] Init today with Betfair balance from daily_config")
    elif len(sys.argv) == 2 and sys.argv[1].startswith("apply="):
        delta = float(sys.argv[1].split("=",1)[1])
        apply_settlement(delta)
        print(f"[BankState] Applied settlement delta {delta}")
    else:
        bal = get_balance()
        print(f"[BankState] Current balance: {bal}")
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

