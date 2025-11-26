#!/usr/bin/env python3
"""
idea_logger.py — persistent idea capture
----------------------------------------
Quickly logs ideas, insights, or improvements to a rolling text file
(data/ideas.log) with timestamps and optional tags.
"""

import os, sys
from datetime import datetime, timezone

IDEA_FILE = "data/ideas.log"
os.makedirs(os.path.dirname(IDEA_FILE), exist_ok=True)

def log_idea(text, tag="💡"):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    entry = f"[{ts}] {tag} {text.strip()}\n"
    with open(IDEA_FILE, "a") as f:
        f.write(entry)
    print(f"✅ Logged: {text.strip()}")

def list_ideas(limit=10):
    if not os.path.exists(IDEA_FILE):
        print("No ideas logged yet.")
        return
    with open(IDEA_FILE) as f:
        lines = f.readlines()[-limit:]
    print("\n=== Recent Ideas ===")
    for l in lines:
        print(l.strip())

if __name__ == "__main__":
    if len(sys.argv) > 1:
        if sys.argv[1] == "--list":
            list_ideas()
        else:
            log_idea(" ".join(sys.argv[1:]))
    else:
        print("Usage:")
        print("  python3 engines/control/idea_logger.py \"your idea here\"")
        print("  python3 engines/control/idea_logger.py --list")
