from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Dict, Set

from engines.decision_engine.decide_once.scope import scope_snapshot
from engines.live import bank_state
from engines.config_paths import open_auto_db, q_retry


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
    def _run_once(self):
        now = datetime.now(timezone.utc)
        stamp = now.strftime("%Y-%m-%d %H:%M:%S")

        scope = scope_snapshot(inplay_window_min=15)
        current_in_play: Set[str] = set()

        for it in scope.get("in_play", []):
            try:
                mid, _elapsed = it
                current_in_play.add(str(mid))
            except Exception:
                pass

        # Determine which markets exited IN_PLAY
        exited_markets = []
        for mid, last_bucket in list(self._seen_markets.items()):
            if last_bucket == "IN_PLAY" and mid not in current_in_play:
                exited_markets.append(mid)

        # Update seen markets
        for mid in current_in_play:
            self._seen_markets[mid] = "IN_PLAY"

        for mid in exited_markets:
            self._seen_markets.pop(mid, None)

        # No work to do
        if not exited_markets:
            self._print_report(
                stamp,
                markets_left=0,
                parents_checked=0,
                released=0,
                skipped=0,
                reasons={}
            )
            return

        parents_checked = 0
        released = 0
        skipped = 0
        reasons: Dict[str, int] = {}

        # Handle exited markets one by one
        for mid in exited_markets:
            p_checked, p_released, p_skipped, p_reasons = self._handle_market(mid)
            parents_checked += p_checked
            released += p_released
            skipped += p_skipped
            for k, v in p_reasons.items():
                reasons[k] = reasons.get(k, 0) + v

        self._print_report(
            stamp,
            markets_left=len(exited_markets),
            parents_checked=parents_checked,
            released=released,
            skipped=skipped,
            reasons=reasons
        )

    # -----------------------------------------------------------
    # Market handler
    # -----------------------------------------------------------
    def _handle_market(self, market_id: str):
        con = open_auto_db(rw=True)
        con.row_factory = None
        cur = con.cursor()

        parents_checked = 0
        released = 0
        skipped = 0
        reasons: Dict[str, int] = {}

        rows = q_retry(cur, """
            SELECT
              p.id,
              p.customerOrderRef,
              p.engine,
              p.entry_odds,
              p.entry_stake
            FROM orders p
            WHERE p.marketId=?
              AND p.role='PARENT'
              AND UPPER(p.entry_status)='MATCHED'
              AND (p.exit_status IS NULL OR UPPER(p.exit_status)<>'MATCHED')
              AND NOT EXISTS (
                    SELECT 1 FROM orders c
                    WHERE c.hedge_of=p.id
              )
        """, (str(market_id),)).fetchall()

        for (pid, cor, engine, odds, stake) in rows:
            parents_checked += 1

            if not stake or stake <= 0:
                skipped += 1
                reasons["no_stake"] = reasons.get("no_stake", 0) + 1
                continue

            try:
                bank_state.on_parent_closed(
                    engine=str(engine),
                    entry_odds=float(odds),
                    entry_stake=float(stake),
                )

                q_retry(cur, """
                    UPDATE orders
                       SET exit_status='EXPIRED',
                           closed_at=datetime('now','utc'),
                           exposure_released=1
                     WHERE id=?
                """, (int(pid),))

                released += 1
                reasons["released_on_scope_exit"] = reasons.get("released_on_scope_exit", 0) + 1

            except Exception as e:
                skipped += 1
                reasons["release_error"] = reasons.get("release_error", 0) + 1
                print(f"[EXPOSURE-GUARDIAN][WARN] release failed cor={cor}: {e}")

        con.commit()
        con.close()

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
