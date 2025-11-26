#!/usr/bin/env python3
# maintenance.py — AutoScalp daily DB & cache maintenance

import sqlite3, os, datetime, glob

AUTO_DB = "data/autoscalp_gui.db"
BLUEPRINT_DIR = "data/blueprints"
LOG = "data/maintenance_log.txt"

def run_daily_maintenance():
    """Run WAL checkpoint, optional vacuum, and tidy blueprints."""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{stamp}] Running daily maintenance…")

    # 1️⃣  Compact the GUI database
    if os.path.exists(AUTO_DB):
        try:
            con = sqlite3.connect(AUTO_DB)
            con.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            con.execute("PRAGMA wal_checkpoint(FULL);")
            con.close()
            print("✅ WAL truncated and DB checkpointed")
        except Exception as e:
            print(f"❌ DB maintenance error: {e}")
    else:
        print("⚠️ autoscalp_gui.db not found")

    # 2️⃣  Delete blueprints older than 7 days
    if os.path.isdir(BLUEPRINT_DIR):
        old_files = []
        for f in glob.glob(os.path.join(BLUEPRINT_DIR, "*.json")):
            if os.stat(f).st_mtime < (datetime.datetime.now() - datetime.timedelta(days=7)).timestamp():
                try:
                    os.remove(f)
                    old_files.append(os.path.basename(f))
                except Exception:
                    pass
        if old_files:
            print(f"🧹 Removed {len(old_files)} old blueprint files")
        else:
            print("🧹 No old blueprints to remove")

    # 3️⃣  Log outcome
    with open(LOG, "a") as fp:
        fp.write(f"{stamp} — maintenance complete\n")

    print("✅ Maintenance complete\n")

if __name__ == "__main__":
    run_daily_maintenance()
