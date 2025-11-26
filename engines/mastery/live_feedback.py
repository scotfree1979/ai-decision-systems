# === PATCH START ===
# 📍 TARGET: engines/mastery/live_feedback.py
# 📆 PATCHED: 2025-10-25Z — Live feedback bridge between Overwatcher and Mastery
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""
Live Feedback Engine — connects Overwatcher output to Mastery adaptive logic.
Listens to event_sink and adjusts per-letter weights and confidence
in real time based on market exposure conditions.
"""

import threading, time, json, sqlite3
from datetime import datetime, timezone
from engines.config_paths import bets_db
from engines.mastery import canonical_digest
from engines.mastery import event_sink

# In-memory live state
_live_risk = {}      # marketId → {ratio, pnl, liab}
_letter_bias = {}    # letter → current multiplier

def _utcnow():
    return datetime.now(timezone.utc).isoformat()

def _adjust_letter_weight(letter: str, delta: float):
    """Smoothly adjust letter multiplier within [0.5, 1.5]."""
    base = canonical_digest.weight_for_letter(letter)
    new_w = max(0.5, min(1.5, base + delta))
    _letter_bias[letter] = round(new_w, 3)
    return _letter_bias[letter]

# === PATCH START ===
# 📍 TARGET: engines/mastery/live_feedback.py:_record_mastery_event
# 📆 PATCHED: 2025-11-21 — use connect_bets_db(rw=True) not raw sqlite

from engines.config_paths import connect_bets_db

def _record_mastery_event(event_type: str, details: dict):
    """Write event into mastery_events table (stable RW Bets DB path)."""
    try:
        con = connect_bets_db(rw=True)   # DAL-safe writer
        con.row_factory = __import__("sqlite3").Row
        con.execute("""
            CREATE TABLE IF NOT EXISTS mastery_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                details_json TEXT,
                source TEXT,
                ts TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        con.execute("""
            INSERT INTO mastery_events(event_type, details_json, source)
            VALUES (?, ?, 'LIVE')
        """, (event_type, json.dumps(details)))
        con.commit()
    except Exception as e:
        print(f"[LIVE_FEEDBACK] DB write warn: {e}")
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


def _on_market_risk(payload: dict):
    """Handle continuous risk telemetry from Overwatcher."""
    mid = payload.get("marketId")
    ratio = payload.get("ratio", 0.0)
    _live_risk[mid] = {
        "ratio": ratio,
        "pnl": payload.get("pnl_total", 0.0),
        "liab": payload.get("liability_total", 0.0),
        "ts": _utcnow()
    }

def _on_greenup(payload: dict):
    """Lock profit — reduce aggressiveness for the same strategy letters."""
    for letter in canonical_digest.avg_by_letter().keys():
        new_w = _adjust_letter_weight(letter, -0.1)
        _record_mastery_event("greenup_lock", {"letter": letter, "new_weight": new_w})

def _on_micro_lay(payload: dict):
    """Reward behaviour — increase bias slightly."""
    for letter in canonical_digest.avg_by_letter().keys():
        new_w = _adjust_letter_weight(letter, +0.05)
        _record_mastery_event("micro_lay_reinforce", {"letter": letter, "new_weight": new_w})

def _on_stoploss(payload: dict):
    """Punitive dampening — freeze aggressive strategies."""
    for letter in canonical_digest.avg_by_letter().keys():
        new_w = _adjust_letter_weight(letter, -0.2)
        _record_mastery_event("stoploss_freeze", {"letter": letter, "new_weight": new_w})

# Subscription map
_HANDLERS = {
    "market_risk": _on_market_risk,
    "greenup": _on_greenup,
    "micro_lay": _on_micro_lay,
    "stoploss": _on_stoploss,
}

def _listen():
    """Continuously listen to Overwatcher event sink."""
    print("[LIVE_FEEDBACK] listening for Overwatcher events …")
    while True:
        try:
            evt = event_sink.get()  # blocking or polling based on implementation
            if not evt:
                time.sleep(0.5)
                continue
            t = evt.get("type")
            handler = _HANDLERS.get(t)
            if handler:
                handler(evt)
        except Exception as e:
            print("[LIVE_FEEDBACK] warn:", e)
            time.sleep(1.0)

def start_live_feedback():
    """Spawn feedback listener thread."""
    t = threading.Thread(target=_listen, name="MasteryLiveFeedback", daemon=True)
    t.start()
    return t
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/live_feedback.py:_insert_plan and stake helpers
# 📆 PATCHED: 2025-10-26Z — exposure-scaled dynamic stakes + confidence + logging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import sqlite3, os, json
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db

# === PATCH START ===
# 📍 TARGET: engines/mastery/live_feedback.py:_auto_path → replaced by _open_auto_rw
# 📆 PATCHED: 2025-11-21 — canonical RW connector into AUTOSCALP_GUI

from engines.config_paths import auto_conn as _auto_conn

def _open_auto_rw():
    """Canonical RW connection into AUTOSCALP_GUI."""
    con = _auto_conn(rw=True)   # guarantees WAL + busy_timeout + shared cache
    con.row_factory = __import__("sqlite3").Row
    return con
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/live_feedback.py:_get_total_open_liability
# 📆 PATCHED: 2025-11-21 — use DAL RW connector + q_retry
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.decision_engine.decide_once.helpers import q_retry as _q

def _get_total_open_liability() -> float:
    """Read total open liability for today from AUTO_DB.book_state."""
    try:
        con = _open_auto_rw()
        row = _q(con, """
            SELECT SUM(open_liability) AS liab
            FROM book_state
            WHERE day = date('now','utc')
        """).fetchone()
        return float(row["liab"] or 0.0)
    except Exception:
        return 0.0
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


def _calc_confidence(event_type:str, pnl_total:float, liab_total:float) -> float:
    """Confidence weighting: how strongly this plan should be prioritised."""
    ratio = 0 if liab_total <= 0 else pnl_total / liab_total
    if event_type == "micro_lay":
        conf = 0.9 + max(0, (16 - pnl_total)/32) * 0.2
    elif event_type == "greenup":
        conf = 0.8 + min(0.4, abs(ratio))
    elif event_type == "stoploss":
        conf = 0.5 - min(0.3, abs(ratio))
    else:
        conf = 1.0
    return round(max(0.3, min(1.2, conf)), 3)

def _calc_stake(event_type:str, pnl_total:float, odds:float, total_liab:float) -> float:
    """Stake sized to move market P&L toward target envelope and stay within global exposure."""
    if odds <= 0:
        return 5.0
    if event_type == "micro_lay":
        stake = max(0, (16.0 - pnl_total) / odds)
    elif event_type == "greenup":
        stake = max(0, (pnl_total - 32.0) / odds)
    elif event_type == "stoploss":
        stake = abs(pnl_total + 90.0) / odds * 1.2
    else:
        stake = 5.0
    # scale down if total exposure > £300
    liab_cap = 300.0
    scale = 1.0 - min(0.7, total_liab / liab_cap)
    stake *= max(0.3, scale)
    return round(max(1.0, min(25.0, stake)), 2)

# === PATCH START ===
# 📍 TARGET: engines/mastery/live_feedback.py:_record_injection_event
# 📆 PATCHED: 2025-11-21 — canonical bets DB writer
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.config_paths import connect_bets_db

def _record_injection_event(event_type: str, details: dict):
    """Record Overwatcher injection into mastery_events (BETS DB)."""
    try:
        con = connect_bets_db(rw=True)
        con.row_factory = __import__("sqlite3").Row
        con.execute("""
            CREATE TABLE IF NOT EXISTS mastery_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT,
                details_json TEXT,
                source TEXT,
                ts TEXT DEFAULT (datetime('now','utc'))
            )
        """)
        con.execute("""
            INSERT INTO mastery_events(event_type, details_json, source)
            VALUES(?, ?, 'LIVE')
        """, (event_type, json.dumps(details)))
        con.commit()
    except Exception as e:
        print(f"[LIVE_FEEDBACK] log warn: {e}")
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/mastery/live_feedback.py:_insert_plan
# 📆 PATCHED: 2025-11-21 — write plans via _open_auto_rw (DAL-safe)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _insert_plan(marketId: str, selectionId: str, side: str, odds: float,
                 letter: str, note: str, pnl_total: float, liab_total: float):
    """Insert plan_ledger row via AUTOSCALP_GUI RW connection."""
    stake = _calc_stake(note, pnl_total, odds, liab_total)
    conf  = _calc_confidence(note, pnl_total, liab_total)

    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plan_id = f"AUTO-{marketId}-{selectionId}-{datetime.now().timestamp()}"

    try:
        con = _open_auto_rw()
        con.execute("""
            CREATE TABLE IF NOT EXISTS plan_ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                day TEXT,
                plan_id TEXT,
                marketId TEXT,
                selectionId TEXT,
                letter TEXT,
                note TEXT,
                scalp_direction TEXT,
                proposed_odds REAL,
                proposed_stake REAL,
                confidence REAL,
                status TEXT,
                why TEXT,
                updated_at TEXT
            );
        """)

        con.execute("""
            INSERT INTO plan_ledger(
                day, plan_id, marketId, selectionId,
                letter, note, scalp_direction,
                proposed_odds, proposed_stake,
                confidence, status, why, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','utc'))
        """, (
            day, plan_id, marketId, selectionId,
            letter, note, side,
            odds, stake, conf, "PENDING",
            f"{note} injected by Overwatcher"
        ))

        con.commit()
        print(f"[LIVE_FEEDBACK] plan injected {note} {side} £{stake}@{odds} conf={conf}")
        _record_injection_event(note, {
            "marketId": marketId, "selectionId": selectionId,
            "side": side, "stake": stake,
            "odds": odds, "confidence": conf,
            "liab_total": liab_total, "pnl_total": pnl_total,
        })
    except Exception as e:
        print(f"[LIVE_FEEDBACK] plan insert warn: {e}")
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


# Update handlers to use new total_liab + pass to _insert_plan ----------
def _on_micro_lay(payload):
    for letter in canonical_digest.avg_by_letter().keys():
        new_w = _adjust_letter_weight(letter, +0.05)
        _record_mastery_event("micro_lay_reinforce", {"letter": letter, "new_weight": new_w})
    try:
        mid = payload.get("marketId")
        pnl_total = float(payload.get("pnl_total", 0.0))
        liab_total = _get_total_open_liability()
        child = (payload.get("children") or [{}])[0]
        sid = str(child.get("selectionId"))
        odds = float(child.get("lay1") or 0.0)
        if mid and sid and odds > 0:
            _insert_plan(mid, sid, "LAY", odds, "A", "micro_lay", pnl_total, liab_total)
    except Exception as e:
        print(f"[LIVE_FEEDBACK] micro_lay inject warn: {e}")

def _on_greenup(payload):
    for letter in canonical_digest.avg_by_letter().keys():
        new_w = _adjust_letter_weight(letter, -0.1)
        _record_mastery_event("greenup_lock", {"letter": letter, "new_weight": new_w})
    try:
        mid = payload.get("marketId")
        pnl_total = float(payload.get("pnl_total", 0.0))
        liab_total = _get_total_open_liability()
        child = (payload.get("children") or [{}])[0]
        sid = str(child.get("selectionId"))
        odds = float(child.get("back1") or 0.0)
        if mid and sid and odds > 0:
            _insert_plan(mid, sid, "BACK", odds, "F", "greenup", pnl_total, liab_total)
    except Exception as e:
        print(f"[LIVE_FEEDBACK] greenup inject warn: {e}")

def _on_stoploss(payload):
    for letter in canonical_digest.avg_by_letter().keys():
        new_w = _adjust_letter_weight(letter, -0.2)
        _record_mastery_event("stoploss_freeze", {"letter": letter, "new_weight": new_w})
    try:
        mid = payload.get("marketId")
        pnl_total = float(payload.get("pnl_total", 0.0))
        liab_total = _get_total_open_liability()
        child = (payload.get("children") or [{}])[0]
        sid = str(child.get("selectionId"))
        odds = float(child.get("back1") or 0.0)
        if mid and sid and odds > 0:
            _insert_plan(mid, sid, "BACK", odds, "X", "stoploss", pnl_total, liab_total)
    except Exception as e:
        print(f"[LIVE_FEEDBACK] stoploss inject warn: {e}")
# === PATCH END ===
