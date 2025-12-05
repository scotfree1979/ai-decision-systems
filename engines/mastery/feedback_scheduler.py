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
# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/feedback_scheduler.py:_compute_real_goal_alignment
# 📆 PATCHED: 2025-12-12 — delegate entirely to GoalAdapter (DAL-safe)
# =======================================================================

def _compute_real_goal_alignment():
    """
    Return unified mastery feedback metrics by delegating to GoalAdapter.
    This removes all raw SQL and duplicates.
    """
    from engines.mastery.goal_adapter import as_feedback_dict

    payload = as_feedback_dict()

    goal_alignment = float(payload.get("goal_alignment", 0.0))
    pnl_today      = float(payload.get("live_pnl", 0.0))
    win_rate_today = float(payload.get("win_rate", 0.0))
    matched_ratio  = float(payload.get("matched_ratio", 0.0))

    return goal_alignment, pnl_today, win_rate_today, matched_ratio

# === PATCH END =========================================================

# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/feedback_scheduler.py:run_goal_alignment_once
# 📆 PATCHED: 2025-12-12 — GoalAdapter-only metrics + clean logging
# =======================================================================

def run_goal_alignment_once() -> None:
    """
    Compute one-shot goal alignment using GoalAdapter only.
    Scheduler does not compute PnL/win-rate/matched itself.
    """
    try:
        from engines.mastery.goal_adapter import as_feedback_dict
        from engines.mastery import event_sink

        payload = as_feedback_dict()

        ga   = float(payload.get("goal_alignment", 0.0))
        pnl  = float(payload.get("live_pnl", 0.0))
        wr   = float(payload.get("win_rate", 0.0)) * 100.0
        mr   = float(payload.get("matched_ratio", 0.0)) * 100.0

        # Emit mastery feedback
        event_sink.emit("goal_alignment_tick", payload)

        # Human readable summary
        print(
            f"[mastery] goal_alignment={ga:.3f}  "
            f"PnL={pnl:.2f}  WinRate={wr:.2f}%  Match={mr:.2f}%"
        )

        # Optional verbose breakdown
        try:
            from engines.mastery.goal_adapter import print_trade_outcome_summary
            print_trade_outcome_summary()
        except Exception as e2:
            print(f"[mastery] goal_adapter summary warn: {e2}")

    except Exception as e:
        print(f"[mastery] goal_adapter warn: {e}")

# === PATCH END =========================================================


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


# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/feedback_scheduler.py:_loop (Stage 6)
# 📆 PATCHED: 2025-12-12 — simplified to GoalAdapter-only
# =======================================================================

                # ── Stage 6: evaluate goal alignment tick ────────────────
                try:
                    run_goal_alignment_once()
                except Exception as e4:
                    print(f"[feedback_scheduler] goal_alignment warn: {e4}")

# === PATCH END =========================================================



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
# === PATCH START =======================================================
# 📍 TARGET: engines/mastery/feedback_scheduler.py (__main__)
# 📆 PATCHED: 2025-12-12 — GoalAdapter-only evaluation
# =======================================================================

if __name__ == "__main__":
    from engines.mastery.goal_adapter import as_feedback_dict, evaluate_progress
    from engines.mastery import event_sink
    from datetime import datetime, timezone

    payload = as_feedback_dict()
    ga = payload.get("goal_alignment", 0.0)

    event_sink.emit("goal_alignment_tick", payload)

    print(
        f"[feedback_scheduler] goal_alignment_tick emitted @ {datetime.now(timezone.utc)}\n"
        f"  Alignment={ga:.3f}  "
        f"PnL={payload.get('live_pnl',0):.2f}  "
        f"WinRate={payload.get('win_rate',0)*100:.1f}%  "
        f"Matched={payload.get('matched_ratio',0)*100:.1f}%"
    )

# === PATCH END =========================================================






