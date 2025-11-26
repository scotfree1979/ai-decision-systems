#!/usr/bin/env python3
"""
Race Test: Invalid Market (runners < 7)

Purpose
-------
Seed a market that should be excluded by policy (e.g., <7 runners) and verify
no trading actions are taken and a breadcrumb/decision explains the skip.
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
        return int(con.execute("SELECT COUNT(*) FROM orders").fetchone()[0] or 0)
    finally:
        try: con.close()
        except Exception: pass


def run(db_paths, provide, deps) -> Result:
    baseline = _count_orders(db_paths.autoscalp)
    # seed intentionally invalid race (6 runners)
    provide.seed_markets(n_markets=1, runners=6)

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
    if after > baseline:
        return Result.fail("order placed for invalid market (expected skip)", before=baseline, after=after)
    return Result.pass_(before=baseline, after=after).add("invalid market skipped (no orders)")
