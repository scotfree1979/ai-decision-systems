# === PATCH START ============================================================
# 📍 TARGET: engines/bus/direction_probe.py
# 🆕 NEW FILE
# 📆 PATCHED: 2026-02-12
# ============================================================================

"""
Direction probe for Legacy.
Asks MSC Exploratory engine for directional bias without producing trades.
"""

from __future__ import annotations

def probe_msc_direction(ctx):
    try:
        from engines.micro_scalper_v7.exploratory_engine import ExploratoryEngine
        exp = ExploratoryEngine()
        d = exp.direction_only(ctx)
        return d
    except Exception:
        return None

# === PATCH END ================================================================
