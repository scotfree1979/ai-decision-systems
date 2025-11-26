# indicators/opportunities.py
from __future__ import annotations
import sqlite3, json, datetime
from typing import Any, Dict

from engines.mastery import event_sink # we will call event_sink.on_decision
from engines.config_paths import auto_conn as _auto_conn, q_retry as _q_retry, autoscalp_db


# === PATCH START ===
# 📍 TARGET: engines/indicators/opportunities.py:_adb_rw
# 🔎 SEARCH: def _adb_rw(
# 📆 PATCHED: 2025-11-21 — DAL-safe writer

from engines.config_paths import auto_conn as _auto_conn

def _adb_rw() -> sqlite3.Connection:
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    return con
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/indicators/opportunities.py:ensure_schema
# 🔎 SEARCH: def ensure_schema(
# 📆 PATCHED: 2025-11-21 — DAL writer

def ensure_schema() -> None:
    """Ensure indicators_opportunities exists."""
    con = _auto_conn(rw=True)
    try:
        _q_retry(con, """
        CREATE TABLE IF NOT EXISTS indicators_opportunities(
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            runner_name TEXT,
            opportunities INTEGER DEFAULT 0,
            taken INTEGER DEFAULT 0,
            conversion INTEGER DEFAULT 0,
            last_ts TEXT,
            PRIMARY KEY(day, marketId, selectionId)
        )
        """)
        con.commit()
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/indicators/opportunities.py:update_opportunities
# 🔎 SEARCH: def update_opportunities(
# 📆 PATCHED: 2025-11-21 — all DB IO routed through DAL

from engines.config_paths import auto_conn as _auto_conn

def update_opportunities(day: str | None = None) -> None:
    """
    For each runner today:
      - Count opportunities from oc_series
      - Count taken (parents)
      - Count conversion (parent→child match)
      - Upsert → indicators_opportunities
      - Emit event_sink.on_decision
    """
    ensure_schema()
    if not day:
        day = datetime.date.today().isoformat()

    con = _auto_conn(rw=True)     # writer for indicators_opportunities
    con.row_factory = sqlite3.Row
    try:
        # 1) distinct runners from today's oc_series (RO safe)
        runners = _q_retry(con, """
            SELECT DISTINCT marketId, selectionId
            FROM oc_series
            WHERE date(snapshot_ts)=?
        """, (day,)).fetchall()

        for r in runners:
            mid, sid = r["marketId"], r["selectionId"]

            # --- opportunities (tick-to-tick moves) ---
            snaps = _q_retry(con, """
                SELECT oc_price
                  FROM oc_series
                 WHERE marketId=? AND selectionId=? AND date(snapshot_ts)=?
                 ORDER BY datetime(snapshot_ts)
            """, (mid, sid, day)).fetchall()
            opps = 0
            last_price = None
            for s in snaps:
                price = s["oc_price"]
                if last_price is not None and price and last_price:
                    if float(price) != float(last_price):
                        opps += 1
                last_price = price

            # --- taken (parents placed) ---
            taken = _q_retry(con, """
                SELECT COUNT(*) AS n
                  FROM orders
                 WHERE date(opened_at)=?
                   AND marketId=? AND selectionId=?
                   AND role='PARENT'
            """, (day, mid, sid)).fetchone()["n"]

            # --- conversion (parent→child) ---
            conv = _q_retry(con, """
                SELECT COUNT(DISTINCT o1.id) AS n
                FROM orders o1
                JOIN orders o2 ON o2.parent_id=o1.id
                WHERE date(o1.opened_at)=?
                  AND o1.marketId=? AND o1.selectionId=?
                  AND o2.role='CHILD'
            """, (day, mid, sid)).fetchone()["n"]

            # runner name lookup (RO OK)
            rn = _q_retry(con, """
                SELECT runner_name
                  FROM dashboard_runners
                 WHERE marketId=? AND selectionId=? LIMIT 1
            """, (mid, sid)).fetchone()
            runner_name = rn["runner_name"] if rn else "?"

            # --- UPSERT ---
            _q_retry(con, """
                INSERT INTO indicators_opportunities(
                    day, marketId, selectionId, runner_name,
                    opportunities, taken, conversion, last_ts
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','utc'))
                ON CONFLICT(day, marketId, selectionId)
                DO UPDATE SET
                    runner_name=excluded.runner_name,
                    opportunities=excluded.opportunities,
                    taken=excluded.taken,
                    conversion=excluded.conversion,
                    last_ts=datetime('now','utc')
            """, (day, mid, sid, runner_name, opps, taken, conv))
            con.commit()

            # --- EventSync emit ---
            payload: Dict[str, Any] = {
                "type": "opportunity",
                "day": day,
                "marketId": mid,
                "selectionId": sid,
                "runner": runner_name,
                "opportunities": opps,
                "taken": taken,
                "taken_pct": (taken / opps * 100.0) if opps else 0.0,
                "conversion": conv,
                "conversion_pct": (conv / taken * 100.0) if taken else 0.0,
            }
            event_sink.on_decision(payload)

    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===

