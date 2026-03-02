# engines/live_view/live_view.py
# ================================================================
# LIVE VIEW ENGINE (Memory Projection Layer)
# ================================================================

from __future__ import annotations
from typing import Dict, Any, List
from datetime import datetime, timezone
import threading
import time

from engines.config_paths import open_bets_db
from engines.market_monitor.monitor import get_market_state
from engines.market_monitor.monitor import classify
from engines.market_monitor.monitor import get_crossover_signal
from tools.betfair_liability_surface import compute_true_market_liability

_LOCK = threading.RLock()
_STATE: Dict[str, Any] = {}
_THREAD = None
_STOP = None

SWEETSPOT = 7.0


# --------------------------------------------------
# Helpers
# --------------------------------------------------

def _utc_now():
    return datetime.now(timezone.utc)

def _next_market_id() -> str | None:
    """
    Returns the next upcoming marketId (UTC).
    """
    con = open_bets_db(rw=False)
    try:
        row = con.execute("""
            SELECT marketId
            FROM bets
            WHERE marketStartTime > datetime('now','utc')
            ORDER BY marketStartTime ASC
            LIMIT 1
        """).fetchone()
        return str(row[0]) if row else None
    finally:
        con.close()


def _load_market_runners(mid: str):
    con = open_bets_db(rw=False)
    con.row_factory = None
    try:
        rows = con.execute("""
            SELECT selectionId, horse_name
            FROM bets
            WHERE marketId=?
            GROUP BY selectionId, horse_name
        """, (mid,)).fetchall()
        return rows
    finally:
        con.close()


# --------------------------------------------------
# Core Projection Builder
# --------------------------------------------------

def _build_state():

    mid = _next_market_id()
    if not mid:
        return {}

    runners = _load_market_runners(mid)
    market_state = get_market_state(mid)

    output_runners: List[Dict[str, Any]] = []

    for sid, horse_name in runners:

        sid = str(sid)

        mm = classify(mid, sid)
        px = mm.get("px")

        # PnL if wins (from execution surface simulation)
        # This is exchange truth world
        # (We can later refine per engine if needed)
        pnl_if_win = 0.0
        try:
            # This will be replaced later with direct surface projection
            pnl_if_win = 0.0
        except Exception:
            pass

        output_runners.append({
            "selectionId": sid,
            "horse_name": horse_name,
            "odds": px,
            "pnl_if_win": pnl_if_win,
        })

    # Sort 1: lowest odds
    lowest_odds = sorted(
        output_runners,
        key=lambda x: (x["odds"] is None, x["odds"])
    )[:4]

    # Sort 2: most money if wins
    most_money = sorted(
        output_runners,
        key=lambda x: x["pnl_if_win"],
        reverse=True
    )[:4]

    return {
        "marketId": mid,
        "generated_at": _utc_now().isoformat(),
        "overview": {
            "marketId": mid,
        },
        "lowest_odds": lowest_odds,
        "most_money": most_money,
    }


# --------------------------------------------------
# Public Access
# --------------------------------------------------

def get_live_view_state() -> Dict[str, Any]:
    with _LOCK:
        return dict(_STATE)


def _loop(period_s: float = 2.0):
    while not _STOP.is_set():
        try:
            new_state = _build_state()
            with _LOCK:
                _STATE.clear()
                _STATE.update(new_state)
        except Exception:
            pass

        time.sleep(period_s)


def start_live_view_loop(period_s: float = 2.0):
    global _THREAD, _STOP

    if _THREAD and _THREAD.is_alive():
        return

    _STOP = threading.Event()

    t = threading.Thread(
        target=_loop,
        args=(period_s,),
        name="LiveViewLoop",
        daemon=True,
    )

    t.start()
    _THREAD = t
    print(f"[LIVE VIEW] started (period={period_s}s)")