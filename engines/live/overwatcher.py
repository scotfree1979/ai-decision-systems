#!/usr/bin/env python3
# engines/live/overwatcher.py
import time, threading, sqlite3, json
from datetime import datetime, timezone
from engines.config_paths import auto_conn, q_retry as _q, autoscalp_db
from engines.stoploss_engine import StopLossInputs, evaluate_stoploss

try:
    from engines.live.live_router import _round_odds, _calc_hedge_stake, _ref, _place, _cancel
except ImportError:
    from engines.live import live_router
    def _calc_hedge_stake(entry_stake, entry_odds, exit_odds, side):
        if side.upper() == "LAY":
            return round(entry_stake * entry_odds / max(exit_odds, 1.01), 4)
        return round(entry_stake * (exit_odds - 1) / max(entry_odds - 1, 0.01), 4)
    def _calc_exposure_drift(anchor, current):
        try:
            return round(((current - anchor) / anchor) * 100.0, 2)
        except Exception:
            return 0.0
    live_router._calc_hedge_stake = _calc_hedge_stake
    live_router._calc_exposure_drift = _calc_exposure_drift
    print("[OVERWATCHER] ⚙️  auto-patched hedge helpers into live_router")

from engines.live.live_router import _orders_conn, _orders_update_parent_cancelled
from engines.utils.api_tools import fetch_live_odds
from engines.mastery import event_sink
# ensure Mastery-v7 bridge is live so on_decision() events route to live_router
import engines.mastery_v7.live_router_bridge  # noqa: F401

from engines.live.live_router import analyze_market_pnl
from engines.live.live_router import _keys

from engines.price_math import calculate_tick_distance as _tick_distance  # ✅ FIX

from engines.mastery import event_sink
# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py (bridge import)
# 📆 PATCHED: 2025-11-10Z — point bridge import to mastery_v7.live_router_bridge
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
try:
    # Use the v7 bridge, not live_router
    from engines.mastery_v7.live_router_bridge import send_plan_through_bridge
    print("[OVERWATCHER] ✅ bridge relay connected (v7)")
except ImportError as e:
    # fallback shim so runtime never blocks
    def send_plan_through_bridge(plan: dict | None = None) -> bool:
        print(f"[OVERWATCHER] ⚠️ bridge relay unavailable: {e}")
        return False
# === PATCH END ===

def check_overwatcher_state():
    import threading
    live_threads = [t.name for t in threading.enumerate() if 'OVERWATCHER' in t.name or 'feedback' in t.name]
    print(f"[CHECK] Overwatcher: threads active → {len(live_threads)} ({','.join(live_threads)})")
    return bool(live_threads)

# === PATCH START ===
# 📍 TARGET: engines/live/overwatcher.py:_on_bridge_pulse
# 📆 PATCHED: 2025-11-10Z — ignore empty brain pulses (no mid/sid)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
import time
_last_brain_note = [0]  # module-level mutable container

def _on_bridge_pulse(payload):
    try:
        coh = float(payload.get("coherence", 0))
        adj = float(payload.get("adjustment", 0))
        reason = payload.get("reason", "Brain pulse")
        ts = payload.get("ts")

        mid = str(payload.get("marketId") or "")
        sid = str(payload.get("selectionId") or "")
        if not mid or not sid:
            now = time.time()
            if now - _last_brain_note[0] > 300:   # every 5 minutes
                print("[OVERWATCHER][BRAIN] ⏳ waiting for market context …")
                _last_brain_note[0] = now
            return

        plan = {
            "type": "BRAIN_BRIDGE",
            "reason": reason,
            "confidence": coh,
            "adjustment": adj,
            "marketId": mid,
            "selectionId": sid,
            "side": "LAY" if adj >= 0 else "BACK",
            "odds": payload.get("odds", 0),
            "stake": payload.get("stake", 2.0),
            "target_ticks": 1,
            "ts": ts,
        }

        print(f"[OVERWATCHER][BRAIN] 🧠 bridge pulse mid={mid} sid={sid} coh={coh:.2f} adj={adj:+.2f}")
        send_plan_through_bridge(plan)
    except Exception as e:
        print(f"[OVERWATCHER][BRAIN] warn: {e}")
# === PATCH END ===


# attach listener to event bus
event_sink.subscribe(_on_bridge_pulse)

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

# 📍 TARGET: engines/live/overwatcher.py:enforce_stop_losses
# 📆 PATCHED: 2025-11-26Z — wire v7.9.5 StopLoss engine (no direct Betfair I/O)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def enforce_stop_losses(default_ticks: int = 4):
    """
    Evaluate stop-loss for each live parent entry using the v7.9.5 StopLoss engine.

    This function:
      • reads open parent orders from autoscalp_gui.db
      • reads latest odds from odds_current for those (marketId, selectionId)
      • calls stoploss_engine.evaluate_stoploss(..)
      • emits a 'stop_loss_breached' decision event when threshold is hit

    It does NOT place or cancel orders directly; live_router_bridge is responsible
    for turning the decision event into a STOPLOSS child order.
    """
    # 1) Get all live, matched parents which are not fully exited yet
    rows = _open_orders()
    if not rows:
        return

    # 2) Preload latest prices from odds_current for all markets in scope
    mids = {str(r["marketId"]) for r in rows}

    try:
        with auto_conn(rw=False) as con:
            con.row_factory = sqlite3.Row
            if not mids:
                return

            placeholders = ",".join("?" * len(mids))
            # Prefer lay1/back1, fall back to ltp; only for today's data to keep it tight
            sql = f"""
                SELECT marketId, selectionId,
                       COALESCE(lay1, back1, ltp) AS px
                  FROM odds_current
                 WHERE day = date('now','utc')
                   AND (lay1 IS NOT NULL OR back1 IS NOT NULL OR ltp IS NOT NULL)
                   AND marketId IN ({placeholders})
            """
            cur = con.execute(sql, tuple(mids))
            price_map = {}
            for rec in cur:
                mid = str(rec["marketId"])
                sid = str(rec["selectionId"])
                px = rec["px"]
                if px is None:
                    continue
                # odds_current is already the latest snapshot per (mid,sid)
                try:
                    price_map[(mid, sid)] = float(px)
                except (TypeError, ValueError):
                    continue
    except Exception as e:
        # If price lookup fails, we skip this tick rather than throwing.
        print(f"[OVERWATCHER][STOPLOSS] price scan error: {e}")
        return

    # 3) For each parent, run the StopLoss engine and emit decision events
    for r in rows:
        try:
            # Skip if we have already marked this parent as having triggered a stop-loss
            if r["stop_loss_triggered"]:
                continue

            mid = str(r["marketId"])
            sid = str(r["selectionId"])
            current = price_map.get((mid, sid))
            if current is None:
                # No price yet for this runner in odds_current
                continue

            entry_odds = float(r["entry_odds"] or 0.0)
            stake = float(r["entry_stake"] or 0.0)
            if entry_odds <= 0.0 or stake <= 0.0:
                continue

            # For now all parents here are treated as Legacy, BALANCED mode.
            # MicroScalper will call the engine separately with is_micro=True.
            sl_in = StopLossInputs(
                marketId=mid,
                selectionId=sid,
                side=(r["side"] or "").upper(),
                entry_odds=entry_odds,
                stake=stake,
                is_micro=False,
                risk_mode="BALANCED",
            )

            state = evaluate_stoploss(sl_in, current_odds=current)

            # Honour a hard minimum from default_ticks as a safety floor
            hard_min = max(1, int(default_ticks or 0))
            effective_threshold = max(state.final_stop_ticks, hard_min)
            ticks_against = state.ticks_against
            should_fire = ticks_against >= effective_threshold

            if not should_fire:
                continue

            # Build a rich, but backwards-compatible, decision event.
            # We include both marketId/selectionId and mid/sid for older handlers.
            event = {
                "type": "stop_loss_breached",
                "parent_id": r["id"],
                "customerOrderRef": r["customerOrderRef"],
                "marketId": mid,
                "mid": mid,
                "selectionId": sid,
                "sid": sid,
                "side": (r["side"] or "").upper(),
                "entry_odds": entry_odds,
                "odds_now": current,
                "ticks_breached": ticks_against,
                "stop_ticks": effective_threshold,
                "entry_stake": stake,
                # v7.9.5 StopLoss diagnostics
                "sleq": state.sleq,
                "widening_factor": state.widening_factor,
                "good_rate": state.good_rate,
                "bad_rate": state.bad_rate,
                "loss_per_tick": state.loss_per_tick,
            }

            event_sink.on_decision(event)

        except Exception as e:
            # Do not let one bad row kill the whole Overwatcher loop
            print("[OVERWATCHER][STOPLOSS]", e)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def risk_weight(market_id: str, selection_id: str) -> float:
    """Return historical win% probability for current shape pattern."""
    import sqlite3
    from engines.config_paths import autoscalp_db, q_retry as _q
    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row
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
        _cancel_orphan_children(conn, mid)
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
                _cancel_orphan_children(conn, mid)
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
            try:
                with auto_conn() as conn:
                    _evaluate_market_guardian(conn)
                    _evaluate_liability_cap(conn)
                    _evaluate_liability_alerts(conn)
                    _evaluate_probability_risk(conn)
                    _evaluate_micro_scalper_balance(conn)

                enforce_stop_losses(default_ticks=stop_ticks_default)
                enforce_cooldown_distribution()
                enforce_greenups()

            except Exception as e:
                print("[OVERWATCHER] loop error", e)
            time.sleep(max(1.0 / hz, 0.5))

    t = threading.Thread(target=loop, name="OverwatcherLoop", daemon=True)
    t.start()
    return t
   


