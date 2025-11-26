#!/usr/bin/env python3
"""
scan_db_calls.py
-----------------------------------------
Static analyzer for the entire repo.

Walks analytics_beta_dev/ and detects ANY DB usage:
    • sqlite3.connect(...)
    • open_*_db(...)
    • connect_*_db(...)
    • *_conn(...)
    • autoscalp_db(), bets_db(), mastery_db(), settlements_db()
    • direct SQL with FROM/INSERT/UPDATE referencing known tables
    • imports from config_paths suggesting DB usage

Produces:
    data/db_map.json
    data/db_map.py

This becomes the unified source of truth for the DAL.
"""

import os, re, json, ast
from collections import defaultdict

# ------------------------------------------
# ROOT PROBE (auto-locates analytics root)
# ------------------------------------------
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, os.pardir))

TARGET_ROOT = None
candidates = [
    os.path.join(ROOT, "analytics_beta_dev"),
    ROOT,
]
for c in candidates:
    if os.path.exists(c):
        TARGET_ROOT = c
        break

if TARGET_ROOT is None:
    raise SystemExit("ERROR: could not locate analytics_beta_dev folder")

print(f"[scan] root = {TARGET_ROOT}")

# ------------------------------------------
# DB function signatures we care about
# ------------------------------------------
DB_PATTERNS = {
    "auto": [
        "open_auto_db", "auto_conn", "connect_auto_db",
        "autoscalp_db", "AUTO_DB"
    ],
    "bets": [
        "open_bets_db", "bets_conn", "connect_bets_db",
        "bets_db", "BETS_DB"
    ],
    "mastery": [
        "open_mastery_db", "mastery_conn", "connect_mastery_db",
        "mastery_v7_db"
    ],
    "settlements": [
        "open_settlements_db", "settle_conn", "connect_settlements_db",
        "settlements_db"
    ]
}

# SQL detection
SQL_RE = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.*?(FROM|INTO|UPDATE)\s+([a-zA-Z0-9_]+)", re.IGNORECASE)

# sqlite3.connect call
SQLITE_CONNECT_RE = re.compile(r"sqlite3\.connect\s*\(", re.IGNORECASE)


def detect_family(call: str) -> str:
    """Return DB family based on known function signatures."""
    for fam, pats in DB_PATTERNS.items():
        for p in pats:
            if p in call:
                return fam
    return "unknown"


def detect_mode(filepath: str) -> str:
    """Infer setup/live mode by file location."""
    fp = filepath.lower()
    if "gui" in fp or "step" in fp:
        return "setup"
    if any(x in fp for x in ["live_router", "decision_engine", "scope", "odds", "monitor"]):
        return "live"
    return "both"


def scan_python_file(path: str):
    """Extract all DB calls + SQL + sqlite3.connect patterns."""
    calls = []

    try:
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return calls

    lines = text.splitlines()

    # AST for function call detection
    try:
        tree = ast.parse(text)
    except Exception:
        tree = None

    if tree:
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                try:
                    if isinstance(node.func, ast.Attribute):
                        call_name = f"{node.func.value.id}.{node.func.attr}" if isinstance(node.func.value, ast.Name) else node.func.attr
                    elif isinstance(node.func, ast.Name):
                        call_name = node.func.id
                    else:
                        continue
                except Exception:
                    continue

                fam = detect_family(call_name)
                if fam != "unknown":
                    calls.append({
                        "line": node.lineno,
                        "call": call_name,
                        "family": fam,
                        "context": "ast",
                    })

    # Regex for SQL table detection + sqlite3.connect()
    for i, line in enumerate(lines, start=1):
        m = SQLITE_CONNECT_RE.search(line)
        if m:
            calls.append({
                "line": i,
                "call": "sqlite3.connect",
                "family": "unknown",
                "context": "sqlite3.connect",
            })

        sm = SQL_RE.search(line)
        if sm:
            table = sm.group(3)
            calls.append({
                "line": i,
                "call": f"sql:{table}",
                "family": "unknown",  # DAL will resolve based on table
                "context": "sql",
            })

    return calls


# ------------------------------------------
# MAIN SCAN
# ------------------------------------------

db_map = {}

for root, dirs, files in os.walk(TARGET_ROOT):
    for fname in files:
        if not fname.endswith(".py"):
            continue
        full = os.path.join(root, fname)
        rel = os.path.relpath(full, TARGET_ROOT)
        calls = scan_python_file(full)
        if calls:
            mode = detect_mode(rel)
            for c in calls:
                c["mode"] = mode
                c["file"] = rel
            db_map.setdefault(rel, []).extend(calls)


# ------------------------------------------
# WRITE OUTPUT
# ------------------------------------------
OUT_JSON = os.path.join(ROOT, "data", "db_map.json")
OUT_PY   = os.path.join(ROOT, "data", "db_map.py")

os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)

with open(OUT_JSON, "w", encoding="utf-8") as f:
    json.dump(db_map, f, indent=2)

with open(OUT_PY, "w", encoding="utf-8") as f:
    f.write("# AUTO-GENERATED DB MAP — DO NOT EDIT\n")
    f.write("DB_MAP = ")
    f.write(json.dumps(db_map, indent=2))
    f.write("\n")

print(f"[scan] wrote {OUT_JSON}")
print(f"[scan] wrote {OUT_PY}")
print("[scan] COMPLETE.")
