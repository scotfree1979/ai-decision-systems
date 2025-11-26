#!/usr/bin/env python3
"""
AutoScalp v7 – unified data helper layer (Phase 2 wiring complete)
Uses db_shim_v7 to fetch live read-only data from existing DBs.
"""
import os, sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import json
from gui.mastery_dashboard_v7.db_shim_v7 import fetch_v7

# ────────────────────────────────────────────────────────────────
def get_mastery_snapshot():
    return fetch_v7("mastery_snapshot")

def get_liability_top():
    return fetch_v7("liability_top")

def get_scalper_opportunities():
    return fetch_v7("scalper_opportunities")

def get_runner_form():
    return fetch_v7("runner_form")

def get_internal_bank():
    rows = fetch_v7("internal_bank")
    return rows[0] if rows else {}

# ────────────────────────────────────────────────────────────────
def get_master_dashboard_payload():
    """Return one consolidated payload used by the dashboard."""
    return {
        "snapshot": get_mastery_snapshot(),
        "liabilities": get_liability_top(),
        "scalper_opportunities": get_scalper_opportunities(),
        "runner_form": get_runner_form(),
        "bank": get_internal_bank(),
    }

# ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    payload = get_master_dashboard_payload()
    print(json.dumps(payload, indent=2, default=str))
