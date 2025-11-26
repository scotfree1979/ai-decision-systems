#!/usr/bin/env python3
# gui/mastery_dashboard_v7/micro_scalper_router.py
"""
AutoScalp v7 Micro-Scalper Router
---------------------------------
Always-on one-tick engine for continuous micro-trading across all active runners.
Integrates with live_router placement and CAP logic.
"""

import uuid, time, sqlite3, random
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db
from engines.mastery import event_sink
from engines.live.live_router import place_parent_and_hedge
from engines.caps import cap_ok, pass_no_for

FLAT_STAKE = 5.0          # default micro-scalp stake
STOP_TICKS = 5             # stop distance
HEDGE_TICKS = 1            # target distance
MAX_OPEN_PER_LETTER = 3    # hard CAP
ONE_TICK_LETTERS = ["A", "S", "X", "F", "G", "I"]

def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _db():
    con = sqlite3.connect(autoscalp_db())
    con.row_factory = sqlite3.Row
    return con

def _next_trade_index(con, mid: str, sid: str, letter: str) -> int:
    """Compute next trade number per (market, selection, letter)."""
    row = con.execute("""
        SELECT COUNT(*) AS n
          FROM orders
         WHERE marketId=? AND selectionId=? AND source=? 
           AND date(opened_at)=date('now','utc')
    """, (str(mid), str(sid), f"{letter}{letter}")).fetchone()
    return int((row["n"] or 0) + 1)

def _emit(evt_type: str, payload: dict):
    """Emit event to Mastery sink (safe)."""
    try:
        event_sink.on_decision({"type": evt_type, **payload})
    except Exception:
        pass

def micro_tick(mid: str, sid: str, letter: str, odds: float, direction: str):
    """Attempt a one-tick micro scalp."""
    con = _db()
    try:
        ok, reason, metrics = cap_ok(mid, sid, f"{letter}{letter}")
        if not ok:
            _emit("cap_block", {"marketId": mid, "selectionId": sid, "letter": letter, "reason": reason})
            return False

        trade_no = _next_trade_index(con, mid, sid, letter)
        subtype = f"{letter}{letter}"
        cref = f"{subtype}{trade_no}-{uuid.uuid4().hex[:8]}"

        plan = {
            "marketId": mid,
            "selectionId": sid,
            "letter": subtype,
            "direction": direction,
            "target_ticks": HEDGE_TICKS,
            "stop_ticks": STOP_TICKS,
            "size": FLAT_STAKE,
            "px": float(odds),
            "customerOrderRef": cref,
        }
        ctx = {"mode": "MICRO", "run_id": "MICRO-AUTO"}

        result = place_parent_and_hedge(_name=subtype, _plan=plan, _ctx=ctx)
        if result and result[0]:
            _emit("micro_entry", {
                "marketId": mid,
                "selectionId": sid,
                "letter": subtype,
                "trade_no": trade_no,
                "odds": odds,
                "stake": FLAT_STAKE,
                "ts": _now()
            })
            return True
        else:
            _emit("micro_fail", {"marketId": mid, "selectionId": sid, "reason": "router_fail"})
            return False
    finally:
        con.close()

def run_once():
    """Scan candidates and fire micro trades."""
    con = _db()
    try:
        # choose active runners with odds within range (flat/active markets)
        rows = con.execute("""
            SELECT marketId, selectionId, ltp
              FROM odds_current
             WHERE ltp BETWEEN 2.0 AND 10.0
             LIMIT 20
        """).fetchall()
        for r in rows:
            mid, sid, odds = r["marketId"], r["selectionId"], float(r["ltp"] or 0)
            for L in ONE_TICK_LETTERS:
                direction = "LAY->BACK" if odds >= 4.0 else "BACK->LAY"
                micro_tick(mid, sid, L, odds, direction)
        print(f"[MICRO] tick processed {len(rows)} runners @ {_now()}")
    finally:
        con.close()

def start(interval_s: int = 30):
    """Main loop."""
    print("[MICRO] scalper loop active (interval={}s)".format(interval_s))
    while True:
        try:
            run_once()
        except Exception as e:
            print("[MICRO] loop warn:", e)
        time.sleep(interval_s)

if __name__ == "__main__":
    start(45)
