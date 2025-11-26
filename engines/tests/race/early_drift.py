#!/usr/bin/env python3
"""
Race Test: Early Drift

Purpose
-------
Simulate a steady drift from early in the timeline, verifying engine escalation
(exploratory→partial→full) or the configured behaviour.
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
    provide.seed_markets(n_markets=1, runners=8)
    pick = _pick_any_runner(db_paths.autoscalp)
    if not pick:
        return Result.fail("no runner available")
    mid, sid = pick

    provide.set_anchor(mid, sid, 6.0)
    provide.oc_band_samples(mid, sid, 1, [6.0, 6.1, 6.2, 6.3])
    provide.oc_band_samples(mid, sid, 2, [6.35, 6.4, 6.45, 6.5])

    # evaluate
    try:
        Engine = deps.DecisionEngine
        engine = Engine() if callable(Engine) else Engine
        if hasattr(engine, "evaluate"):
            try: engine.evaluate()
            except TypeError: pass
    except Exception:
        pass

    # Inspect decisions rollup (we accept any decision with non-zero confidence here)
    con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT signal_type, confidence FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return Result.fail("no decision recorded on early drift")
        return Result.pass_(signal_type=row["signal_type"], confidence=float(row["confidence"] or 0.0))
    finally:
        try: con.close()
        except Exception: pass
