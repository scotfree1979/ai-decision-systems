#!/usr/bin/env python3
"""
DB CLUSTER BUILDER V2
---------------------
Consumes:
    data/db_map_v2.json

Produces:
    data/db_clusters_v2.json
    data/db_clusters_v2.py

Cluster output:
    {
        "families": [...],
        "attaches": [...],
        "mode": "setup" | "live" | "both"
    }
"""

import os, json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")

MAP_JSON = os.path.join(DATA, "db_map_v2.json")
OUT_JSON = os.path.join(DATA, "db_clusters_v2.json")
OUT_PY   = os.path.join(DATA, "db_clusters_v2.py")

print("====== DB CLUSTER BUILDER V2 ======")

if not os.path.exists(MAP_JSON):
    raise SystemExit(f"[ERR] Cannot find {MAP_JSON}")

with open(MAP_JSON, "r") as f:
    db_map = json.load(f)

clusters = {}

for file_path, calls in db_map.items():
    fams = set()
    attaches = set()
    modes = set()

    for c in calls:
        fam = c.get("family", "unknown")
        if fam != "unknown":
            fams.add(fam)

        for a in c.get("attaches", []):
            attaches.add(a)

        modes.add(c.get("mode", "both"))

    # If family known but no attach list → default attaches = families
    if fams and not attaches:
        attaches = set(fams)

    # Mode resolution
    mode = "both"
    if len(modes) == 1:
        mode = modes.pop()

    # GUI ALWAYS setup (belt + braces)
    if file_path.lower().startswith("gui/") or file_path.lower().startswith("gui\\"):
        mode = "setup"

    clusters[file_path] = {
        "families": sorted(fams),
        "attaches": sorted(attaches),
        "mode": mode,
    }

with open(OUT_JSON, "w") as f:
    json.dump(clusters, f, indent=2)
print(f"[write] {OUT_JSON}")

with open(OUT_PY, "w") as f:
    f.write("# AUTO-GENERATED — DO NOT EDIT\n")
    f.write("DB_CLUSTERS_V2 = ")
    json.dump(clusters, f, indent=2)
    f.write("\n\n")
    f.write("def cluster_for(path: str):\n")
    f.write("    return DB_CLUSTERS_V2.get(path, {})\n")
print(f"[write] {OUT_PY}")

print("[summary] Files with clusters:", len(clusters))
print("[summary] DONE")
