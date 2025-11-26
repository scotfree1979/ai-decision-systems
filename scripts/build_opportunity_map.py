#!/usr/bin/env python3
"""
scripts/build_opportunity_map.py
─────────────────────────────────────────────────────────────────────────────
Builds opportunity_map in mastery_v7.db from tick sequences in inbound_oc_cache.
Quantifies drift, reversals, micro-scalp zones, and expected trade swings.
"""

import sqlite3, time, math, os, sys
from engines.config_paths import connect_mastery_v7_cache
con_out = connect_mastery_v7_cache()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _safe_float(x):
    try: return float(x)
    except Exception: return 0.0

def _tick_size(o: float) -> float:
    o = float(o)
    if o < 2: return 0.01
    if o < 3: return 0.02
    if o < 4: return 0.05
    if o < 6: return 0.1
    if o < 10: return 0.2
    if o < 20: return 0.5
    if o < 30: return 1.0
    if o < 50: return 2.0
    if o < 100: return 5.0
    return 10.0

# ──────────────────────────────────────────────────────────────────────────────
# Schema patch
# ──────────────────────────────────────────────────────────────────────────────
def ensure_opportunity_map_schema(con):
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS opportunity_map(
            marketId TEXT,
            selectionId TEXT,
            ticks_up INTEGER,
            ticks_down INTEGER,
            net_ticks INTEGER,
            oscillations INTEGER,
            avg_move_ticks REAL,
            corridor_ticks REAL,
            micro_zone_flag INTEGER,
            trend_bias INTEGER,
            expected_swings INTEGER,
            expected_ticks_total INTEGER,
            created_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)
    con.commit()

# ──────────────────────────────────────────────────────────────────────────────
# Core logic
# ──────────────────────────────────────────────────────────────────────────────
def build_opportunity_map():
    src_path = autoscalp_db()
    dst_path = mastery_v7_db()
    print(f"[OPP] reading={src_path}")
    print(f"[OPP] writing={dst_path}")

    con_src = sqlite3.connect(src_path)
    con_src.row_factory = sqlite3.Row
    con_dst = sqlite3.connect(dst_path)
    con_dst.row_factory = sqlite3.Row
    ensure_opportunity_map_schema(con_dst)
    cur_out = con_dst.cursor()

    rows = con_src.execute("""
        SELECT marketId, selectionId,
               oc1, oc2, oc3, oc4, oc5, oc6, oc7, oc8, oc9,
               oc10, oc11, oc12, oc13, oc14, oc15, oc16, oc17, oc18, oc19, oc20
          FROM inbound_oc_cache
         WHERE marketId IS NOT NULL AND selectionId IS NOT NULL
    """).fetchall()

    print(f"[OPP] processing {len(rows)} runners …")
    t0 = time.time()
    inserted = 0

    for r in rows:
        seq = [_safe_float(r[f"oc{i}"]) for i in range(1, 21) if r[f"oc{i}"] is not None]
        seq = [x for x in seq if x > 0]
        if len(seq) < 3:
            continue

        tick_moves = [
            round((seq[i+1] - seq[i]) / max(1e-9, _tick_size(seq[i])))
            for i in range(len(seq)-1)
        ]
        if not tick_moves:
            continue

        up  = sum(t for t in tick_moves if t > 0)
        dn  = sum(-t for t in tick_moves if t < 0)
        net = up - dn
        osc = sum(1 for i in range(1, len(tick_moves)) if tick_moves[i] * tick_moves[i-1] < 0)
        avg_move = sum(abs(t) for t in tick_moves) / max(1, len(tick_moves))
        corridor = (max(seq) - min(seq)) / _tick_size(seq[0])

        micro_flag = 1 if abs(net) <= 2 and osc >= 4 and avg_move >= 1 else 0
        trend_bias = 1 if abs(net) > 4 and osc < 3 else 0

        expected_swings = int(corridor / max(1, avg_move))
        expected_ticks_total = expected_swings

        cur_out.execute("""
            INSERT INTO opportunity_map(
              marketId, selectionId, ticks_up, ticks_down, net_ticks,
              oscillations, avg_move_ticks, corridor_ticks, micro_zone_flag,
              trend_bias, expected_swings, expected_ticks_total
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            r["marketId"], r["selectionId"], up, dn, net,
            osc, avg_move, corridor, micro_flag, trend_bias,
            expected_swings, expected_ticks_total
        ))
        inserted += 1

    con_dst.commit()
    print(f"[OPP] ✅ inserted {inserted} rows in {time.time()-t0:.2f}s")

    # quick sample summary
    smry = con_dst.execute("""
        SELECT COUNT(*) AS n, SUM(micro_zone_flag) AS micro_zones,
               AVG(oscillations) AS avg_osc, AVG(avg_move_ticks) AS avg_move
          FROM opportunity_map
    """).fetchone()
    print(f"[SUMMARY] total={smry['n']}  micro_zones={smry['micro_zones']}  "
          f"avg_osc={smry['avg_osc']:.2f}  avg_move={smry['avg_move']:.2f}")

    con_src.close()
    con_dst.close()


# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    build_opportunity_map()
