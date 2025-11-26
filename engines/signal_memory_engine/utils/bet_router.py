# bet_router.py
from bet_placer import execute_scalp

def fire_initial_scalp(signal, customer_order_ref):
    return execute_scalp(
        market_id=signal["marketId"],
        selection_id=signal["selectionId"],
        odds=signal["odds"],
        direction=signal.get("scalp_direction", "lay_to_back"),
        signal_type=signal.get("signal_type", "unknown"),
        confidence=signal.get("confidence", 0.0),
        customer_order_ref=customer_order_ref,
        stake_override=None,  # Optional
        is_exploratory=signal.get("signal_type") == "exploratory"
    )
