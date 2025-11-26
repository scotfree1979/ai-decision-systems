#!/usr/bin/env python3
import os, shutil, subprocess
from datetime import datetime, timedelta, timezone

# ============================================================
# make_reports_daily hybrid
#   • Prompt: today / yesterday / last7
#   • Always outputs into reports/<YYYY-MM-DD>/runN/
#   • Collects 4 CSVs from make_daily_review.py
# ============================================================

def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()

def _yesterday() -> str:
    return (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()

def _next_run_dir(base_dir: str) -> str:
    """Find the next run folder (run1, run2, …) inside base_dir."""
    n = 1
    while True:
        run_dir = os.path.join(base_dir, f"run{n}")
        if not os.path.exists(run_dir):
            os.makedirs(run_dir, exist_ok=True)
            return run_dir
        n += 1

def main():
    print("Select report range:")
    print("1) Today")
    print("2) Yesterday")
    print("3) Last 7 days")
    choice = input("Enter choice [1-3]: ").strip()

    if choice == "1":
        label = "today"
        day = _today()
        args = []
    elif choice == "2":
        label = "yesterday"
        day = _yesterday()
        args = ["--yesterday"]
    elif choice == "3":
        label = "last7"
        day = f"{_today()}_last7"
        args = ["--last7"]
    else:
        print("[ERR] Invalid choice.")
        return

    reports_root = "reports"
    day_dir = os.path.join(reports_root, day)
    os.makedirs(day_dir, exist_ok=True)
    run_dir = _next_run_dir(day_dir)

    # Run the review harness
    try:
        subprocess.run(
            ["python3", "scripts/reports/make_daily_review.py"] + args,
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"[ERR] make_daily_review.py failed: {e}")
        return

    outputs = [
        "runner_story.csv",
        "strategy_story.csv",
        "scalp_summary.csv",
        "opportunity_story.csv",
    ]

    moved = 0
    for base in outputs:
        src = os.path.join(reports_root, base)
        dst = os.path.join(run_dir, base)
        if os.path.exists(src):
            shutil.move(src, dst)
            print(f"[OK] Wrote {dst}")
            moved += 1
        else:
            print(f"[WARN] Missing expected output: {src}")

    print(f"[DONE] {label} reports → {run_dir} ({moved}/{len(outputs)} files moved)")

if __name__ == "__main__":
    main()
