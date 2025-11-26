import time
from signal_memory_engine.reader_access import get_active_runner_ids as get_active_runners, get_minutes_to_off

from signal_memory_engine.snapshot_builder import build_band_json
from signal_memory_engine.ram_writer import enqueue_write
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engines.config_paths import DB_PATH




def persist_band_json_to_snapshot(runner_id: str, band_json: dict, minutes_to_off: int):
    """
    Persists the band_json for a specific runner into the runner_ram_snapshots table.
    """
    enqueue_write(
        db_path=DB_PATH,
        table_name="runner_ram_snapshots",
        data={
            "runner_id": runner_id,
            "minutes_to_off": minutes_to_off,
            "snapshot": band_json,
        }
    )


def tick_and_record_oc_data():
    """
    Iterates through all active runners, builds band_json, and persists it to snapshot DB.
    """
    runners = get_active_runners()

    for runner_id in runners:
        band_json = build_band_json(runner_id)
        minutes_to_off = get_minutes_to_off(runner_id)

        if band_json and isinstance(band_json, dict):
            persist_band_json_to_snapshot(runner_id, band_json, minutes_to_off)

        time.sleep(0.05)  # prevent overload
