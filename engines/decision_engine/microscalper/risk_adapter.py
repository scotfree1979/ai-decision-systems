# /engines/decision_engine/microscalper/risk_adapter.py

from typing import Dict, Any

def extract_risk_signals(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract signals relevant to RiskEngine operation:
      - parent_id
      - entry_side
      - stoploss events
      - oc-phase drift
      - current price
    """

    return {
        "legacy_parent_id": ctx.get("legacy_parent_id"),
        "legacy_entry_side": ctx.get("legacy_entry_side"),
        "current_price": ctx.get("current_price"),
        "stoploss_triggered_for_parent": ctx.get("stoploss_triggered_for_parent"),
        "oc_phase": ctx.get("oc_phase"),
    }
