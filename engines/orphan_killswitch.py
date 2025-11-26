# === PATCH START ===
# 📍 TARGET: engines/orphan_killswitch.py
# 🔎 SEARCH: ^\Z
from __future__ import annotations
import os
def orphan_enabled() -> bool:
    # Default OFF unless explicitly re-enabled
    val = os.environ.get("AUTOSCALP_ORPHAN_ENABLED", "0").strip()
    return val in ("1", "true", "TRUE", "yes", "YES")
# === PATCH END ===
