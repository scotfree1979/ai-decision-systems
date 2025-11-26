#!/usr/bin/env python3
"""
race_distance_canonical.py — UK/IRE canonical race-distance catalogue
---------------------------------------------------------------------

Permanent mapping of all common race lengths to:
    • official metres
    • expected average completion time (seconds)
    • canonical surface classification

Used by: v_race_timing, replay trainer, mastery timing model, and dashboards.
"""

RACE_DISTANCE_CANONICAL = {
    # === FLAT & ALL-WEATHER ===============================================
    "5f":   {"metres": 1000, "avg_secs": 60,  "surface": "flat"},
    "5½f":  {"metres": 1100, "avg_secs": 66,  "surface": "flat"},
    "6f":   {"metres": 1200, "avg_secs": 72,  "surface": "flat"},
    "7f":   {"metres": 1400, "avg_secs": 84,  "surface": "flat"},
    "1m":   {"metres": 1600, "avg_secs": 96,  "surface": "flat"},
    "1m1f": {"metres": 1800, "avg_secs": 108, "surface": "flat"},
    "1m2f": {"metres": 2000, "avg_secs": 120, "surface": "flat"},
    "1m3f": {"metres": 2200, "avg_secs": 132, "surface": "flat"},
    "1m4f": {"metres": 2400, "avg_secs": 144, "surface": "flat"},
    "1m5f": {"metres": 2600, "avg_secs": 156, "surface": "flat"},
    "1m6f": {"metres": 2800, "avg_secs": 168, "surface": "flat"},
    "1m7f": {"metres": 3000, "avg_secs": 180, "surface": "flat"},
    "2m":   {"metres": 3200, "avg_secs": 192, "surface": "flat"},
    "2m1f": {"metres": 3400, "avg_secs": 204, "surface": "flat"},
    "2m2f": {"metres": 3600, "avg_secs": 216, "surface": "flat"},
    "2m3f": {"metres": 3800, "avg_secs": 228, "surface": "flat"},
    "2m4f": {"metres": 4000, "avg_secs": 240, "surface": "flat"},

    # === JUMPS (Hurdles / Chases) =========================================
    "2m":   {"metres": 3200, "avg_secs": 230, "surface": "jumps"},
    "2m½f": {"metres": 3300, "avg_secs": 240, "surface": "jumps"},
    "2m1f": {"metres": 3400, "avg_secs": 250, "surface": "jumps"},
    "2m2f": {"metres": 3600, "avg_secs": 265, "surface": "jumps"},
    "2m4f": {"metres": 4000, "avg_secs": 290, "surface": "jumps"},
    "2m5f": {"metres": 4200, "avg_secs": 305, "surface": "jumps"},
    "2m6f": {"metres": 4400, "avg_secs": 320, "surface": "jumps"},
    "2m7f": {"metres": 4600, "avg_secs": 335, "surface": "jumps"},
    "3m":   {"metres": 4800, "avg_secs": 350, "surface": "jumps"},
    "3m1f": {"metres": 5000, "avg_secs": 360, "surface": "jumps"},
    "3m2f": {"metres": 5200, "avg_secs": 370, "surface": "jumps"},
    "3m3f": {"metres": 5400, "avg_secs": 385, "surface": "jumps"},
    "3m4f": {"metres": 5600, "avg_secs": 395, "surface": "jumps"},
    "3m5f": {"metres": 5800, "avg_secs": 405, "surface": "jumps"},
    "3m6f": {"metres": 6000, "avg_secs": 415, "surface": "jumps"},
    "4m":   {"metres": 6400, "avg_secs": 440, "surface": "jumps"},
    "4m2f": {"metres": 6800, "avg_secs": 460, "surface": "jumps"},
    "4m4f": {"metres": 7200, "avg_secs": 480, "surface": "jumps"},

    # === ALL-WEATHER (identical to flat pacing) ===========================
    "aw_5f":  {"metres": 1000, "avg_secs": 60,  "surface": "all_weather"},
    "aw_6f":  {"metres": 1200, "avg_secs": 72,  "surface": "all_weather"},
    "aw_7f":  {"metres": 1400, "avg_secs": 84,  "surface": "all_weather"},
    "aw_1m":  {"metres": 1600, "avg_secs": 96,  "surface": "all_weather"},
    "aw_1m2f": {"metres": 2000, "avg_secs": 120, "surface": "all_weather"},
    "aw_1m4f": {"metres": 2400, "avg_secs": 144, "surface": "all_weather"},
    "aw_2m":  {"metres": 3200, "avg_secs": 192, "surface": "all_weather"},
}

# --- Helper lookups ---------------------------------------------------------
def normalize_distance_label(raw: str) -> str:
    """
    Normalize market distance labels (e.g. '1m 2f', '2m4f', '6F') to canonical key.
    Returns e.g. '1m2f' or '6f'.
    """
    import re
    if not raw:
        return "unknown"
    s = raw.lower().strip().replace(" ", "")
    m = re.match(r"(\d+m)?(\d+f)?", s)
    return (m.group(1) or "") + (m.group(2) or "") if m else "unknown"

def estimate_race_duration_secs(label: str) -> float:
    """
    Return expected race duration in seconds based on canonical averages.
    """
    key = normalize_distance_label(label)
    if key in RACE_DISTANCE_CANONICAL:
        return RACE_DISTANCE_CANONICAL[key]["avg_secs"]
    # fallback heuristics
    if "f" in key:
        f = int(re.findall(r"(\d+)f", key)[0])
        return f * 12.0  # 12 s per furlong flat
    if "m" in key:
        m = int(re.findall(r"(\d+)m", key)[0])
        return m * 120.0  # rough average
    return 150.0
