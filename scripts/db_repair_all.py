#!/usr/bin/env python3
"""
AutoScalp — Full Database Repair System (MASTER)
------------------------------------------------
This script does all of the following:

1. Identify all AutoScalp DBs:
      - data/autoscalp_gui.db
      - data/bets.db
      - data/settlements.db
      - data/mastery_v7.db
      - data/livecache/*.db
      - cloud mirrors (AutoScalpCache/*.db)

2. Run corruption checks (PRAGMA integrity_check).

3. For each DB:
      A. If healthy → nothing to do.
      B. If corrupted → call db_repair_single.py
      C. If BOTH copies corrupted → fallback to full schema rebuild.

4. Produces a final repair report.

Place this script in: scripts/db_repair_all.py
"""

import os, glob, subprocess, sqlite3, json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache"

# All DBs to check
DB_LIST = {
    "autoscalp_gui": DATA / "autoscalp_gui.db",
    "bets": DATA / "bets.db",
    "settlements": DATA / "settlements.db",
    "mastery_v7": DATA / "mastery_v7.db",
}

def find_livecache():
    """Return all DBs in data/livecache."""
    lc_dir = DATA / "livecache"
    if lc_dir.exists():
        return list(lc_dir.glob("*.db"))
    return []

def find_cloud_db(name):
    """Locate cloud mirror using fuzzy match."""
    matches = list(CACHE.glob(f"*{name}*.db"))
    return matches[0] if matches else None

def check_integrity(path):
    try:
        con = sqlite3.connect(str(path))
        row = con.execute("PRAGMA integrity_check;").fetchone()
        con.close()
        ok = str(row[0]).lower() == "ok"
        return ok, row[0]
    except Exception as e:
        return False, f"EXCEPTION: {e}"

def header(msg):
    print("\n" + "="*80)
    print(msg)
    print("="*80)

def run_single_repair(dbname, local_path, cloud_path):
    """
    Delegate to db_repair_single.py
    This script decides the best source and repairs local or cloud DB.
    """
    cmd = [
        "python3", str(ROOT / "scripts/db_repair_single.py"),
        "--name", dbname,
        "--local", str(local_path),
        "--cloud", str(cloud_path) if cloud_path else ""
    ]
    return subprocess.call(cmd)

def ensure_schema_rebuild(dbname, local_path):
    """Fallback when both local and cloud DBs are corrupted."""
    cmd = [
        "python3", str(ROOT / "scripts/db_schema_rebuild.py"),
        "--name", dbname,
        "--local", str(local_path),
        "--schema", str(ROOT / "Full Schema.txt")
    ]
    return subprocess.call(cmd)

def main():
    header("AutoScalp DB Repair System — FULL RUN")

    # Include livecache DBs
    for idx, lcdb in enumerate(find_livecache()):
        DB_LIST[f"livecache_{idx}"] = lcdb

    # Check each DB
    results = {}

    for name, local_path in DB_LIST.items():
        print(f"\n🔍 Checking DB: {name}")
        local_ok, local_msg = check_integrity(local_path)

        cloud_path = find_cloud_db(name)
        cloud_ok, cloud_msg = (True, "NO_CLOUD_COPY") if not cloud_path else check_integrity(cloud_path)

        results[name] = {
            "local": (local_ok, local_msg, str(local_path)),
            "cloud": (cloud_ok, cloud_msg, str(cloud_path) if cloud_path else None)
        }

        # Outcome analysis
        if local_ok:
            print(f"   ✅ LOCAL HEALTHY → no action needed")
            continue

        if cloud_ok:
            print(f"   ❌ Local corrupted → Repair from CLOUD")
            run_single_repair(name, local_path, cloud_path)
            continue

        # Neither is healthy
        print(f"   ❌ BOTH corrupted → running full schema rebuild")
        ensure_schema_rebuild(name, local_path)

    # Write final report
    report = ROOT / "scripts/db_repair_report.json"
    with open(report, "w") as f:
        json.dump(results, f, indent=2)

    print("\n📄 Repair complete! Full report saved to:", report)

if __name__ == "__main__":
    main()
