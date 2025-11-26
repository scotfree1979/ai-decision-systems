#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
engines/intelligence_v7_infer.py
───────────────────────────────────────────────
Inferred Form Engine — self-learning v7 Intelligence

Reads live & historical odds data to infer each runner’s
in-race positional curve (25/50/75/100%) and normalises it
against evolving bucket priors derived from Blueprints +
Playbooks.

Usage:
    python3 engines/intelligence_v7_infer.py
"""

import os, sys, sqlite3, datetime
from statistics import median
from collections import defaultdict

# --- repo-root shim ----------------------------------------------------------
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_here, "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

# --- imports ----------------------------------------------------------
from engines.config_paths import bets_db, autoscalp_db
from engines.config_paths import q_retry as _q

# --- config -----------------------------------------------------------
TODAY = datetime.date.today().isoformat()
PHASES = [25, 50, 75, 100]
BUCKETS = [
    ("FRONT", 1.01, 1.99),
    ("CONTENDER", 2.00, 3.99),
    ("MID", 4.00, 7.99),
    ("OUTSIDER", 8.00, 14.99),
    ("LONG", 15.00, 1000.0),
]

# --- helpers ----------------------------------------------------------
def _bucket_for_odds(o: float) -> str:
    for name, lo, hi in BUCKETS:
        if lo <= o <= hi:
            return name
    return "LONG"

def _conn(path: str) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con

# --- ensure tables ----------------------------------------------------
def _ensure_schema(con: sqlite3.Connection):
    _q(con, """
        CREATE TABLE IF NOT EXISTS v7_intelligence_inferred(
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            anchor_odd REAL,
            bucket TEXT,
            pos_25 TEXT, pos_50 TEXT, pos_75 TEXT, pos_100 TEXT,
            delta_25 REAL, delta_50 REAL, delta_75 REAL, delta_100 REAL,
            outcome TEXT,
            PRIMARY KEY(day, marketId, selectionId)
        )
    """)
    _q(con, """
        CREATE TABLE IF NOT EXISTS v7_intelligence_priors(
            bucket TEXT,
            phase INTEGER,
            median_drift REAL,
            variance REAL,
            updated_at TEXT,
            PRIMARY KEY(bucket, phase)
        )
    """)
    con.commit()

# --- step 1: build per-runner inference -------------------------------------
def build_inferred():
    bets_con = _conn(bets_db())
    oc_con   = _conn(autoscalp_db())

    oc_con.row_factory = sqlite3.Row
    bets_con.row_factory = sqlite3.Row

    # gather anchor odds
    anchors = {
        (r["marketId"], r["selectionId"]): float(r["anchor_odd"] or 0.0)
        for r in _q(bets_con, """
            SELECT marketId, selectionId, anchor_odd
              FROM bets
             WHERE anchor_odd IS NOT NULL
               AND date(marketStartTime)=date('now','utc')
        """).fetchall()
    }

    # gather final outcomes from playbooks if available
    outcomes = {}
    try:
        rows = _q(oc_con, """
            SELECT marketId, selectionId,
                   CASE WHEN pnl>0 THEN 'WIN'
                        WHEN pnl<0 THEN 'LOSS'
                        ELSE 'NEUTRAL' END AS outcome
              FROM playbooks
             WHERE date(day)=date('now','utc')
        """).fetchall()
        for r in rows:
            outcomes[(r["marketId"], r["selectionId"])] = r["outcome"]
    except Exception:
        pass

    # gather odds timeline (schema-verified: inbound_oc_cache.oc1…oc20)
    rows = _q(oc_con, """
        SELECT marketId, selectionId,
               oc1, oc5, oc10, oc15, oc20
          FROM inbound_oc_cache
         WHERE oc1 IS NOT NULL
    """).fetchall()

    by_runner = {}
    for r in rows:
        timeline = [
            float(r["oc1"] or 0),
            float(r["oc5"] or 0),
            float(r["oc10"] or 0),
            float(r["oc15"] or 0),
            float(r["oc20"] or 0),
        ]
        by_runner[(r["marketId"], r["selectionId"])] = timeline


    con = _conn(autoscalp_db())
    _ensure_schema(con)

    inserted = 0
    for (mid, sid), timeline in by_runner.items():
        if (mid, sid) not in anchors:
            continue
        anchor = anchors[(mid, sid)]
        if anchor <= 0:
            continue
        bucket = _bucket_for_odds(anchor)
        odds = [x[1] for x in timeline]
        n = len(odds)
        if n < 4:
            continue

        def _slice(k):
            idx = int(n * (k/100))
            return odds[min(idx, n-1)]

        deltas = {p: _slice(p)/anchor for p in PHASES}
        positions = {}
        for p, d in deltas.items():
            if d < 0.6: pos="LEADING"
            elif d < 0.9: pos="PROMINENT"
            elif d < 1.1: pos="HOLDING"
            elif d < 2.0: pos="PRESSURED"
            else: pos="TRAILING"
            positions[p]=pos

        outc = outcomes.get((mid,sid),"?")
        _q(con, """
            INSERT OR REPLACE INTO v7_intelligence_inferred
            (day,marketId,selectionId,anchor_odd,bucket,
             pos_25,pos_50,pos_75,pos_100,
             delta_25,delta_50,delta_75,delta_100,outcome)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            TODAY, mid, sid, anchor, bucket,
            positions[25], positions[50], positions[75], positions[100],
            deltas[25], deltas[50], deltas[75], deltas[100], outc
        ))
        inserted += 1

    con.commit()
    con.close()
    print(f"[v7] inferred {inserted} runners")

# --- step 2: update rolling priors ------------------------------------------
def update_priors():
    con = _conn(autoscalp_db())
    _ensure_schema(con)
    priors = defaultdict(lambda: defaultdict(list))
    rows = _q(con, """
        SELECT bucket, delta_25, delta_50, delta_75, delta_100
          FROM v7_intelligence_inferred
         WHERE date(day)>=date('now','-90 day')
    """).fetchall()
    for r in rows:
        for p in PHASES:
            priors[r["bucket"]][p].append(float(r[f"delta_{p}"] or 0))
    for bucket, phases in priors.items():
        for p, vals in phases.items():
            if not vals:
                continue
            med = median(vals)
            var = sum((x-med)**2 for x in vals)/len(vals)
            _q(con, """
                INSERT OR REPLACE INTO v7_intelligence_priors
                (bucket,phase,median_drift,variance,updated_at)
                VALUES (?,?,?,?,datetime('now','utc'))
            """, (bucket,p,med,var))
    con.commit()
    con.close()
    print(f"[v7] updated priors for {len(priors)} buckets")

# --- entrypoint --------------------------------------------------------------
if __name__ == "__main__":
    print("=== v7 Intelligence Inference ===")
    build_inferred()
    update_priors()
    print("✅ v7 Intelligence Layer refreshed")
