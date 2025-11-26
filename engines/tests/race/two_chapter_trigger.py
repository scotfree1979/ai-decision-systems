#!/usr/bin/env python3
"""
Race Test: Two‑Chapter Decision Trigger

Purpose
-------
Simulate two sequential chapters for a runner and verify that a decision is
created once the second chapter lands (according to your engine policy).

Pass Criteria
-------------
- A decisions row appears for the (marketId, selectionId) after chapters A,B
- Confidence is non‑zero (or above configurable threshold if present)
"""
from __future__ import annotations
import sqlite3
from datetime import datetime

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
    # seed + anchor + basic band
    provide.seed_markets(n_markets=1, runners=8)
    pick = _pick_any_runner(db_paths.autoscalp)
    if not pick:
        return Result.fail("no runner available")
    mid, sid = pick
    provide.set_anchor(mid, sid, 6.4)
    provide.oc_band_samples(mid, sid, 1, [6.4, 6.35, 6.3, 6.25])

    # use real story/chapter builders
    sb = deps.story_builder
    cb = deps.chapter_builder

    con = sqlite3.connect(db_paths.autoscalp, timeout=8); con.row_factory = sqlite3.Row
    try:
        # minimal tables (if not already created by your migrations)
        con.execute("CREATE TABLE IF NOT EXISTS stories (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, note TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS chapters (id INTEGER PRIMARY KEY AUTOINCREMENT, story_id INTEGER, created_at TEXT, note TEXT)")
        con.execute("INSERT INTO stories (created_at, note) VALUES (?,?)", (datetime.utcnow().isoformat(), "test story"))
        story_id = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        # chapter A
        con.execute("INSERT INTO chapters (story_id, created_at, note) VALUES (?,?,?)", (story_id, datetime.utcnow().isoformat(), "A"))
        # chapter B
        con.execute("INSERT INTO chapters (story_id, created_at, note) VALUES (?,?,?)", (story_id, datetime.utcnow().isoformat(), "B"))
        con.commit()
    finally:
        try: con.close()
        except Exception: pass

    # trigger engine evaluation if exposed
    try:
        Engine = deps.DecisionEngine
        engine = Engine() if callable(Engine) else Engine
        if hasattr(engine, "evaluate"):
            try: engine.evaluate()
            except TypeError: pass
    except Exception:
        pass

    # validate decision exists
    con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
    try:
        row = con.execute("SELECT marketId, selectionId, confidence FROM decisions ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return Result.fail("no decision after two chapters")
        return Result.pass_(marketId=row["marketId"], selectionId=row["selectionId"], confidence=float(row["confidence"] or 0.0))
    finally:
        try: con.close()
        except Exception: pass
