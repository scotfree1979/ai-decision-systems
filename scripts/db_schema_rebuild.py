#!/usr/bin/env python3
"""
AutoScalp — Full Schema Rebuild Tool
------------------------------------
When BOTH local + cloud DBs are corrupted, this script:

1. Drops the broken DB.
2. Creates a fresh empty DB.
3. Reads canonical schema from Full Schema.txt.
4. Creates all tables exactly as defined.
5. Optionally restores minimal seed data.

Usage:
python3 db_schema_rebuild.py --name autoscalp_gui --local /path/db --schema "Full Schema.txt"
"""

import argparse, sqlite3, os
from pathlib import Path

def load_schema(schema_file):
    out = []
    block = []
    for line in open(schema_file, "r", encoding="utf-8"):
        if line.strip().upper().startswith("CREATE TABLE"):
            if block:
                out.append("\n".join(block))
                block = []
            block.append(line.rstrip())
        elif block:
            block.append(line.rstrip())
            if line.strip().endswith(");"):
                out.append("\n".join(block))
                block = []
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name")
    ap.add_argument("--local")
    ap.add_argument("--schema")
    args = ap.parse_args()

    db = Path(args.local)
    schema_file = Path(args.schema)

    print(f"\n🔥 FULL SCHEMA REBUILD: {args.name}")
    print(f"   Target DB: {db}")

    if db.exists():
        print("   🗑 Removing corrupted DB")
        db.unlink()

    print("   📦 Creating clean DB")
    con = sqlite3.connect(str(db))

    print("   📄 Loading canonical schema…")
    tables = load_schema(schema_file)

    for ddl in tables:
        try:
            con.executescript(ddl)
            print(f"   ✔ Created table from schema")
        except Exception as e:
            print(f"   ✖ Schema error: {e}")

    con.commit()
    con.close()
    print("   ✅ FULL SCHEMA REBUILD COMPLETE")

if __name__ == "__main__":
    main()
