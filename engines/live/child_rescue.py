# engines/live/child_rescue.py

import time
import sqlite3
from engines.config_paths import open_auto_db

from engines.price_math import walk_ticks
from engines.live.live_router import enqueue_router_child


GRACE_SECONDS = 3.0


def ensure_single_child_for_parent(
    *,
    parent_id: int,
    marketId: str,
    selectionId: str,
    side: str,
    px: float,
    stake: float,
    exit_kind: str,
    engine: str,
    lane: int,          # 👈 REQUIRED (5 or 6)
    reason: str,
):
    """
    HARD INVARIANT:
    - Exactly ONE active CHILD per hedge_of (parent_id)

    PX is NOT identity.
    """

    now = time.time()

    con = open_auto_db(rw=False)
    con.row_factory = sqlite3.Row

    try:
        rows = con.execute(
            """
            SELECT
                id,
                opened_at,
                entry_odds
            FROM orders
            WHERE role = 'CHILD'
              AND hedge_of = ?
              AND (exit_status IS NULL OR UPPER(exit_status) <> 'MATCHED')
            ORDER BY id DESC
            """,
            (parent_id,),
        ).fetchall()
    finally:
        con.close()

    # --------------------------------------------------
    # PX authority — LANE OWNS THIS
    # --------------------------------------------------
    if lane == 5:
        # OVERWATCHER: reactive stoploss
        # Uses live market PX
        if px is None:
            raise RuntimeError("LANE 5 requires live PX")
        hedge_px = float(px)

    elif lane == 6:
        # DB CORRECTNESS: deterministic rescue hedge
        # Uses parent entry_odds + target_ticks
        con2 = open_auto_db(rw=False)
        con2.row_factory = sqlite3.Row
        try:
            prow = con2.execute(
                """
                SELECT entry_odds, target_ticks, side
                FROM orders
                WHERE id = ?
                """,
                (parent_id,),
            ).fetchone()
        finally:
            con2.close()

        if not prow:
            raise RuntimeError("parent not found for LANE 6 rescue")

        entry_odds   = float(prow["entry_odds"])
        target_ticks = int(prow["target_ticks"])
        parent_side  = prow["side"].upper()

        # Canonical trading rule:
        # LAY first  → BACK higher (odds UP)
        # BACK first → LAY lower  (odds DOWN)
        if parent_side == "LAY":
            hedge_px = walk_ticks(entry_odds, target_ticks, direction="up")
        else:
            hedge_px = walk_ticks(entry_odds, target_ticks, direction="down")

    else:
        raise RuntimeError(f"invalid lane={lane}")

    # PX authority resolved by lane
    final_px = hedge_px

    # --------------------------------------------------
    # CASE A — HEDGE CHILD (normal lifecycle)
    # --------------------------------------------------
    if exit_kind == "HEDGE":
        from engines.live.live_router import _orders_insert_child_queued, enqueue_router_child

        con3 = open_auto_db(rw=False)
        con3.row_factory = sqlite3.Row
        try:
            row = con3.execute(
                "SELECT customerOrderRef FROM orders WHERE id = ?",
                (parent_id,)
            ).fetchone()
        finally:
            con3.close()

        if not row or not row["customerOrderRef"]:
            return "NO_CHILD_CREATED"

        parent_cor = str(row["customerOrderRef"])

        child_id = _orders_insert_child_queued(parent_cor)
        if not child_id:
            return "NO_CHILD_CREATED"

        enqueue_router_child(
            plan={
                "child_id": child_id,
                "parent_id": parent_id,
                "marketId": marketId,
                "selectionId": selectionId,
                "exit_kind": exit_kind,
                "engine": engine,
            },
            ctx={}
        )

        return f"CREATED_{exit_kind}_CHILD"

    # --------------------------------------------------
    # CASE B — STOPLOSS CHILD (OVERWATCHER)
    # --------------------------------------------------
    if exit_kind == "STOPLOSS":
        from engines.live.live_router import _orders_insert_child_queued, enqueue_router_child

        con3 = open_auto_db(rw=False)
        con3.row_factory = sqlite3.Row
        try:
            row = con3.execute(
                "SELECT customerOrderRef FROM orders WHERE id = ?",
                (parent_id,)
            ).fetchone()
        finally:
            con3.close()

        if not row or not row["customerOrderRef"]:
            return "NO_CHILD_CREATED"

        parent_cor = str(row["customerOrderRef"])

        child_id = _orders_insert_child_queued(parent_cor)
        if not child_id:
            return "NO_CHILD_CREATED"

        enqueue_router_child(
            plan={
                "child_id": child_id,
                "parent_id": parent_id,
                "marketId": marketId,
                "selectionId": selectionId,
                "exit_kind": exit_kind,
                "engine": engine,
            },
            ctx={}
        )


        return f"CREATED_{exit_kind}_CHILD"

    # --------------------------------------------------
    # NO ACTION
    # --------------------------------------------------
    return "NO_ACTION"



def _cancel_child(child_id: int, *, reason: str):
    con = open_auto_db(rw=True)
    try:
        con.execute(
            """
            UPDATE orders
            SET exit_status = 'CANCELLED',
                exit_kind = ?
            WHERE id = ?
            """,
            (reason, child_id),
        )
        con.commit()
    finally:
        con.close()
