#!/usr/bin/env python3
"""
DB Cluster Builder
------------------
Reads data/db_map.json (the raw scan output) and produces:

    data/db_clusters.json   ← per-file clusters (A only)
    data/db_clusters.py     ← Python helper module

A cluster = {
    "file": "path/to/file.py",
    "families": ["auto","bets",...]
}

Unknown families are ignored unless they are the ONLY family a file uses.
"""

import json
import os
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
MAP_JSON = os.path.join(DATA, "db_map.json")
OUT_JSON = os.path.join(DATA, "db_clusters.json")
OUT_PY   = os.path.join(DATA, "db_clusters.py")

VALID = {"auto", "bets", "mastery", "settlements"}

print("====== DB CLUSTER BUILDER ======")

if not os.path.exists(MAP_JSON):
    raise SystemExit(f"[ERR] Cannot find {MAP_JSON}")

with open(MAP_JSON, "r") as f:
    db_map = json.load(f)

clusters = {}

for file_path, calls in db_map.items():
    fams = set()

    for c in calls:
        fam = c.get("family", "unknown")
        if fam in VALID:
            fams.add(fam)

    # If a file uses NO known families, skip it unless all calls were unknown
    if not fams:
        # check if file has ONLY unknown family calls → keep it, mark as empty cluster
        all_fams = {c.get("family", "unknown") for c in calls}
        if all_fams == {"unknown"}:
            clusters[file_path] = []
        continue

    clusters[file_path] = sorted(fams)

# Write JSON
with open(OUT_JSON, "w") as f:
    json.dump(clusters, f, indent=2)
print(f"[write] {OUT_JSON}")

# Write Python helper
with open(OUT_PY, "w") as f:
    f.write("# Auto-generated DB clusters (per file)\n")
    f.write("DB_CLUSTERS = ")
    json.dump(clusters, f, indent=2)
    f.write("\n\n")
    f.write("def families_for(path: str):\n")
    f.write("    return DB_CLUSTERS.get(path, [])\n")
print(f"[write] {OUT_PY}")

# Summary
total_files = len(clusters)
multi = {k:v for k,v in clusters.items() if len(v) > 1}

print(f"[summary] Files with DB clusters: {total_files}")
print(f"[summary] Files using multiple families: {len(multi)}")

print("====== DONE ======")
