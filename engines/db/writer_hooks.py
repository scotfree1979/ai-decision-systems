# ✅ writer_hooks_injection.py
# Injects STM, RAM, and LTM logging into signal_memory_engine workflow

from signal_memory_engine import signal_memory
from signal_memory_db_writer import (
    save_signal_to_stm,
    save_snapshot_to_ram,
    save_playbook_to_ltm
)

# --- Hook 1: STM - Save signal when it's first created ---
original_track_unmatched_signal = signal_memory.track_unmatched_signal

def hooked_track_unmatched_signal(signal):
    try:
        save_signal_to_stm(signal)
    except Exception as e:
        print(f"⚠️ STM hook failed: {e}")
    return original_track_unmatched_signal(signal)

signal_memory.track_unmatched_signal = hooked_track_unmatched_signal


# --- Hook 2: RAM - Save snapshot after memory updates ---
original_build_final_runner_snapshot = signal_memory.build_final_runner_snapshot

def hooked_build_final_runner_snapshot(market_id, selection_id):
    snapshot = original_build_final_runner_snapshot(market_id, selection_id)
    try:
        save_snapshot_to_ram(market_id, selection_id, snapshot)
    except Exception as e:
        print(f"⚠️ RAM hook failed: {e}")
    return snapshot

signal_memory.build_final_runner_snapshot = hooked_build_final_runner_snapshot


# --- Hook 3: LTM - Save full playbook when completed ---
original_finalise_playbook = signal_memory.finalise_playbook

def hooked_finalise_playbook(market_id, selection_id, result, profit):
    key = (market_id, selection_id)
    try:
        playbook = signal_memory.live_playbooks.get(key)
        if playbook:
            save_playbook_to_ltm(market_id, selection_id, playbook)
    except Exception as e:
        print(f"⚠️ LTM hook failed: {e}")
    return original_finalise_playbook(market_id, selection_id, result, profit)

signal_memory.finalise_playbook = hooked_finalise_playbook
