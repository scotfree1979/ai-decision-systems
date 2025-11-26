# ✅ SCALPING ENGINE v2 – Adaptive, Directional, Multi-Horse Strategy

import requests
import json
import logging
from datetime import datetime
from upgrade_import_patch import get_tick_size, place_bet
from rules.head_trader_rules import *

# -----------------------------
# ✅ LADDER LOGIC (POSITIONAL ENTRY FOR TIME_20)
# -----------------------------

def calculate_ladder_bets(odds, total_stake, num_ladders=5):
    stake_per_leg = round(total_stake / num_ladders, 2)
    tick = get_tick_size(odds)
    ladder_odds = []

    # Define range logic
    if odds < 2.0:  # odds-on horses
        start_offset = 3  # overlap 50% below price
        for i in range(num_ladders):
            ladder_odds.append(round(odds - (start_offset - i) * tick, 2))

    elif 2.0 <= odds <= 4.0:
        start_offset = 2  # partial overlap
        for i in range(num_ladders):
            ladder_odds.append(round(odds - (start_offset - i) * tick, 2))

    else:  # odds 4+ - start close to price
        for i in range(num_ladders):
            ladder_odds.append(round(odds - (i * tick), 2))

    # Replace any odds below 1.01 (invalid)
    valid_ladder = [max(1.01, leg) for leg in ladder_odds]
    return list(zip(valid_ladder, [stake_per_leg] * num_ladders))

# -----------------------------
# ✅ CONFIGURATION
# -----------------------------

BETFAIR_ENDPOINT = "https://api.betfair.com/exchange/betting/rest/v1.0/"
MAX_SCALP_BANK = 400

# -----------------------------
# ✅ RANGE ANALYSIS PER HORSE
# -----------------------------

def analyze_market_range(runner):
    # Extract top 3 available prices to back and lay
    back = runner.get("ex", {}).get("availableToBack", [])
    lay = runner.get("ex", {}).get("availableToLay", [])

    if not back or not lay:
        return None

    top_back = back[0]["price"] if isinstance(back[0], dict) else back[0].get("price")
    top_lay = lay[0]["price"] if isinstance(lay[0], dict) else lay[0].get("price")

    anchor = round((top_back + top_lay) / 2, 2)
    spread = round(top_lay - top_back, 2)
    tick = get_tick_size(anchor)

    return {
        "selectionId": runner.get("selectionId"),
        "top_back": top_back,
        "top_lay": top_lay,
        "anchor": anchor,
        "spread": spread,
        "tick_size": tick,
        "range_low": round(top_back - tick, 2),
        "range_high": round(top_lay + tick, 2)
    }

# -----------------------------
# ✅ DIRECTION DECISION BASED ON ODDS MOVEMENT
# -----------------------------

def determine_scalp_direction(anchor, odds_check):
    try:
        anchor_prob = 1 / float(anchor)
        current_prob = 1 / float(odds_check)
        change = ((current_prob - anchor_prob) / anchor_prob) * 100
        return "back_to_lay" if change > 0 else "lay_to_back"
    except:
        return "lay_to_back"  # fallback

# -----------------------------
# ✅ ENTRY LOGIC BASED ON CURRENT ODDS
# -----------------------------

def should_enter_scalp(runner_profile, last_price):
    if not runner_profile:
        return False
    direction = runner_profile.get("direction")
    anchor = runner_profile.get("anchor")
    tick = runner_profile.get("tick_size")

    if direction == "back_to_lay":
        return last_price <= anchor - tick
    elif direction == "lay_to_back":
        return last_price >= anchor + tick
    return False

# -----------------------------
# ✅ STAKE CONTROLLED BY RISK MODEL
# -----------------------------

def calculate_scalp_stake(active_scalps):
    if active_scalps >= 8:
        return 6.0
    elif active_scalps >= 4:
        return 10.0
    return 12.0

# -----------------------------
# ✅ PLACE SCALP STRATEGY TRADE
# -----------------------------

def place_lay_strategy(signal, tag="Scalp"):
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    runner_id = signal.get("selectionId")
    odds = signal.get("odds")
    stake = signal.get("stake") or 10.0
    direction = signal.get("scalp_direction", "lay_to_back")

    if direction == "back_to_lay":
        place_bet(session_token, marketId, runner_id, stake, odds, "BACK")
        tick_size = get_tick_size(odds)
        lay_odds = round(odds + (2 * tick_size), 2)
        place_bet(session_token, marketId, runner_id, stake, lay_odds, "LAY")

    else:
        place_bet(session_token, marketId, runner_id, stake, odds, "LAY")
        tick_size = get_tick_size(odds)
        back_odds = round(odds - (2 * tick_size), 2)
        place_bet(session_token, marketId, runner_id, stake, back_odds, "BACK")

# -----------------------------
# ✅ PENNY MATCH MIRRORING LOGIC
# -----------------------------

def penny_logic(signal):
    direction = signal.get("scalp_direction", "lay_to_back")
    odds = signal.get("odds")
    tick = get_tick_size(odds)

    if direction == "lay_to_back":
        back_odds = round(odds - 2 * tick, 2)
        return ("BACK", back_odds)
    else:
        lay_odds = round(odds + 2 * tick, 2)
        return ("LAY", lay_odds)

# -----------------------------
# ✅ FILL CONTROL (RETRY TICKS UP OR DOWN)
# -----------------------------

def manage_bet_hedging(signal):
    base_odds = signal.get("odds")
    direction = signal.get("scalp_direction")
    ref = signal.get("customerOrderRef")
    tick = get_tick_size(base_odds)

    for attempt in range(3):
        adj_odds = base_odds + tick if direction == "lay_to_back" else base_odds - tick
        bet_type = "LAY" if direction == "lay_to_back" else "BACK"
        logging.info(f"🔁 Retry scalp {ref}: {bet_type} at {adj_odds}")
        place_bet(signal.get("session_token"), signal.get("marketId"), signal.get("selectionId"), signal.get("stake"), round(adj_odds, 2), bet_type)
        time.sleep(1)

