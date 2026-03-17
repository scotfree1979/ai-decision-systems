# engines/bus_route_startup_ctx.py

from __future__ import annotations
from datetime import datetime, timezone
from typing import List, Tuple
import sqlite3

from engines.mastery.context_builder import build_context_for_runner
from engines.config_paths import connect_db


class StartupCTXBuilder:
    """
    Day-scoped CTX bootstrapper.

    CONTRACT:
    - Writes CTX directly into BusRouteSnapshot.ctx_map
    - Never blocks
    - Idempotent
    - Dies once all today's markets are built
    """

    def __init__(self, route_snapshot):
        self.route = route_snapshot
        self.today = datetime.now(timezone.utc).date().isoformat()

        self.market_queue: List[str] = self._load_today_markets()
        self.market_idx = 0
        self.done = False

        print(f"[CTX-BOOT] loaded {len(self.market_queue)} markets for {self.today}")

    # ------------------------------------------------------------------
    # Load today's markets ordered by start time
    # ------------------------------------------------------------------
    def _load_today_markets(self) -> List[str]:
        con = connect_db(ro=True)
        con.row_factory = sqlite3.Row

        try:
            rows = con.execute(
                """
                SELECT DISTINCT marketId, marketStartTime
                FROM bets
                WHERE datetime(marketStartTime) >= datetime('now','utc')
                ORDER BY datetime(marketStartTime) ASC
                """
            ).fetchall()
        finally:
            con.close()

        now = datetime.now(timezone.utc)

        # Start from the *next* upcoming market
        ordered = []
        for r in rows:
            try:
                off = datetime.fromisoformat(
                    r["marketStartTime"].replace("Z", "+00:00")
                )
            except Exception:
                continue

        return ordered

    # ------------------------------------------------------------------
    # One incremental build step (call from BUS loop)
    # ------------------------------------------------------------------
    def step(self, *, max_builds: int = 20) -> None:
        """
        Build CTX incrementally.
        Safe to call every BUS tick.
        """

        if self.done:
            return

        built = 0

        while self.market_idx < len(self.market_queue):
            mid = self.market_queue[self.market_idx]

            # Build ALL runners for this market
            runners = self._runners_for_market(mid)

            for sid in runners:
                key = (mid, sid)

                if key in self.route.ctx_map:
                    continue

                try:
                    ctx, _ = build_context_for_runner(mid, sid, source="LIVE")
                    self.route.ctx_map[key] = ctx
                    built += 1
                except Exception:
                    continue

                if built >= max_builds:
                    return  # yield to BUS

            # Finished this market
            self.market_idx += 1

        # All markets done
        self.done = True
        print(f"[CTX-BOOT] COMPLETE — all CTX built for {self.today}")

    # ------------------------------------------------------------------
    # Runner lookup (DB-first)
    # ------------------------------------------------------------------
    def _runners_for_market(self, market_id: str) -> List[str]:
        con = connect_db(ro=True)
        con.row_factory = sqlite3.Row

        try:
            rows = con.execute(
                """
                SELECT DISTINCT selectionId
                FROM bets
                WHERE marketId = ?
                """,
                (str(market_id),),
            ).fetchall()
        finally:
            con.close()

        return [str(r["selectionId"]) for r in rows if r["selectionId"]]
