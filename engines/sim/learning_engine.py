# engines/sim/learning_engine.py
# ============================================================
# AutoScalp Learning Engine (Multi-Day Replay + Reinforcement)
# ============================================================
from __future__ import annotations
import sqlite3, time, threading, json
from datetime import datetime, timezone
from typing import List, Tuple, Dict, Optional

# Replay clock (existing engine)
from engines.replay_clock import configure_replay, replay_now_utc

# DB paths
from engines.config_paths import autoscalp_db, connect_db

# Context builder output slot (Learning Mode override)
_LAST_LEARNING_CTX: Tuple[dict, dict] = ({}, {})


# ============================================================
# Utility helpers
# ============================================================

def _row(conn, sql, args=()):
    try:
        cur = conn.execute(sql, args)
        return cur.fetchone()
    except Exception:
        return None

def _rows(conn, sql, args=()):
    try:
        cur = conn.execute(sql, args)
        return cur.fetchall() or []
    except Exception:
        return []

def _utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc)


# ============================================================
# STEP 1 — FIND TOP N REPLAY DAYS (most complete)
# ============================================================

def find_top_replay_days(n_days: int = 10) -> List[str]:
    """
    Returns list of top N days from oc_series by snapshot count.
    Most-complete days first.
    """
    conn = connect_db(ro=True)
    conn.row_factory = sqlite3.Row
    rows = _rows(conn, """
        SELECT date(snapshot_ts) AS d, COUNT(*) AS n
        FROM oc_series
        GROUP BY date(snapshot_ts)
        ORDER BY n DESC
    """)
    conn.close()
    return [str(r["d"]) for r in rows[:n_days]]


# ============================================================
# STEP 2 — LOAD OC SNAPSHOTS FOR A REPLAY DAY
# ============================================================

def load_oc_snapshots_for_day(day_iso: str) -> List[sqlite3.Row]:
    """
    Returns full set of oc_series rows for that day sorted by snapshot_ts.
    """
    conn = connect_db(ro=True)
    conn.row_factory = sqlite3.Row
    rows = _rows(conn, """
        SELECT *
        FROM oc_series
        WHERE date(snapshot_ts)=?
        ORDER BY snapshot_ts ASC
    """, (day_iso,))
    conn.close()
    return rows


# ============================================================
# STEP 3 — REWRITE MARKET START TIMES TO MATCH REPLAY DAY
# ============================================================

def rewrite_market_start_times_for_day(day_iso: str):
    """
    Rewrites markets_schedule.off_at_utc date portion to replay_day.
    Required so minutes_to_off works using replay clock.
    """
    adb = connect_db(ro=False)
    adb.row_factory = sqlite3.Row

    exists = _row(adb, "SELECT 1 FROM sqlite_master WHERE type='table' AND name='markets_schedule'")
    if not exists:
        adb.close()
        return

    rows = _rows(adb, "SELECT marketId, off_at_utc FROM markets_schedule")
    for r in rows:
        mid = str(r["marketId"])
        off = str(r["off_at_utc"])
        # Keep the HH:MM:SS portion, replace the date
        try:
            time_part = off.split("T")[1]
        except Exception:
            time_part = "10:00:00Z"
        new_ts = f"{day_iso}T{time_part}"
        adb.execute("""
            UPDATE markets_schedule
            SET off_at_utc=?
            WHERE marketId=?
        """, (new_ts, mid))
    adb.commit()
    adb.close()


# ============================================================
# STEP 4 — APPLY OC SNAPSHOT INTO INBOUND & ODDS_CURRENT
# ============================================================
# === PATCH START ===
# 📍 TARGET: engines/sim/learning_engine.py
# 🔎 SEARCH: from engines.decision_engine.decide_once.helpers import (
# 📆 PATCHED: 2025-12-01 — correct learning_* helper imports

from engines.decision_engine.decide_once.helpers import (
    learning_update_cache_oc as _autoscalp_update_cache_oc,
    learning_upsert_cache_anchor as _autoscalp_upsert_cache_anchor,
    learning_record_oc_series as _record_oc_series,
    learning_cache_has_oc as _autoscalp_cache_has_oc,
)
# === PATCH END ===

from engines.decision_engine.decide_once.helpers import _q_retry as qh
from engines.decision_engine.decide_once.helpers import open_auto_db

def apply_oc_snapshot(row: sqlite3.Row):
    """
    Push one oc_series row into inbound_oc_cache and odds_current.
    Used during replay.
    """
    mid = row["marketId"]
    sid = row["selectionId"]
    odd = row["odd"]
    stage = row["stage"] or "OC0"
    if odd is None:
        return

    # inbound
    _autoscalp_update_cache_oc(mid, sid, stage, float(odd), band_json=row["band_json"])

    # odds_current
    now_iso = replay_now_utc().isoformat()
    day = now_iso.split("T")[0]

    db = open_auto_db(ro=False)
    try:
        qh(db, """
            INSERT OR REPLACE INTO odds_current
            (day, marketId, selectionId, updated_ts, ltp, back1, lay1)
            VALUES (?,?,?,?,?,?,?)
        """, (day, mid, sid, now_iso, odd, odd, odd))
        db.commit()
    except Exception:
        pass
    finally:
        try: db.close()
        except: pass


# ============================================================
# STEP 5 — LEARNING CONTEXT BUILDER (replay-time minutes_to_off)
# ============================================================
from engines.mastery.context_builder import build_context_from_test_db
from engines.replay_clock import replay_now_utc

def grab_latest_ctx() -> Tuple[dict, dict]:
    global _LAST_LEARNING_CTX
    return _LAST_LEARNING_CTX

def build_learning_context():
    """
    Build test-db ctx and patch minutes_to_off using replay clock.
    """
    global _LAST_LEARNING_CTX

    try:
        ctx, meta = build_context_from_test_db()
    except Exception:
        ctx, meta = {}, {}

    # --- Patch minutes_to_off for Learning Mode --------------------
    try:
        mid = ctx.get("marketId")
        if mid:
            from engines.config_paths import connect_db
            con = connect_db(ro=True); con.row_factory = sqlite3.Row

            row = con.execute("""
                SELECT off_at_utc FROM markets_schedule
                WHERE marketId=? LIMIT 1
            """, (mid,)).fetchone()

            if row and row["off_at_utc"]:
                import datetime, math

                # Off time of race
                off = datetime.datetime.fromisoformat(
                    str(row["off_at_utc"]).replace("Z","+00:00")
                )

                # Replay clock (synthetic now)
                now = replay_now_utc()

                # Compute synthetic minutes-to-off
                mto = (off - now).total_seconds() / 60.0

                # Patch it
                ctx["minutes_to_off"] = float(mto)

            con.close()
    except Exception as e:
        print("[LEARNING] mto patch warn:", e)

    _LAST_LEARNING_CTX = (ctx, meta)
    return ctx, meta


# ============================================================
# STEP 6 — REPLAY THREAD FOR ONE DAY
# ============================================================

def start_replay_thread(rows: List[sqlite3.Row]):
    """
    Run OC snapshot replay in a fast background thread.
    """
    def _thr():
        for r in rows:
            apply_oc_snapshot(r)
            time.sleep(0.015)  # ~66 updates/sec
        print("[LEARNING] Replay complete for day.")

    t = threading.Thread(target=_thr, daemon=True, name="LEARN_OC_REPLAY")
    t.start()
    return t


# ============================================================
# STEP 7 — SIMULATED LEARNING LOOP
# ============================================================
from engines.mastery import mastery_policy as mp
from engines.mastery.context_builder import latest_price
# === PATCH START ===
# 📍 TARGET: engines/sim/learning_engine.py
# 🔎 SEARCH: from engines.decision_engine.orchestrator import (
# 📆 PATCHED: 2025-12-01 — redirect CAP to helpers, keep pnl function

from engines.decision_engine.decide_once.helpers import can_open_scalp as _can_open_scalp
from engines.decision_engine.orchestrator import _pnl_ticks_to_amount
# === PATCH END ===

from engines.decision_engine.decide_once.helpers import apply_rulebook as _apply_rb

from engines.decision_engine.orchestrator import (
    _ensure_orders_link_col,
    queue_order, set_order_status, place_companion_hedge
)
from engines.decision_engine.orchestrator import check_and_close

def run_learning_loop(run_id: str, duration_sec: int = 600, logger=None):
    """
    Reinforcement loop for one replay day.
    Uses build_learning_context() each tick.
    """
    start = time.time()
    live_positions: Dict[int, Tuple] = {}

    while (time.time() - start) < duration_sec:
        # Build ctx each tick
        ctx, meta = build_learning_context()

        mid = ctx.get("marketId")
        sid = ctx.get("selectionId")
        if not mid or not sid:
            if logger: logger("NO-TRADE | no runner ctx yet")
            time.sleep(0.2)
            continue

        # propose plan
        try:
            plan = mp.propose_trade(ctx)
        except Exception:
            plan = {"enter": False, "why": "mp_fail"}

        if not plan.get("enter"):
            if logger: logger(f"NO-TRADE | {plan.get('why','')}")
            time.sleep(0.2)
            continue

        # Gate: CAP check
        ok_cap, why_cap = _can_open_scalp(mid, sid, max_per_runner=3, run_id=run_id)
        if not ok_cap:
            if logger: logger(f"NO-TRADE | cap={why_cap}")
            time.sleep(0.2)
            continue

        # Get price
        last, _ = latest_price(mid, sid)
        entry_odds = float(last) if last else 6.0

        # Queue parent
        size = float(plan.get("size") or 2.0)
        direction = "LAY" if plan.get("direction","").startswith("LAY") else "BACK"

        try:
            parent_id = queue_order(
                run_id=run_id,
                side=direction.upper(),
                odds=entry_odds,
                stake=size,
                marketId=str(mid),
                selectionId=str(sid),
                mode_override="LEARNING"
            )
            set_order_status(parent_id, "live")
        except Exception:
            time.sleep(0.2)
            continue

        # Queue hedge
        ticks = int(plan.get("target_ticks") or 1)
        try:
            place_companion_hedge(
                parent_id=parent_id,
                direction=plan.get("direction","LAY->BACK"),
                entry_odds=entry_odds,
                parent_stake=size,
                target_ticks=ticks,
                marketId=str(mid),
                selectionId=str(sid),
                run_id=run_id,
                mode_override="LEARNING"
            )
        except Exception:
            pass

        # track open position
        live_positions[parent_id] = (
            plan.get("direction","LAY->BACK"),
            entry_odds,
            ticks,
            size,
            mid, sid,
            dict(ctx)
        )

        # Close loop
        to_del = []
        for oid, (dir_tag, e_odds, tt, st, mmid, ssid, cctx) in list(live_positions.items()):
            try:
                if check_and_close(oid, dir_tag, e_odds, tt, st, mmid, ssid, cctx):
                    if logger: logger(f"MATCH order={oid} ticks={tt}")
                    to_del.append(oid)
            except Exception:
                pass

        for oid in to_del:
            live_positions.pop(oid, None)

        time.sleep(0.05)

    return live_positions

# ============================================================
# STEP 8 — MAIN ENGINE CLASS
# ============================================================

class LearningEngine:
    def __init__(self, n_days: int = 10, logger=None):
        self.n_days = n_days
        self.logger = logger or (lambda msg: print(msg, flush=True))

    def run_day(self, day_iso: str, run_id: str):
        """
        Run a single replay day (OC replay + RL loop).
        """
        self.logger(f"[LEARNING] Starting replay day {day_iso}")
        from engines.decision_engine.decide_once.helpers import enable_learning_time
        enable_learning_time(day_iso)


        # rewrite schedule
        rewrite_market_start_times_for_day(day_iso)

        # load oc snapshots
        rows = load_oc_snapshots_for_day(day_iso)
        self.logger(f"[LEARNING] Loaded {len(rows)} oc snapshots")

        # configure replay clock
        try:
            configure_replay(
                replay_day_iso=day_iso,
                start_at_iso=f"{day_iso}T08:00:00Z",
                speed_x=120.0
            )
        except Exception as e:
            self.logger(f"[LEARNING] replay clock error: {e}")

        # start OC replay thread
        t = start_replay_thread(rows)

        # RL loop
        run_learning_loop(run_id, duration_sec=600, logger=self.logger)

        # Wait thread end if needed
        t.join(timeout=1.0)
        self.logger(f"[LEARNING] Finished day {day_iso}")

    def run_multi_day(self):
        """
        Run top N replay days sequentially.
        """
        days = find_top_replay_days(self.n_days)
        self.logger(f"[LEARNING] Days selected: {days}")

        for i, day in enumerate(days):
            run_id = f"LEARN-{day}-{i}"
            self.logger(f"[LEARNING] >>> RUN DAY {i+1}/{len(days)}: {day}")
            self.run_day(day, run_id)

        self.logger("[LEARNING] ALL DAYS COMPLETE")


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

def start_learning(n_days: int = 10, logger=None):
    eng = LearningEngine(n_days=n_days, logger=logger)
    eng.run_multi_day()


# ============================================================
# END OF learning_engine.py
# ============================================================

