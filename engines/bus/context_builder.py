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
from engines.risk.risk_price_helper_v2 import get_legacy_parent_odds_snapshot

def build_ctx_v7():
    """
    Returns (scope_map, base_context)
    scope_map = { mid: [sid1, sid2, ...] }
    """

    # --------------------------------------------
    # MSC / Parent-driven universe
    # --------------------------------------------
    parents = get_legacy_parent_odds_snapshot()

    if parents:
        scope_map = {}

        for p in parents:
            mid = p["marketId"]
            sid = p["selectionId"]

            scope_map.setdefault(mid, []).append(sid)

        ctx = {
            "run_id": None,
            "phase": "LIVE",
            "source": "PARENT_DRIVEN",
        }

        return scope_map, ctx

    # --------------------------------------------
    # Legacy / Market-driven fallback
    # --------------------------------------------
    snap = build_and_maintain_scope(show_dashboard=False)

    scope_map = {
        m["marketId"]: list(m.get("active_sids") or [])
        for m in snap.get("markets", [])
    }

    ctx = {
        "run_id": None,
        "phase": "LIVE",
        "source": "MARKET_DRIVEN",
    }

    return scope_map, ctx


# === PATCH END ================================================================
