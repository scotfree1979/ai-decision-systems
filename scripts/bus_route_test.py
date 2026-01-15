#!/usr/bin/env python3

import sqlite3
import time
from collections import defaultdict

# ======================================================
# CONFIG
# ======================================================
TOTAL_RUNNERS = 30
TICKS = 30
DB_PATH = "data/autoscalp_gui.db"

ENGINES = ["LEGACY", "MSC_RISK", "MSC_INPLAY", "MSC_EXPLORATORY"]

# ======================================================
# DB SETUP (CLEAN SLATE)
# ======================================================
con = sqlite3.connect(DB_PATH)
cur = con.cursor()

cur.execute("DELETE FROM orders")
con.commit()

print("\n================ TEST #11 START ================\n")

# ======================================================
# SYNTHETIC RUNNERS (SCHEMA SAFE)
# ======================================================
RUNNERS = [
    (100000 + i, 200000 + i)
    for i in range(TOTAL_RUNNERS)
]

# ------------------------------------------------------
# ID COUNTERS (INTEGER — IMPORTANT)
# ------------------------------------------------------
NEXT_ORDER_ID = 1

def next_id():
    global NEXT_ORDER_ID
    nid = NEXT_ORDER_ID
    NEXT_ORDER_ID += 1
    return nid

# ======================================================
# DB HELPERS (INTEGER IDs)
# ======================================================
def insert_parent(engine, mid, sid, matched=True):
    pid = next_id()
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    cur.execute(
        """
        INSERT INTO orders (
            id,
            engine,
            role,
            entry_status,
            side,
            entry_odds,
            entry_stake,
            marketId,
            selectionId,
            mode,
            opened_at
        )
        VALUES (?, ?, 'PARENT', ?, 'BACK', 3.0, 2.0, ?, ?, 'LIVE', ?)
        """,
        (
            pid,
            engine,
            "MATCHED" if matched else "PLACED",
            int(mid),
            int(sid),
            now,
        ),
    )
    return pid


def insert_child(engine, parent_id, matched=False):
    cid = next_id()
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

    cur.execute(
        """
        INSERT INTO orders (
            id,
            engine,
            role,
            entry_status,
            exit_status,
            hedge_of,
            side,
            entry_odds,
            entry_stake,
            mode,
            opened_at
        )
        VALUES (?, ?, 'CHILD', 'MATCHED', ?, ?, 'LAY', 3.0, 2.0, 'LIVE', ?)
        """,
        (
            cid,
            engine,
            "MATCHED" if matched else None,
            parent_id,
            now,
        ),
    )


# ======================================================
# DB-AUTHORITATIVE CTX GATE (BUS-IDENTICAL)
# ======================================================
def ctx_allowed(engine, mid, sid):
    if engine == "LEGACY":
        return True

    row = cur.execute(
        """
        SELECT
            p.id,
            c.exit_status
        FROM orders p
        LEFT JOIN orders c
            ON c.hedge_of = p.id
        WHERE p.engine = ?
          AND p.marketId = ?
          AND p.selectionId = ?
          AND p.role = 'PARENT'
          AND UPPER(p.entry_status) = 'MATCHED'
        LIMIT 1
        """,
        (engine, int(mid), int(sid)),
    ).fetchone()

    if row is None:
        return True

    _, child_exit_status = row
    return bool(child_exit_status and child_exit_status.upper() == "MATCHED")


# ======================================================
# TEST LOOP
# ======================================================
for tick in range(1, TICKS + 1):

    print(f"\n--- TICK {tick} ---")

    ctx_counts = defaultdict(int)

    # LEGACY: exactly 3 per tick
    legacy_slice = RUNNERS[(tick - 1) * 3 : (tick - 1) * 3 + 3]

    for engine in ENGINES:
        if engine == "LEGACY":
            ctx_counts["LEGACY"] = len(legacy_slice)
        else:
            for mid, sid in RUNNERS:
                if ctx_allowed(engine, mid, sid):
                    ctx_counts[engine] += 1

    for eng in ENGINES:
        print(f"{eng:<16}: {ctx_counts[eng]} CTX")

    # --------------------------------------------------
    # SIMULATED LIFECYCLE EVENTS
    # --------------------------------------------------

    if tick == 1:
        for i in range(5):
            mid, sid = RUNNERS[i]
            insert_parent("MSC_EXPLORATORY", mid, sid, matched=True)
        con.commit()
        print("↳ inserted 5 MSC_EXPLORATORY parents")

    if tick == 3:
        rows = cur.execute(
            """
            SELECT id FROM orders
            WHERE engine='MSC_EXPLORATORY'
              AND role='PARENT'
            """
        ).fetchall()

        for (pid,) in rows:
            insert_child("MSC_EXPLORATORY", pid, matched=False)

        con.commit()
        print("↳ created UNMATCHED MSC_EXPLORATORY children")

    if tick == 6:
        cur.execute(
            """
            UPDATE orders
            SET exit_status='MATCHED'
            WHERE role='CHILD'
              AND engine='MSC_EXPLORATORY'
            """
        )
        con.commit()
        print("↳ matched MSC_EXPLORATORY children")

# ======================================================
# CLEANUP
# ======================================================
con.close()

print("\n================ TEST #11 COMPLETE ================\n")
