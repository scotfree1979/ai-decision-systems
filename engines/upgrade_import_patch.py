# engines/upgrade_import_patch.py
# Clean token registry: no side effects, no imports of bet_core, no env reads.

from threading import RLock
from typing import Optional

__all__ = ["set_session_token", "get_session_token", "get_app_key", "clear_credentials"]

_lock = RLock()
_token: Optional[str] = None
_app_key: Optional[str] = None

# --- Mode shim (TEST | LEARNING | LIVE) ---
_mode = "learning"  # default

def set_mode(m: str) -> None:
    global _mode
    m = (m or "").strip().lower()
    if m not in ("test", "learning", "live"):
        m = "learning"
    _mode = m

def get_mode() -> str:
    return _mode

# 📍 TARGET: engines/upgrade_import_patch.py
# 🔎 SEARCH: def get_session_token
def get_session_token() -> str:
    """Existing: returns the current Betfair session token (unchanged)."""
    try:
        import sqlite3
        from engines.config_paths import autoscalp_db
        con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
        row = con.execute("SELECT value FROM app_kv WHERE key='betfair_session_token'").fetchone()
        con.close()
        return (row["value"] if row else "").strip()
    except Exception:
        return ""

# 📍 TARGET: engines/upgrade_import_patch.py
# 🔎 SEARCH: def get_app_key
def get_app_key() -> str:
    """
    Canonical AppKey.
    Always comes from daily_config.APP_KEY; never from DB or env.
    """
    try:
        from engines.daily_config import APP_KEY as DK_APP
        return (DK_APP or "").strip()
    except Exception:
        return ""


# 📍 TARGET: engines/upgrade_import_patch.py
# 🔎 SEARCH: def persist_app_key_to_db
def persist_app_key_to_db(app: str | None = None) -> str:
    """Write the canonical app key into app_kv so child processes see the right value."""
    import sqlite3
    from engines.config_paths import autoscalp_db
    app = (app or get_app_key()).strip()
    con = sqlite3.connect(autoscalp_db())
    con.execute("CREATE TABLE IF NOT EXISTS app_kv (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("""
        INSERT INTO app_kv(key,value) VALUES('betfair_app_key',?)
        ON CONFLICT(key) DO UPDATE SET value=excluded.value
    """, (app,))
    con.commit(); con.close()
    return app




def set_session_token(token: Optional[str], app_key: Optional[str] = None) -> None:
    """Set/replace credentials from the GUI Launch step."""
    global _token, _app_key
    with _lock:
        _token = (token or "").strip() or None
        if app_key is not None:
            _app_key = (app_key or "").strip() or None

def get_session_token() -> Optional[str]:
    with _lock:
        return _token



def clear_credentials() -> None:
    """Clear both values (used on shutdown/reset)."""
    global _token, _app_key
    with _lock:
        _token = None
        _app_key = None

# ──────────────────────────────────────────────────────────────────────────────
# Strategy import compatibility + discovery
# ──────────────────────────────────────────────────────────────────────────────
def install_strategy_import_alias() -> None:
    """
    Make `import engines.strategies.*` resolve to the actual
    `engines.decision_engine.strategies.*` modules at runtime.
    No filesystem edits; works immediately for any existing loader.
    """
    import sys, types, importlib
    try:
        actual_pkg = importlib.import_module("engines.decision_engine.strategies")
    except Exception as e:
        print(f"[strategies] alias not installed: cannot import engines.decision_engine.strategies ({e})")
        return

    # lightweight namespace pkg for engines.strategies
    shim = types.ModuleType("engines.strategies")
    # mirror the package path so subpackages enumerate correctly
    shim.__path__ = getattr(actual_pkg, "__path__", [])
    sys.modules.setdefault("engines.strategies", shim)

    # alias common subpackages
    for sub in ("new", "legacy", "base", "common"):
        try:
            sys.modules[f"engines.strategies.{sub}"] = importlib.import_module(
                f"engines.decision_engine.strategies.{sub}"
            )
        except Exception:
            # not all submodules must exist; that's okay
            pass

def discover_strategies():
    """
    Return (active_new: list[str], active_legacy: list[str], discovered: list[str], reasons: list[str]).
    Scans both the decision_engine path and the legacy alias path.
    """
    try:
        from engines.decision_engine.strategies.registry import ORDER
    except Exception as e:
        # Nothing to report if registry import fails
        return [], [], [], [f"registry import failed: {e}"]

    try:
        names = [name for (name, _fn) in ORDER]
    except Exception as e:
        return [], [], [], [f"registry ORDER read failed: {e}"]

    # Heuristic split: treat obvious legacy prefixes as legacy; the rest are "new".
    legacy_prefixes = ("LEGACY", "BTL_", "OG_", "LADDER")
    leg = [n for n in names if n.startswith(legacy_prefixes) or "LEGACY" in n]
    new = [n for n in names if n not in leg]

    return new, leg, names, []
    
    import importlib, pkgutil, inspect
    pkgs = [
        ("new",    "engines.decision_engine.strategies.new"),
        ("legacy", "engines.decision_engine.strategies.legacy"),
        ("new",    "engines.strategies.new"),      # alias path (if some code still points here)
        ("legacy", "engines.strategies.legacy"),
    ]
    reasons, discovered = [], []
    active = {"new": [], "legacy": []}

    # Try both base locations
    Base = None
    for base_mod in ("engines.decision_engine.strategies.base", "engines.strategies.base", "engines.decision_engine.strategies.common"):
        try:
            m = importlib.import_module(base_mod)
            Base = getattr(m, "StrategyBase", None) or getattr(m, "BaseStrategy", None)
            if Base:
                break
        except Exception:
            pass

    for label, pkg_name in pkgs:
        try:
            pkg = importlib.import_module(pkg_name)
        except Exception as e:
            reasons.append(f"import {pkg_name} failed: {e}")
            continue
        for modinfo in pkgutil.iter_modules(getattr(pkg, "__path__", []), pkg_name + "."):
            try:
                mod = importlib.import_module(modinfo.name)
                # find classes
                for _, obj in inspect.getmembers(mod, inspect.isclass):
                    if Base and issubclass(obj, Base) and obj is not Base:
                        fq = f"{obj.__name__}@{modinfo.name}"
                        discovered.append(fq)
                        if getattr(obj, "ENABLED", True):
                            active[label].append(fq)
                    else:
                        # allow marker attr if there's no common Base found
                        if getattr(obj, "IS_STRATEGY", False):
                            fq = f"{obj.__name__}@{modinfo.name}"
                            discovered.append(fq)
                            if getattr(obj, "ENABLED", True):
                                active[label].append(fq)
            except Exception as e:
                reasons.append(f"import {modinfo.name} failed: {e}")

    return active["new"], active["legacy"], discovered, reasons

