from signal_memory_engine.ram_reader import get_ram_snapshot
from db.signal_memory_db_writer import save_snapshot_to_ram
from signal_memory_engine.blueprint_partial import match_blueprint_partial
from collections import defaultdict
import time
import uuid


def enrich(self, signal):
    key = (signal.get("marketId"), signal.get("selectionId"))
    snapshot = get_ram_snapshot(*key).get("snapshot", {})

    signal["tick_pattern"] = self.classify_tick_pattern_from_memory(
        signal["marketId"], signal["selectionId"]
    )
    snapshot["tick_pattern"] = signal["tick_pattern"]
    # ✅ Fix: snapshot, not runner
    snapshot["odds_history"] = snapshot.get("tick_trail", [])
    save_snapshot_to_ram(key[0], key[1], snapshot)

    if not snapshot:
        return signal

    signal["tick_pattern"] = snapshot.get("tick_pattern", "flat")
    signal["direction_bias"] = snapshot.get("direction_bias", "flat")
    signal["range_low"] = snapshot.get("range_low")
    signal["range_high"] = snapshot.get("range_high")

    anchor = snapshot.get("anchor_odd")
    oc_snaps = snapshot.get("oc_snapshots", {})
    latest_label = sorted(oc_snaps.keys())[-1] if oc_snaps else None
    latest_oc = oc_snaps.get(latest_label) if latest_label else None

    if anchor and latest_oc:
        move_type = snapshot.get("tick_pattern", "flat")
        direction = signal.get("scalp_direction")
        pattern_key = f"{snapshot.get('direction_bias', 'flat')}→{latest_label}→{move_type}→{direction}"
        if pattern_key in self.KNOWN_BLUEPRINT_PATTERNS:
            signal["blueprint_match"] = pattern_key

    outcomes = snapshot.get("signal_outcomes", [])
    failures = [o for o in outcomes if o["result"] != "success"]

    if len(failures) >= 2:
        print(f"⚠️ Runner {key[1]} has failed {len(failures)} times — consider blocking.")

    return signal
