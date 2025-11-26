#!/usr/bin/env python3
"""
ml_reinforce_daemon.py — nightly auto-learning loop
Runs Playbooks → Digest → Consolidate → Train sequentially every 24h.
"""

import os, time, datetime, subprocess

def run_step(label, cmd):
    print(f"[auto] {label} started {datetime.datetime.now(datetime.timezone.utc)}")
    code = subprocess.call(cmd, shell=True)
    print(f"[auto] {label} finished with code {code}\n")

def main():
    while True:
        try:
            print("\n────────────────────────────────────────────")
            print(f"[auto] Learning cycle start {datetime.datetime.utcnow():%Y-%m-%d %H:%M:%S}Z")

            run_step("PLAYBOOKS",   "python3 engines/playbooks/playbooks_builder.py")
            run_step("DIGEST",      "python3 replay_digest.py")
            run_step("CONSOLIDATE", "python3 engines/consolidate_posteriors.py")
            run_step("TRAIN-ML",    "python3 engines/ml/ml_training_engine.py --days 90")

            print(f"[auto] Cycle complete — sleeping 24 h …")
            time.sleep(24*3600)  # rerun every 24 h
        except KeyboardInterrupt:
            print("[auto] stopped manually.")
            break
        except Exception as e:
            print(f"[auto] error: {e}")
            time.sleep(3600)  # retry after 1 h

if __name__ == "__main__":
    main()
