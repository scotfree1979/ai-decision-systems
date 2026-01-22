from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict, Set

from engines.decision_engine.decide_once.scope import scope_snapshot
from engines.live.live_router import _release_parent_exposure_db

from engines.config_paths import open_auto_db, q_retry

GRACE_MINUTES = 6
# ───────────────────────────────────────────────────────────────
# Exposure Guardian
# ───────────────────────────────────────────────────────────────
#
# Purpose:
#   Final, authoritative exposure lifecycle failsafe.
#
#   Releases exposure ONLY when:
#     • Parent is MATCHED
#     • No child exists
#     • Market has exited IN_PLAY scope
#
#   Uses scope as a read-only signal.
#   Never blocks trading.
#   Never guesses.
#
# ───────────────────────────────────────────────────────────────


class ExposureGuardian:
    def __init__(self, *, interval_s: int = 300):
        self.interval_s = int(interval_s)

        # Runtime-only memory (no DB state)
        self._seen_markets: Dict[str, str] = {}  # marketId → last_bucket
        self._running = False
        self._thread: threading.Thread | None = None

    # -----------------------------------------------------------
    # Public API
    # -----------------------------------------------------------
    def start(self):
        if self._running:
            return
        self._running = True
        t = threading.Thread(
            target=self._loop,
            name="ExposureGuardian",
            daemon=True,
        )
        t.start()
        self._thread = t
        print(f"[EXPOSURE-GUARDIAN] started (interval={self.interval_s}s)")

    def stop(self):
        self._running = False

    # -----------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------
    def _loop(self):
        while self._running:
            try:
                self._run_once()
            except Exception as e:
                print(f"[EXPOSURE-GUARDIAN][ERROR] {e}")
            time.sleep(self.interval_s)

    # -----------------------------------------------------------
    # One audit cycle
    # -----------------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/exposure/exposure_guardian.py
# 🔎 ANCHOR: def _run_once(self):
# 🧩 ACTION: REPLACE exited-market handling + reporting semantics
# 📆 PATCHED: 2026-01-16 — Promote ExposureGuardian to authoritative exposure reconciler
#
# PURPOSE:
#   • Count ALL parents that carried exposure and exited scope
#   • Enforce final invariant: finished market ⇒ zero exposure
#   • Produce meaningful, monotonic diagnostics even when system is healthy
#
# ARCHITECTURAL SHIFT:
#   ExposureGuardian is no longer a “panic cleaner”.
#   It is the authoritative exposure lifecycle reconciler.
# ======================================================================================================
    def _run_once(self):
        con = open_auto_db(rw=False)
        con.row_factory = None
        cur = con.cursor()

        stamp = datetime.now(timezone.utc).isoformat()

        parents_checked = 0
        released = 0
        skipped = 0
        reasons: Dict[str, int] = {}

# ======================================================================================================
# 📍 TARGET: engines/exposure/exposure_guardian.py
# 🔎 ANCHOR: def _run_once(self):
# 🧩 ACTION: ADD unmatched parent timeout release (5-minute rule)
# 📆 PATCHED: 2026-03-25 — Release exposure for UNMATCHED parents older than 5 minutes
#
# PURPOSE:
# - Prevent exposure being trapped on parents that never matched
# - Recycle capital deterministically
#
# RULE:
# - LIVE parent
# - entry_status IN (QUEUED, PLACED)
# - NOT MATCHED
# - opened_at <= now - 5 minutes
# - exposure_released = 0
#
# SAFETY:
# - DB-first
# - Idempotent
# - MATCHED parents are explicitly excluded
# ======================================================================================================

        # -----------------------------------------------------------
        # UNMATCHED PARENT TIMEOUT (5-MINUTE RULE)
        # -----------------------------------------------------------
        con_u = open_auto_db(rw=True)
        cur_u = con_u.cursor()

        rows = q_retry(cur_u, """
            SELECT
                id,
                customerOrderRef
            FROM orders
            WHERE mode='LIVE'
              AND role='PARENT'
              AND UPPER(COALESCE(entry_status,'')) IN ('PLACED')
              AND datetime(opened_at) <= datetime('now','utc','-5 minutes')
              AND COALESCE(exposure_released,0) = 0
        """).fetchall()

        for pid, cor in rows:
            try:
                # 1️⃣ Cancel parent (DB-authoritative)
                q_retry(cur_u, """
                    UPDATE orders
                       SET entry_status='CANCELLED',
                           exit_status='EXPIRED',
                           closed_at=datetime('now','utc')
                     WHERE id=?
                       AND UPPER(COALESCE(entry_status,'')) != 'MATCHED'
                """, (int(pid),))

                # 2️⃣ Release exposure (idempotent, DB-locked)
                _release_parent_exposure_db(int(pid))

            except Exception as e:
                print(
                    f"[EXPOSURE-GUARDIAN][WARN] "
                    f"unmatched timeout release failed parent_ref={cor}: {e}"
                )

        con_u.commit()
        con_u.close()

        # -----------------------------------------------------------
        # Identify markets that have exited scope (authoritative)
        # -----------------------------------------------------------
        rows = q_retry(cur, """
            SELECT DISTINCT p.marketId
            FROM orders p
            WHERE p.role='PARENT'
              AND p.required_exposure IS NOT NULL
              AND p.required_exposure > 0
              AND (
                   p.exit_status IN ('SETTLED','CANCELLED','EXPIRED')
                OR p.marketId IN (
                       SELECT marketId
                       FROM bets
                       WHERE datetime(marketStartTime)
                             < datetime('now','utc', ?)
                   )
              )
        """, (f"-{GRACE_MINUTES} minutes",)).fetchall()

        con.close()

        # -----------------------------------------------------------
        # Reconcile exposure per finished market
        # -----------------------------------------------------------
        for (market_id,) in rows:
            pc, r, s, rs = self._handle_market(str(market_id))
            parents_checked += pc
            released += r
            skipped += s
            for k, v in rs.items():
                reasons[k] = reasons.get(k, 0) + v

        self._print_report(
            stamp,
            markets_left=len(rows),
            parents_checked=parents_checked,
            released=released,
            skipped=skipped,
            reasons=reasons,
        )

    # -----------------------------------------------------------
    # Market handler
    # -----------------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/exposure/exposure_guardian.py
# 🔎 ANCHOR: def _handle_market(self, market_id: str):
# 🧩 ACTION: REPLACE parent scan + release logic
# 📆 PATCHED: 2026-01-16 — DB-truth exposure reconciliation
#
# INVARIANT:
#   If a market has exited IN_PLAY, ALL parents with required_exposure
#   must have exposure_released = 1.
#
# NOTES:
#   • Parents already released are counted as SKIPPED (healthy)
#   • Guardian only RELEASES when invariant is violated
# ======================================================================================================
    def _handle_market(self, market_id: str):
        con = open_auto_db(rw=False)
        con.row_factory = None
        cur = con.cursor()

        parents_checked = 0
        released = 0
        skipped = 0
        reasons: Dict[str, int] = {}

        # -----------------------------------------------------------
        # Scan ALL parents on this market that ever carried exposure
        # -----------------------------------------------------------
        rows = q_retry(cur, """
            SELECT
                id,
                customerOrderRef
            FROM orders
            WHERE marketId = ?
              AND role = 'PARENT'
              AND required_exposure IS NOT NULL
              AND required_exposure > 0
        """, (str(market_id),)).fetchall()

        con.close()

        for (pid, cor) in rows:
            parents_checked += 1

            try:
                # Canonical, DB-first, idempotent exposure release
                released_now = _release_parent_exposure_db(int(pid))

                if released_now:
                    released += 1
                    reasons["forced_release"] = (
                        reasons.get("forced_release", 0) + 1
                    )
                else:
                    skipped += 1
                    reasons["already_released_or_not_eligible"] = (
                        reasons.get("already_released_or_not_eligible", 0) + 1
                    )

            except Exception as e:
                skipped += 1
                reasons["release_error"] = reasons.get("release_error", 0) + 1
                print(
                    f"[EXPOSURE-GUARDIAN][WARN] release failed "
                    f"market={market_id} cor={cor}: {e}"
                )

        return parents_checked, released, skipped, reasons



    # -----------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------
    def _print_report(
        self,
        stamp: str,
        *,
        markets_left: int,
        parents_checked: int,
        released: int,
        skipped: int,
        reasons: Dict[str, int],
    ):
        print(f"[EXPOSURE-GUARDIAN] tick @ {stamp}")
        print(f"  markets_left_scope: {markets_left}")
        print(f"  parents_checked:    {parents_checked}")
        print(f"  released:           {released}")
        print(f"  skipped:            {skipped}")
        if reasons:
            print(f"  reasons:")
            for k, v in sorted(reasons.items()):
                print(f"    - {k}: {v}")
        else:
            print(f"  reasons: (none)")
