#!/usr/bin/env python3
"""
AutoScalp Control Center — Idea Logger
--------------------------------------
Logs development ideas, tasks, or future improvements into a local text file.
Used via the `idea "Title" "Description"` shell alias.
"""

import sys
import os
import datetime
import textwrap

# === Paths ===
ROOT = os.path.expanduser("~/Dev/analytics_beta_dev")
LOG_PATH = os.path.join(ROOT, "data", "idea_log.txt")

def log_idea(title: str, description: str):
    """Append a new idea entry to the log file."""
    ts = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

    entry = textwrap.dedent(f"""
    ✅ Logged idea: {title}
    {description}
    [{ts}]
    ----------------------------------------
    """).strip() + "\n"

    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(entry + "\n")

    print(f"✅ Logged: {title}")

def main():
    if len(sys.argv) < 3:
        print("Usage: idea \"Title\" \"Description\"")
        sys.exit(1)

    title = sys.argv[1]
    description = " ".join(sys.argv[2:])
    log_idea(title, description)

if __name__ == "__main__":
    main()
