# =============================================================================
# engines/inplay/inplay_flag_helper.py
# =============================================================================
#
# PURPOSE
# -------
# Deterministic in-play detection using multi-signal quorum logic.
#
# DOES NOT:
# - Depend on Betfair inplay flag
# - Depend solely on marketStartTime
#
# DOES:
# - Combine multiple structural signals
# - Maintain race timer
# - Provide race quartile
# - Support standalone simulation
#
# =============================================================================

from typing import Dict, List, Any, Tuple
from datetime import datetime, timezone
import time
import math

# -----------------------------------------------------------------------------
# CONFIGURATION
# -----------------------------------------------------------------------------

START_TIME_GRACE_SECONDS = 120        # weak signal tolerance
VOL_TICK_THRESHOLD = 6                # ticks moved
VOL_WINDOW_SECONDS = 10               # volatility window
FAV_SHOCK_TICKS = 8                   # favourite shock threshold
QUORUM_REQUIRED = 2                   # signals required


# -----------------------------------------------------------------------------
# INTERNAL STATE
# -----------------------------------------------------------------------------

_market_state: Dict[str, Dict[str, Any]] = {}


# -----------------------------------------------------------------------------
# HELPERS
# -----------------------------------------------------------------------------

def _now_utc() -> float:
    return time.time()


def _ticks_between(p1: float, p2: float) -> float:
    if not p1 or not p2:
        return 0.0
    return abs(math.log(p1) - math.log(p2)) * 100  # simplified tick proxy


# -----------------------------------------------------------------------------
# CORE EVALUATION
# -----------------------------------------------------------------------------

def evaluate_inplay(
    market_id: str,
    market_start_ts: float,
    runner_prices: Dict[str, float]
) -> Dict[str, Any]:
    """
    runner_prices:
        {
            selectionId: current_price
        }

    Returns:
        {
            is_inplay: bool,
            signals: list,
            confidence: float,
            race_elapsed_seconds: int,
            race_quartile: str|None
        }
    """

    now = _now_utc()

    state = _market_state.setdefault(market_id, {
        "started": False,
        "start_ts": None,
        "last_prices": {},
        "price_history": [],
    })

    signals_triggered: List[str] = []

    # -------------------------------------------------------------------------
    # Signal A — Scheduled Start Passed
    # -------------------------------------------------------------------------

    if now >= market_start_ts - START_TIME_GRACE_SECONDS:
        signals_triggered.append("scheduled_passed")

    # -------------------------------------------------------------------------
    # Signal B — Cross-runner volatility spike
    # -------------------------------------------------------------------------

    vol_count = 0

    for sid, px in runner_prices.items():
        prev = state["last_prices"].get(sid)
        if prev:
            ticks = _ticks_between(prev, px)
            if ticks >= VOL_TICK_THRESHOLD:
                vol_count += 1

    if vol_count >= 3:
        signals_triggered.append("volatility_spike")

    # -------------------------------------------------------------------------
    # Signal C — Favourite shock
    # -------------------------------------------------------------------------

    if runner_prices:
        fav_sid = min(runner_prices, key=lambda k: runner_prices[k])
        fav_px = runner_prices[fav_sid]
        prev_fav = state["last_prices"].get(fav_sid)

        if prev_fav:
            fav_ticks = _ticks_between(prev_fav, fav_px)
            if fav_ticks >= FAV_SHOCK_TICKS:
                signals_triggered.append("favourite_shock")

    # -------------------------------------------------------------------------
    # Determine quorum
    # -------------------------------------------------------------------------

    is_inplay = len(signals_triggered) >= QUORUM_REQUIRED

    # -------------------------------------------------------------------------
    # Start timer once
    # -------------------------------------------------------------------------

    if is_inplay and not state["started"]:
        state["started"] = True
        state["start_ts"] = now

    race_elapsed = None
    quartile = None

    if state["started"]:
        race_elapsed = int(now - state["start_ts"])

        # Simple quartile assumption: 240 second race baseline
        race_len = 240
        pct = min(1.0, race_elapsed / race_len)

        if pct <= 0.25:
            quartile = "Q1"
        elif pct <= 0.50:
            quartile = "Q2"
        elif pct <= 0.75:
            quartile = "Q3"
        else:
            quartile = "Q4"

    # -------------------------------------------------------------------------
    # Update state
    # -------------------------------------------------------------------------

    state["last_prices"] = runner_prices.copy()

    return {
        "is_inplay": state["started"],
        "signals": signals_triggered,
        "confidence": len(signals_triggered) / 3.0,
        "race_elapsed_seconds": race_elapsed,
        "race_quartile": quartile,
    }

# === PATCH START ==============================================================
# 📍 TARGET: engines/inplay/inplay_flag_helper.py
# 🔎 SEARCH: def build_race_intelligence(
# 🛠 ACTION: REPLACE FUNCTION — Add V7 + Monitor authoritative layer
# 📆 PATCHED: 2026-02-20 — Stage 1 V7 Integration (authoritative overlay)
#
# PURPOSE:
# - Extend primitive intelligence with V7 structural layer
# - Use race position inference if available
# - Use drift unfolded view if available
# - Use monitor crossover signals
# - Keep primitive logic as fallback
# - Remain fully standalone testable
#
# INVARIANT:
# - No BUS dependency
# - No engine wiring
# - If V7 unavailable → fallback logic remains active
# ==============================================================================

def build_race_intelligence(
    market_id: str,
    market_start_ts: float,
    runner_prices: Dict[str, float]
) -> Dict[str, Any]:

    from tools.betfair_runner_trend_surface import get_runner_trend

    # Optional V7 layers (fail-safe)
    try:
        from engines.config_paths import auto_conn
        import sqlite3
        con = auto_conn(rw=False)
        con.row_factory = sqlite3.Row

        v7_positions = {}
        v7_drift = {}

        # --- Race Position Inferred ---
        rows = con.execute("""
            SELECT selectionId, pos_inplay
            FROM v7_race_position_inferred
            WHERE marketId = ?
        """, (str(market_id),)).fetchall()

        for r in rows:
            v7_positions[str(r["selectionId"])] = r["pos_inplay"]

        # --- Drift Unfolded (Schema-Correct) ---
        rows = con.execute("""
            SELECT selectionId, drift_ratio
            FROM v7_oc_drift_unfolded
            WHERE marketId = ?
        """, (str(market_id),)).fetchall()

        for r in rows:
            sid = str(r["selectionId"])
            drift_ratio = float(r["drift_ratio"] or 0.0)

            # Convert ratio into directional drift proxy
            # drift_ratio > 1 = drift
            # drift_ratio < 1 = steam
            drift_speed = drift_ratio - 1.0

            v7_drift[sid] = {
                "drift_speed": drift_speed,
                "drift_acceleration": 0.0  # not available in this view
            }

        con.close()

    except Exception:
        v7_positions = {}
        v7_drift = {}

    # Optional monitor layer
    try:
        from engines.market_monitor.monitor import (
            get_crossover_signal,
            signals_for_runner
        )
        monitor_available = True
    except Exception:
        monitor_available = False

    # --- Step 1: Primitive InPlay Detection ---
    flag = evaluate_inplay(
        market_id,
        market_start_ts,
        runner_prices
    )

    runners_output = []

    # --- Step 2: Runner Intelligence Stack ---
    for sid, px in runner_prices.items():

        trend = get_runner_trend(market_id, sid) or {}

        direction = trend.get("direction")
        ticks_moved = trend.get("ticks_moved", 0)
        confidence = trend.get("confidence", 0.0)

        # -----------------------------
        # Primitive collapse model
        # -----------------------------
        collapse_score = 0.0

        if direction == "LAY->BACK":
            collapse_score += 1.0

        if ticks_moved >= VOL_TICK_THRESHOLD:
            collapse_score += 1.0

        if confidence >= 0.20:
            collapse_score += 1.0

        volatility_score = min(1.0, ticks_moved / 10.0)

        # -----------------------------
        # V7 Authoritative Overlay
        # -----------------------------
        role = None
        drift_speed = None
        drift_acc = None

        if sid in v7_positions:
            role = v7_positions[sid]

            # Position-based enhancement
            if role in ("TRAILING", "COLLAPSING", "EXTENDED"):
                collapse_score += 1.5

            elif role == "MIDFIELD":
                collapse_score += 0.5

        if sid in v7_drift:
            drift_speed = v7_drift[sid].get("drift_speed")
            drift_acc = v7_drift[sid].get("drift_acceleration")

            if drift_speed and drift_speed > 0:
                collapse_score += 0.5

            if drift_acc and drift_acc > 0:
                collapse_score += 0.5

        # -----------------------------
        # Monitor Structural Signals
        # -----------------------------
        structure_signals = {}

        if monitor_available:
            crossover = get_crossover_signal(market_id, sid)
            signals = signals_for_runner(market_id, sid, px)

            structure_signals = {
                "crossover_recent": crossover.get("crossed_over_recent", False),
                "new_fav_recent": signals.get("new_fav_recent", False),
                "p2a_recent": signals.get("p2a_recent", False),
            }

            if structure_signals["crossover_recent"]:
                collapse_score += 0.5

            if structure_signals["p2a_recent"]:
                collapse_score += 0.5

        runners_output.append({
            "selectionId": sid,
            "price": px,
            "role": role,
            "direction": direction,
            "ticks_moved": ticks_moved,
            "confidence": confidence,
            "collapse_score": round(collapse_score, 2),
            "volatility_score": round(volatility_score, 2),
            "structure_signals": structure_signals,
            "drift_speed": drift_speed,
            "drift_acceleration": drift_acc,
        })

    # --- Sort by collapse potential ---
    runners_output.sort(
        key=lambda r: (r["collapse_score"], r["volatility_score"]),
        reverse=True
    )

    return {
        "market": {
            "is_inplay": flag["is_inplay"],
            "race_elapsed_seconds": flag["race_elapsed_seconds"],
            "race_quartile": flag["race_quartile"],
            "confidence": flag["confidence"],
        },
        "runners": runners_output
    }

# === PATCH START ==============================================================
# 📍 TARGET: engines/inplay/inplay_flag_helper.py
# 🔎 SEARCH: def _print_inplay_authority_report(
# 🛠 ACTION: ADD horse_name lookup
# 📆 PATCHED: 2026-02-20 — Stage 2B Horse Name Integration
#
# PURPOSE:
# - Replace selectionId with bets.horse_name
# - Do NOT use runner_name (it is blank)
# - Pure observability improvement
#
# INVARIANT:
# - If lookup fails → fallback to selectionId
# ==============================================================================

def _resolve_horse_name(market_id: str, selection_id: str) -> str:
    try:
        from engines.config_paths import auto_conn
        import sqlite3

        con = auto_conn(rw=False)
        con.row_factory = sqlite3.Row

        row = con.execute("""
            SELECT horse_name
            FROM bets
            WHERE marketId = ?
              AND selectionId = ?
            LIMIT 1
        """, (str(market_id), str(selection_id))).fetchone()

        con.close()

        if row and row["horse_name"]:
            return str(row["horse_name"])

    except Exception:
        pass

    return str(selection_id)


# === PATCH MODIFY EXISTING REPORT PRINT =====================================

# =============================================================================
# 📍 TARGET: engines/inplay/inplay_flag_helper.py
# 🔎 SEARCH: # -----------------------------------------------------------------------------
# 🧩 ACTION: ADD V7 INPLAY AUTHORITY REPORT (STAGE 2A)
# 📆 PATCHED: 2026-02-20
#
# PURPOSE:
# - Provide V7-style in-play authority report
# - Pure observability
# - No trade execution
# - No BUS dependency
# - Standalone testable
#
# INVARIANT:
# - Does NOT modify detection logic
# - Does NOT wire into InPlayEngine yet
# =============================================================================

import threading


# -----------------------------------------------------------------------------
# Pretty Printer
# -----------------------------------------------------------------------------

def _print_inplay_authority_report(
    market_id: str,
    intel: Dict[str, Any]
) -> None:

    now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")

    market = intel.get("market", {})
    runners = intel.get("runners", [])[:5]

    print("\n══════════════════════════════════════════════════════")
    print("V7 INPLAY AUTHORITY REPORT")
    print(f"t={now_str}   mode=LIVE")
    print("══════════════════════════════════════════════════════")

    print("\nMARKET")
    print("------------------------------------------------------")
    print(f"marketId                : {market_id}")
    print(f"is_inplay               : {'YES' if market.get('is_inplay') else 'NO'}")
    print(f"confidence              : {round(market.get('confidence', 0.0), 2)}")
    print(f"race_elapsed_seconds    : {market.get('race_elapsed_seconds')}")
    print(f"race_quartile           : {market.get('race_quartile')}")
    print("------------------------------------------------------")

    print("\nRUNNERS (Top 5 by collapse_score)")
    print("------------------------------------------------------")
    print(f"{'Horse':<20} {'Px':<6} {'Dir':<10} {'Ticks':<6} {'Coll':<6} {'Vol':<6}")

    for r in runners:
# === PATCH MODIFY EXISTING REPORT PRINT =====================================

# Replace this line inside _print_inplay_authority_report:

# horse = r.get("selectionId")

# WITH:

        horse = _resolve_horse_name(market_id, r.get("selectionId"))

# === PATCH END ==============================================================
        px = r.get("price")
        direction = r.get("direction")
        ticks = r.get("ticks_moved")
        collapse = r.get("collapse_score")
        vol = r.get("volatility_score")

        print(f"{str(horse):<20} {str(px):<6} {str(direction):<10} {str(ticks):<6} {str(collapse):<6} {str(vol):<6}")

    print("------------------------------------------------------")

    if runners:
        top = runners[0]
        print("\nSTATUS")
        print("------------------------------------------------------")
        print(f"trigger_candidate      : {top.get('selectionId')}")
        print(f"collapse_score         : {top.get('collapse_score')}")
        print("======================================================")
    else:
        print("\nSTATUS")
        print("------------------------------------------------------")
        print("no_runners_detected")
        print("======================================================")


# -----------------------------------------------------------------------------
# Loop Runner (Standalone Use)
# -----------------------------------------------------------------------------

_REPORT_THREAD = None
_REPORT_ACTIVE = False

# === PATCH START ==============================================================
# 📍 TARGET: engines/inplay/inplay_flag_helper.py
# 🔎 SEARCH: def start_inplay_authority_report_loop(
# 🛠 ACTION: REPLACE ENTIRE LOOP — Authoritative Live Version
# 📆 PATCHED: 2026-02-20 — Stage 2C Autonomous Authority Loop
#
# PURPOSE:
# - Remove dependency injection
# - Self-discover markets from bets table
# - Self-fetch marketStartTime
# - Self-fetch live runner prices
# - Fully authoritative
# - Zero parameters required
#
# INVARIANT:
# - No BUS dependency
# - No orchestrator parameter passing
# - Fully autonomous
# ==============================================================================

def start_inplay_authority_report_loop(interval_s: int = 5):

    global _REPORT_THREAD, _REPORT_ACTIVE

    if _REPORT_THREAD and _REPORT_THREAD.is_alive():
        return

    _REPORT_ACTIVE = True

    def _loop():

        from engines.config_paths import open_bets_db
        from tools.betfair_runner_trend_surface import get_runner_trend
        import sqlite3

        while _REPORT_ACTIVE:
            try:
                # --------------------------------------------------
                # 1️⃣ Discover today's markets
                # --------------------------------------------------
                con = open_bets_db(rw=False)
                con.row_factory = sqlite3.Row

                rows = con.execute("""
                    SELECT DISTINCT marketId, marketStartTime
                    FROM bets
                    WHERE substr(marketStartTime,1,10)=date('now','utc')
                """).fetchall()

                con.close()

                if not rows:
                    time.sleep(interval_s)
                    continue

                # --------------------------------------------------
                # 2️⃣ For each market
                # --------------------------------------------------
                for row in rows:

                    market_id = str(row["marketId"])
                    start_raw = row["marketStartTime"]

                    if not start_raw:
                        continue

                    try:
                        market_start_ts = datetime.fromisoformat(
                            start_raw.replace("Z","")
                        ).replace(tzinfo=timezone.utc).timestamp()
                    except Exception:
                        continue

                    # --------------------------------------------------
                    # 3️⃣ Build live price map from runner_trend_surface
                    # --------------------------------------------------
                    runner_prices = {}

                    # discover runners from trend surface memory
                    # this assumes surface already active
                    try:
                        from tools.betfair_runner_trend_surface import _TREND_CACHE
                        for (mid, sid), v in list(_TREND_CACHE.items()):
                            if str(mid) == market_id:
                                runner_prices[str(sid)] = v.get("px")
                    except Exception:
                        continue

                    if not runner_prices:
                        continue

                    # --------------------------------------------------
                    # 4️⃣ Build intelligence
                    # --------------------------------------------------
                    intel = build_race_intelligence(
                        market_id,
                        market_start_ts,
                        runner_prices
                    )

                    # --------------------------------------------------
                    # 5️⃣ Print authority report
                    # --------------------------------------------------
                    _print_inplay_authority_report(
                        market_id,
                        intel
                    )

            except Exception as e:
                print(f"[INPLAY REPORT][WARN] {e}")

            time.sleep(interval_s)

    import threading

    t = threading.Thread(
        target=_loop,
        name="InPlayAuthorityReport",
        daemon=True
    )

    _REPORT_THREAD = t
    t.start()

    print("[InPlayAuthorityReport] AUTONOMOUS LOOP STARTED")

# === PATCH END ==============================================================



def stop_inplay_authority_report_loop():
    global _REPORT_ACTIVE
    _REPORT_ACTIVE = False


# -----------------------------------------------------------------------------
# Standalone Simulation Extension
# -----------------------------------------------------------------------------

if __name__ == "__main__":

    print("\n=== AUTHORITY REPORT LOOP TEST ===\n")

    market_id = "TEST"
    start_time = _now_utc() + 3

    simulated = [
        {"1": 2.0, "2": 3.0, "3": 10.0},
        {"1": 2.1, "2": 3.2, "3": 10.5},
        {"1": 2.8, "2": 3.6, "3": 14.0},
        {"1": 3.5, "2": 4.0, "3": 20.0},
    ]

    idx = {"i": 0}

    def supplier():
        snap = simulated[idx["i"] % len(simulated)]
        idx["i"] += 1
        return snap

    start_inplay_authority_report_loop(
        market_id,
        start_time,
        supplier,
        interval_s=2
    )

    time.sleep(10)
    stop_inplay_authority_report_loop()

    print("\n=== END AUTHORITY REPORT TEST ===\n")


    print("\n=== RACE INTELLIGENCE TEST ===\n")

    for snap in simulated:

        intel = build_race_intelligence(
            market_id,
            start_time,
            snap
        )

        print("Prices:", snap)
        print("Market:", intel["market"])
        print("Top Runner:", intel["runners"][0] if intel["runners"] else None)
        print("-" * 50)

        time.sleep(1)

    print("\n=== END RACE INTELLIGENCE TEST ===\n")


    print("\n=== END TEST ===\n")
