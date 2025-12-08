# === PATCH START ============================================================
# 📍 TARGET: engines/bus/context_builder.py
# 🆕 NEW FILE
# 📆 PATCHED: 2026-02-12
# ============================================================================

from __future__ import annotations
from typing import Dict, Any
from engines.decision_engine.decide_once.scope import build_and_maintain_scope
from engines.odds.odds_service import BUFFERS
from engines.live.bank_state import get_engine_budget

def build_ctx_v7():
    """
    Returns (scope_map, base_context)
    scope_map = { mid: [sid1,sid2,...] }
    """
    snap = build_and_maintain_scope(show_dashboard=False)

    scope_map = {
        m["marketId"]: list(m.get("active_sids") or [])
        for m in snap.get("markets", [])
    }

    ctx = {
        "run_id": None,
        "phase": "LIVE",

    }

    return scope_map, ctx

# === PATCH END ================================================================
