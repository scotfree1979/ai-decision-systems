# engines/session_token.py
from __future__ import annotations
import os, json, threading
from datetime import datetime, timezone
from typing import Optional

_LOCK = threading.Lock()

def _data_dir() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.abspath(os.path.join(here, os.pardir))
    d = os.path.join(root, "data")
    os.makedirs(d, exist_ok=True)
    return d

_APPKEY_FILE = os.path.join(_data_dir(), ".betfair_app_key")
_TOKEN_FILE  = os.path.join(_data_dir(), ".betfair_session_token")

def _read_text(path: str) -> Optional[str]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            s = f.read().strip()
            return s or None
    except Exception:
        return None

def _write_text(path: str, value: str) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(value.strip())
    os.replace(tmp, path)

def set_app_key(app_key: str, *, persist: bool = True) -> None:
    with _LOCK:
        os.environ["BETFAIR_APP_KEY"] = app_key.strip()
        if persist:
            _write_text(_APPKEY_FILE, app_key.strip())

def get_app_key() -> Optional[str]:
    v = os.getenv("BETFAIR_APP_KEY")
    if v:
        return v.strip()
    v = _read_text(_APPKEY_FILE)
    if v:
        return v
    # fallback to daily_config if present
    try:
        import engines.daily_config as daily_config
        ak = getattr(daily_config, "APP_KEY", None)
        return ak.strip() if isinstance(ak, str) and ak.strip() else None
    except Exception:
        return None

def set_session_token(token: str, *, persist: bool = True) -> None:
    with _LOCK:
        os.environ["BETFAIR_SESSION_TOKEN"] = token.strip()
        if persist:
            _write_text(_TOKEN_FILE, token.strip())

def get_session_token() -> Optional[str]:
    # 1) env
    v = os.getenv("BETFAIR_SESSION_TOKEN")
    if v and v.strip():
        return v.strip()
    # 2) file
    v = _read_text(_TOKEN_FILE)
    if v:
        return v
    # 3) daily_config provider (legacy)
    try:
        import engines.daily_config as daily_config
        getter = getattr(daily_config, "get_session_token", None)
        if callable(getter):
            tok = getter()
            if tok and isinstance(tok, str):
                return tok.strip()
    except Exception:
        pass
    return None

def mark_token_from_prompt(token: str) -> None:
    """
    Convenience: called after a CLI prompt. Persists + exports so other modules immediately see it.
    """
    set_session_token(token, persist=True)

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
