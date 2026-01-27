# engines/live/child_rescue.py

import time
import sqlite3
from engines.config_paths import open_auto_db
from engines.decision_engine.decide_once.placement import enqueue_for_placement
from engines.price_math import walk_ticks


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
                created_at,
                entry_odds
            FROM orders
            WHERE role = 'CHILD'
              AND hedge_of = ?
              AND (exit_status IS NULL OR UPPER(exit_status) <> 'MATCHED')
            ORDER BY created_at DESC
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

    # --------------------------------------------------
    # CASE A — no child → create
    # --------------------------------------------------
    if not rows:
        plan = {
            "engine": engine,
            "role": "CHILD",
            "exit_kind": exit_kind,
            "marketId": marketId,
            "selectionId": selectionId,
            "side": side,
            "px": px,
            "size": stake,
            "hedge_of": parent_id,
            "why": reason,
        }
        enqueue_for_placement(engine, plan, {})
        return "CREATED"

    # --------------------------------------------------
    # CASE B — one child → maybe replace
    # --------------------------------------------------
    if len(rows) == 1:
        child = rows[0]
        created_ts = child["created_at"]

        # Grace window → do nothing
        if created_ts and (now - created_ts) < GRACE_SECONDS:
            return "SKIPPED_GRACE"

        # Replace existing child
        _cancel_child(child["id"], reason="child_replace")

        plan = {
            "engine": engine,
            "role": "CHILD",
            "exit_kind": exit_kind,
            "marketId": marketId,
            "selectionId": selectionId,
            "side": side,
            "px": px,
            "size": stake,
            "hedge_of": parent_id,
            "why": reason,
        }
        enqueue_for_placement(engine, plan, {})
        return "REPLACED"

    # --------------------------------------------------
    # CASE C — duplicates → collapse
    # --------------------------------------------------
    survivor = rows[0]
    for r in rows[1:]:
        _cancel_child(r["id"], reason="child_dedupe")

    return "DEDUPED"


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
