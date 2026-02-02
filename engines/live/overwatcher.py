#!/usr/bin/env python3
# engines/live/overwatcher.py
import time, threading, sqlite3, json
from datetime import datetime, timezone
from engines.config_paths import open_auto_db
from engines.live.live_router import _cancel, _keys, _orders_conn, process_stoploss

# === PATCH START ============================================================
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 SEARCH: from engines.config_paths import auto_conn, q_retry as _q, autoscalp_db
# 📆 PATCHED: 2025-12-03 — Route Overwatcher through LiveCache (cloud) while keeping all aliases

# OLD:
# from engines.config_paths import auto_conn, q_retry as _q, autoscalp_db

# NEW:
from engines.config_paths import (
    auto_conn_live as auto_conn,     # 🔥 Overwatcher now uses LiveCache everywhere
    auto_conn_live as _auto_conn,    # 🔥 ensure all internal alias calls also use LiveCache
    q_retry as _q,
    autoscalp_db,                    # unchanged — still returns path only
)

# Backwards compatibility for anything else that imported auto_conn FOR THIS MODULE:
auto_conn_local = auto_conn          # legacy alias if needed
# === PATCH END ==============================================================
# === PATCH START ===
_q_retry = _q   # back-compat alias
# === PATCH END ===


#try:
#    from engines.live.live_router import _round_odds, _calc_hedge_stake, _ref, _place, _cancel
#except ImportError:
#    from engines.live import live_router
#    def _calc_hedge_stake(entry_stake, entry_odds, exit_odds, side):
#        if side.upper() == "LAY":
#            return round(entry_stake * entry_odds / max(exit_odds, 1.01), 4)
#        return round(entry_stake * (exit_odds - 1) / max(entry_odds - 1, 0.01), 4)
#    def _calc_exposure_drift(anchor, current):
#        try:
#            return round(((current - anchor) / anchor) * 100.0, 2)
#        except Exception:
#            return 0.0
#    live_router._calc_hedge_stake = _calc_hedge_stake
#    live_router._calc_exposure_drift = _calc_exposure_drift
#    print("[OVERWATCHER] ⚙️  auto-patched hedge helpers into live_router")

from engines.live.live_router import _orders_conn, _orders_update_parent_cancelled
from engines.utils.api_tools import fetch_live_odds
from engines.mastery import event_sink
# ensure Mastery-v7 bridge is live so on_decision() events route to live_router
import engines.mastery_v7.live_router_bridge  # noqa: F401

from engines.live.live_router import analyze_market_pnl
from engines.live.live_router import _keys

# overwatcher.py (patched)


# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 SEARCH: from engines.stoploss_engine import StopLossInputs, evaluate_stoploss
# 📆 PATCHED: 2025-11-29 — Replace old SL engine with new dynamic trailing engine

from engines.live.stoploss_engine import StopLossEngine, ParentState
MSC_STOPLOSS = StopLossEngine()
# === PATCH END ===
# ======================================================================
# 📍 TARGET: engines/live/overwatcher.py
# 🔁 REPLACE: all trailing / mixed stop-loss executors
# 📆 PATCHED: 2025-12-18 — Single-source MSC stop-loss with SLEQ scaling
# ======================================================================

from engines.price_math import walk_ticks
from engines.live.stoploss_engine import StopLossEngine, ParentState

MSC_STOPLOSS = StopLossEngine()  # provides SLEQ + baseline

# ======================================================================================================
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 ANCHOR: def _compute_msc_stop_px(
# 🧩 ACTION: REPLACE stop-loss direction logic (explicit adverse direction)
# 📆 PATCHED: 2026-01-22 — Correct MSC_EXPLORATORY stop direction (rule-accurate)
#
# RULE (CANONICAL):
#   Lay first  → adverse move = UP   → stop ABOVE entry
#   Back first → adverse move = DOWN → stop BELOW entry
# ======================================================================================================
def _compute_msc_stop_px(entry_odds: float, side: str, sleq: float) -> float:
    BASE_TICKS = 4
    MIN_TICKS  = 2
    MAX_TICKS  = 10

    ticks = int(round(BASE_TICKS * sleq))
    ticks = max(MIN_TICKS, min(MAX_TICKS, ticks))

    side = side.upper()

    if side == "LAY":
        # adverse = odds UP
        return walk_ticks(entry_odds, ticks, direction="up")
    else:
        # BACK → adverse = odds DOWN
        return walk_ticks(entry_odds, ticks, direction="down")


def evaluate_redistribution(ctx: dict) -> dict:
    """
    Pure analysis function.
    NO execution.
    NO DB writes.
    NO side effects.

    Returns redistribution intent or empty dict.
    """
    try:
        market_id = ctx.get("marketId")
        if not market_id:
            return {}

        con = _orders_conn()
        pnl_vec = analyze_market_pnl(con, market_id)
        con.close()

        if not pnl_vec:
            return {}

        values = list(pnl_vec.values())
        p_min = min(values)
        p_max = max(values)
        dispersion = p_max - p_min

        # pull core values
        from engines.config_core_values import CORE_VALUES
        target = CORE_VALUES["target_profit_per_race"]   # 16
        max_loss = CORE_VALUES["max_loss_per_race"]      # -90

        oc = ctx.get("oc_phase", 0)

        # gating
        if oc < 7:
            return {}

        if dispersion < 5:   # trivial imbalance
            return {}

        urgency = (
            "HIGH"   if oc >= 10 else
            "MEDIUM" if oc >= 8  else
            "LOW"
        )

        return {
            "type": "redistribution_intent",
            "marketId": market_id,
            "pnl_vector": pnl_vec,
            "p_min": p_min,
            "p_max": p_max,
            "dispersion": dispersion,
            "target_profit": target,
            "max_loss": max_loss,
            "urgency": urgency,
            "oc_phase": oc,
        }

    except Exception:
        return {}



def enforce_msc_exploratory_stoploss():
    """
    MSC_EXPLORATORY STOPLOSS — ONLY EXECUTOR
    ---------------------------------------
    - No trailing execution
    - No legacy execution
    - No bus
    - Emits STOPLOSS plans only
    """

    con = _orders_conn(); con.row_factory = sqlite3.Row
    parents = _q_retry(con, """
        SELECT id, customerOrderRef, marketId, selectionId,
               side, entry_odds, entry_stake, source
          FROM orders
         WHERE role='PARENT'
           AND entry_status='matched'
           AND exit_status IS NULL
           AND UPPER(source)='MSC_EXPLORATORY'
    """).fetchall()
    con.close()

    if not parents:
        return

    con = _orders_conn(); con.row_factory = sqlite3.Row
    prices = {
        (str(r["marketId"]), str(r["selectionId"])): float(r["ltp"])
        for r in _q_retry(con, """
            SELECT marketId, selectionId, ltp
              FROM odds_current
             WHERE ltp IS NOT NULL
        """).fetchall()
    }
    con.close()

    for p in parents:
        mid = str(p["marketId"])
        sid = str(p["selectionId"])
        px  = prices.get((mid, sid))
        if px is None:
            continue

        # Build parent state for SLEQ
        parent_state = ParentState(
            parent_id=int(p["id"]),
            entry_side=p["side"],
            entry_odds=float(p["entry_odds"]),
            entry_stake=float(p["entry_stake"]),
        )

        sleq = MSC_STOPLOSS.get_sleq(parent_state)

        stop_px = _compute_msc_stop_px(
            entry_odds=float(p["entry_odds"]),
            side=p["side"],
            sleq=sleq
        )

        hit = (
            p["side"].upper() == "LAY"  and px >= stop_px or
            p["side"].upper() == "BACK" and px <= stop_px
        )

        if not hit:
            continue

        process_stoploss({
            "marketId": mid,
            "selectionId": sid,
            "entry_side": p["side"],
            "entry_stake": float(p["entry_stake"]),
            "current_odds": px,
            "stop_loss_px": stop_px,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })


from engines.price_math import walk_ticks, calculate_tick_distance as _tick_distance  # ✅ FIX

from engines.mastery import event_sink

def enforce_parent_stoploss_px():
    """
    Detect stop-loss for MSC_EXPLORATORY parents only.
    Emits STOPLOSS plans; never executes.
    """
    con = _orders_conn(); con.row_factory = sqlite3.Row
    parents = _q_retry(con, """
        SELECT id, customerOrderRef, marketId, selectionId,
               side, entry_odds, entry_stake, stop_loss_px, source
          FROM orders
         WHERE role='PARENT'
           AND entry_status='matched'
           AND exit_status IS NULL
           AND stop_loss_px IS NOT NULL
    """).fetchall()
    con.close()

    if not parents:
        return

    con = _orders_conn(); con.row_factory = sqlite3.Row
    prices = {
        (str(r["marketId"]), str(r["selectionId"])): float(r["ltp"])
        for r in _q_retry(con, """
            SELECT marketId, selectionId, ltp
              FROM odds_current
             WHERE ltp IS NOT NULL
        """).fetchall()
    }
    con.close()

    for p in parents:
        if (p["source"] or "").upper() != "MSC_EXPLORATORY":
            continue

        mid = str(p["marketId"])
        sid = str(p["selectionId"])
        px = prices.get((mid, sid))
        if px is None:
            continue

        side = p["side"].upper()

        if side == "LAY":
            hit = px >= stop_px   # adverse: odds drift UP
        else:
            hit = px <= stop_px   # adverse: odds drift DOWN

        if not hit:
            continue
        sl = float(p["stop_loss_px"])

        hit = (side == "LAY" and px >= sl) or (side == "BACK" and px <= sl)
        if not hit:
            continue

        process_stoploss({
            "marketId": mid,
            "selectionId": sid,
            "entry_side": side,
            "entry_stake": float(p["entry_stake"]),
            "current_odds": px,
            "stop_loss_px": sl,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

from engines.live.live_router import _cancel, _keys, _orders_conn

def _cancel_active_hedge_child(parent_id: int):
    """
    Cancel the active HEDGE (H) child on Betfair after STOPLOSS fires.
    This is authoritative execution — DB updates are secondary.
    """
    con = _orders_conn()
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    row = cur.execute("""
        SELECT id, entry_bet_id
          FROM orders
         WHERE role='CHILD'
           AND hedge_of=?
           AND UPPER(exit_kind)='HEDGE'
           AND entry_status IN ('QUEUED','PLACED')
           AND entry_bet_id IS NOT NULL
         LIMIT 1
    """, (int(parent_id),)).fetchone()

    if not row:
        con.close()
        return False

    child_id = int(row["id"])
    bet_id   = str(row["entry_bet_id"])

    try:
        app_key, token = _keys()
        _cancel(app_key, token, bet_id)   # 🔥 REAL cancellation
    except Exception as e:
        print(f"[OVERWATCHER] hedge cancel failed bet_id={bet_id}: {e}")

    # Mirror Betfair reality in DB
    try:
        cur.execute("""
            UPDATE orders
               SET entry_status='CANCELLED',
                   closed_at=datetime('now','utc'),
                   exit_kind='CANCELLED_BY_STOPLOSS'
             WHERE id=?
        """, (child_id,))
        con.commit()
    except Exception:
        pass

    con.close()
    print(f"[OVERWATCHER] hedge child cancelled child_id={child_id}")
    return True



def check_overwatcher_state():
    import threading
    live_threads = [t.name for t in threading.enumerate() if 'OVERWATCHER' in t.name or 'feedback' in t.name]
    print(f"[CHECK] Overwatcher: threads active → {len(live_threads)} ({','.join(live_threads)})")
    return bool(live_threads)

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:_on_bridge_pulse
# 📆 PATCHED: 2025-12-01 — store last valid brain pulse for MSC + ignore empty pulses
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import time
_last_brain_note = [0]       # module-level mutable container (existing)
_last_brain_pulse = [None]   # NEW: stores last full valid pulse (same style)

def _on_brain_pulse(payload):
    """
    Disabled execution path.
    Brain is now observer-only and must not route or execute.
    """
    return



# =====================================================================
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 ANCHOR: event_sink.subscribe(...) region
# 🆕 ADD: stop-loss-trigger event mapper → MicroScalper RiskEngine
# 📆 PATCHED: 2025-11-28Z
# =====================================================================

def _on_tsl_event(payload):
    """
    Forward TSL stop-loss events into ctx-range variable so
    MicroScalper RiskEngine can react and clean up its own children.
    Expected payload:
        {
            "type": "stop_loss_triggered",
            "parent_id": <int>,
            ...
        }
    """
    try:
        if payload.get("type") != "stop_loss_triggered":
            return
        pid = payload.get("parent_id")
        if pid is None:
            return
        # Store parent_id into a global tick-ctx slot
        # Overwatcher uses this value when building ctx per runner
        global _TSL_LAST_PARENT
        _TSL_LAST_PARENT = pid
    except Exception as e:
        print(f"[TSL][WARN] failed to map stop-loss event: {e}")

# ensure at module top:
# === PATCH C START ===
# Safe early export of TSL bridge context before any imports use it
_TSL_LAST_PARENT = None




def get_tsl_parent():
    """Safe accessor used by MicroScalper ctx injection."""
    return _TSL_LAST_PARENT
# === PATCH C END ===


# subscribe into Overwatcher event sink
event_sink.subscribe(_on_brain_pulse)
event_sink.subscribe(_on_tsl_event)

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py (after other event_sink.subscribe calls)
# 🔎 SEARCH: event_sink.subscribe(_on_bridge_pulse)
# 📆 PATCHED: 2025-11-20
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _on_band_change(payload):
    try:
        if payload.get("type") != "band_change":
            return
        mid = str(payload.get("marketId"))
        sid = str(payload.get("selectionId"))
        prev_b = payload.get("prev_band")
        new_b  = payload.get("new_band")

        print(f"[OVERWATCHER] band_change mid={mid} sid={sid} {prev_b}→{new_b}")

        # FUTURE: trigger risk reactions here
        # e.g. if new_b=="ACTIVE": begin defensive hedging
        #      if new_b=="IGNORED": relax liability checks
        # (keeping this minimal: only print for now)
    except Exception as e:
        print(f"[OVERWATCHER][BAND] warn: {e}")

event_sink.subscribe(_on_band_change)
# === PATCH END ===



# --- Helpers -----------------------------------------------------------------

def _utcnow_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

def _open_orders():
    """Return list of open parents (matched, not closed)."""
    con = _orders_conn(); con.row_factory = sqlite3.Row
    rows = _q(con, """
        SELECT id, customerOrderRef, marketId, selectionId, side,
               entry_odds, entry_stake, stop_ticks, stop_loss_triggered
          FROM orders
         WHERE role='PARENT'
           AND entry_status='matched'
           AND (exit_status IS NULL OR exit_status<>'matched')
    """).fetchall()
    con.close()
    return rows or []



# --- Stop-Loss ---------------------------------------------------------------
# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 SEARCH FOR: def enforce_stop_losses(
# 📆 PATCHED: 2025-11-30 — integrate Trailing Stop-Loss Engine v1.0
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

from engines.live.stoploss_engine import StopLossEngine, ParentState

# Instantiate a singleton TSL engine (persists SLEQ across ticks)
_TSL_ENGINE = StopLossEngine()

# === PATCH START ============================================================
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 SEARCH: def enforce_stop_losses_trailing(
# 📆 PATCHED: 2025-12-01 — MSC-only trailing SL (Legacy excluded)
# ============================================================================

def enforce_stop_losses_trailing():
    """
    Trailing Stop-Loss — MSC ONLY
    -----------------------------
    Applies trailing SL ONLY to:
        • MSC-EXPLORATORY children
     
    Legacy parents:
        ❌ NEVER receive stop-loss
        ✔ Use boundaries (1.5 / 12.0)
        ✔ Use trailing for hedge-only behaviour
    """

    con = _orders_conn(); con.row_factory = sqlite3.Row
    parents = _q_retry(con, """
        SELECT id, customerOrderRef, marketId, selectionId, side,
               entry_odds, entry_stake, source
          FROM orders
         WHERE role='PARENT'
           AND entry_status='matched'
           AND (exit_status IS NULL OR exit_status<>'matched')
    """).fetchall()
    con.close()

    if not parents:
        return

    # === MSC_EXPLORATORY ONLY FILTER ===
    msc_parents = [
        p for p in parents
        if str(p["source"] or "").upper() == "MSC_EXPLORATORY"
    ]

    if not msc_parents:
        return

    # === Preload prices =====================================================
    con = _orders_conn(); con.row_factory = sqlite3.Row
    px_rows = _q_retry(con, """
        SELECT marketId, selectionId,
               COALESCE(lay1, back1, ltp) AS px
          FROM odds_current
         WHERE date(updated_ts)=date('now','utc')
    """).fetchall()
    con.close()

    price_map = {
        (str(r["marketId"]), str(r["selectionId"])): float(r["px"])
        for r in px_rows if r["px"] is not None
    }

    # === Evaluate SL ========================================================
    for p in msc_parents:
        mid = str(p["marketId"])
        sid = str(p["selectionId"])

        px = price_map.get((mid, sid))
        if px is None:
            continue

        exit_side = "BACK" if p["side"].upper() == "LAY" else "LAY"
        entry_stake = float(p["entry_stake"])
        current_odds = px
        parent_ref = p["customerOrderRef"]


        parent_state = ParentState(
            parent_id=int(p["id"]),
            entry_side=str(p["side"]),
            entry_odds=float(p["entry_odds"]),
            entry_stake=float(p["entry_stake"])
        )

        ev = MSC_STOPLOSS.evaluate(
            parent_state,
            mid,
            sid,
            current_odds=px,
            oc_phase=0
        )

        if not ev:
            continue

        # Forward to RiskEngine + router
        from engines.live.live_router import enqueue_router_child
        exit_side = "BACK" if p["side"].upper() == "LAY" else "LAY"
        enqueue_router_child(
            plan={
                # child_id intentionally omitted → router will INSERT QUEUED
                "role": "CHILD",
                "engine": "OVERWATCHER",
                "exit_kind": "STOPLOSS",
                "source": "S",                      # letter S
                "parent_cor": p["customerOrderRef"],
                "marketId": mid,
                "selectionId": sid,
                "side": exit_side,                  # BACK or LAY
                "px": current_odds,
                "size": entry_stake,
            },
            ctx={
                "engine": "OVERWATCHER",
                "mode": "LIVE",
            }
        )

        # === PATCH END ==============================================================

# === PATCH START ============================================================
# 📍 TARGET: engines/live/overwatcher.py
# 📆 PATCHED: 2025-12-01 — Legacy-only boundary exits
# ============================================================================

def enforce_legacy_boundaries_and_trailing():
    """
    Legacy Exit Rules
    -----------------
    Legacy parents DO NOT use stop-loss.

    Instead:
        ✔ Boundary exit at LTP <= 1.5  (BACK side)
        ✔ Boundary exit at LTP >= 12.0 (LAY side)
        ✔ Trailing only for hedge-follow behaviour (never stops)
    """
    con = _orders_conn(); con.row_factory = sqlite3.Row
    parents = _q_retry(con, """
        SELECT id, customerOrderRef, marketId, selectionId,
               side, entry_odds, entry_stake, source
          FROM orders
         WHERE role='PARENT'
           AND entry_status='matched'
           AND (exit_status IS NULL OR exit_status <> 'matched')
    """).fetchall()
    con.close()

    legacy = [
        p for p in parents
        if str(p["source"] or "").upper() == "LEGACY"
    ]

    if not legacy:
        return

    # Load prices
    con = _orders_conn(); con.row_factory = sqlite3.Row
    px_map = {
        (str(r["marketId"]), str(r["selectionId"])): float(r["ltp"])
        for r in _q_retry(con, """
            SELECT marketId, selectionId, ltp
              FROM odds_current
             WHERE date(updated_ts)=date('now','utc')
        """).fetchall()
        if r["ltp"] is not None
    }
    con.close()

    # Boundary exit evaluation
    for p in legacy:
        mid = str(p["marketId"])
        sid = str(p["selectionId"])
        side = p["side"].upper()

        if side == "LAY":
            hit = px >= stop_px   # adverse: odds drift UP
        else:
            hit = px <= stop_px   # adverse: odds drift DOWN

        if not hit:
            continue

        ltp = px_map.get((mid, sid))
        if ltp is None:
            continue

        boundary_hit = (
            (side == "BACK" and ltp <= 1.5) or
            (side == "LAY"  and ltp >= 12.0)
        )

        if not boundary_hit:
            continue

        # Mark exit


        event_sink.on_decision({
            "type": "legacy_boundary_exit",
            "marketId": mid,
            "selectionId": sid,
            "entry_side": side,
            "exit_odds": ltp,
            "ts": datetime.now(timezone.utc).isoformat()
        })

        print(f"[LEGACY] boundary exit mid={mid} sid={sid} ltp={ltp}")

# === PATCH END ===============================================================


# === PATCH START ===============================================================
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 SEARCH: def enforce_stop_losses(
# ⛏ ACTION: insert AFTER enforce_stop_losses(), BEFORE _evaluate_market_guardian()
# 📆 PATCHED: 2025-11-29  — Unified Trailing Stop-Loss Engine
# ===============================================================================
import sqlite3
from engines.mastery import event_sink

def enforce_trailing_stops(trail_ticks: int = 10, minimum_profit_ticks: int = 5):
    """
    Unified Trailing Stop-Loss Engine (PRE-OFF & IN-PLAY)

    Applies to:
      • Legacy parents (market-level finalisation)
      • MSC Exploratory children (positive/negative trailing)
    
    Behaviour:
      - Establish base stop at entry_odds ± trail_ticks
      - If price moves in favour:
            widen trailing floor/ceiling accordingly
      - If price pulls back enough:
            fire trailing stop loss
      - If price exceeds 1.5 or 12.0:
            force exit (market-end protection)
    """
    # 1) fetch open parents
    con = _orders_conn(); con.row_factory = sqlite3.Row
    parents = _q_retry(con, """
        SELECT id, customerOrderRef, marketId, selectionId, side,
               entry_odds, entry_stake, entry_status, exit_status,
               COALESCE(source,'') AS source,
               COALESCE(trail_floor,0) AS trail_floor,
               COALESCE(trail_ceil,0) AS trail_ceil
        FROM orders
        WHERE role='PARENT'
          AND entry_status='matched'
          AND (exit_status IS NULL OR exit_status <> 'matched')
    """).fetchall()

    if not parents:
        con.close()
        return

    # Preload prices for all markets
    mids = {str(p["marketId"]) for p in parents}
    price_map = {}
    with _auto_conn(rw=False) as acon:
        acon.row_factory = sqlite3.Row
        placeholders = ",".join("?" * len(mids))
        rows = acon.execute(f"""
            SELECT marketId, selectionId,
                   COALESCE(lay1, back1, ltp) AS px
              FROM odds_current
             WHERE day=date('now','utc')
               AND marketId IN ({placeholders})
        """, tuple(mids)).fetchall()
        for r in rows:
            price_map[(str(r["marketId"]), str(r["selectionId"]))] = float(r["px"] or 0)

    updates = []
    events = []

    for p in parents:
        mid = str(p["marketId"])
        sid = str(p["selectionId"])
        px  = price_map.get((mid, sid))
        if px is None or px <= 0:
            continue

        side   = (p["side"] or "").upper()
        entry  = float(p["entry_odds"] or 0.0)
        floor  = float(p["trail_floor"])
        ceil   = float(p["trail_ceil"])

        # Initialize trailing bands if missing
        if floor == 0 and ceil == 0:
            if side == "LAY":
                # favourable = downwards; floor is minimum
                floor = entry - trail_ticks * pm.get_tick_size(entry)
                ceil  = entry + trail_ticks * pm.get_tick_size(entry)
            else:
                # BACK: favourable = upwards; ceil moves up
                floor = entry - trail_ticks * pm.get_tick_size(entry)
                ceil  = entry + trail_ticks * pm.get_tick_size(entry)

        # Update trailing bands based on favourable move
        if side == "LAY":
            # favourable move = px < entry
            if px < entry:
                # widen floor downward
                floor = min(floor, px - minimum_profit_ticks * pm.get_tick_size(px))
        else:
            # BACK: favourable = px > entry
            if px > entry:
                ceil = max(ceil, px + minimum_profit_ticks * pm.get_tick_size(px))

        # Evaluate trailing breach
        hit = False
        kind = "trailing_negative"

        if side == "LAY":
            # breach floor (profit pullback) or exceed ceil (loss)
            if px >= ceil:
                hit = True
                kind = "trailing_negative"
            elif px <= floor:
                hit = True
                kind = "trailing_positive"
        else:
            # BACK
            if px <= floor:
                hit = True
                kind = "trailing_negative"
            elif px >= ceil:
                hit = True
                kind = "trailing_positive"

        # Hard market bounds
        if px <= 1.5 or px >= 12.0:
            hit  = True
            kind = "market_end_exit"

        # If not hit → update trailing bands
        if not hit:
            updates.append((floor, ceil, r["customerOrderRef"]))
            continue

        # If hit → emit decision + close
        events.append({
            "type": f"msc_trailing_{kind}",
            "parent_ref": p["customerOrderRef"],
            "marketId": mid,
            "selectionId": sid,
            "entry_odds": entry,
            "odds_now": px,
            "source": p["source"],
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

        # Mark exit (STOPLOSS)
        _q_retry(con, """
            UPDATE orders
               SET exit_status='matched',
                   exit_kind='STOPLOSS',
                   closed_at=datetime('now','utc'),
                   exit_odds=?, exit_stake=entry_stake
             WHERE customerOrderRef=?
        """, (px, r["customerOrderRef"]))

    # apply updates
    for floor, ceil, cref in updates:
        _q_retry(con, """
            UPDATE orders
               SET trail_floor=?, trail_ceil=?
             WHERE customerOrderRef=?
        """, (floor, ceil, cref))

    con.commit()
    con.close()

    # Emit events after DB closes
    for evt in events:
        event_sink.on_decision(evt)
# === PATCH END =================================================================


# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:enforce_stop_losses
# 🔎 SEARCH: def enforce_stop_losses(
# 📆 PATCHED: 2025-11-29 — Dynamic trailing stop-loss + classification + SLEQ
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def enforce_stop_losses():
    """
    Evaluate dynamic trailing stop-loss across all live parents.
    Emits a stop_loss_triggered event when a trailing or boundary stop hits.
    Overwatcher handles lifecycle, router places SL child, MSC detaches.
    """

    # 1) Fetch all matched parents not yet exited
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        rows = _q_retry(con, """
            SELECT id, customerOrderRef, marketId, selectionId,
                   side, entry_odds, entry_stake
              FROM orders
             WHERE mode='LIVE'
               AND role='PARENT'
               AND entry_status='MATCHED'
               AND (exit_status IS NULL OR UPPER(exit_status)<>'MATCHED')
        """).fetchall()
        con.close()
    except Exception as e:
        print(f"[SL] fetch parents fail: {e}")
        return

    if not rows:
        return

    # 2) Preload latest prices from odds_current
    try:
        con = _orders_conn(); con.row_factory = sqlite3.Row
        px_map = {
            (str(r["marketId"]), str(r["selectionId"])): float(r["ltp"])
            for r in _q_retry(con, """
                SELECT marketId, selectionId, ltp
                  FROM odds_current
                 WHERE day=date('now','utc')
                   AND ltp IS NOT NULL
            """).fetchall()
        }
        con.close()
    except Exception as e:
        print(f"[SL] price map fail: {e}")
        return

    # 3) Evaluate trailing SL per parent
    for r in rows:
        source = str(r.get("source") or "").upper()
        if source not in ("MSC_EXPLORATORY", "LEGACY"):
            continue
        mid = str(r["marketId"])
        sid = str(r["selectionId"])
        px = px_map.get((mid, sid))
        if px is None:
            continue

        parent = ParentState(
            parent_id=int(r["id"]),
            entry_side=str(r["side"]),
            entry_odds=float(r["entry_odds"]),
            entry_stake=float(r["entry_stake"]),
        )

        # OC-phase 0–7 PRE, >=7 INPLAY (same SL logic)
        oc_phase = 0

        ev = MSC_STOPLOSS.evaluate(
            parent=parent,
            mid=mid,
            sid=sid,
            current_odds=px,
            oc_phase=oc_phase
        )

        if not ev:
            continue

        # 4) enqueue STOPLOSS child for ROUTER (DB-first, execution-owned)
        from engines.live.live_router import enqueue_router_child

        exit_side = "BACK" if r["side"].upper() == "LAY" else "LAY"

        enqueue_router_child(
            plan={
                # child_id intentionally omitted → router will INSERT QUEUED
                "role": "CHILD",
                "engine": "OVERWATCHER",
                "exit_kind": "STOPLOSS",
                "source": "S",                      # letter S
                "parent_cor": r["customerOrderRef"],
                "marketId": mid,
                "selectionId": sid,
                "side": exit_side,                  # BACK or LAY
                "px": px,
                "size": float(r["entry_stake"]),
            },
            ctx={
                "engine": "OVERWATCHER",
                "mode": "LIVE",
            }
        )

        # --------------------------------------------------------------
        # STOPLOSS FOLLOW-UP:
        # If STOPLOSS (S) child is MATCHED → cancel active HEDGE (H) child
        # --------------------------------------------------------------
        try:
            con = _orders_conn()
            con.row_factory = sqlite3.Row
            cur = con.cursor()

            sl_rows = cur.execute("""
                SELECT
                    s.id        AS s_id,
                    s.hedge_of  AS parent_id
                FROM orders s
                WHERE s.role='CHILD'
                  AND UPPER(s.exit_kind)='STOPLOSS'
                  AND UPPER(s.entry_status)='MATCHED'
                  AND NOT EXISTS (
                        SELECT 1
                          FROM orders h
                         WHERE h.hedge_of = s.hedge_of
                           AND UPPER(h.exit_status)='CANCELLED'
                  )
            """).fetchall()

            for sl in sl_rows:
                parent_id = int(sl["parent_id"])

                # 🔑 RISC TERMINATION FLAG (AUTHORITATIVE)
                try:
                    cur.execute("""
                        UPDATE orders
                           SET stoploss_triggered = 1
                         WHERE id = ?
                    """, (parent_id,))
                    con.commit()
                except Exception:
                    pass

                # Runtime bridge (same tick)
                global _TSL_LAST_PARENT
                _TSL_LAST_PARENT = parent_id


            if sl_rows:
                app_key, token = _keys()

                for sl in sl_rows:
                    # find active HEDGE child
                    h = cur.execute("""
                        SELECT id, entry_bet_id
                          FROM orders
                         WHERE role='CHILD'
                           AND hedge_of=?
                           AND UPPER(exit_kind)='HEDGE'
                           AND entry_status IN ('QUEUED','PLACED','MATCHED')
                           AND exit_status IS NULL
                         ORDER BY id DESC
                         LIMIT 1
                    """, (int(sl["parent_id"]),)).fetchone()

                    if not h:
                        continue

                    bet_id = h["entry_bet_id"]
                    if bet_id:
                        try:
                            _cancel(app_key, token, str(bet_id))  # 🔥 Betfair cancel
                        except Exception:
                            pass

                    # mirror Betfair state in DB
                    cur.execute("""
                        UPDATE orders
                           SET exit_status='CANCELLED',
                               closed_at=datetime('now','utc'),
                               error='cancelled_by_stoploss'
                         WHERE id=?
                    """, (int(h["id"]),))

                con.commit()

        except Exception as e:
            print(f"[OVERWATCHER][STOPLOSS][CANCEL-H][ERR] {e}")

        finally:
            try:
                con.close()
            except Exception:
                pass



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def risk_weight(market_id: str, selection_id: str) -> float:
    """Return historical win% probability for current shape pattern."""
    import sqlite3
    from engines.config_paths import autoscalp_db, q_retry as _q
    from engines.config_paths import open_auto_db

    # old:
    # con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row

    # new:
    con = open_auto_db(rw=True)
    con.row_factory = sqlite3.Row
    row = _q(con, """
        SELECT win_pct
          FROM v7_liability_risk
         WHERE marketId=? AND selectionId=?
         LIMIT 1
    """, (market_id, selection_id)).fetchone()
    con.close()
    return float(row["win_pct"]) if row and row["win_pct"] is not None else 0.0

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:_evaluate_probability_risk
# 📆 PATCHED: 2025-11-13Z — fix list/fetchall hybrid handling
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _evaluate_probability_risk(conn: sqlite3.Connection):
    """
    Probability-aware risk overlay (hybrid cursor/list safe).
    Emits early hedge/stop-loss when high liability aligns with strong win% shape.
    """
    try:
        res = _q(conn, """
            SELECT marketId, selectionId, liability, win_pct
              FROM v7_liability_risk
             WHERE liability > 0
        """)

        # ✅ handle both list + cursor types
        if hasattr(res, "fetchall"):
            rows = res.fetchall()
        else:
            rows = res

        if not rows:
            return

        for r in rows:
            mid, sid = str(r["marketId"]), str(r["selectionId"])
            liab = float(r["liability"] or 0.0)
            prob = float(r["win_pct"] or 0.0)

            if liab >= 100.0 and prob >= 40.0:
                event_sink.on_decision({
                    "type": "probability_risk_signal",
                    "marketId": mid,
                    "selectionId": sid,
                    "liability": round(liab, 2),
                    "win_pct": prob,
                    "reason": "liab+prob>threshold",
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")
                })
                print(f"[OVERWATCHER][PROB] ⚠️ high-risk mid={mid} sid={sid} liab={liab:.2f} win%={prob:.1f}")
    except Exception as e:
        print(f"[OVERWATCHER][PROB] warn: {e}")
# === PATCH END ===


# --- Cooldown Redistribution -------------------------------------------------

def enforce_cooldown_distribution(thresh_disp: float = 5.0):
    """
    Between T-3m and OFF: flatten P&L across runners if dispersion > thresh.
    """
    con = auto_conn(); con.row_factory = sqlite3.Row
    mids = _q(con, """
        SELECT DISTINCT marketId
          FROM markets_schedule
         WHERE datetime(off_at_utc) BETWEEN datetime('now','utc','-3 minutes') AND datetime('now','utc')
    """).fetchall()
    con.close()

    app_key, token = _keys()
    for m in mids or []:
        mid = str(m["marketId"])
        pnl = analyze_market_pnl(_orders_conn(), mid)
        if not pnl: continue
        lo, hi = min(pnl.values()), max(pnl.values())
        if hi - lo >= thresh_disp:
            for sid in pnl.keys():
                odds = fetch_live_odds(token, mid, sid)
                odds_now = odds.get("lay") or odds.get("back")
                event_sink.on_decision({
                    "type": "cooldown_distribution",
                    "mid": mid,
                    "sid": sid,
                    "odds_now": odds_now,
                    "pnl_spread": hi - lo,
                })


# --- Green-Up Enforcement ----------------------------------------------------

def enforce_greenups(thresh_disp: float = 10.0):
    """
    For open markets: if PnL imbalance too wide, enforce green-up.
    """
    con = auto_conn(); con.row_factory = sqlite3.Row
    mids = _q(con, "SELECT DISTINCT marketId FROM orders WHERE role='PARENT' AND exit_status IS NULL").fetchall()
    con.close()

    app_key, token = _keys()
    for m in mids or []:
        mid = str(m["marketId"])
        pnl = analyze_market_pnl(_orders_conn(), mid)
        if not pnl: continue
        lo, hi = min(pnl.values()), max(pnl.values())
        if hi - lo >= thresh_disp:
            for sid in pnl.keys():
                odds = fetch_live_odds(token, mid, sid)
                odds_now = odds.get("lay") or odds.get("back")
                event_sink.on_decision({
                    "type": "greenup_enforced",
                    "mid": mid,
                    "sid": sid,
                    "odds_now": odds_now,
                    "pnl_spread": hi - lo,
                })


# --- Loop Starter ------------------------------------------------------------

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py
# 🔎 SEARCH: ^def start_overwatcher\(hz: int = 2, stop_ticks_default: int = 4\):
# 📆 PATCHED: 2025-10-25Z — Market-aware Profit Guardian (16/32/90 envelope)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.cashout_calc import cashout_calc
from engines.mastery import canonical_digest

def _evaluate_market_guardian(conn):
    """
    Market-aware Profit Guardian.
    Uses cashout_calc() aggregates per marketId to apply
    the 16 / 32 / 90 profit envelope and emit Mastery events.
    """
    results = cashout_calc(conn, write_to_db=False)
    if not results:
        return

    weights = canonical_digest.avg_by_letter()

    for mid, info in results.items():
        if not isinstance(info, dict):
            continue
        pnl = float(info.get("total", 0.0))
        liab = float(info.get("liability", 0.0))

        ratio = pnl / liab if liab else 0.0
        children = info.get("children", [])

        # --- Event snapshot for Mastery learning ---
        event_sink.emit("market_risk", {
            "marketId": mid,
            "pnl_total": pnl,
            "liability_total": liab,
            "ratio": round(ratio, 3),
            "canonical_weights": weights
        })

        # --- Envelope logic (market-level) ---
        try:
            # ✅ PROFIT ZONE (lock profits > £32)
            if pnl >= 32.0:
                event_sink.on_decision({
                    "type": "greenup",
                    "marketId": mid,
                    "pnl": round(pnl, 2),
                    "liability": round(liab, 2),
                    "reason": "lock_profit_>32",
                    "children": children
                })

            # --- Loss-cut learning signal for Mastery -------------------
            if liab > 0.0:
                ratio = abs(pnl) / liab
                signal = "LOW_RISK"
                if ratio >= 0.25:
                    signal = "MEDIUM_RISK"
                if ratio >= 0.5:
                    signal = "HIGH_RISK"

                payload = {
                    "type": "loss_cut_signal",
                    "marketId": mid,
                    "pnl": round(pnl, 2),
                    "liability": round(liab, 2),
                    "ratio": round(ratio, 3),
                    "signal": signal,
                    "children": children,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")
                }

                # continuous learning telemetry
                event_sink.emit("cashout_tick", payload)

                # only flag decisions when risk is medium/high
                if signal in ("MEDIUM_RISK", "HIGH_RISK"):
                    event_sink.on_decision(payload)
            # ✅ LOSS CAP (cut losses beyond £90)
            elif pnl <= -90.0:
                event_sink.on_decision({
                    "type": "stoploss",
                    "marketId": mid,
                    "pnl": round(pnl, 2),
                    "liability": round(liab, 2),
                    "reason": "loss_cap_90",
                    "children": children
                })

            # ✅ UNDER-TARGET (top-up to reach ~£16)
            elif pnl < 16.0 and liab > 0.0 and ratio > -0.10:
                event_sink.on_decision({
                    "type": "micro_lay",
                    "marketId": mid,
                    "pnl": round(pnl, 2),
                    "liability": round(liab, 2),
                    "reason": "below_target_16",
                    "children": children
                })

        except Exception as e:
            print(f"[OVERWATCHER][GUARDIAN] warn mid={mid}: {e}")

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py
# 📆 PATCHED: 2025-10-30Z — diagnostic micro-scalper evaluator (non-trading)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import time, sqlite3
from engines.price_math import calculate_tick_distance, full_tick_ladder
from engines.mastery.microstructure import compute_p_fill

from engines.indicators.opportunities import update_opportunities
from engines.indicators.wom import compute_wom_for_scope

from engines.bias.engine import compute_bias
# --- Decision Engine range logic ---
from engines.decision_engine.range_tracker import RangeTracker
from engines.decision_engine.strategies.range_breakout import decide as decide_breakout

from engines.config_paths import auto_conn as _auto_conn, q_retry as _q
from engines.mastery.microstructure import gather_v7_signals


# --- tunables ---
MICRO_INTERVAL_SEC   = 1.5
RANGE_BUFFER_TICKS   = 2
RANGE_HOLD_SECS      = 3
BIAS_CONF_MIN        = 0.55
P_FILL_MIN           = 0.6
WOM_LO, WOM_HI       = 0.4, 0.6

_range_tracker = RangeTracker()

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:_evaluate_micro_scalper_diag
# 🔎 SEARCH: def _evaluate_micro_scalper_diag(conn:
# 📆 PATCHED: 2025-11-21

from engines.config_paths import connect_bets_db

def _evaluate_micro_scalper_diag(conn: sqlite3.Connection):
    """
    Non-trading micro-scalper evaluator.
    Logs breakout/bias/p_fill/WOM reasoning for each active runner.
    """
    wom_map = compute_wom_for_scope()

    # open both databases using DAL-safe connectors
    gui_con = conn                      # existing GUI DB connection
    bets_con = connect_bets_db(ro=True) # ✔ DAL read-only (safe)

    bets_con.row_factory = sqlite3.Row

    rows = _q(gui_con, """
        SELECT DISTINCT marketId, selectionId, ltp
          FROM odds_current
         WHERE ltp IS NOT NULL
           AND date(updated_ts)=date('now','utc')
    """).fetchall()

    if not rows:
        bets_con.close()
        return

    anchors = {
        (r["marketId"], r["selectionId"]): float(r["anchor_odd"] or 0.0)
        for r in bets_con.execute("""
            SELECT marketId, selectionId, anchor_odd
              FROM bets
             WHERE anchor_odd IS NOT NULL
        """).fetchall()
    }
    bets_con.close()

    for r in rows:
        mid = str(r["marketId"])
        sid = int(r["selectionId"])
        anchor = anchors.get((mid, sid), float(r["ltp"] or 0.0))
        current = float(r["ltp"] or 0.0)

        sig = gather_v7_signals(mid, sid, conn)
        sig["wom"] = wom_map.get((mid, str(sid)), 0.5)
        sig["anchor"] = anchor
        sig["current"] = current


        # interpret signal weights for context scoring
        momentum   = float(sig.get("slope_ppm")       or 0.0)
        drift_spd  = float(sig.get("drift_speed")     or 0.0)
        bias_conf  = float(bias_conf                  or 0.0)
        wom_val    = float(sig.get("wom")             or 0.0)
        pos_ratio  = rs.position_ratio if rs.position_ratio is not None else 0.0
        breakout   = rs.breakout if rs.breakout is not None else False



        # range snapshot
        rs = _range_tracker.update(mid, sid, anchor, current)

        # bias context
        ctx = {"direction": None, "ltp": current, "odds": anchor, "minutes_to_off": 10}

        # 🔥 NEW: TSL → RiskEngine bridge
        # If trailing stop-loss fired last tick, this will be a parent_id (int)
        # Otherwise None; RiskEngine reads this and performs cleanup.
        from engines.live.overwatcher import get_tsl_parent
        ctx["stoploss_triggered_for_parent"] = get_tsl_parent()


        # --- NEW: compute fused bias direction ---------------------------------
        bias_dir = "FLAT"
        bias_conf = 0.0


        try:
            if momentum > 0 and drift_spd > 0:
                bias_dir = "B2L"
            elif momentum < 0 and drift_spd < 0:
                bias_dir = "L2B"

            bias_conf = min(1.0, abs(momentum * 0.01) + abs(drift_spd * 0.01) + conf)
        except Exception:
            # if any value is None, leave defaults
            pass



        # p_fill (microstructure quality)
        pctx = {"stake": 2.0, "depth_total": 120.0, "sigma": 1.0}
        p_fill = compute_p_fill(pctx, {})

        # choose diagnostic action
        direction = None
        reason = ""
        if rs.breakout_confirmed:
            direction = "LAY->BACK" if bias_dir == "B2L" else "BACK->LAY"
            reason = f"breakout:{rs.breakout}"
        elif rs.position_ratio is not None:
            if rs.position_ratio > 0.9 and bias_dir == "B2L":
                direction = "LAY->BACK"
                reason = "near_top"
            elif rs.position_ratio < 0.1 and bias_dir == "L2B":
                direction = "BACK->LAY"
                reason = "near_bottom"

        ok = (
            direction
            and bias_conf >= BIAS_CONF_MIN
            and WOM_LO <= sig["wom"] <= WOM_HI

        )

        print(
            f"[MICRO] {mid}:{sid} pos={rs.position_ratio!s:<5} "
            f"br={rs.breakout:<4} conf={bias_conf:.2f} bias={bias_dir:<3} "
            f"slope={momentum:+.1f} drift={drift_spd:+.2f} "
            f"wom={sig['wom']:.2f} → {'ARM' if ok else 'wait'} {direction or '-'} {reason}"
        )


# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:_evaluate_micro_scalper_diag
# 📆 PATCHED: 2025-11-02Z — tick-aware risk balancer for legacy exposure
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _evaluate_micro_scalper_balance(conn: sqlite3.Connection):
    """
    Adaptive risk-balancer:
    • hedges legacy trades tick-by-tick
    • rides with legacy when price moves in favour
    • flattens and reverses when trend flips
    """
    gui_con = conn
    open_orders = {
        (r["marketId"], str(r["selectionId"])): r
        for r in _q(gui_con, """
            SELECT marketId, selectionId, side, entry_odds, entry_stake
              FROM orders
             WHERE role='PARENT' AND exit_status IS NULL
        """).fetchall()
    }

    # latest odds snapshot
    px = {
        (r["marketId"], str(r["selectionId"])): float(r["ltp"] or 0)
        for r in _q(gui_con, """
            SELECT marketId, selectionId, ltp
              FROM odds_current
             WHERE ltp IS NOT NULL
        """).fetchall()
    }

    for (mid, sid), legacy in open_orders.items():
        legacy_side = (legacy["side"] or "").upper()
        entry_odds  = float(legacy["entry_odds"] or 0)
        stake       = float(legacy["entry_stake"] or 0)
        current     = px.get((mid, sid), entry_odds)
        if entry_odds <= 0 or current <= 0:
            continue

        tick_diff = abs(_tick_distance(entry_odds, current))
        if tick_diff < 1:
            continue

        # direction logic
        moving_against = (legacy_side == "LAY" and current > entry_odds) or \
                         (legacy_side == "BACK" and current < entry_odds)
        moving_favour  = (legacy_side == "LAY" and current < entry_odds) or \
                         (legacy_side == "BACK" and current > entry_odds)

        if moving_against:
            event_sink.on_decision({
                "type": "micro_scalp",
                "marketId": mid,
                "selectionId": sid,
                "direction": "BACK->LAY" if legacy_side == "LAY" else "LAY->BACK",
                "reason": "hedge_tick",
                "ticks": tick_diff,
                "stake": stake * 0.25,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")
            })
        elif moving_favour:
            event_sink.on_decision({
                "type": "micro_scalp",
                "marketId": mid,
                "selectionId": sid,
                "direction": "LAY->BACK" if legacy_side == "LAY" else "BACK->LAY",
                "reason": "stack_tick",
                "ticks": tick_diff,
                "stake": stake * 0.25,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")
            })
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:start_micro_scalper_diag
# 📆 PATCHED: 2025-11-06Z — share same connection with gather_v7_signals (fix drift_speed)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def start_micro_scalper_diag():
    """Start background diagnostic loop."""
    def loop():
        while True:
            # one connection per tick; share it across all calls
            try:
                with _auto_conn() as conn:
                    conn.execute("PRAGMA schema_version;")  # keep schema fresh
                    # pass conn into the evaluator so all helpers reuse it
                    _evaluate_micro_scalper_diag(conn)
            except Exception:
                # swallow silently; no prints needed once schema unified
                pass
            time.sleep(MICRO_INTERVAL_SEC)

    t = threading.Thread(target=loop, name="MicroScalperDiag", daemon=True)
    t.start()
    return t
# === PATCH END ===




# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py
# 📆 PATCHED: 2025-10-27Z — hybrid Overwatcher (adds MLM liability cap + orphan cleanup, keeps all existing logic)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.decision_engine.strategies import mlm

def _cancel_orphan_children(con: sqlite3.Connection, marketId: str) -> int:
    """
    Cancels any open child orders for a given market when MLM cap triggers.
    """
    try:
        rows = _q(con, """
            SELECT id FROM orders
             WHERE marketId=? AND closed_at IS NULL AND role='CHILD'
        """, (marketId,)).fetchall()
        if not rows:
            return 0
        ids = [r["id"] for r in rows]
        for oid in ids:
            _q(con, """
                UPDATE orders
                   SET closed_at=datetime('now','utc'),
                       exit_status='cancelled'
                 WHERE id=?""", (oid,))
        con.commit()
        print(f"[OVERWATCHER][MLM] cancelled {len(ids)} orphan children in {marketId}")
        return len(ids)
    except Exception as e:
        print(f"[OVERWATCHER][MLM] orphan-cancel warn mid={marketId}: {e}")
        return 0


def _evaluate_liability_cap(conn: sqlite3.Connection):
    """
    Evaluate the Market Liability Manager (MLM) strategy in live mode.
    If a market exceeds its liability cap, cancel open children and emit an event.
    """
    try:
        ctx = type("Ctx", (), {"phase": "LIVE", "minutes_to_off": 1.5})()
        instr = mlm.decide(ctx)
        if not instr or not getattr(instr, "meta", None):
            return
        meta = instr.meta
        mid = meta.get("marketId") or meta.get("meta_marketId")
        if not mid:
            return
        reason = meta.get("why") or "mlm_cap_hit"


        event_sink.on_decision({
            "type": "mlm_enforced",
            "marketId": mid,
            "reason": reason,
            "meta": meta,
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")
        })
        print(f"[OVERWATCHER][MLM] enforcement active mid={mid} reason={reason}")
    except Exception as e:
        print(f"[OVERWATCHER][MLM] warn: {e}")

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:_evaluate_liability_alerts
# 📆 PATCHED: 2025-10-28Z — Liability Traffic-Light Assistant (alert/emergency/cap)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
from engines.cashout_calc import cashout_calc

def _evaluate_liability_alerts(conn: sqlite3.Connection):
    """
    Liability Traffic-Light Assistant.
    Reads live market liabilities from cashout_calc() and reacts:
      • ≥150 → ALERT (log + emit)
      • ≥200 → EMERGENCY (emit + optional hedge)
      • ≥250 → CAP (emit + cancel open children)
    Uses same thresholds as budget_manager.
    """
    ALERT, EMERGENCY, CAP = 150.0, 200.0, 250.0
    try:
        results = cashout_calc(conn, write_to_db=False)
        if not results:
            return
        for mid, info in results.items():
            liab = float(info.get("liability", 0.0))
            if liab < ALERT:
                continue
            level = "ALERT" if liab < EMERGENCY else \
                     "EMERGENCY" if liab < CAP else "CAP"
            event_sink.on_decision({
                "type": "liability_signal",
                "marketId": mid,
                "liability": round(liab, 2),
                "level": level,
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")
            })
            print(f"[OVERWATCHER][LIAB] {level:<9} mid={mid} £{liab:>7.2f}")
            # --- optional enforcement for CAP --------------------------------
            if liab >= CAP:
                event_sink.on_decision({
                    "type": "mlm_enforced",
                    "marketId": mid,
                    "reason": reason,
                    "meta": meta,
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")
                })
    except Exception as e:
        print(f"[OVERWATCHER][LIAB] warn: {e}")
# === PATCH END ===


def start_overwatcher(hz: int = 2, stop_ticks_default: int = 4):
    """
    Unified Overwatcher loop:
      - Market Guardian (profit/loss envelope 16/32/90)
      - Diagnostic micro-scalper evaluator
      - MLM liability enforcement
      - Stop-loss enforcement
      - Cooldown and greenup enforcement
    """

    # ✅ start diagnostic micro loop once
    start_micro_scalper_diag()
    print(
        "[OVERWATCHER] startup check → "
        "StopLoss ✅  MLM ✅  MicroScalper ✅  Guardian ✅  Bridge ✅"
    )


    def loop():
        while True:
# === PATCH START ============================================================
# 📍 TARGET: engines/live/overwatcher.py:start_overwatcher loop
# 📆 PATCHED: 2025-12-04 — Correct try/finally and DAL-safe connection
# ============================================================================

            try:
                # DALReadProxy does NOT support `with`, so use explicit open/close
                conn = open_auto_db(rw=False)

                # Core guardian / liability / micro-scalper diagnostics


                # MSC-Exploratory only trailing stop-loss
                enforce_msc_exploratory_stoploss()


                # Legacy boundary exits
                enforce_legacy_boundaries_and_trailing()
                

            except Exception as e:
                print("[OVERWATCHER] loop error", e)

            finally:
                try:
                    conn.close()
                except:
                    pass
# === PATCH END ============================================================



            time.sleep(max(1.0 / hz, 0.5))

    t = threading.Thread(target=loop, name="OverwatcherLoop", daemon=True)
    t.start()
    return t
   


