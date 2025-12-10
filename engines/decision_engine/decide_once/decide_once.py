# engines/decision_engine/decide_once/decide_once.py
from __future__ import annotations
from typing import Optional
import importlib, os, time, sys

# ========================================================================
# 📍 TARGET: engines/decision_engine/decide_once/decide_once.py
# 🔎 SEARCH: def decide_once(run_id: str, source_override: str | None = None, logger=None):
# 🔧 ACTION: Replace entire function body with BUS-mode DecideOnce
# 📆 PATCHED: 2025-12-10 — DecideOnce now only triggers BUS and never exits loop
# ========================================================================
def decide_once(run_id: str, source_override: str | None = None, logger=None):
    """
    BUS-mode DecideOnce
    -------------------
    • NO context building
    • NO lanes
    • NO runner filtering
    • NO strategy logic
    • NO Mastery inference
    • DecideOnce simply triggers BUS for this tick
    • Always returns True so LiveLoop never exits

    BUS is now the authoritative orchestrator for:
        – engine activation
        – plan generation
        – stop-loss integration
        – Mastery routing
        – Router calls
    """
    try:
        from engines.bus.bus import BUS

        # Kick BUS for this tick — BUS handles everything
        BUS.decide(run_id=run_id)

        # ❗ LiveLoop interprets None as STOP — so always return True
        return True

    except Exception as e:
        msg = f"[DECIDE][BUS] error: {type(e).__name__}: {e}"
        if logger:
            logger(msg)
        else:
            print(msg)

        # Still return truthy so LiveLoop continues
        return True
# ========================================================================


