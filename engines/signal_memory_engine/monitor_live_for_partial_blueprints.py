import time
import threading
import copy
from price_math import calculate_tick_distance
from upgrade.nextgen_trading import launch_nextgen_thread


def monitor_live_for_partial_blueprints(self):
    def scan():
        while True:
            try:
                with self.unmatched_lock:
                    snapshot = copy.deepcopy(self.unmatched_signals)

                snapshot = {
                    (market_id, selection_id): signals[-3:]
                    for (market_id, selection_id), signals in snapshot.items()
                }

                if len(self.unmatched_signals) > 0:
                    print(f"📊 Unmatched snapshot keys available: {len(self.unmatched_signals)}")

                for key, history in snapshot.items():
                    print(f"🔎 Checking unmatched history for {key}: length {len(history)}")

                    if len(history) < 2:
                        continue

                    recent = history[:3]
                    tick_chain = "→".join([x["tick_pattern"] for x in recent])

                    for pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
                        if tick_chain not in pattern_key:
                            continue

                        parts = pattern_key.split("→")
                        if len(parts) < 4:
                            continue

                        direction_prefix = parts[0]
                        oc_range = parts[1]
                        exit_type = parts[2]
                        action = parts[3]

                        start_oc = int(oc_range.split("-")[0].replace("OC", ""))
                        end_oc = int(oc_range.split("-")[1].replace("OC", ""))
                        current_oc = start_oc + len(recent) - 1
                        remaining = end_oc - current_oc

                        if remaining < 1:
                            continue

                        scalp_ticks = min(3, max(1, remaining))

                        print(f"🧐 [Memory] Partial match on {pattern_key} (ticks={scalp_ticks})")

                        fake_signal = {
                            "marketId": key[0],
                            "selectionId": key[1],
                            "odds": recent[0]["odds"],
                            "scalp_direction": action,
                            "confidence": 0.74,
                            "tick_pattern": recent[0]["tick_pattern"],
                            "pattern_key": pattern_key,
                            "blueprint_match": pattern_key,
                            "signal_type": "partial_blueprint",
                            "forced": True,
                            "scalp_ticks": scalp_ticks
                        }

                        def delayed_launch(signal_copy):
                            time.sleep(0.1)
                            launch_nextgen_thread(signal_copy)

                        self.track_unmatched_signal(fake_signal)
                        break

            except Exception as e:
                print(f"⚠️ Error in partial blueprint scan: {e}")
            time.sleep(4)

    threading.Thread(target=scan, name="BlueprintScanner", daemon=True).start()
