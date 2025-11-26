# ✅ bet_placer.py – Full Restore with SESSION TOKEN integration and correct imports


from rules.head_trader_rules import *
from upgrade_import_patch import get_session_token, get_app_key

import json
import sqlite3
import logging
from datetime import datetime
from bet_core import (
    place_bet,
    execute_scalp,
    green_up,
    calculate_tick,
    DB_PATH
)
from price_math import get_tick_size
from database_hijack_monitor import enqueue_write
from upgrade_import_patch import APP_KEY

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")

# -----------------------------
# ✅ ROUND TO VALID BETFAIR TICK
# -----------------------------

def round_to_valid_odds(odds):
    tick = get_tick_size(odds)
    return max(1.01, round(round(odds / tick) * tick, 2))

def ensure_session_token(signal):
    if not signal.get("session_token"):
        signal["session_token"] = SESSION_TOKEN

# -----------------------------
# ✅ BASIC LAY
# -----------------------------

def place_basic_lay_bet(signal):
    ensure_session_token(signal)
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    odds = round_to_valid_odds(signal.get("odds"))
    stake = signal.get("stake", 2.0)
    ref = signal.get("customerOrderRef")

    logging.info(f"📤 [BasicLay] Sending bet: {selectionId} @ {odds} (£{stake})")
    bet_id = place_bet(session_token, marketId, selectionId, stake, odds, "LAY", ref)

    if bet_id:
        logging.info(f"✅ [BasicLay] {selectionId} @ {odds} (£{stake}) - Ref: {ref}")
        enqueue_write(
            "UPDATE bets SET status = ?, placed_at = ?, betfair_bet_id = ? WHERE customerOrderRef = ?",
            ("placed", datetime.utcnow().isoformat(), bet_id, ref)
        )
    else:
        logging.warning(f"🔴 [BasicLay] Lay failed for {selectionId} @ {odds} - Ref: {ref}")

# -----------------------------
# ✅ LADDER LAY
# -----------------------------

def place_ladder_lay_bet(signal):
    ensure_session_token(signal)
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    base_odds = round_to_valid_odds(signal.get("odds"))
    stake = signal.get("stake", 2.0)

    ticks = [0, -1, -2, -3, -4]
    for i, offset in enumerate(ticks):
        leg_odds = round_to_valid_odds(base_odds + offset * get_tick_size(base_odds))
        leg_ref = f"{signal['customerOrderRef']}_L{i}"

        logging.info(f"📤 [Ladder] Leg {i+1}/5: {selectionId} @ {leg_odds} (£{stake})")
        bet_id = place_bet(session_token, marketId, selectionId, stake, leg_odds, "LAY", leg_ref)

        if bet_id:
            logging.info(f"🦪 [Ladder] {i+1}/5: {selectionId} @ {leg_odds} (£{stake})")
            enqueue_write(
                "INSERT INTO bets (customerOrderRef, marketId, selectionId, odds, stake, status, placed_at, betfair_bet_id, strategy_name) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (leg_ref, marketId, selectionId, leg_odds, stake, "placed", datetime.utcnow().isoformat(), bet_id, "Ladder Lays")
            )
        else:
            logging.warning(f"🔴 [Ladder] Leg {i+1} failed: {selectionId} @ {leg_odds}")

# -----------------------------
# ✅ SCALP
# -----------------------------

def place_scalp_trade(signal):
    ensure_session_token(signal)
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    odds = round_to_valid_odds(signal.get("odds"))
    stake = signal.get("stake", 10.0)
    direction = signal.get("scalp_direction", "lay_to_back")
    ref = signal.get("customerOrderRef")

    logging.info(f"📤 [Scalp] Sending scalp trade: {selectionId} @ {odds} (£{stake}) [{direction}]")

    if direction == "lay_to_back":
        bet_id_lay = place_bet(session_token, marketId, selectionId, stake, odds, "LAY", ref)
        back_odds = round_to_valid_odds(odds - 2 * get_tick_size(odds))
        bet_id_back = place_bet(session_token, marketId, selectionId, stake, back_odds, "BACK")
    else:
        bet_id_back = place_bet(session_token, marketId, selectionId, stake, odds, "BACK", ref)
        lay_odds = round_to_valid_odds(odds + 2 * get_tick_size(odds))
        bet_id_lay = place_bet(session_token, marketId, selectionId, stake, lay_odds, "LAY")

    logging.info(f"📉 [Scalp] {direction} executed on {selectionId} @ {odds} (£{stake})")

    if bet_id_lay:
        enqueue_write(
            "UPDATE bets SET status = ?, placed_at = ?, betfair_bet_id = ? WHERE customerOrderRef = ?",
            ("placed", datetime.utcnow().isoformat(), bet_id_lay, ref)
        )

# -----------------------------
# ✅ GREENUP
# -----------------------------

def place_greenup_exit(signal):
    ensure_session_token(signal)
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    logging.info(f"🟩 [GreenUp] Triggered for {marketId}")
    green_up(session_token, marketId)

# -----------------------------
# ✅ IN-PLAY LAY
# -----------------------------

def place_inplay_lay_bet(signal):
    ensure_session_token(signal)
    session_token = signal.get("session_token")
    marketId = signal.get("marketId")
    selectionId = signal.get("selectionId")
    odds = round_to_valid_odds(signal.get("odds"))
    stake = signal.get("stake", 2.0)
    ref = signal.get("customerOrderRef")

    logging.info(f"📤 [InPlay] Sending lay bet: {selectionId} @ {odds} (£{stake})")
    bet_id = place_bet(session_token, marketId, selectionId, stake, odds, "LAY", ref)

    if bet_id:
        logging.info(f"🏇 [InPlay] Lay placed on {selectionId} @ {odds} (£{stake})")
        enqueue_write(
            "UPDATE bets SET status = ?, placed_at = ?, betfair_bet_id = ? WHERE customerOrderRef = ?",
            ("placed", datetime.utcnow().isoformat(), bet_id, ref)
        )
    else:
        logging.warning(f"🔴 [InPlay] Lay failed for {selectionId} @ {odds} - Ref: {ref}")
