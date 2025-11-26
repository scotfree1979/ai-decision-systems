# engines/gui_hooks.py
# Thin glue that the GUI calls. Keeps GUI free of legacy modules.

import threading

# Optional keep-alive is provided by your existing scalper_module
try:
    from scalper_module import keep_alive_loop  # pragma: no cover
except Exception:  # if not available, we simply don't start it
    def keep_alive_loop():  # no-op fallback
        pass

# We require an explicit setter so GUI controls the global token
try:
    from upgrade_import_patch import set_session_token, get_session_token  # noqa: F401
except Exception:
    # Provide a minimal in-memory setter if the module lacks one (safeguard)
    _SESSION_TOKEN = None
    _APP_KEY = None

    def set_session_token(token: str, app_key: str | None = None):
        global _SESSION_TOKEN, _APP_KEY
        _SESSION_TOKEN = token
        if app_key:
            _APP_KEY = app_key

    def get_session_token():  # type: ignore[override]
        return _SESSION_TOKEN

# Clean launch lives in engines/clean_launch.py
from engines.clean_launch import launch_data_collection


def gui_set_credentials(token: str, app_key: str | None = None, keep_alive: bool = False) -> None:
    if not token or not isinstance(token, str):
        raise ValueError("Empty session token")
    set_session_token(token, app_key)
    if keep_alive:
        threading.Thread(target=keep_alive_loop, name="BetfairKeepAlive", daemon=True).start()


def gui_start_engine(mode: str = "AUTO") -> None:
    # v0 ignores mode; we always run data-only path
    launch_data_collection(days=(0, 1), enable_scheduler=True)


def gui_run_blueprints() -> None:
    """Optional: wire your blueprint export here (no-op in v0)."""
    try:
        from engines.blueprints import run_blueprint_export  # if you have it
        run_blueprint_export()
    except Exception:
        # Not required for v0
        pass
