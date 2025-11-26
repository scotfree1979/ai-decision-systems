#!/usr/bin/env python3
"""
DB MAP BUILDER V2
-----------------
Scans the ENTIRE repo source tree and extracts EVERY database usage:

    • sqlite3.connect(...)
    • open_db(...)
    • open_*_db(...)
    • *_conn(...)
    • direct "data/*.db" paths
    • ATTACH DATABASE statements
    • any dynamic connection via variables

Produces:

    data/db_map_v2.json
    data/db_map_v2.py

RULES:
  • unknown → attach all four
  • GUI always → mode = "setup"
  • mode: inferred by file path (setup/live/both)
"""

import os, re, json
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
os.makedirs(DATA, exist_ok=True)

OUT_JSON = os.path.join(DATA, "db_map_v2.json")
OUT_PY   = os.path.join(DATA, "db_map_v2.py")

# -------------------------------------------------------------------
# DB families mapped by filename
# -------------------------------------------------------------------
FAMILY_DB_MAP = {
    "auto":        ["autoscalp_gui.db", "autoscalp_gui_cache.db"],
    "bets":        ["bets.db", "bets_cache.db"],
    "mastery":     ["mastery_v7.db", "mastery_cache.db"],
    "settlements": ["settlements.db", "settlements_cache.db"],
}
DB_TO_FAMILY = {db: fam for fam, paths in FAMILY_DB_MAP.items() for db in paths}
FOUR_FAMS = ["auto", "bets", "mastery", "settlements"]

# -------------------------------------------------------------------
# Regex patterns
# -------------------------------------------------------------------
RE_CONNECT = re.compile(r"sqlite3\.connect\s*\(\s*([\"'])(.+?)(\1)")
RE_OPEN_FUNC = re.compile(r"(open_[a-z_]+db|[a-z_]+_conn|connect_[a-z_]+db)\s*\(")
RE_ATTACH = re.compile(r"ATTACH\s+DATABASE\s+['\"](.+?\.db)['\"]", re.IGNORECASE)
RE_DB_PATH = re.compile(r"['\"](.+?\.db)['\"]")

# -------------------------------------------------------------------
# Mode inference
# -------------------------------------------------------------------
def infer_mode(filepath):
    fp = filepath.lower()
    if fp.startswith("gui/"):
        return "setup"
    if any(x in fp for x in ["preflight", "step", "setup"]):
        return "setup"
    if any(x in fp for x in ["live", "decide", "orchestrator", "odds", "scope", "router"]):
        return "live"
    return "both"

# -------------------------------------------------------------------
# Family inference
# -------------------------------------------------------------------
def infer_family_from_path(path):
    base = os.path.basename(path).lower()
    return DB_TO_FAMILY.get(base, "unknown")

def infer_family_from_function(func):
    f = func.lower()
    if "auto" in f:        return "auto"
    if "bet" in f:         return "bets"
    if "mastery" in f:     return "mastery"
    if "settlement" in f:  return "settlements"
    return "unknown"

# -------------------------------------------------------------------
# Extract DB calls
# -------------------------------------------------------------------
def extract_db_calls(filepath):
    text = open(filepath, "r", encoding="utf-8", errors="ignore").read()
    results = []

    # sqlite3.connect
    for m in RE_CONNECT.finditer(text):
        db = m.group(2)
        fam = infer_family_from_path(db)
        results.append({
            "call": "sqlite3.connect",
            "db": db,
            "family": fam,
            "attaches": []
        })

    # literal *.db
    for m in RE_DB_PATH.finditer(text):
        db = m.group(1)
        if db.endswith(".db"):
            fam = infer_family_from_path(db)
            results.append({
                "call": "literal_db",
                "db": db,
                "family": fam,
                "attaches": []
            })

    # open_*_db, *_conn, connect_*_db
    for m in RE_OPEN_FUNC.finditer(text):
        func = m.group(1)
        fam = infer_family_from_function(func)
        results.append({
            "call": func,
            "db": None,
            "family": fam,
            "attaches": []
        })

    # ATTACH
    for m in RE_ATTACH.finditer(text):
        db = m.group(1)
        fam = infer_family_from_path(db)
        results.append({
            "call": "ATTACH",
            "db": db,
            "family": fam,
            "attaches": [fam] if fam != "unknown" else FOUR_FAMS
        })

    return results

# -------------------------------------------------------------------
# Repo scanner
# -------------------------------------------------------------------
def scan_repo():
    dbmap = {}

    for root, dirs, files in os.walk(ROOT):
        if any(x in root for x in ["venv", ".venv", "__pycache__", "site-packages"]):
            continue
        for f in files:
            if not f.endswith(".py"):
                continue

            path = os.path.join(root, f)
            rel = os.path.relpath(path, ROOT)

            calls = extract_db_calls(path)
            mode = infer_mode(rel)

            # enforce GUI override here (always setup)
            if rel.lower().startswith("gui/"):
                mode = "setup"

            for c in calls:
                c["mode"] = mode
                if c["family"] == "unknown":
                    c["attaches"] = FOUR_FAMS

            dbmap[rel] = calls

    return dbmap

# -------------------------------------------------------------------
# Main
# -------------------------------------------------------------------
def main():
    print("[db_map_v2] Scanning repo…")
    mapping = scan_repo()

    with open(OUT_JSON, "w") as f:
        json.dump(mapping, f, indent=2)

    with open(OUT_PY, "w") as f:
        f.write("# AUTO-GENERATED — DO NOT EDIT\n")
        f.write("DB_MAP_V2 = ")
        f.write(json.dumps(mapping, indent=2))
        f.write("\n")

    print(f"[db_map_v2] wrote: {OUT_JSON}")
    print(f"[db_map_v2] wrote: {OUT_PY}")
    print("[db_map_v2] COMPLETE")

if __name__ == "__main__":
    main()
