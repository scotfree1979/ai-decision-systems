# signal_memory_engine/injection.py

from signal_memory_engine.controller import SignalMemoryEngine

# Instantiate once (separate from global signal_memory)
_memory_instance = SignalMemoryEngine()

def inject_live_market_data(market_id, runners):
    """
    🔁 Public injection point from market_loop or evaluate_market_loop.
    This is the only external call needed.
    """
    _memory_instance.ingest_market_snapshot(market_id, runners)

# 💉 Injection Logic – Ladder Scalp Trigger based on OCX Band
# This logic will live inside the injection pipeline to evaluate snapshot and trigger ladder scalps

def evaluate_band_scalp_trigger(snapshot):
    """
    Evaluates whether to trigger a scalp based on OCX band delta momentum.
    We want to catch drift-drift-drift and take the 3rd tick.
    """
    deltas = snapshot.get("ocx_band_deltas", [])
    if len(deltas) < 3:
        return False

    last_three = deltas[-3:]

    # Define rules for upward drift (e.g. +1 +1 +1) or downward drift (-1 -1 -1)
    if last_three == [1, 1, 1] or last_three == [-1, -1, -1]:
        return True

    return False


def should_stop_ladder(snapshot):
    """
    Determines if ladder trading should be stopped based on reversal signal.
    Ladder must halt if a reversal pattern is detected.
    """
    return snapshot.get("ocx_band_reversal", False)


# Usage Example (in controller or runner loop):
# if evaluate_band_scalp_trigger(snapshot):
#     trigger_ladder_trade(market_id, selection_id)
# elif should_stop_ladder(snapshot):
#     stop_ladder_trade(market_id, selection_id)

