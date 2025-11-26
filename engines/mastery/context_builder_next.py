# engines/mastery/context_builder_next.py
from engines.mastery.mastery_policy import _SCOPE_STATE
from engines.market_monitor.monitor import get_market_state, allowed_for_letter
from engines.bias.engine import compute_bias
from engines.indicators.opportunities import update_opportunities
from engines.indicators.wom import compute_wom_for_scope
from engines.live import bank_state


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/mastery/context_builder_next.py
# ⛏️ PATCH: replace import block at top of build_context_from_scope

def build_context_from_scope(marketId: str | None = None,
                             selectionId: str | None = None,
                             source: str | None = None) -> tuple[dict, dict]:
    """
    Unified context builder for DecideOnce dryruns and Mastery live loops.
    If marketId/selectionId are given, context is built around that runner only.
    Otherwise, falls back to the next available market/runner in scope.

    Returns (ctx, meta).
    """
    from datetime import datetime, timezone
    # ✅ FIX: pull scope from Mastery, not from DecideOnce
    from engines.mastery.mastery_policy import _SCOPE_STATE
    from engines.market_monitor.monitor import get_market_state


    now = datetime.now(timezone.utc)

    # fallback to first market in scope if none provided
    mids = list(_SCOPE_STATE.get("mids", set()))
    if not marketId and mids:
        marketId = mids[0]

    # fallback to any active SID from MarketMonitor
    sids = []
    try:
        ms = get_market_state(str(marketId))
        runner = (ms.get("runners", {}) or {}).get(str(selectionId), {})
        band_now = runner.get("band", "UNKNOWN")
        sids = list(ms.get("runners", {}).keys()) if ms else []
    except Exception:
        pass
    if not selectionId and sids:
        selectionId = sids[0]

    # base context (you can extend this later)
    ctx = {
        "source": (source or "LIVE").upper(),
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "minutes_to_off": 10.0,
        "phase": "PRE",
        "band": band_now,
        "depth_total": 900.0,
        "matched_per_min": 280.0,
        "direction": "LAY->BACK",
    }

    meta = {
        "marketId": str(marketId),
        "selectionId": str(selectionId),
        "scope_count": len(mids),
        "runner_count": len(sids),
        "updated_at": now.isoformat(),
    }

    # optional bias integration
    try:
        from engines.bias.engine import compute_bias
        b = compute_bias(ctx)
        ctx.update(b.as_plan_fields())
        ctx["bias_why"] = b.why
    except Exception:
        pass

    return ctx, meta
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
