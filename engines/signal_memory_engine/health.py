from signal_memory_engine.ram_reader import get_static_snapshot


def assess_signal_health(self, market_id, selection_id):
    key = (market_id, selection_id)
    runner = get_static_snapshot().get(key)

    if not runner:
        return {
            "status": "unknown",
            "failures": 0,
            "last_result": None,
            "flagged": False
        }

    outcomes = runner.get("snapshot", {}).get("signal_outcomes", [])

    if not outcomes:
        return {
            "status": "unknown",
            "failures": 0,
            "last_result": None,
            "flagged": False
        }

    failures = [o for o in outcomes if o.get("result") != "success"]
    last_result = outcomes[-1].get("result") if outcomes else None
    flagged = len(failures) >= 2

    if flagged:
        print(f"🚫 [SignalMemory] Runner {selection_id} flagged due to repeated failures ({len(failures)} total).")

    return {
        "status": "flagged" if flagged else "healthy",
        "failures": len(failures),
        "last_result": last_result,
        "flagged": flagged
    }
