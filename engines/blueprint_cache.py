# === PATCH START ===
# 📍 TARGET: engines/blueprint_cache.py
# 📆 PATCHED: 2025-10-28Z — hybrid auto-hydrating bridge (fixes empty cache)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
engines/blueprint_cache.py — hybrid bridge for AutoScalp v6
Ensures legacy blueprint_loader / strategies see a hydrated cache immediately.
"""

from engines.blueprint import runtime

# Global cache shared across all modules
_BLUEPRINTS_CACHE: dict = {}

def _hydrate(verbose: bool = False):
    """Load today's blueprints JSON from runtime and fill the global cache."""
    global _BLUEPRINTS_CACHE
    try:
        obj = runtime.load_blueprints()
        patterns = obj.get("pattern_dict_export") or {}
        _BLUEPRINTS_CACHE = patterns
        if verbose:
            print(f"[blueprints] cache reloaded from latest JSON ({len(patterns)} patterns)")
    except Exception as e:
        _BLUEPRINTS_CACHE = {}
        if verbose:
            print(f"[blueprints] bridge warn: {e}")

# --- Auto-load once on import (prevents empty cache during Step 4) ---
_hydrate(verbose=False)

# === PATCH START ===
# 📍 TARGET: engines/blueprint_cache.py:reload_cache
# 📆 PATCHED: 2025-10-28Z — auto-load both flat and jumps JSONs
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def reload_cache(verbose: bool = False) -> dict:
    """Load today's flat + jumps blueprints into the shared cache."""
    import glob, os, json
    from datetime import date
    global _BLUEPRINTS_CACHE
    root = os.path.join(os.getcwd(), "data", "blueprints")
    day = date.today().isoformat()

    patterns = {"flat": {}, "jumps": {}}
    for surf in ("flat", "jumps"):
        files = sorted(glob.glob(os.path.join(root, f"blueprint_signals_{surf}_{day}.json")))
        if not files:
            continue
        fn = files[-1]
        try:
            with open(fn, "r") as f:
                data = json.load(f)
            if isinstance(data, dict):
                patterns[surf].update(data)
        except Exception as e:
            if verbose:
                print(f"[blueprints] warn {surf} load failed: {e}")

    _BLUEPRINTS_CACHE = patterns
    if verbose:
        for surf, d in patterns.items():
            print(f"[blueprints] cache ready {len(d)} patterns key={surf}")
    return _BLUEPRINTS_CACHE
# === PATCH END ===


def get(key: str, default=None):
    """Legacy API: return a blueprint pattern by key (auto-refresh if empty)."""
    if not _BLUEPRINTS_CACHE:
        _hydrate(verbose=False)
    return _BLUEPRINTS_CACHE.get(key, default)
# === PATCH END ===
