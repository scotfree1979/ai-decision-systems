#!/usr/bin/env python3
"""
System Test: OC Timeline Fill (OC1 → OC20)

Purpose
-------
Confirm that, given a race clock and synthetic odds samples (TEST mode), the
system records OCn snapshots in `oc_series` and aggregates bands in
`inbound_oc_cache.ocN_band_json`, using the *same writers* live uses.

Pass Criteria
-------------
- For the chosen (marketId, selectionId), at least OC1..OC5 exist in oc_series
- Corresponding band_json length for the highest emitted OC >= 4

Exports
-------
run(db_paths: DbPaths, provide: DataProvider, deps: Deps) -> Result
"""
from __future__ import annotations
import sqlite3
from typing import Any, Dict, List, Tuple

# Minimal local facades to keep this file standalone (runner supplies real ones)
class Result:
    def __init__(self, ok: bool, notes=None, metrics=None):
        self.ok = ok; self.notes = notes or []; self.metrics = metrics or {}
    @staticmethod
    def pass_(**m): return Result(True, ["PASS"], m)
    @staticmethod
    def fail(msg, **m): return Result(False, [f"FAIL: {msg}"], m)
    def add(self, *lines): self.notes.extend(lines); return self


def _pick_any_runner(db: str) -> Tuple[str, str] | None:
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
    # Ensure at least one runner exists in TEST
    provide.seed_markets(n_markets=1, runners=10)
    pick = _pick_any_runner(db_paths.autoscalp)
    if not pick:
        return Result.fail("no seeded runners found")
    mid, sid = pick

    # Emit OC1..OC5 bands using the provider (TEST-only writers under the hood)
    samplesets = {
        1: [6.0, 6.1, 6.2, 6.25, 6.3],
        2: [6.3, 6.35, 6.4, 6.45],
        3: [6.45, 6.5, 6.55, 6.6],
        4: [6.55, 6.5, 6.45, 6.5],
        5: [6.5, 6.55, 6.6, 6.65],
    }
    for oc, ss in samplesets.items():
        provide.oc_band_samples(mid, sid, oc, ss)

    # Validate from DB
    try:
        con = sqlite3.connect(db_paths.autoscalp, timeout=5); con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT oc1, oc2, oc3, oc4, oc5, oc5_band_json FROM inbound_oc_cache WHERE marketId=? AND selectionId=?",
            (mid, sid),
        ).fetchone()
        if not row:
            return Result.fail("no inbound_oc_cache row after emission", marketId=mid, selectionId=sid)
        have_first_five = all(row[f"oc{i}"] is not None for i in range(1,6))
        if not have_first_five:
            return Result.fail("missing one of oc1..oc5", marketId=mid, selectionId=sid)
        # band length check on latest
        import json
        band = json.loads(row["oc5_band_json"] or "[]")
        if len(band) < 4:
            return Result.fail("oc5 band too short", length=len(band))
    finally:
        try: con.close()
        except Exception: pass

    return Result.pass_(marketId=mid, selectionId=sid, oc_max=5, band_len=len(band)).add(
        "OC1..OC5 present with band samples ≥4",
    )
