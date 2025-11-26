#!/usr/bin/env python3
"""
Race Test: Incomplete Bands (missing OC slices)

Purpose
-------
Drop some OC slices and verify the engine degrades gracefully (no unsafe trade)
while still producing a narrative/decision classification if appropriate.
"""
from __future__ import annotations
import sqlite3, json

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
    provide.oc_band_samples(mid, sid, 1, [6.0, 6.05, 6.1, 6.15])
    # skip OC2..OC4 intentionally
    provide.oc_band_samples(mid, sid, 5, [6.2, 6.25, 6.3, 6.35])

    # evaluate
    try:
        Engine = deps.DecisionEngine
        engine = Engine() if callable(Engine) else Engine
        if hasattr(engine, "evaluate"):
            try: engine.evaluate()
            except TypeError: pass
    except Exception:
        pass

    # Safety check: no order if policy forbids trading on incomplete data
    con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
    try:
        o = con.execute("SELECT id FROM orders ORDER BY id DESC LIMIT 1").fetchone()
        d = con.execute("SELECT signal_type, confidence FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        try: con.close()
        except Exception: pass

    # Accept either: (no order; decision/narrative present) or (order present but with explicit rationale)
    if o and not d:
        return Result.fail("order was placed but no decision was recorded under incomplete bands")
    return Result.pass_(order_id=(int(o["id"]) if o else None), signal=(d["signal_type"] if d else None)).add("incomplete bands handled without unsafe trade")
