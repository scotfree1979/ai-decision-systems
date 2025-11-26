import time
import uuid
import json
from collections import defaultdict, deque

from db.signal_memory_db_writer import save_signal_to_stm
from signal_memory_engine.bet_router import fire_initial_scalp
from signal_memory_engine.matching_engine import evaluate_matching_opportunity


def track_unmatched_signal(signal):
    print(f"🧠 Tracking unmatched signal: {signal.get('marketId')} | {signal.get('selectionId')} | {signal.get('tick_pattern')}")
    print(f"🚨 track_unmatched_signal() was called: {signal}")

    self._track_signal_counter = getattr(self, "_track_signal_counter", defaultdict(int))
    self._last_track_reported = getattr(self, "_last_track_reported", 0)

    self._track_signal_counter[signal["marketId"]] += 1
    total = sum(self._track_signal_counter.values())
    if total != self._last_track_reported:
        self._last_track_reported = total

    self.debug_signal_counter = getattr(self, "debug_signal_counter", 0) + 1
    key = (signal.get("marketId"), signal.get("selectionId"))

    # === Use new blueprint matching logic ===
    signal = self.evaluate_matching_opportunity(signal["marketId"], signal["selectionId"])
    if not signal:
        return

    if signal.get("confidence", 0) >= 0.65 and key not in self.active_ladders:
        customer_order_ref = f"mem_{uuid.uuid4().hex[:6]}"
        response = fire_initial_scalp(signal, customer_order_ref)
        if response:
            print(f"✅ Approved signal scalp fired for {signal['selectionId']}")

    self.active_blueprints_today.add(signal["blueprint_match"])

    from signal_memory_engine.ladders import start_ladder
    start_ladder(self, key, signal)

    # Track to unmatched history
    with self.unmatched_lock:
        if key not in self.unmatched_signals:
            self.unmatched_signals[key] = []
        self.unmatched_signals[key].append({
            "odds": signal["odds"],
            "confidence": signal["confidence"],
            "tick_pattern": signal["tick_pattern"],
            "timestamp": time.time(),
            "range_low": signal["range_low"],
            "range_high": signal["range_high"]
        })

        self.live_playbooks[key]["steps"].append(f"ladder:{json.dumps(self.active_ladders[key]['rung_log'])}")
