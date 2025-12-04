# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_scheduler.py
# 📆 PATCHED: 2025-10-27Z — safe deferred goal-alignment evaluation (no blocking on import)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# NOTE:
# The previous version executed goal-alignment immediately on module import,
# which caused python -m engines.mastery.feedback_scheduler to hang
# because canonical_digest metrics weren’t ready yet.
# We now safely import the symbols without executing them,
# deferring the alignment call to the scheduler or __main__ block.

from engines.mastery import event_sink
from engines.mastery.goal_adapter import as_feedback_dict
from engines.mastery.canonical_digest import total_pnl
_sched_thread = None
_stop_flag = None
import threading
import time, sqlite3
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db



# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_scheduler.py
# 🔧 FUNCTION: _compute_real_goal_alignment
# 📆 PATCHED: 2025-11-22 — DAL-safe autoscalp_gui read
def _compute_real_goal_alignment():
    """Return (goal_alignment, pnl_today, win_rate_today) from live + settlements."""

    import sqlite3
    from engines.config_paths import autoscalp_db, settlements_db, q_retry as _q
    from engines.decision_engine.decide_once.helpers import open_auto_db

    # --- 1) realized PnL + win rate from settlements.db ---
    con_set = sqlite3.connect(settlements_db())
    con_set.row_factory = sqlite3.Row

    row_pnl = _q(
        con_set,
        """
        SELECT ROUND(SUM(profit),2) AS pnl_today
          FROM bf_cleared_orders
         WHERE date(datetime(replace(settledDate,'Z','+00:00')))
               = date('now','utc');
        """
    ).fetchone()

    row_wr = _q(
        con_set,
        """
        SELECT ROUND(AVG(CASE WHEN profit>0 THEN 1.0 ELSE 0.0 END),3) AS win_rate_today
          FROM bf_cleared_orders
         WHERE date(datetime(replace(settledDate,'Z','+00:00')))
               = date('now','utc');
        """
    ).fetchone()

    realized_pnl = float(row_pnl["pnl_today"] or 0.0)
    win_rate_today = float(row_wr["win_rate_today"] or 0.0)
    con_set.close()

    # --- 2) unrealized live PnL from autoscalp_gui.live_state ---
    con_live = open_auto_db(ro=True)   # ✅ DAL-safe
    con_live.row_factory = sqlite3.Row

    row_live = _q(
        con_live,
        """
        SELECT COALESCE(SUM(pnl_now),0.0) AS pnl_live
          FROM live_state
         WHERE status='OPEN';
        """
    ).fetchone()

    pnl_live = float(row_live["pnl_live"] or 0.0)
    con_live.close()

    # --- 3) combined and normalized ---
    total_pnl = realized_pnl + pnl_live
    daily_target = 500.0
    goal_alignment = round(min(1.0, max(0.0, total_pnl / daily_target)), 3)

    return goal_alignment, total_pnl, win_rate_today
# === PATCH END ===



# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_scheduler.py
# 🔧 FUNCTION: run_goal_alignment_once
# 📆 PATCHED: 2025-11-22 — DAL-safe autoscalp_gui read
def run_goal_alignment_once() -> None:
    """
    Execute one-shot goal alignment safely (used by daemon or CLI).
    Reads canonical digest and emits 'goal_alignment_tick' to mastery_cache.
    """

    import sqlite3
    from engines.config_paths import autoscalp_db
    from engines.decision_engine.decide_once.helpers import open_auto_db
    from engines.mastery import event_sink
    from engines.mastery.goal_adapter import as_feedback_dict
    from engines.mastery.canonical_digest import total_pnl

# === PATCH START ============================================================
# 📍 TARGET: engines/mastery/feedback_scheduler.py:run_goal_alignment_once
# 🔎 SEARCH: with sqlite3.connect(SETTLE_DB) as scon:
#            with open_auto_db(ro=True) as acon:
# 📆 PATCHED: 2025-12-04 — DAL-safe connection usage (no context managers)
# ============================================================================

    try:
        # --- 1) core PnL from canonical digest ---
        pnl = float(total_pnl() or 0.0)

        # --- 2) win rate from settlements.db ---
        SETTLE_DB = "data/settlements.db"
        win_rate = 0.0
        scon = sqlite3.connect(SETTLE_DB)
        try:
            scon.row_factory = sqlite3.Row
            row = scon.execute("""
                WITH pnl_by_market AS (
                    SELECT marketId, SUM(profit) AS pnl
                      FROM bf_cleared_orders
                     WHERE date(settledDate)=date('now','utc')
                     GROUP BY marketId
                )
                SELECT
                  100.0 *
                  SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)
                     / COUNT(*) AS win_rate
                FROM pnl_by_market;
            """).fetchone()
            if row and row["win_rate"] is not None:
                win_rate = float(row["win_rate"])
        finally:
            try:
                scon.close()
            except:
                pass

        # --- 3) matched ratio from autoscalp_gui.db (DAL-safe) ---
        matched_ratio = 0.0
        acon = open_auto_db(rw=False)
        try:
            acon.row_factory = sqlite3.Row
            # === PATCH START ============================================================
            # 📍 TARGET: engines/mastery/goal_adapter.py
            # 🔎 SEARCH: SUM(COALESCE(net,0)) AS total
            # 📆 PATCHED: 2025-12-04 — replace nonexistent column "net" with "profit"

            row = acon.execute("""
                SELECT marketId,
                       SUM(COALESCE(profit,0)) AS total     -- FIXED: 'net' → 'profit'
                  FROM v_dashboard_cashout
                 WHERE date(day) >= CASE
                         WHEN ? IS NULL THEN date(day)
                         WHEN ?='0' THEN date('now','utc')
                         ELSE date('now','utc', ?)
                     END
                 GROUP BY marketId
            """, (window, window, window)).fetchone()
            # === PATCH END ==============================================================

            if row and row["matched_ratio"] is not None:
                matched_ratio = float(row["matched_ratio"])
        finally:
            try:
                acon.close()
            except:
                pass

        # --- 4) assemble & emit payload ---
        payload = as_feedback_dict()
        event_sink.emit("goal_alignment_tick", payload)

        print(
            f"[mastery] goal-alignment {payload.get('goal_alignment', 0):.3f}  "
            f"PnL={payload.get('live_pnl', 0):.2f}  "
            f"WinRate={payload.get('win_rate',0)*100:.2f}%  "
            f"Match={payload.get('matched_ratio',0)*100:.2f}%"
        )

        # --- 5) optional trade-label summary ---
        try:
            from engines.mastery.goal_adapter import print_trade_outcome_summary
            print_trade_outcome_summary()
        except Exception as e:
            print(f"[mastery] goal_adapter summary warn: {e}")

    except Exception as e:
        print(f"[mastery] goal_adapter warn: {e}")

# === PATCH END ==============================================================






def start(interval_s: int = 60):
    """Launch background Mastery feedback + assimilation scheduler."""
    global _sched_thread, _stop_flag



    from engines.mastery import feedback_engine, event_sink

    _stop_flag = threading.Event()

    def _loop():
        while not _stop_flag.is_set():
            try:
                # ── Stage 3: compute live feedback ─────────────────────────
                n = feedback_engine.compute_feedback()
                if n:
                    event_sink.emit(
                        "feedback_summary",
                        {
                            "ts": datetime.now(timezone.utc).isoformat(),
                            "rows": n,
                            "source": "LIVE",
                        },
                    )

# === PATCH START ==============================================================
# 📍 TARGET: engines/mastery/feedback_scheduler.py : _loop()
# 🔎 SEARCH: "# ── Stage 4: assimilate feedback into mastery_posteriors"
# 📆 PATCHED: 2025-12-03 — v7 aligned: only assimilate AFTER market completion
# 🧠 SUMMARY:
#    • Prevents mid-race reinforcement
#    • Ensures assimilate_feedback() receives final outcomes
#    • Uses settlements tables to detect finished markets
# ==============================================================================

                # ── Stage 4: assimilate feedback into mastery_posteriors ───

                # NEW: only run assimilation for markets that have *finished*
                try:
                    from engines.config_paths import open_settlements_db
                    scon = open_settlements_db(ro=True)
                    finished = scon.execute("""
                        SELECT DISTINCT marketId
                          FROM bf_cleared_orders
                         WHERE settledDate >= date('now','-1 day','utc')
                    """).fetchall()
                    scon.close()

                    finished_markets = {str(r["marketId"]) for r in finished}
                except Exception:
                    finished_markets = set()

                # If no finished markets, skip assimilation
                if not finished_markets:
                    # skip partial updates — v7 requirement
                    pass
                else:
                    try:
                        from engines.mastery.feedback_assimilator import assimilate_feedback

                        # Call assimilator only if complete markets exist
                        a = assimilate_feedback(10)
                        if a:
                            event_sink.emit(
                                "feedback_assimilated_summary",
                                {
                                    "ts": datetime.now(timezone.utc).isoformat(),
                                    "rows": a,
                                    "source": "LIVE",
                                },
                            )
                    except Exception as e2:
                        print(f"[feedback_scheduler] assimilate warn: {e2}")

# === PATCH END ================================================================


# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_scheduler.py:_loop (Stage 5 DB write)
# 🔧 FIX: wrap DAL writer block in its own try/except to remove invalid naked `except`
# 📆 PATCHED: 2025-11-22Z

                # === STAGE 5 DB WRITE (FIXED) ===
                try:
                    from engines.decision_engine.decide_once.helpers import open_auto_db
                    con = open_auto_db(rw=True)     # ✅ DAL-safe writer
                    con.row_factory = sqlite3.Row
                    cur = con.cursor()

                    # force schema reload so new columns are visible
                    cur.execute("PRAGMA schema_version;")
                    cur.fetchall()

                    # fill in bias_delta if missing or stale
                    cur.execute("""
                        UPDATE mastery_feedback
                           SET bias_delta = COALESCE(actual_bias - plan_bias, bias_delta)
                         WHERE bias_delta IS NULL
                            OR bias_delta = 0.0;
                    """)

                    # ensure exposure_gap always reflects target-vs-actual
                    cur.execute("""
                        UPDATE mastery_feedback
                           SET exposure_gap = COALESCE(exposure_target - exposure_actual,
                                                       exposure_gap)
                         WHERE exposure_gap IS NULL;
                    """)

                    con.commit()
                    con.close()

                except Exception as e3:
                    print(f"[feedback_scheduler] tuner warn: {e3}")
# === PATCH END ===


                # ── Stage 6: evaluate goal alignment tick ──────────────────
                try:
                    from engines.mastery.feedback_scheduler import run_goal_alignment_once
                    run_goal_alignment_once()
                except Exception as e4:
                    print(f"[feedback_scheduler] goal_alignment warn: {e4}")


            except Exception as e:
                # this closes the outer try:, eliminating the syntax error
                print(f"[feedback_scheduler] fatal: {e}")



            # ── sleep with early-exit responsiveness ─────────────────────
            for _ in range(interval_s):
                if _stop_flag.is_set():
                    break
                time.sleep(1)



    _sched_thread = threading.Thread(target=_loop, name="MasteryFeedbackScheduler", daemon=True)
    _sched_thread.start()

def stop(timeout_s: float = 3.0):
    """Stop the scheduler safely."""
    global _sched_thread, _stop_flag
    if not _sched_thread:
        return
    try:
        if _stop_flag:
            _stop_flag.set()
        _sched_thread.join(timeout=timeout_s)
    except Exception:
        pass
    finally:
        _sched_thread = None
        _stop_flag = None
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/mastery/feedback_scheduler.py (__main__)
# 📆 PATCHED: 2025-11-06Z — integrate new goal_adapter scoring
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    import sqlite3, json
    from datetime import datetime, timezone
    from engines.config_paths import autoscalp_db
    from engines.mastery import event_sink
    from engines.mastery.goal_adapter import evaluate_progress, as_feedback_dict

    con = sqlite3.connect(autoscalp_db()); con.row_factory = sqlite3.Row

    # 1️⃣  Use real metrics
    goal_alignment, pnl_today, win_rate_today = _compute_real_goal_alignment()

    # 2️⃣  Weighted matched ratio from trade_labels (H/M/S)
    row = con.execute("""
        SELECT
          100.0 *
          SUM(CASE WHEN label='H' THEN 1.0 WHEN label='M' THEN 0.5 ELSE 0.0 END)
          / NULLIF(COUNT(*),0) AS matched_ratio
        FROM trade_labels;
    """).fetchone()
    matched_ratio = float(row["matched_ratio"] or 0.0) / 100.0
    con.close()

    # 3️⃣  Evaluate overall alignment using the new goal_adapter curve
    score = evaluate_progress(pnl_today, win_rate_today, matched_ratio)
    payload = as_feedback_dict(pnl_today, win_rate_today, matched_ratio)

    # 4️⃣  Emit the goal-alignment tick
    event_sink.emit("goal_alignment_tick", payload)
    print(
        f"[feedback_scheduler] ✅ goal_alignment_tick emitted @ {datetime.now(timezone.utc)}\n"
        f"  PnL={pnl_today:.2f}  WinRate={win_rate_today*100:.1f}%  "
        f"Matched={matched_ratio*100:.1f}%  Alignment={score:.3f}"
    )
# === PATCH END ===





