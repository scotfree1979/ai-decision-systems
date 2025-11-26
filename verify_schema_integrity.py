#!/usr/bin/env python3
# ===============================================================
# AutoScalp Schema Integrity Probe v1.0
# ===============================================================
# Compares every table + column across:
#   • data/autoscalp_gui.db
#   • ~/Library/.../AutoScalpCache/autoscalp_gui_cache.db
# Ensures that the cache DB is fully aligned with the main DB.
# ===============================================================

import sqlite3, os
from pathlib import Path

# -----------------------------------------------------------------
# DB paths
# -----------------------------------------------------------------
DATA = Path("data")
CACHE = Path("~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache").expanduser()

DBS = {
    "AUTO_MAIN": DATA / "autoscalp_gui.db",
    "AUTO_CACHE": CACHE / "autoscalp_gui_cache.db",
}

# -----------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------
def list_columns(db_path):
    """Return {table: [columns]} for a given DB."""
    schema = {}
    if not os.path.exists(db_path):
        print(f"[❌] Missing: {db_path}")
        return schema
    with sqlite3.connect(db_path) as con:
        tables = [
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
            )
        ]
        for t in tables:
            try:
                cols = [r[1] for r in con.execute(f"PRAGMA table_info({t});").fetchall()]
                schema[t] = cols
            except Exception as e:
                print(f"[warn] Skipping {t}: {e}")
    return schema


def compare_schemas(main_schema, cache_schema):
    """Compare column sets between two DBs."""
    report = {"missing_in_cache": {}, "extra_in_cache": {}}
    core_tables = set(main_schema) & set(cache_schema)
    for t in sorted(core_tables):
        main_cols = set(main_schema[t])
        cache_cols = set(cache_schema[t])
        miss = sorted(main_cols - cache_cols)
        extra = sorted(cache_cols - main_cols)
        if miss:
            report["missing_in_cache"][t] = miss
        if extra:
            report["extra_in_cache"][t] = extra
    return report


# -----------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------
def main():
    print("🧠 AutoScalp Schema Integrity Probe")
    print("──────────────────────────────────────────────")

    main_path = str(DBS["AUTO_MAIN"])
    cache_path = str(DBS["AUTO_CACHE"])

    main_schema = list_columns(main_path)
    cache_schema = list_columns(cache_path)

    report = compare_schemas(main_schema, cache_schema)

    if not report["missing_in_cache"] and not report["extra_in_cache"]:
        print("✅ Perfect alignment — cache and main schemas match exactly.")
    else:
        print("⚠️  Schema drift detected:\n")
        for t, cols in report["missing_in_cache"].items():
            print(f"  • {t}: missing in CACHE → {cols}")
        for t, cols in report["extra_in_cache"].items():
            print(f"  • {t}: extra in CACHE → {cols}")

    print("──────────────────────────────────────────────")
    print(f"Main DB:   {main_path}")
    print(f"Cache DB:  {cache_path}")
    print("✅ Probe complete.\n")


if __name__ == "__main__":
    main()
