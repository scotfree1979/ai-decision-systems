# ======================================================================================================
# 📍 FILE: engines/micro_scalper_v7/blueprint_helper.py
# 🧠 Blueprint Intelligence Helper
#
# PURPOSE
# -------
# Provide expected market behaviour surfaces derived from blueprint history.
#
# Data source:
#   data/blueprints/runner_story_oc_cache.json
#
# Used by:
#   MSC_BLUEPRINT engine
#
# CONTRACT
# --------
# This module NEVER touches BUS, DB, or market state.
# It only reads blueprint statistical surfaces.
# ======================================================================================================

import json
from pathlib import Path

# --------------------------------------------------------------------------------
# Blueprint cache location
# --------------------------------------------------------------------------------

BLUEPRINT_CACHE = Path("data/blueprints/runner_story_oc_cache.json")

_story_cache = None


# --------------------------------------------------------------------------------
# Load cache
# --------------------------------------------------------------------------------

def load_blueprint_cache():
    global _story_cache

    if _story_cache is not None:
        return _story_cache

    if not BLUEPRINT_CACHE.exists():
        print("[BLUEPRINT] cache missing:", BLUEPRINT_CACHE)
        _story_cache = {}
        return _story_cache

    with open(BLUEPRINT_CACHE, "r") as f:
        _story_cache = json.load(f)

    print(f"[BLUEPRINT] runner story cache loaded → {len(_story_cache)} surfaces")

    return _story_cache


# --------------------------------------------------------------------------------
# Expected behaviour lookup
# --------------------------------------------------------------------------------

def get_expected_move(surface_key: str, oc_phase: int):
    """
    Return expected market move for surface + OC phase.

    Returns:
        dict
        {
            expected_move
            drift_rate
            steam_rate
            flat_rate
            samples
        }
    """

    cache = load_blueprint_cache()

    surface = cache.get(surface_key)

    if not surface:
        return None

    oc_key = f"OC{oc_phase}"

    return surface.get(oc_key)


# --------------------------------------------------------------------------------
# Blueprint scoring
# --------------------------------------------------------------------------------

def score_blueprint_alignment(surface_key, oc_phase, live_move):
    """
    Compare live behaviour vs historical expectation.

    Returns:
        float score adjustment
    """

    story = get_expected_move(surface_key, oc_phase)

    if not story:
        return 0.0

    expected = story["expected_move"]

    if live_move == expected:
        return +1.0

    if live_move == "flat":
        return -0.25

    return -0.5


# --------------------------------------------------------------------------------
# Debug helper
# --------------------------------------------------------------------------------

def debug_surface(surface_key, oc_phase):

    story = get_expected_move(surface_key, oc_phase)

    if not story:
        print("[BLUEPRINT] surface not found")
        return

    print("\n[BLUEPRINT EXPECTATION]")
    print("surface:", surface_key)
    print("OC:", oc_phase)
    print(story)