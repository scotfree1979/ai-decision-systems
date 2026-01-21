import os
import json
import requests
import logging
from datetime import datetime

# -----------------------------
# 🔐 Session Management (Unified Refactor)
# -----------------------------
# Betfair APP KEY — single source of truth (do not read from DB/env)
BETFAIR_APP_KEY = "CZHojduNWa3kxWIn"   # <-- keep your literal here
# Optional: legacy alias if anything still imports APP_KEY
APP_KEY = BETFAIR_APP_KEY

# (Optional) a parking spot for temporary session (not required if you prefer DB/env only)
BETFAIR_SESSION = None
# === PATCH END ===

# engines/daily_config.py  (top-level, alongside your other constants)

APP_KEY = "CZHojduNWa3kxWIn"

def get_app_key() -> str:
    """Single source of truth for the App Key."""
    try:
        return APP_KEY.strip()
    except Exception:
        return ""


# -----------------------------
# 📦 Path Setup
# -----------------------------
BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
DB_PATH = os.path.join(BASE_DIR, "data", "bets.db")

# -----------------------------
# ⚙️ Environment & Settings
# -----------------------------
TEST_MODE = False
STAKE_MULTIPLIER = 1.5
TODAY_DATE = datetime.utcnow().strftime("%Y-%m-%d")
# autoscalp LIVE auto‑reconcile cadence (seconds)
AUTO_RECONCILE_SECONDS = 60

# Global toggle: force router to use dynamic sizing for ALL letters (LIVE).
USE_ROUTER_DYNAMIC_STAKE = True        # ← set True to enforce for every letter

# ...or keep this list if you want per-letter forcing later:
USE_ROUTER_DYNAMIC_STAKE_LETTERS = []  # e.g. ["A","B","G","X","R","F","L","Z","P","I","T","C","E","K"]

# Add or adjust this structure
MARKET_MONITOR = {
    "bands": {
        "ACTIVE_MIN": 1.50,   # odds >= 1.5
        "ACTIVE_MAX": 8.00,   # up to 8.0
        "PASSIVE_MAX": 12.00, # up to 12.0
        # >12.0 = ignored automatically
    },

    "letter_policy": {
        # Overarching
        "P": ("ACTIVE", "PASSIVE"),  # BLUEPRINTS

        # Always-on & steam families
        "A": ("ACTIVE", "PASSIVE"),  # ALWAYS_ON
        "S": ("ACTIVE", "PASSIVE"),  # STEAM
        "Z": ("ACTIVE", "PASSIVE"),  # OG_STRATEGY
        "F": ("ACTIVE", "PASSIVE"),  # STEAM_FADE

        # BTL families
        "B": ("ACTIVE", "PASSIVE"),  # BTL_SCOUT
        "G": ("ACTIVE", "PASSIVE"),  # BTL_AGGR

        # Tactical
        "X": ("ACTIVE",),            # CROSSOVER
        "R": ("ACTIVE",),            # BREAKOUT

        # Mechanical
        "L": ("ACTIVE", "PASSIVE"),  # LADDER

        # Risk
        "M": ("ACTIVE", "PASSIVE"),  # MLM

        # In-play families
        "I": ("IN_PLAY",),           # IP1_SHOCK_DRIFT
        "T": ("IN_PLAY",),           # IP2_TIRED_LEADER
        "C": ("IN_PLAY",),           # IP3_CLOSE_FINISH
        "E": ("IN_PLAY",),           # IP4_FENCE_ERROR
        "K": ("IN_PLAY",),           # IP5_COLLAPSE_FADE
    },

    # Optional flags
    # We deliberately do NOT enable fav_sub_1p5_active,
    # since horses starting below 1.5 should be left alone
    # and only re-enter if they drift into ACTIVE >= 1.5.
}



# ============================================================
# 💰 Budget & Risk Controls (Global)
# ============================================================
BANK_PCT_PER_ENTRY    = 0.004   # 0.4% of bank per entry (main sizing knob)
MAX_BANK_PCT_PER_TRADE = 0.02   # absolute safety cap (2% of bank per trade)
MIN_STAKE             = 2.00    # Betfair minimum
L1_FRACTION_CAP       = 0.35    # at most 35% of L1 liquidity on the relevant side

# If you want a "scout always-on" toggle for testing/learning:
SCOUT_ALWAYS_ON       = True    # <<< flip this to start/stop scout strategy

# ============================================================
# 📍 TARGET: engines/daily_config.py
# 🔎 ACTION: append these tunables (anywhere near the other risk knobs)
# ============================================================
# --------------------------------------------
# 📐 Direction Engine Tunables (story + trend)
# --------------------------------------------
# Analyzer override threshold
MIN_ANALYZER_CONF = 0.55

# Lifespan story threshold (anchor -> now, in ticks)
T_TOTAL = 2

# Mid-window settings (minutes)
W_MID_PRE = 15    # pre-off
W_MID_IP  = 2     # in-play

# Mid-window slope threshold (ticks per minute; approx)
S_MID = 0.10

# Micro momentum threshold (10s oc_momentum_ticks)
M_MICRO = 2

# Flatness / volatility thresholds (ticks std over mid window)
V_STD       = 0.60   # if below, considered quiet
V_STD_CHOP  = 1.40   # (reserved) treat as choppy when exceeded

# Passive band stake multiplier (already used in sizing)
# PASSIVE_MULT = 0.5   # keep your existing setting here


# ============================================================
# 📊 Per-letter Stake Configuration (Single Source of Truth)
# ============================================================
# PRE-OFF families
BASE_STAKE_P = 3.00;  STAKE_MAX_P = 10.00   # Blueprints (overarching)
BASE_STAKE_A = 3.00;  STAKE_MAX_A = 8.00    # Always-On
BASE_STAKE_S = 3.00;  STAKE_MAX_S = 10.00   # Steam
BASE_STAKE_Z = 3.00;  STAKE_MAX_Z = 8.00    # OG Strategy (legacy Steam)
BASE_STAKE_L = 2.50;  STAKE_MAX_L = 6.00    # Ladder (multi-entry tool)
BASE_STAKE_B = 4.00;  STAKE_MAX_B = 12.00   # BTL Scout
BASE_STAKE_G = 4.00;  STAKE_MAX_G = 12.00   # BTL Aggro
BASE_STAKE_X = 4.50;  STAKE_MAX_X = 12.00   # Crossover (decisive tactical)
BASE_STAKE_R = 4.50;  STAKE_MAX_R = 12.00   # Breakout (big conviction)
BASE_STAKE_F = 3.50;  STAKE_MAX_F = 8.00    # Steam Fade (contrarian)

# IN-PLAY families
BASE_STAKE_I = 3.00;  STAKE_MAX_I = 6.00    # IP1 Shock Drift
BASE_STAKE_T = 3.00;  STAKE_MAX_T = 6.00    # IP2 Tired Leader
BASE_STAKE_C = 3.00;  STAKE_MAX_C = 6.00    # IP3 Close Finish
BASE_STAKE_E = 3.00;  STAKE_MAX_E = 6.00    # IP4 Fence Error
BASE_STAKE_K = 3.00;  STAKE_MAX_K = 6.00    # IP5 Collapse Fade

# ============================================================
# 🧠 MSC Families (MicroScalper v7 Engines)
# ============================================================
# D → Exploratory (PRE-OFF micro scalp)
BASE_STAKE_D = 3.50;  STAKE_MAX_D = 10.00

# J → Risk-Reactive (PRE-OFF shadowing legacy)
BASE_STAKE_J = 4.00;  STAKE_MAX_J = 12.00

# V → Intelligent In-Play Laying Engine
BASE_STAKE_V = 3.00;  STAKE_MAX_V = 8.00


# ============================================================
# 📉 Passive Zone Scaling
# ============================================================
# Multiplier for stake sizing when odds fall in the PASSIVE band (8.0 < odds <= 12.0).
# Applied after BANK_PCT_PER_ENTRY scaling and before per-letter caps.
PASSIVE_MULT = 0.5


# Per-family multipliers (fine tune per letter)
LETTER_MULT = {
    # PRE
    "A":1.00, "S":1.00, "B":1.10, "G":1.15, "F":1.00, "X":1.20, "R":1.20, "L":0.90, "Z":0.90,
    "P":1.25,  # ← BLUEPRINTS
    # IN-PLAY
    "I":0.70, "T":0.70, "C":0.70, "E":0.70, "K":0.70, "P":1.25, "D":0.70, # ← BLUEPRINTS
}


# Hard cash caps by phase (keeps IP smaller than PRE)
HARD_CAP_PRE = 5.00
HARD_CAP_IP  = 3.00


# --- Stop-Loss Tick Rules ---------------------------------------------------
# Defines max ticks allowed before stop-loss fires, depending on entry odds
STOP_TICKS_PER_ODDS = [
    (3.0, 2),   # odds < 3.0 → 2 ticks
    (6.0, 1),   # 3.0–6.0 → 1 tick
    (12.0, 1),  # 6.0–12.0 → 1 tick
    (1000.0, 1) # everything above → 1 tick
]

def get_stop_ticks(entry_odds: float, default_ticks: int = 1) -> int:
    for threshold, ticks in STOP_TICKS_PER_ODDS:
        if entry_odds <= threshold:
            return ticks
    return default_ticks

# === PATCH START ============================================================
# 📍 TARGET: engines/daily_config.py
# 🔎 SEARCH: BANK_PCT_PER_ENTRY
# 🧩 ACTION: ADD ENGINE-LEVEL STAKE BOUNDS (FINAL)
# 📆 PATCHED: 2026-03-20 — Engine economic envelopes
#
# PURPOSE:
# - Enforce minimum viable trade size per engine
# - Prevent liquidity impact via hard maximums
# - Preserve pot-based monotonic growth
# ============================================================================

ENGINE_MIN = {
    "LEGACY": 4.00,
    "MSC_EXPLORATORY": 5.00,
    "MSC_RISK": 6.00,
    "MSC_INPLAY": 3.00,
}

ENGINE_MAX = {
    "LEGACY": 10.00,
    "MSC_EXPLORATORY": 15.00,
    "MSC_RISK": 20.00,
    "MSC_INPLAY": 10.00,
}


def _apply_bus_stake_gate(*, engine: str, stake: float) -> float:
    """
    FINAL stake authority.

    This is the LAST mutation of plan["size"] before routing.
    No odds logic. No phase logic. No scaling.

    If this is wrong, BUS is wrong.
    """

    if stake is None or stake <= 0:
        raise RuntimeError("BUS invariant violated: stake <= 0")

    min_stake = ENGINE_MIN_STAKE.get(engine)
    max_stake = ENGINE_MAX_STAKE.get(engine)

    if min_stake is None or max_stake is None:
        raise RuntimeError(
            f"BUS invariant violated: missing stake caps for engine={engine}"
        )

    if stake < min_stake:
        return float(min_stake)

    if stake > max_stake:
        return float(max_stake)

    return float(stake)

# === PATCH END ==============================================================

# === PATCH START ============================================
# 📍 TARGET: engines/daily_config.py
# 🔎 SEARCH: def get_session_token
# 🛠 ACTION: replace with KV-backed reader (GUI-compatible)
# ============================================================

import sqlite3
from engines.config_paths import autoscalp_db as _adb_path

def get_session_token() -> str | None:
    """
    Canonical getter for Betfair session token.

    Priority:
      1) app_kv['betfair_session_token']
      2) environment variable SESSION_TOKEN
      3) in-memory SESSION_TOKEN (legacy)
    """
    import os, sqlite3
    from engines.config_paths import autoscalp_db

    # 1) App KV always wins — GUI writes token HERE in Step-1
    try:
        con = sqlite3.connect(autoscalp_db())
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT value FROM app_kv WHERE key='betfair_session_token'"
        ).fetchone()
        con.close()
        if row and row["value"]:
            return row["value"]
    except Exception:
        pass

    # 2) Environment fallback
    env_tok = os.environ.get("SESSION_TOKEN")
    if env_tok:
        return env_tok

    # 3) In-memory fallback (legacy)
    try:
        return SESSION_TOKEN
    except Exception:
        return None



# -----------------------------
# 💰 Budget Fetching (only when called)
# -----------------------------
# =====================================================================
# 📍 FINAL: fetch_available_budget()
# Fetches real Betfair balance using APP_KEY + SESSION_TOKEN
# Falls back ONLY if the API fails.
# =====================================================================

def fetch_available_budget() -> float:
    import json, logging, requests
    from engines.daily_config import APP_KEY, get_session_token

    token = get_session_token()

    # 1) If no token → fallback immediately
    if not token:
        logging.warning("[daily_config] No session token — using fallback 350.0")
        return 350.0

    # 2) Build correct Betfair headers
    headers = {
        "X-Application": APP_KEY,          # The key already in DailyConfig
        "X-Authentication": token,         # Pulled from AppKV/environment
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    payload = json.dumps([{
        "jsonrpc": "2.0",
        "method": "AccountAPING/v1.0/getAccountFunds",
        "params": {},
        "id": 1
    }])

    # 3) Try Betfair API
    try:
        resp = requests.post(
            "https://api.betfair.com/exchange/account/json-rpc/v1",
            headers=headers,
            data=payload,
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()

        # Extract real balance
        bal = float(data[0]["result"]["availableToBetBalance"])
        return bal

    except Exception as e:
        logging.warning(f"[daily_config] Balance API failed: {e} — using 300.0 fallback")
        return 300.0




def allowed_stake(engine: str, *, letter: str = None) -> float:
    """
    Unified stake limit for this engine.
    Replaces old DailyConfig stake caps.
    LiveRouter still computes dynamic stake per letter; 
    this returns the engine-level ceiling.
    """
    try:
        live_bank = bank_state.get_engine_pot(engine)
        return budget_manager.allowed_stake_for_engine(engine, live_bank)
    except Exception:
        return 0.0


# --- Alias old CONSTANT names to these new dynamic delegates --------------

def get_bank_pct_per_entry() -> float:
    """
    Old logic: BANK_PCT_PER_ENTRY
    New logic: derived from engine pot sizing.
    Safe fallback preserves original behaviour.
    """
    try:
        return BANK_PCT_PER_ENTRY
    except Exception:
        return 0.004


def get_min_stake() -> float:
    """
    MIN_STAKE now authoritative from BankState for dynamic environments.
    """
    try:
        return getattr(bank_state, "MIN_STAKE", MIN_STAKE)
    except Exception:
        return MIN_STAKE


# Legacy-compatible ACCESSORS that now read from BankState:


# === PATCH START: Back-compat stake aliases for Router & Sizers ============
def get_engine_pot(engine: str) -> float:
    return ENGINE_POT(engine)

def get_engine_available(engine: str) -> float:
    return ENGINE_AVAILABLE(engine)

def can_place_for_engine(engine: str, stake: float) -> bool:
    return ENGINE_CAN_PLACE(engine, stake)

def engine_allocations() -> dict:
    return ENGINE_ALLOCATIONS()

def engine_pots_snapshot() -> dict:
    return ENGINE_POTS_SNAPSHOT()
# === PATCH END ==============================================================
# === PATCH START ============================================================
# 📍 TARGET: engines/daily_config.py
# 📆 PATCHED: 2026-02-15 — stake defaults for DynamicStake v7
# ============================================================================

# Base stake per strategy letter (legacy compatibility)
BASE_STAKE = 2.0        # default £2
STAKE_MAX = 25.0        # max hard cap per bet unless overridden
LETTER_MULT = {
    "A": 1.0,
    "B": 1.0,
    "C": 1.0,
    "D": 1.0,
    "E": 1.0,
    "F": 1.0,
    "G": 1.0,
    "I": 1.0,
    "L": 1.0,
    "R": 1.0,
    "S": 1.0,
    "T": 1.0,
    "X": 1.0,
}

BANK_PCT_PER_ENTRY = 0.01   # 1% of pot allowed per bet
MIN_STAKE = 2.0             # never allow less than £2
HARD_CAP_PRE = 50.0         # max stake allowed in PRE
HARD_CAP_IP = 25.0          # max stake allowed INPLAY
# === PATCH END ==============================================================



