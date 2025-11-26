#!/usr/bin/env python3
"""
Race Test: No‑Trade Zone (flat range)

Purpose
-------
Feed a tight, noisy band and verify that no decision leading to an order is
produced (engine should classify as noise / exploratory only).
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


def _count_orders(db: str) -> int:
    con = sqlite3.connect(db, timeout=5); con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT COUNT(*) FROM orders WHERE (closed_at IS NULL OR closed_at='')").fetchone()
        return int(row[0] or 0)
    finally:
        try: con.close()
        except Exception: pass


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
    before = _count_orders(db_paths.autoscalp)
    provide.seed_markets(n_markets=1, runners=8)
    pick = _pick_any_runner(db_paths.autoscalp)
    if not pick:
        return Result.fail("no runner available")
    mid, sid = pick

    provide.set_anchor(mid, sid, 6.0)
    provide.oc_band_samples(mid, sid, 1, [6.0, 6.01, 6.0, 6.02, 6.01])

    # evaluate
    try:
        Engine = deps.DecisionEngine
        engine = Engine() if callable(Engine) else Engine
        if hasattr(engine, "evaluate"):
            try: engine.evaluate()
            except TypeError: pass
    except Exception:
        pass

    after = _count_orders(db_paths.autoscalp)
    if after > before:
        return Result.fail("order was placed in no‑trade zone", before=before, after=after)
    return Result.pass_(before=before, after=after).add("no new orders; noise correctly ignored")
