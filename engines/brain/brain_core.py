#!/usr/bin/env python3
"""
brain_core.py — AutoScalp Brain Integration Layer (Phase 7.7)
--------------------------------------------------------------
• Reads current Mastery / River / Forest states
• Computes coherence, entropy, and divergence
• Persists snapshot into mastery_v7.db → brain_prints
"""

import sqlite3, json, math
from datetime import datetime, timezone
from engines.config_paths import autoscalp_db

BRAIN_DB = "data/mastery_v7.db"

def _connect_brain():
    con = sqlite3.connect(BRAIN_DB)
    con.row_factory = sqlite3.Row
    return con

def _entropy(values):
    """Shannon entropy over normalised probabilities."""
    total = sum(values)
    if total <= 0: return 0.0
    probs = [v/total for v in values if v > 0]
    return -sum(p*math.log2(p) for p in probs)

def record_brain_state(mean_bucket, river_mean, forest_mean, coherence, entropy, divergence=None, notes=None):
    """Append a brain_print row after each training cycle."""
    con = _connect_brain()
    con.execute("""
        CREATE TABLE IF NOT EXISTS brain_prints(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recorded_at TEXT DEFAULT (datetime('now','utc')),
            mean_bucket REAL, river_mean REAL, forest_mean REAL,
            coherence REAL, entropy REAL, divergence REAL, notes TEXT
        )
    """)
    con.execute("""
        INSERT INTO brain_prints(mean_bucket, river_mean, forest_mean, coherence, entropy, divergence, notes)
        VALUES(?,?,?,?,?,?,?)
    """, (mean_bucket, river_mean, forest_mean, coherence, entropy, divergence, notes))
    con.commit(); con.close()
    print(f"[brain] 🧠 state recorded — mean_bucket={mean_bucket:.4f} coherence={coherence:.4f}")

def summarise_brain_history(limit=10):
    """Print recent brain states for verification."""
    con = _connect_brain()
    rows = con.execute("SELECT * FROM brain_prints ORDER BY recorded_at DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    for r in rows:
        print(f"{r['recorded_at']} | mean={r['mean_bucket']:.4f} coh={r['coherence']:.4f} ent={r['entropy']:.4f}")
