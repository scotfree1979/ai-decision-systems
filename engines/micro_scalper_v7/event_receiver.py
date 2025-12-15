# engines/micro_scalper_v7/event_receiver.py

from collections import defaultdict
from engines.mastery import event_sink

# In-memory learning buffer (LIVE safe)
MSC_OUTCOMES = defaultdict(lambda: {"GOOD": 0, "BAD": 0})

def _on_plan_outcome(event: dict):
    """
    Receive router-emitted plan outcomes.
    This is a passive listener only — no execution side effects.
    """
    try:
        if event.get("type") != "plan_outcome":
            return

        engine = event.get("engine")
        outcome = event.get("outcome")

        if not engine or outcome not in ("GOOD", "BAD"):
            return

        MSC_OUTCOMES[engine][outcome] += 1

    except Exception:
        # NEVER allow learning to break trading
        pass


# Subscribe ONCE at import time
event_sink.subscribe(_on_plan_outcome)


def get_engine_outcomes(engine: str) -> dict:
    """
    Read-only accessor for tick logic or diagnostics.
    """
    return dict(MSC_OUTCOMES.get(engine, {}))
