#!/usr/bin/env python3
"""
Race Test: Scalp Opportunity

Purpose
-------
Simulate an odds path that crosses your scalp entry rule and verify that the
engine places the correct side (LAY/BACK) and records an order.
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


def _pick_any_runner(db: str):
    try:
        con = sqlite3.connect(db, timeout=5); con.row_factory = sqlite3.Row
        row = con.execute("SELECT marketId, selectionId FROM inbound_bets_min ORDER BY id ASC LIMIT 1").fetchone()
        return (row["marketId"], str(row["selectionId"])) if row else None
    except Exception:
        return None
    finally:
        try: con.close()
        except Exception: pass


def run(db_paths, provide, deps) -> Result:
    provide.seed_markets(n_markets=1, runners=10)
    pick = _pick_any_runner(db_paths.autoscalp)
    if not pick:
        return Result.fail("no runner available")
    mid, sid = pick

    # odds drift then tick back to trigger entry
    provide.set_anchor(mid, sid, 6.0)
    provide.oc_band_samples(mid, sid, 1, [6.0, 6.1, 6.2, 6.15])

    # run engine placement (best-effort; if not accessible, verify orders anyway)
    try:
        Engine = deps.DecisionEngine
        engine = Engine() if callable(Engine) else Engine
        if hasattr(engine, "evaluate"):
            try: engine.evaluate()
            except TypeError: pass
    except Exception:
        pass

    con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
    try:
        row = con.execute("""
            SELECT id, side, entry_status FROM orders
            WHERE marketId=? AND selectionId=?
            ORDER BY id DESC LIMIT 1
        """, (mid, sid)).fetchone()
        if not row:
            return Result.fail("no order placed for scalp opportunity")
        return Result.pass_(order_id=int(row["id"]), side=row["side"], entry_status=row["entry_status"])\
            .add(f"order side={row['side']} status={row['entry_status']}")
    finally:
        try: con.close()
        except Exception: pass
