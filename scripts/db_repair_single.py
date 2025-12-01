#!/usr/bin/env python3
"""
AutoScalp — Single Database Repair Engine
-----------------------------------------

Repairs ONE database with the following steps:

1. Check integrity (PRAGMA).
2. If OK → exit.
3. Remove WAL & SHM files.
4. Retry PRAGMA.
5. If still corrupt:
        - If cloud copy healthy → clone cloud → rebuild WAL → success.
        - If local copy healthy → clone local → rebuild WAL → success.
6. If both corrupt → return failure (db_schema_rebuild.py will take over)

Usage:
python3 db_repair_single.py --name autoscalp_gui --local /path/db --cloud /path/cloud.db
"""

import argparse, sqlite3, shutil, os
from pathlib import Path

def check_ok(p):
    try:
        con = sqlite3.connect(str(p))
        row = con.execute("PRAGMA integrity_check;").fetchone()
        con.close()
        return str(row[0]).lower() == "ok"
    except Exception:
        return False

def clean_wal(path):
    wal = Path(str(path) + "-wal")
    shm = Path(str(path) + "-shm")
    if wal.exists(): wal.unlink()
    if shm.exists(): shm.unlink()

def copy_db(src, dst):
    shutil.copy2(src, dst)
    clean_wal(dst)

def rebuild_wal(path):
    """Force SQLite to rebuild WAL from scratch."""
    try:
        con = sqlite3.connect(str(path))
        con.execute("PRAGMA journal_mode=WAL;")
        con.commit()
        con.close()
    except Exception:
        pass

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--local")
    ap.add_argument("--cloud")
    args = ap.parse_args()

    local = Path(args.local)
    cloud = Path(args.cloud) if args.cloud and args.cloud.strip() else None

    print(f"\n📌 Repairing DB: {args.name}")
    print(f"   Local: {local}")
    if cloud: print(f"   Cloud: {cloud}")

    # 1. Initial integrity check
    if check_ok(local):
        print("   ✅ Local DB healthy → exit")
        return

    print("   ❌ Local DB CORRUPTED")

    # 2. Try WAL/SHM cleanup
    print("   🧹 Cleaning WAL/SHM…")
    clean_wal(local)

    if check_ok(local):
        print("   ✅ Recovered after WAL cleanup")
        rebuild_wal(local)
        return

    # 3. Try cloud rescue
    if cloud and cloud.exists() and check_ok(cloud):
        print("   🟦 Cloud DB healthy → cloning to local")
        copy_db(cloud, local)
        rebuild_wal(local)
        print("   ✅ Local repaired from cloud")
        return

    # 4. Try local → cloud rescue (if cloud is dead but local may recover)
    if cloud and not check_ok(cloud) and check_ok(local):
        print("   🟩 Local is good → repairing cloud")
        copy_db(local, cloud)
        rebuild_wal(cloud)
        print("   ✅ Cloud repaired from local")
        return

    # 5. Both corrupted → escalate
    print("   🔥 Both DB copies corrupted → handover to full schema rebuild")
    exit(2)   # tell db_repair_all that schema rebuild is needed

if __name__ == "__main__":
    main()
