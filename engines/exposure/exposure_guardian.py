from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict

from engines.config_paths import open_auto_db, q_retry
from engines.live.live_router import _release_parent_exposure_db
from engines.config_paths import connect_db

GRACE_MINUTES = 6


class ExposureGuardian:
    """
    Final, authoritative exposure reconciliation loop.

    DB is the source of truth.
    BankState is NOT consulted.
    """

    def __init__(self, *, interval_s: int = 300):
        self.interval_s = int(interval_s)
        self._running = False
        self._thread: threading.Thread | None = None

    # --------------------------------------------------
    # Public API
    # --------------------------------------------------
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

    # --------------------------------------------------
    # Main loop
    # --------------------------------------------------
    def _loop(self):
        while self._running:
            try:
                self._run_once()
            except Exception as e:
                print(f"[EXPOSURE-GUARDIAN][ERROR] {e}")
            time.sleep(self.interval_s)

    # --------------------------------------------------
    # One reconciliation pass
    # --------------------------------------------------
    def _run_once(self):
        stamp = datetime.now(timezone.utc).isoformat()

        parents_checked = 0
        released = 0
        skipped = 0
        reasons: Dict[str, int] = {}

        con = open_auto_db(rw=False)
        con.row_factory = None
        cur = con.cursor()

        # --------------------------------------------------
        # 1️⃣ Find ALL parents that STILL hold exposure
        # --------------------------------------------------
        rows = q_retry(cur, """
            SELECT
                id,
                customerOrderRef,
                marketId,
                entry_status,
                exit_status
            FROM orders
            WHERE role = 'PARENT'
              AND mode = 'LIVE'
              AND required_exposure > 0
              AND COALESCE(exposure_released, 0) = 0
        """).fetchall()

        con.close()

        # --------------------------------------------------
        # 2️⃣ Decide release eligibility
        # --------------------------------------------------
        for pid, cor, market_id, entry_status, exit_status in rows:
            parents_checked += 1

            try:
                # A) Child matched → release immediately
                con2 = open_auto_db(rw=False)
                cur2 = con2.cursor()
                child_matched = q_retry(cur2, """
                    SELECT 1
                      FROM orders
                     WHERE hedge_of = ?
                       AND role = 'CHILD'
                       AND UPPER(entry_status) = 'MATCHED'
                     LIMIT 1
                """, (int(pid),)).fetchone() is not None
                con2.close()

                if child_matched:
                    _release_parent_exposure_db(int(pid))
                    released += 1
                    reasons["child_matched"] = reasons.get("child_matched", 0) + 1
                    continue

                # B) Parent terminal → release
                if (exit_status or "").upper() in ("SETTLED", "CANCELLED", "EXPIRED"):
                    _release_parent_exposure_db(int(pid))
                    released += 1
                    reasons["parent_terminal"] = reasons.get("parent_terminal", 0) + 1
                    continue

                # C) Market past grace → release
                try:
                    bdb = connect_db(ro=True)
                    row = q_retry(bdb, """
                        SELECT
                          CAST((julianday('now','utc') - julianday(marketStartTime))*1440 AS INTEGER)
                          AS mins_after
                        FROM bets
                        WHERE marketId=?
                        LIMIT 1
                    """, (str(market_id),)).fetchone()
                    bdb.close()

                    if row and row[0] is not None and int(row[0]) >= GRACE_MINUTES:
                        _release_parent_exposure_db(int(pid))
                        released += 1
                        reasons["market_finished"] = reasons.get("market_finished", 0) + 1
                        continue
                except Exception:
                    pass

                # Otherwise not eligible yet
                skipped += 1
                reasons["not_eligible"] = reasons.get("not_eligible", 0) + 1

            except Exception as e:
                skipped += 1
                reasons["error"] = reasons.get("error", 0) + 1
                print(
                    f"[EXPOSURE-GUARDIAN][WARN] parent={cor} err={e}"
                )

        # --------------------------------------------------
        # 3️⃣ Report
        # --------------------------------------------------
        print(f"[EXPOSURE-GUARDIAN] tick @ {stamp}")
        print(f"  parents_checked: {parents_checked}")
        print(f"  released:        {released}")
        print(f"  skipped:         {skipped}")
        if reasons:
            print("  reasons:")
            for k, v in sorted(reasons.items()):
                print(f"    - {k}: {v}")
        else:
            print("  reasons: none")
