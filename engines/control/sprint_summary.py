#!/usr/bin/env python3
"""
sprint_summary.py — Interactive Sprint Analyzer
------------------------------------------------
Scans engines/sprints/ for sprint files, lets you select one,
summarises its progress, and suggests the next sprint scaffold.
"""

import os, re, importlib.util, json
from datetime import datetime

# ──────────────────────────────────────────────────────────────────────────────
# Helper: Locate sprint files
# ──────────────────────────────────────────────────────────────────────────────
def find_sprint_files():
    """Return list of sprint files from engines/sprints/ matching '*sprint*.py'."""
    base_dir = os.path.join("engines", "sprints")
    if not os.path.isdir(base_dir):
        raise FileNotFoundError("No 'engines/sprints/' directory found.")
    return sorted(
        [os.path.join(base_dir, f) for f in os.listdir(base_dir)
         if "sprint" in f.lower() and f.endswith(".py")]
    )

def pick_sprint_file(files):
    """Prompt user to choose which sprint file to open."""
    print("\n📁 Available Sprint Files:")
    for i, f in enumerate(files, 1):
        print(f"  [{i}] {os.path.basename(f)}")
    while True:
        try:
            choice = int(input("\nSelect sprint number to view → ").strip())
            if 1 <= choice <= len(files):
                return files[choice - 1]
        except Exception:
            pass
        print("Invalid selection. Try again.")

# ──────────────────────────────────────────────────────────────────────────────
# Core Sprint Load + Analysis
# ──────────────────────────────────────────────────────────────────────────────
def load_sprint(path):
    spec = importlib.util.spec_from_file_location("SPRINT", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return getattr(module, "SPRINT", None), path

def analyse_tasks(sprint):
    phases = sprint.get("phases", {})
    totals, done = 0, 0
    pending = []

    for name, data in phases.items():
        tasks = data.get("tasks", [])
        for t in tasks:
            totals += 1
            if t.strip().startswith(("✅", "🏁")):
                done += 1
            else:
                pending.append((name, t))
    ratio = round(100 * done / max(1, totals), 1)
    return ratio, pending

def suggest_next_sprint(path, sprint):
    version = sprint.get("version", "0.0")
    base, _ = version.split(".")[0], version.split(".")[-1]
    new_ver = f"{base}.{int(version.split('.')[-1])+1}" if "." in version else f"{float(version)+0.1}"
    return {
        "from_version": version,
        "suggested_version": new_ver,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "next_phase": "Phase 7B – Dashboard Integration",
        "actions": [
            "Review pending tasks from previous sprint",
            "Verify schema consistency across new intelligence views",
            "Validate Playbooks ingestion and cross-source tagging",
            "Plan Dashboard wiring + Control Center hooks",
        ],
    }

# ──────────────────────────────────────────────────────────────────────────────
# Display summary
# ──────────────────────────────────────────────────────────────────────────────
def show_summary(path, sprint):
    ratio, pending = analyse_tasks(sprint)
    print(f"\n📊 Sprint Summary — {os.path.basename(path)}")
    print("=" * 70)
    print(f"Version: {sprint['version']}")
    print(f"Current Phase: {sprint['current_phase']}")
    print(f"Overall Completion: {ratio}%")
    print("-" * 70)

    if pending:
        print("Pending tasks:")
        for phase, task in pending[:10]:
            print(f"  [{phase}] {task}")
        if len(pending) > 10:
            print(f"  ... and {len(pending)-10} more")
    else:
        print("🎯 All tasks complete!")

    suggestion = suggest_next_sprint(path, sprint)
    print("=" * 70)
    print("Next Sprint Suggestion:")
    print(json.dumps(suggestion, indent=2))
    print("=" * 70)
    print("Tip: run with '--json' to export machine-readable output.\n")

# === PATCH START ===
# 📍 TARGET: engines/control/sprint_summary.py
# 📆 PATCHED: 2025-10-31Z — unified CLI entry + recent ideas integration
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import os, json, sys
from datetime import datetime

def _show_recent_ideas(n: int = 3):
    """Display last N ideas from data/ideas.log."""
    path = os.path.join("data", "ideas.log")
    if not os.path.exists(path):
        return
    try:
        with open(path, "r") as f:
            lines = [ln.strip() for ln in f.readlines() if ln.strip()]
        if not lines:
            return
        print("\n=== RECENT IDEAS ===")
        for ln in lines[-n:]:
            print(ln)
        print()
    except Exception as e:
        print(f"[sprint_summary] warn: could not read ideas.log ({e})")


# ──────────────────────────────────────────────────────────────────────────────
# CLI entry (single definitive block)
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    sprint_files = find_sprint_files()
    if not sprint_files:
        print("❌ No sprint files found in engines/sprints/")
        sys.exit(1)

    # JSON export mode
    if "--json" in sys.argv:
        sprint_path = sprint_files[-1]  # default to latest
        sprint, _ = load_sprint(sprint_path)
        ratio, pending = analyse_tasks(sprint)
        data = {
            "sprint_file": os.path.basename(sprint_path),
            "version": sprint["version"],
            "completion_percent": ratio,
            "pending_tasks": pending,
            "suggested_next": suggest_next_sprint(sprint_path, sprint),
            "generated_at": datetime.now().isoformat() + "Z"
        }
        print(json.dumps(data, indent=2))
        sys.exit(0)

    # Interactive summary mode
    sprint_path = pick_sprint_file(sprint_files)
    sprint, _ = load_sprint(sprint_path)
    show_summary(sprint_path, sprint)

    # ✅ show_recent_ideas automatically at the end
    _show_recent_ideas()
# === PATCH END ===

