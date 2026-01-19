# engines/live/ctx_loader.py

from __future__ import annotations
import sqlite3
from engines.config_paths import open_auto_db


def load_ctx_for_parent(parent_id: int) -> dict:
    """
    Rehydrate FULL execution ctx for a parent order from DB.

    Contract:
    - DB is the single source of truth
    - NO casting
    - NO inference
    - NO enrichment
    """

    con = None
    try:
        con = open_auto_db(rw=False)
        con.row_factory = sqlite3.Row

        row = con.execute(
            """
            SELECT
                id,
                run_id,
                marketId,
                selectionId,
                engine,
                side,
                entry_odds,
                entry_stake,
                target_ticks,
                source,
                route_id,
                bus_stop,
                tick_id
            FROM orders
            WHERE id = ?
              AND role = 'PARENT'
            """,
            (int(parent_id),)
        ).fetchone()

        if not row:
            raise RuntimeError(
                f"[CTX_LOADER] parent_id={parent_id} not found in orders"
            )

        # DB-FIRST: return raw values only
        return {
            # identity
            "parent_id": row["id"],
            "run_id": row["run_id"],

            # market identity
            "marketId": row["marketId"],
            "selectionId": row["selectionId"],

            # execution identity
            "engine": row["engine"],
            "letter": row["source"],
            "side": row["side"],

            # pricing / sizing (RAW — do not cast)
            "px": row["entry_odds"],
            "entry_odds": row["entry_odds"],
            "entry_stake": row["entry_stake"],
            "target_ticks": row["target_ticks"],

            # ordering metadata
            "route_id": row["route_id"],
            "bus_stop": row["bus_stop"],
            "tick_id": row["tick_id"],
        }

    finally:
        try:
            if con:
                con.close()
        except Exception:
            pass
