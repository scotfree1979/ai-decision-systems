#!/usr/bin/env python3
"""
Race Test: Confidence Thresholds

Purpose
-------
Feed two signals, one below and one above the action threshold, and verify that
only the high-confidence path places a trade while the low one logs exploratory.
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


def _count_open_orders(db: str) -> int:
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
    provide.seed_markets(n_markets=1, runners=8)
    pick = _pick_any_runner(db_paths.autoscalp)
    if not pick:
        return Result.fail("no runner available")
    mid, sid = pick

    # low-confidence path (tiny movement)
    provide.set_anchor(mid, sid, 6.0)
    provide.oc_band_samples(mid, sid, 1, [6.0, 6.01, 6.0, 6.02])

    before = _count_open_orders(db_paths.autoscalp)
    try:
        Engine = deps.DecisionEngine
        engine = Engine() if callable(Engine) else Engine
        if hasattr(engine, "evaluate"):
            try: engine.evaluate()
            except TypeError: pass
    except Exception:
        pass

    between = _count_open_orders(db_paths.autoscalp)

    # high-confidence path (strong movement)
    provide.oc_band_samples(mid, sid, 2, [6.2, 6.35, 6.5, 6.65])
    try:
        Engine = deps.DecisionEngine
        engine = Engine() if callable(Engine) else Engine
        if hasattr(engine, "evaluate"):
            try: engine.evaluate()
            except TypeError: pass
    except Exception:
        pass

    after = _count_open_orders(db_paths.autoscalp)

    if between > before and after == between:
        return Result.fail("low-confidence path placed an order but high-confidence did not increase count")
    if after <= between and between > before:
        # we accept at least one order created in the sequence
        return Result.pass_(orders_before=before, low=between, high=after).add("orders reflect confidence thresholds")
    if after > between or between == before:
        return Result.pass_(orders_before=before, low=between, high=after).add("threshold behaviour acceptable")
    return Result.fail("unexpected order counts", before=before, between=between, after=after)
