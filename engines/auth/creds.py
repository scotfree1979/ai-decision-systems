# === PATCH START ===
# 📍 TARGET: engines/auth/creds.py
# 🔎 SEARCH: ^\Z
from __future__ import annotations
import os, sqlite3
from typing import Optional

def _adb():
    try:
        from engines.config_paths import autoscalp_db
        con = sqlite3.connect(autoscalp_db())
    except Exception:
        con = sqlite3.connect("data/autoscalp_gui.db")
    con.row_factory = sqlite3.Row
    return con

# ----- APP KEY: DAILY CONFIG ONLY -----
def get_app_key() -> str:
    import engines.daily_config as dc
    key = getattr(dc, "BETFAIR_APP_KEY", None) or getattr(dc, "APP_KEY", None)
    if not key:
        raise RuntimeError("Daily Config missing BETFAIR_APP_KEY / APP_KEY")
    return str(key)

# ----- SESSION: Step-1 (DB) → env → daily (optional backstop) -----
def _sess_db() -> Optional[str]:
    try:
        con = _adb()
        r = con.execute("SELECT value FROM app_kv WHERE key='BETFAIR_SESSION'").fetchone()
        return str(r["v"]) if r and r["v"] else None
    except Exception:
        return None

def _sess_env() -> Optional[str]:
    for k in ("BETFAIR_SESSION","SESSION_TOKEN","BF_SESSION"):
        v = os.environ.get(k)
        if v: return str(v)
    return None

def _sess_daily() -> Optional[str]:
    try:
        import engines.daily_config as dc
        v = getattr(dc, "BETFAIR_SESSION", None) or getattr(dc, "SESSION", None)
        return str(v) if v else None
    except Exception:
        return None

def get_session_token() -> Optional[str]:
    return _sess_db() or _sess_env() or _sess_daily()

def set_session_token(token: str) -> None:
    token = (token or "").strip()
    if not token: return
    con = _adb()
    con.execute("CREATE TABLE IF NOT EXISTS app_kv (k TEXT PRIMARY KEY, v TEXT, ts TEXT)")
    con.execute(
        "INSERT INTO app_kv(k,v,ts) VALUES('BETFAIR_SESSION', ?, datetime('now','utc')) "
        "ON CONFLICT(k) DO UPDATE SET v=excluded.v, ts=excluded.ts", (token,)
    )
    con.commit(); con.close()
    os.environ["BETFAIR_SESSION"] = token
    os.environ["SESSION_TOKEN"] = token
    os.environ["BF_SESSION"] = token
    try:
        from engines.upgrade_import_patch import set_session_token as _set
        _set(token)  # notify adapters if they cache it
    except Exception:
        pass

def betfair_headers() -> dict:
    h = {
        "X-Application": get_app_key(),
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    sess = get_session_token()
    if sess:
        h["X-Authentication"] = sess
    return h
# === PATCH END ===
