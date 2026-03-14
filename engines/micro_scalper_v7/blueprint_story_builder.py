import json
import os
from collections import defaultdict

BLUEPRINT_DIR = "data/blueprints"
OUTPUT_FILE = "data/blueprints/runner_story_oc_cache.json"


def load_blueprint_trades():

    files = [
        os.path.join(BLUEPRINT_DIR, f)
        for f in os.listdir(BLUEPRINT_DIR)
        if f.startswith("blueprint_signals") and f.endswith(".json")
    ]

    trades = []

    for f in files:

        try:
            with open(f, "r") as fh:
                data = json.load(fh)

            if isinstance(data, dict):

                for block in data.values():
                    block_trades = block.get("trades", [])
                    trades.extend(block_trades)

        except Exception:
            pass

    return trades


def build_runner_story(trades):

    story = defaultdict(lambda: defaultdict(lambda: {
        "samples": 0,
        "drift": 0,
        "steam": 0,
        "flat": 0,
        "ticks_total": 0
    }))

    for t in trades:

        surface = t.get("surface_key")
        move = t.get("move")
        ticks = t.get("trend_ticks", 0)

        pattern = t.get("pattern_key", "")

        oc = None
        if "OC" in pattern:
            try:
                oc = pattern.split("OC")[1][:1]
                oc = int(oc)
            except Exception:
                oc = None

        if not surface or oc is None:
            continue

        s = story[surface][oc]

        s["samples"] += 1
        s["ticks_total"] += ticks

        if move == "drift":
            s["drift"] += 1
        elif move == "steam":
            s["steam"] += 1
        else:
            s["flat"] += 1

    result = {}

    for surface, oc_data in story.items():

        result[surface] = {}

        for oc, stats in oc_data.items():

            c = max(stats["samples"], 1)

            result[surface][f"OC{oc}"] = {
                "samples": c,
                "drift_rate": round(stats["drift"] / c, 3),
                "steam_rate": round(stats["steam"] / c, 3),
                "flat_rate": round(stats["flat"] / c, 3),
                "avg_trend_ticks": round(stats["ticks_total"] / c, 2),
            }

    return result


def main():

    print("\nLoading blueprint trades...")
    trades = load_blueprint_trades()

    print("Total trades loaded:", len(trades))

    story = build_runner_story(trades)

    print("\nExample surfaces:\n")

    for surface in list(story.keys())[:5]:
        print(surface)
        for oc, data in story[surface].items():
            print(" ", oc, data)

    with open(OUTPUT_FILE, "w") as f:
        json.dump(story, f, indent=2)

    print("\nRunner OC story cache written →", OUTPUT_FILE)
    print("Surfaces:", len(story))


if __name__ == "__main__":
    main()