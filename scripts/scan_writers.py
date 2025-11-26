#!/usr/bin/env python3
"""
scan_writers.py
----------------
Scan the entire project for *writer modules* — any module that touches DBs, DAL,
autoscalp_gui, bets.db, settlements.db, or uses sqlite3.execute().

Outputs:
  1) List of writer modules
  2) A ready-to-paste Step-4 reload block

Usage:
    python3 scripts/scan_writers.py
"""

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Directories to scan
SCAN_DIRS = [
    "engines",
    "gui",
    "indicators",
    "adapters",
    "market_monitor",
]

# Writer detection regex
WRITER_PATTERNS = [
    r"sqlite3\.connect",
    r"\.execute\(",
    r"\.executemany\(",
    r"INSERT ",
    r"UPDATE ",
    r"DELETE ",
    r"enqueue_write",
    r"open_auto_db",
    r"open_bets_db",
    r"open_settlements_db",
    r"open_mastery_db",
]


def is_writer_file(path):
    """Return True if file likely contains DB write logic."""
    try:
        text = open(path, "r", encoding="utf-8", errors="ignore").read()
    except Exception:
        return False

    for pat in WRITER_PATTERNS:
        if re.search(pat, text, re.IGNORECASE):
            return True
    return False


def scan():
    writer_modules = []

    for root_dir in SCAN_DIRS:
        full_root = os.path.join(ROOT, root_dir)

        for folder, dirs, files in os.walk(full_root):
            for f in files:
                if not f.endswith(".py"):
                    continue
                full_path = os.path.join(folder, f)
                if is_writer_file(full_path):
                    module_rel = os.path.relpath(full_path, ROOT)
                    module_dot = module_rel.replace("/", ".").replace("\\", ".").replace(".py", "")
                    writer_modules.append(module_dot)

    return sorted(set(writer_modules))


def format_reload_block(mods):
    out = []
    out.append("        # === AUTO-GENERATED DAL RELOAD BLOCK ===")
    out.append("        import importlib")
    out.append("")
    for m in mods:
        var = "_" + m.replace(".", "_")
        out.append(f"        import {m} as {var}")
        out.append(f"        importlib.reload({var})")
        out.append("")
    out.append("        print('[LIVE DAL] reloaded all writer modules.')")
    out.append("        # === END AUTO-GENERATED RELOAD BLOCK ===")
    return "\n".join(out)


if __name__ == "__main__":
    mods = scan()

    print("\n=== WRITER MODULES DETECTED ===")
    for m in mods:
        print(m)

    print("\n=== STEP-4 RELOAD BLOCK ===")
    print(format_reload_block(mods))
