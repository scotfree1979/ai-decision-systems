# /engines/decision_engine/microscalper/plan_builder.py

from typing import Dict, Any

def build_plan_from_msc(msc_plan: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert MicroScalperEngine output plan into a standard
    DecideOnce/LiveRouter-compatible plan dict.

    MicroScalper plan fields:
        enter: bool
        close: bool (optional)
        role: CHILD
        family: MSC / MSC_RISK / MSC_IP
        subtype: micro strategy subtype
        direction: BACK/LAY
        target_ticks: int
        size: float
        px: entry price
        why: text
        parent_id: (RiskEngine only)
    """

    plan = {
        "enter": msc_plan.get("enter", False),
        "close": msc_plan.get("close", False),

        "role": msc_plan.get("role", "CHILD"),
        "family": msc_plan.get("family", "MSC"),
        "subtype": msc_plan.get("subtype"),

        "direction": msc_plan.get("direction"),
        "target_ticks": msc_plan.get("target_ticks"),
        "size": float(msc_plan.get("size") or 0.0),
        "px": msc_plan.get("px"),

        "why": msc_plan.get("why", "msc"),
        "parent_id": msc_plan.get("parent_id"),
    }

    return plan
