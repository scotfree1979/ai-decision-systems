# blueprint_loader.py
# Loads blueprint_signals_YYYY-MM-DD.json

from __future__ import annotations

# === PATCH START ===
# 📍 TARGET: engines/mastery/blueprint_loader.py
# 📆 PATCHED: 2025-10-28Z — redirect legacy loader to new blueprint_cache
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
try:
    from engines import blueprint_cache as _bp_bridge
    from engines.blueprint import runtime as _bp_runtime
    # Hydrate immediately
    _bp_bridge.reload_cache(verbose=False)
    print("[blueprints] cache reloaded via bridge (mastery redirect)")
except Exception as e:
    print(f"[blueprints] bridge warn in loader: {e}")
# === PATCH END ===

import os  # ← needed for join/dirname in get_known_blueprint_patterns
try:
    from engines.config_paths import autoscalp_db
except Exception:
    from config_paths import autoscalp_db
# === PATCH END ===

import json
from datetime import date, timedelta

# === PATCH START ===
# 📍 TARGET: engines/blueprint_loader.py
# 🔎 SEARCH: ^def get_known_blueprint_patterns\(\):[\s\S]*$
# ⛏️ ACTION: replace the function so it ALWAYS returns a container (dict), never None

def get_known_blueprint_patterns():
    """
    Load yesterday's blueprint_signals_YYYY-MM-DD.json from the data/blueprints folder.
    Always returns a container (dict). On any failure, returns {} (never None).
    """
    import json
    from datetime import date, timedelta

    yday = (date.today() - timedelta(days=1)).isoformat()
    outdir = os.path.join(os.path.dirname(autoscalp_db()), "blueprints")
    fn = os.path.join(outdir, f"blueprint_signals_{yday}.json")

    if not os.path.exists(fn):
        print(f"ℹ️ No prior blueprint file for {yday} in {outdir} (continuing)")
        return {}

    try:
        with open(fn, "r") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️ Failed to read prior blueprints {fn}: {e}")
        return {}

    # Normalize: prefer dict keyed by pattern_key; convert lists if needed
    if isinstance(data, dict):
        return data
    if isinstance(data, list):
        try:
            # Convert list of items (each with "pattern_key") into a dict keyed by pattern_key
            as_dict = {}
            for item in data:
                if isinstance(item, dict):
                    k = item.get("pattern_key")
                    if k:
                        as_dict[k] = item
            return as_dict
        except Exception:
            return {}
    # Unknown format
    return {}
# === PATCH END ===


_CACHE = {"bp": {"day": None, "patterns": [], "loaded_ts": 0.0}}

# === PATCH START ===
# 📍 TARGET: engines/blueprint_loader.py:get_blueprints_for_market
# 📆 PATCHED: 2025-10-28Z — surface-aware selector (flat/jumps unified loader)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, json, time
from datetime import datetime, timezone, date

def _bp_path_for(surface: str) -> str:
    """Return the latest JSON for the given surface ('flat' or 'jumps')."""
    root = os.path.join(os.path.dirname(autoscalp_db()), "blueprints")
    day = date.today().isoformat()
    surface = (surface or "").lower()
    # try exact flat/jumps first
    direct = os.path.join(root, f"blueprint_signals_{surface}_{day}.json")
    if os.path.exists(direct):
        return direct
    # fallback: any file matching pattern (e.g. flat_middle_handicap)
    import glob
    matches = sorted(glob.glob(os.path.join(root, f"blueprint_signals_{surface}_*.json")))
    if matches:
        return max(matches, key=os.path.getmtime)
    return ""

def get_blueprints_for_market(market_name: str | None) -> dict:
    """
    Surface-aware loader: returns dict of patterns for flat or jumps races.
    - Detects surface from market name if provided.
    - Reads blueprint_signals_flat_<date>.json or blueprint_signals_jumps_<date>.json.
    """
    # --- Determine surface ---
    surface = "flat"
    if market_name:
        n = market_name.lower()
        if any(k in n for k in ("hurdle", "chase", "nhf", "jump", "hrd", "chs")):
            surface = "jumps"

    path = _bp_path_for(surface)
    if not path or not os.path.exists(path):
        print(f"[blueprints] no blueprint_signals_{surface}_*.json found")
        return {}

    try:
        with open(path, "r") as f:
            data = json.load(f)
        if isinstance(data, dict) and "pattern_dict_export" in data:
            data = data["pattern_dict_export"]
        print(f"[blueprints] loaded {len(data)} patterns for {surface} from {os.path.basename(path)}")
        return data
    except Exception as e:
        print(f"[blueprints] load warn for {surface}: {e}")
        return {}
# === PATCH END ===



def _blueprints_dir():
    try:
        from engines.config_paths import blueprints_dir
        return blueprints_dir()
    except Exception:
        return os.path.join(os.getcwd(), "data", "blueprints")

def _today_utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

def load_signals_for_day(day: str | None = None, root: str | None = None, key: str = "bp") -> list[dict]:
    day = day or _today_utc()
    root = root or _blueprints_dir()

    candidates = [
        f"blueprint_signals_{day}.json",   # builder format we saw
        f"blueprints_signals_{day}.json",
        f"blueprints_{day}.json",
    ]
    tried = []
    for name in candidates:
        p = os.path.join(root, name)
        tried.append(p)
        if not os.path.exists(p):
            continue
        try:
            with open(p, "r") as f:
                payload = json.load(f)
            # tolerate different envelopes
            patterns = (payload.get("patterns")
                        or payload.get("signals")
                        or (payload if isinstance(payload, list) else []))
            _CACHE[key] = {"day": day, "patterns": patterns, "loaded_ts": time.time()}
            print(f"[blueprints] loaded {len(patterns)} patterns from {p} into key={key}")
            return patterns
        except Exception as e:
            print(f"[blueprints] failed to load {p}: {e}")

    # not found: make it explicit
    print(f"[blueprints] no file for {day} in {root}; tried {len(candidates)}: {', '.join(tried)}")
    _CACHE[key] = {"day": day, "patterns": [], "loaded_ts": time.time()}
    return []

def ensure_today_loaded(key: str = "bp", max_age_s: int = 300) -> list[dict]:
    day = _today_utc()
    entry = _CACHE.get(key) or {}
    if (entry.get("day") != day) or (time.time() - float(entry.get("loaded_ts", 0)) > max_age_s) or (not entry.get("patterns")):
        return load_signals_for_day(day=day, key=key)
    return entry.get("patterns", [])


