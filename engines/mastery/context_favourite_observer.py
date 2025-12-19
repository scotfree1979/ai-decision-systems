#!/usr/bin/env python3
"""
context_favourite_observer.py
-----------------------------
TRAINING-ONLY observer.

Derives favourite ordinal context from cache_mastery_day and
writes into context_favourite_ordinals.

• NO live execution impact
• NO EventSink
• NO DAL
• Local mastery_v7.db only
"""

import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, List, Tuple

DB_PATH = "data/mastery_v7.db"


# ---------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------
def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


# ---------------------------------------------------------------------
# Bucketing logic (LOCKED)
# ---------------------------------------------------------------------
def _fav_bucket(rank: int) -> str:
    if rank == 1:
        return "FAV"
    if rank <= 3:
        return "TOP3"
    if rank <= 5:
        return "MID"
    return "LONGSHOT"


# ---------------------------------------------------------------------
# Core observer
# ---------------------------------------------------------------------
def rebuild_favourite_ordinals(days: int = 90) -> None:
    """
    Rebuild context_favourite_ordinals from cache_mastery_day.

    Safe to run repeatedly.
    """
    print(f"[fav-ctx] rebuilding favourite ordinals (days={days})")

    con = _connect()
    cur = con.cursor()

    # 1️⃣ Load cache rows
    rows = cur.execute(
        """
        SELECT
            day,
            marketId,
            selectionId,
            anchor_odd
        FROM cache_mastery_day
        WHERE date(day) >= date('now','-{} day')
          AND anchor_odd IS NOT NULL
        ORDER BY day, marketId, anchor_odd ASC
        """.format(days)
    ).fetchall()

    if not rows:
        print("[fav-ctx] ⚠️ no cache rows found — nothing to do")
        con.close()
        return

    # 2️⃣ Group by (day, marketId)
    markets: Dict[Tuple[str, str], List[sqlite3.Row]] = defaultdict(list)
    for r in rows:
        key = (r["day"], r["marketId"])
        markets[key].append(r)

    print(f"[fav-ctx] markets found: {len(markets)}")

    # 3️⃣ Clear existing derived data (rebuildable by design)
    cur.execute("DELETE FROM context_favourite_ordinals;")
    con.commit()

    # 4️⃣ Build ordinals
    inserts = []

    for (day, marketId), runners in markets.items():
        # sort by anchor odds (ascending = favourite first)
        ordered = sorted(
            runners,
            key=lambda r: float(r["anchor_odd"])
        )

        field_size = len(ordered)
        if field_size == 0:
            continue

        for idx, r in enumerate(ordered, start=1):
            fav_rank = idx
            fav_pct = round(idx / field_size, 6)

            inserts.append(
                (
                    day,
                    marketId,
                    int(r["selectionId"]),
                    fav_rank,
                    field_size,
                    fav_pct,
                    _fav_bucket(fav_rank),
                    1 if fav_rank == 1 else 0,
                    1 if fav_rank <= 3 else 0,
                    1 if fav_rank >= 6 else 0,
                )
            )

    # 5️⃣ Persist
    cur.executemany(
        """
        INSERT INTO context_favourite_ordinals (
            day,
            marketId,
            selectionId,
            fav_rank,
            field_size,
            fav_percentile,
            fav_bucket,
            is_favourite,
            is_top_3,
            is_longshot
        )
        VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        inserts,
    )

    con.commit()
    con.close()

    print(f"[fav-ctx] ✅ inserted {len(inserts)} ordinal rows")


# ---------------------------------------------------------------------
# CLI entry
# ---------------------------------------------------------------------
if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()

    rebuild_favourite_ordinals(days=args.days)
