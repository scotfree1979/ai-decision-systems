# ============================================
# 📍 CONTENT CATEGORY TRACKER (AUTO MODE)
# Run this file daily to select + update category
# ============================================

import json
import os

TRACKER_FILE = "content_tracker.json"

DEFAULT_CATEGORIES = {
    "Problem Misunderstood": False,
    "System vs Tool": False,
    "Debugging Reality": False,
    "Thinking Shift": False,
    "Process / Workflow": False,
    "Hidden Complexity": False,
    "Why Things Fail": False,
    "Insight Moments": False,
    "Building in Public": False,
    "Value Mispricing": False
}

# --------------------------------------------
# LOAD OR INIT
# --------------------------------------------
def load_tracker():
    if not os.path.exists(TRACKER_FILE):
        save_tracker(DEFAULT_CATEGORIES)
        return DEFAULT_CATEGORIES
    with open(TRACKER_FILE, "r") as f:
        return json.load(f)

def save_tracker(data):
    with open(TRACKER_FILE, "w") as f:
        json.dump(data, f, indent=4)

# --------------------------------------------
# RESET IF COMPLETE
# --------------------------------------------
def reset_if_needed(data):
    if all(data.values()):
        print("\nAll categories used. Resetting...\n")
        return {k: False for k in data}
    return data

# --------------------------------------------
# SHOW AVAILABLE
# --------------------------------------------
def show_available(data):
    print("\nAvailable Categories:\n")
    available = [k for k, v in data.items() if not v]
    for i, cat in enumerate(available, 1):
        print(f"{i}. {cat}")
    return available

# --------------------------------------------
# MAIN FLOW
# --------------------------------------------
def main():
    data = load_tracker()
    data = reset_if_needed(data)

    available = show_available(data)

    if not available:
        print("No categories available.")
        return

    choice = input("\nSelect category number used today: ")

    try:
        choice = int(choice)
        selected = available[choice - 1]
    except:
        print("Invalid selection.")
        return

    data[selected] = True
    save_tracker(data)

    print(f"\n✅ Marked '{selected}' as used.\n")

# --------------------------------------------
if __name__ == "__main__":
    main()