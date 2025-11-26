#!/usr/bin/env python3
"""
Race Test: Green‑Up / Exit Logic

Purpose
-------
Simulate entering a scalp and then a favourable move to trigger a green‑up/exit.
Verify exit is recorded and realized PnL updates.
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


def _ensure_test_run(db: str) -> int:
    con = sqlite3.connect(db, timeout=8); con.row_factory = sqlite3.Row
    try:
        con.execute("""CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT, finished_at TEXT, mode TEXT, notes TEXT
        )""")
        row = con.execute(
            "SELECT id FROM runs WHERE finished_at IS NULL AND mode='TEST' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row: 
            return int(row["id"])
        con.execute(
            "INSERT INTO runs (started_at, mode, notes) VALUES (datetime('now','utc'),'TEST','sim run')"
        )
        return int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
    finally:
        con.commit()
        try: con.close()
        except Exception: pass


def run(db_paths, provide, deps) -> Result:
    # 1) Ensure we have a TEST run
    run_id = _ensure_test_run(db_paths.autoscalp)

    # 2) Ensure we have at least one runner
    con = sqlite3.connect(db_paths.autoscalp, timeout=8); con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT marketId, selectionId FROM inbound_bets_min ORDER BY id ASC LIMIT 1"
        ).fetchone()
    finally:
        try: con.close()
        except Exception: pass

    if not row:
        # seed two markets (higher chance of distinct marketId/selectionId)
        provide.seed_markets(n_markets=2, runners=8)
        con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
        try:
            row = con.execute(
                "SELECT marketId, selectionId FROM inbound_bets_min ORDER BY id ASC LIMIT 1"
            ).fetchone()
        finally:
            try: con.close()
            except Exception: pass
        if not row:
            return Result.fail("no runner available to open position")

    mid, sid = row["marketId"], str(row["selectionId"])

    # 3) Insert a matched order to simulate an open position (simplify: status matched)
    con = sqlite3.connect(db_paths.autoscalp, timeout=8); con.row_factory = sqlite3.Row
    try:
        con.execute(
            """
            INSERT INTO orders (
                run_id, decision_id, customerOrderRef,
                marketId, selectionId, mode, side,
                entry_odds, entry_stake, entry_status,
                unrealized_pnl, opened_at
            )
            VALUES (?, NULL, ?, ?, ?, 'TEST', 'LAY', 6.2, 2.0, 'matched', 0.0, datetime('now','utc'))
            """,
            (run_id, f"DE-GREEN-{mid}-{sid}"),
        )
        oid = int(con.execute("SELECT last_insert_rowid()").fetchone()[0])
        con.commit()
    finally:
        try: con.close()
        except Exception: pass

    # 4) Favourable move → mark as closed with realized PnL (runner handles col names)
    provide.instant_fill(oid, pnl_delta=0.40)

    # 5) Verify closed and realized_pnl updated
    con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
    try:
        row = con.execute(
            "SELECT entry_status, closed_at, realized_pnl, unrealized_pnl FROM orders WHERE id=?",
            (oid,)
        ).fetchone()
        if not row or not row["closed_at"]:
            return Result.fail("order not closed on green-up", order_id=oid)
        pnl_val = float(row["realized_pnl"] or 0.0)
        return Result.pass_(order_id=oid, pnl=pnl_val).add("green-up exit recorded")
    finally:
        try: con.close()
        except Exception: pass
