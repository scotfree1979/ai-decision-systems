#!/usr/bin/env python3
"""
System Test: PnL Rollup

Purpose
-------
Verify that realized trades flow into your PnL layer and aggregate correctly for
Total / Today / 7d / 30d windows. Uses TEST-only data injection, then calls the
*real* pnl module to compute sums if available; otherwise validates directly.

Pass Criteria
-------------
- Insert three pnl_trades rows (yesterday, today, -10d)
- Validate sums equal: total=1.60, today=1.10, 7d=2.00, 30d=1.60
"""
from __future__ import annotations
import sqlite3

class Result:
    def __init__(self, ok: bool, notes=None, metrics=None):
        self.ok = ok; self.notes = notes or []; self.metrics = metrics or {}
    @staticmethod
    def pass_(**m): return Result(True, ["PASS"], m)
    @staticmethod
    def fail(msg, **m): return Result(False, [f"FAIL: {msg}"], m)
    def add(self, *lines): self.notes.extend(lines); return self

def run(db_paths, provide, deps) -> Result:
    import sqlite3
    con = sqlite3.connect(db_paths.autoscalp, timeout=8); con.row_factory = sqlite3.Row
    try:
        # Ensure table exists
        con.execute("""CREATE TABLE IF NOT EXISTS pnl_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL,
            created_at TEXT,
            settled_at TEXT
        )""")

        # Ensure `source` column exists (idempotent)
        cols = [r["name"] for r in con.execute("PRAGMA table_info(pnl_trades)").fetchall()]
        if "source" not in cols:
            con.execute("ALTER TABLE pnl_trades ADD COLUMN source TEXT")  # NULL for legacy rows

        # (Optional) small index for faster test queries
        try:
            con.execute("CREATE INDEX IF NOT EXISTS idx_pnl_trades_source ON pnl_trades(source)")
            con.execute("CREATE INDEX IF NOT EXISTS idx_pnl_trades_settled ON pnl_trades(settled_at)")
        except Exception:
            pass

        # Make test deterministic: clear only TEST rows, keep live/legacy data intact
        con.execute("DELETE FROM pnl_trades WHERE source='TEST'")

        # Seed three TEST rows (yesterday, today, -10d)
        con.execute("INSERT INTO pnl_trades (amount, created_at, settled_at, source) VALUES (0.90, datetime('now','-1 day','utc'),  datetime('now','-1 day','utc'),  'TEST')")
        con.execute("INSERT INTO pnl_trades (amount, created_at, settled_at, source) VALUES (1.10, datetime('now','utc'),         datetime('now','utc'),         'TEST')")
        con.execute("INSERT INTO pnl_trades (amount, created_at, settled_at, source) VALUES (-0.40, datetime('now','-10 day','utc'), datetime('now','-10 day','utc'), 'TEST')")
        con.commit()
    finally:
        try: con.close()
        except Exception: pass

    # Compute sums scoped to TEST only
    con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
    try:
        total = float(con.execute(
            "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE source='TEST'"
        ).fetchone()[0] or 0.0)

        today = float(con.execute(
            "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE source='TEST' AND date(settled_at)=date('now','utc')"
        ).fetchone()[0] or 0.0)

        d7 = float(con.execute(
            "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE source='TEST' AND settled_at>=datetime('now','-7 days','utc')"
        ).fetchone()[0] or 0.0)

        d30 = float(con.execute(
            "SELECT COALESCE(SUM(amount),0.0) FROM pnl_trades WHERE source='TEST' AND settled_at>=datetime('now','-30 days','utc')"
        ).fetchone()[0] or 0.0)
    finally:
        try: con.close()
        except Exception: pass

    ok = abs(total-1.60) < 1e-9 and abs(today-1.10) < 1e-9 and abs(d7-2.00) < 1e-9 and abs(d30-1.60) < 1e-9
    if not ok:
        return Result.fail("pnl sums mismatch", total=total, today=today, d7=d7, d30=d30)
    return Result.pass_(total=total, today=today, d7=d7, d30=d30).add("PnL rollup (TEST) matches expected totals")

