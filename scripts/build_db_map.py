#!/usr/bin/env python3
"""
Build a full DB-CALL MAP from the giant all_calls.txt file.

INPUT:
    python3 build_db_map.py path/to/all\ calls.txt

OUTPUT:
    data/db_map.json     (full raw map)
    data/db_map.py       (importable Python dictionary)

The map includes:
    • file → list of function calls
    • inferred DB family (auto/bets/mastery/settlements)
    • mode = setup/live/both   (inferred heuristics)
"""

import os, sys, re, json
from collections import defaultdict

# ---------------------------------------------------------
# DB function patterns we want to detect
# ---------------------------------------------------------
DB_FUNCS = {
    "auto": [
        "open_auto_db", "connect_autoscalp_db", "auto_conn",
        "autoscalp_db", "AUTO_DB"
    ],
    "bets": [
        "open_bets_db", "connect_bets_db", "bets_conn",
        "bets_db", "BETS_DB"
    ],
    "mastery": [
        "open_mastery_db", "connect_mastery_db", "mastery_conn",
        "mastery_v7_db"
    ],
    "settlements": [
        "open_settlements_db", "connect_settlements_db", "settle_conn",
        "settlements_db"
    ],
}

# regex to find python dotted paths / functions
CALL_RE = re.compile(r"([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+|\b[a-zA-Z0-9_]+)\(")

def infer_family(call):
    """Return which DB family this call belongs to."""
    for fam, funcs in DB_FUNCS.items():
        for f in funcs:
            if f in call:
                return fam
    return "unknown"

def infer_mode(call, file):
    """
    Heuristic mode inference:
      • setup mode: GUI.py, migrations, db_preflight, Step 1
      • live mode: decision engine, odds, scope, live_router
      • both: anything shared or unclear
    """
    lf = file.lower()
    c = call.lower()

    if "gui" in lf or "step" in lf or "preflight" in lf:
        # setup-phase writers
        return "setup"

    if any(x in lf for x in [
        "orchestrator", "decide", "routes", "live", "odds", "scope", "overwatch"
    ]):
        return "live"

    return "both"


def parse_file(path):
    """Parse the giant text file and extract DB calls per file."""
    content = open(path, "r", encoding="utf-8", errors="ignore").read()

    # split into sections by filename markers like:
    # ===== FILE: engines/xxx/xxx.py =====
    SECT_RE = re.compile(r"={3,}\s*FILE:\s*(.*?)\s*={3,}", re.IGNORECASE)
    chunks = SECT_RE.split(content)

    # chunks looks like: ["", "filename1", code1, "filename2", code2, ...]
    # if no markers found, fallback to whole file
    if len(chunks) <= 1:
        return {"__whole_file__": extract_calls(content)}

    dbmap = {}
    i = 1
    while i < len(chunks):
        fn = chunks[i].strip()
        code = chunks[i+1]
        dbmap[fn] = extract_calls(code)
        i += 2

    return dbmap


def extract_calls(code):
    """Return list of structured call entries."""
    calls = CALL_RE.findall(code)

    out = []
    for call in calls:
        fam = infer_family(call)
        # skip obvious noise
        if call.startswith("__") or call in ("print", "len", "range"):
            continue
        out.append({
            "call": call,
            "family": fam,
            "mode": None,  # infer later once we know the file
        })
    return out


def infer_modes(dbmap):
    """Fill in mode for each call based on file/context."""
    for file, items in dbmap.items():
        for entry in items:
            entry["mode"] = infer_mode(entry["call"], file)
    return dbmap


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 build_db_map.py /path/to/all calls.txt")
        sys.exit(1)

    infile = sys.argv[1]
    if not os.path.exists(infile):
        print(f"ERROR: file not found: {infile}")
        sys.exit(1)

    print(f"[db_map] parsing: {infile}")

    raw_map = parse_file(infile)
    full_map = infer_modes(raw_map)

    out_json = os.path.join("data", "db_map.json")
    out_py   = os.path.join("data", "db_map.py")

    os.makedirs("data", exist_ok=True)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(full_map, f, indent=2)

    with open(out_py, "w", encoding="utf-8") as f:
        f.write("# AUTO-GENERATED — DO NOT EDIT\n")
        f.write("DB_MAP = ")
        f.write(json.dumps(full_map, indent=2))
        f.write("\n")

    print(f"[db_map] wrote {out_json}")
    print(f"[db_map] wrote {out_py}")
    print("[db_map] COMPLETE — use in config_paths: from data.db_map import DB_MAP")


if __name__ == "__main__":
    main()
