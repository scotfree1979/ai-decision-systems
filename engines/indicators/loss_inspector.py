# engines/indicators/loss_inspector.py
from __future__ import annotations
import sqlite3, json, datetime
from engines.config_paths import autoscalp_db, q_retry as _q

DB_PATH = autoscalp_db()

# === PATCH START ===
# 📍 TARGET: engines/indicators/loss_inspector.py:_adb_rw
# 🔎 SEARCH: def _adb_rw(
# 📆 PATCHED: 2025-11-21 — DAL-safe writer (WAL, timeout, no RO paths)

def _adb_rw(timeout: float = 8.0):
    """
    Writable connector into AUTOSCALP_GUI (autoscalp_gui.db),
    using the DAL auto_conn(rw=True) path.
    """
    from engines.config_paths import auto_conn as _auto_conn
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=8000;")
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return con
# === PATCH END ===


def _ensure_table(con: sqlite3.Connection):
    _q(con, """
    CREATE TABLE IF NOT EXISTS loss_tags(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        trade_id TEXT NOT NULL,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        runner_name TEXT,
        tag TEXT NOT NULL,
        context_json TEXT,
        created_at TEXT DEFAULT (datetime('now','utc'))
    )
    """)
    con.commit()

# === PATCH START ===
# 📍 TARGET: engines/indicators/loss_inspector.py:tag_loss
# 🔎 SEARCH: def tag_loss(
# 📆 PATCHED: 2025-11-21 — enforce DAL writer + safe commits

def tag_loss(day: str, trade_id: str, marketId: str, selectionId: str,
             runner_name: str, tag: str, context: dict|None=None):
    """Insert a tagged loss event."""
    con = _adb_rw()
    try:
        _ensure_table(con)
        con.execute("""
            INSERT INTO loss_tags(day, trade_id, marketId, selectionId, runner_name, tag, context_json)
            VALUES (?,?,?,?,?,?,?)
        """, (
            day,
            str(trade_id),
            str(marketId),
            str(selectionId),
            runner_name,
            tag,
            json.dumps(context or {}, separators=(",",":"))
        ))
        con.commit()
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===


# ────────────────────────────────────────────────
# Inspector rules (stubs for now — to be filled)
# ────────────────────────────────────────────────
def inspect_trade(row: sqlite3.Row) -> list[dict]:
    """
    Inspect a single trade row and return a list of tags.
    Each tag = {"tag": str, "context": dict}
    """
    tags: list[dict] = []

    # Placement errors
    # if row["placed_at"] very close to off → late entry
    # if liquidity < threshold → low_liquidity_entry
    # CAP breach etc.
    # (stub for now)
    
    # Hedge failures
    # if no child or unmatched → no_hedge
    # if stoploss missing → stoploss_not_triggered
    # (stub)

    # Market conditions
    # if vol spread wide → volatile_tape
    # if in_top6=0 → runner_ignored
    # (stub)

    # P&L outcome
    # if abs(net_pl) > X% bank → large_loss
    # (stub)

    return tags

# === PATCH START ===
# 📍 TARGET: engines/indicators/loss_inspector.py:run_loss_inspector
# 🔎 SEARCH: def run_loss_inspector(
# 📆 PATCHED: 2025-11-21 — DAL writer, uniform row_factory, safe close

def run_loss_inspector(day: str, limit: int = 500):
    """
    Go through trades for a given day, tag losses, and store in loss_tags.
    """
    con = _adb_rw()
    con.row_factory = sqlite3.Row
    _ensure_table(con)
    try:
        rows = _q(con, """
            SELECT o.id AS trade_id, o.marketId, o.selectionId, o.horse_name AS runner_name,
                   o.placed_at, o.net_pl, o.side
              FROM orders o
             WHERE date(o.placed_at)=?
               AND o.role='PARENT'
               AND o.net_pl < 0
             ORDER BY datetime(o.placed_at) ASC
             LIMIT ?
        """, (day, limit)).fetchall() or []

        for r in rows:
            tags = inspect_trade(r)
            for t in tags:
                tag_loss(
                    day,
                    r["trade_id"],
                    r["marketId"],
                    r["selectionId"],
                    r["runner_name"],
                    t["tag"],
                    t.get("context"),
                )
    finally:
        try: con.close()
        except Exception: pass
# === PATCH END ===

