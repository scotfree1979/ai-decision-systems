# === PATCH START ============================================================
# 📍 NEW FILE: engines/bus/dryrun.py
# 📆 PATCHED: 2026-02-14 — BUS dry-run
# ============================================================================

#!/usr/bin/env python3
import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from engines.bus import BUS
import time

def run(seconds=30):
    print("=== BUS DRY-RUN START ===")
    t0 = time.time()
    while time.time() - t0 < seconds:
        BUS.tick()
        time.sleep(1)
    print("=== BUS DRY-RUN END ===")

if __name__ == "__main__":
    run()

# === PATCH END ================================================================
