# /engines/decision_engine/microscalper/router.py

from typing import Dict, Any, Optional

from engines.micro_scalper_v7.micro_scalper_engine import MicroScalperEngine
from .context_adapter import build_msc_context
from .plan_builder import build_plan_from_msc


# Instantiate a single global MicroScalperEngine for DecideOnce
MSC = MicroScalperEngine()

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/microscalper/router.py
# 🔎 SEARCH: def run_microscalper(ctx)
# 📆 PATCHED: 2025-11-28 — Log MicroScalper plans & outcomes into Mastery event sink
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from engines.mastery.event_sink import record_decision, record_outcome

def run_microscalper(ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    MicroScalper → DecideOnce router.
    Now logs MSC plans into Mastery event_sink so that
    Mastery learns from MSC exploratory, risk, and in-play actions.
    """
    msc_ctx = build_msc_context(ctx)
    msc_result = MSC.tick(msc_ctx)

    if not msc_result:
        return None

    # Convert MSC output into DecideOnce/LiveRouter-compatible plan
    plan = build_plan_from_msc(msc_result)

    # 🔥 NEW: Log the MicroScalper plan into Mastery event sink
    try:
        record_decision(plan, ctx)
    except Exception as e:
        if ctx.get("logger"):
            ctx["logger"](f"[MSC][event_sink] record_decision warn: {e}")

    # When MSC signals a close, also record an outcome event
    if plan.get("close"):
        try:
            record_outcome(plan, ctx)
        except Exception as e:
            if ctx.get("logger"):
                ctx["logger"](f"[MSC][event_sink] record_outcome warn: {e}")

    return plan

# === PATCH END ===

