# ghost_helpers.py
# Centralized utility + helper functions for GhostTrader Dashboard

import os
import json
import pandas as pd
from datetime import datetime, timedelta

# ========== 🧠 Blueprint Signal Loader ==========

def load_latest_runner_trades():
    filenames = [
        f"blueprint_signals_{datetime.utcnow().date().isoformat()}.json",
        f"blueprint_signals_{(datetime.utcnow().date() - timedelta(days=1)).isoformat()}.json"
    ]
    for f in filenames:
        if os.path.exists(f):
            with open(f, "r") as file:
                data = json.load(file)
                return data if isinstance(data, list) else data.get("runner_trades", [])
    return []


# ========== 🔬 Confidence Breakdown ==========

def calculate_confidence_components(bracket, tick_range, blueprint_match, range_source, breakout, stop_loss):
    base = 0.0
    emojis = {
        "Base Confidence": "📐",
        "Blueprint Boost": "📘",
        "Breakout": "🔺",
        "Stop Loss": "🚫",
        "Range Source": "🔁"
    }

    if bracket == "favourite":
        base = 0.72 if tick_range <= 6 else 0.68 if tick_range <= 10 else 0.65
    elif bracket == "2nd":
        base = 0.70 if tick_range <= 8 else 0.66
    elif bracket == "3rd":
        base = 0.68 if tick_range <= 9 else 0.65
    else:
        base = 0.64 if tick_range <= 12 else 0.60

    stack = {
        "Base Confidence": base,
        "Blueprint Boost": 0.10 if blueprint_match else 0.00,
        "Breakout": 0.05 if breakout else 0.00,
        "Stop Loss": -0.08 if stop_loss else 0.00,
        "Range Source": {
            "anchor": 0.02,
            "partial": 0.04,
            "full": 0.07
        }.get(range_source, 0.00)
    }

    total = round(sum(stack.values()), 2)

    return {
        "stack": stack,
        "total": total,
        "emojis": emojis
    }


def get_signal_confidence_band(conf):
    if conf >= 0.80:
        return "80%+"
    elif conf >= 0.75:
        return "75–79%"
    elif conf >= 0.70:
        return "70–74%"
    elif conf >= 0.65:
        return "65–69%"
    else:
        return "60–64%"


# ========== 💾 Save Playbooks ==========

PLAYBOOKS_PATH = "playbooks.json"

def save_playbooks(playbooks):
    filtered = {
        k: v for k, v in playbooks.items()
        if v['avg_confidence'] >= 0.70 and v['sample_size'] >= 3
    }
    with open(PLAYBOOKS_PATH, 'w') as f:
        json.dump(filtered, f, indent=2)
    return PLAYBOOKS_PATH, len(filtered)


# ========== 📘 Pattern → Playbook Helper ==========

def send_to_playbook_engine(pattern_keys):
    print(f"[PlaybookEngine] Received {len(pattern_keys)} patterns for export...")
    # Placeholder: integrate with actual builder if needed
    return pattern_keys



