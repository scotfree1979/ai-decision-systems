#!/usr/bin/env python3
import sys, os, time
# ensure repo root on sys.path
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import sqlite3, csv
from datetime import datetime, timedelta, timezone
from engines.config_paths import autoscalp_db

db_path = autoscalp_db()

def open_ro(db_path: str) -> sqlite3.Connection:
    """
    Open autoscalp_gui.db in read-only, safe for concurrent GUI writers (WAL).
    """
    uri = f"file:{db_path}?mode=ro&cache=shared"
    con = sqlite3.connect(uri, uri=True, check_same_thread=False, timeout=30.0)
    con.row_factory = sqlite3.Row
    # read-only, connection-scoped hints (no writes)
    con.execute("PRAGMA busy_timeout=8000")   # wait for readers' turn
    con.execute("PRAGMA temp_store=MEMORY")   # keep temp ops in memory
    con.execute("PRAGMA cache_size=-200000")  # ~200MB page cache if available
    con.execute("PRAGMA query_only=ON")       # belt & braces: no writes allowed
    return con

con = open_ro(db_path)

# Yesterday [start,end)
yday_date = (datetime.now(timezone.utc) - timedelta(days=1)).date()
start = f"{yday_date} 00:00:00"
end   = f"{(yday_date + timedelta(days=1)).isoformat()} 00:00:00"

def export_csv(rows, headers, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.writer(f); w.writerow(headers)
        for r in rows:
            # sqlite3.Row supports dict-like access
            w.writerow([r[h] for h in headers])

def timed(label, fn):
    t0 = time.time()
    rows, headers = fn()
    dt = time.time() - t0
    print(f"[OK] {label}: {len(rows)} rows in {dt:.2f}s")
    return rows, headers

db_path = autoscalp_db()
con = sqlite3.connect(db_path)
con.row_factory = sqlite3.Row
# help SQLite pick better plans


con.execute("PRAGMA temp_store=MEMORY")
con.execute("PRAGMA cache_size=-200000")  # ~200MB if available

# 1) Runner story (anchor/high/low) — FAST: range filter + join for anchor
def q_runner_story():
    # anchor per (mid,sid)
    anchor = con.execute("""
        WITH mins AS (
          SELECT marketId, selectionId, MIN(snapshot_ts) AS ts_min
          FROM oc_series
          WHERE snapshot_ts >= ? AND snapshot_ts < ?
          GROUP BY marketId, selectionId
        )
        SELECT o.marketId, o.selectionId, o.odd AS anchor_odd
        FROM oc_series o
        JOIN mins m
          ON m.marketId=o.marketId
         AND m.selectionId=o.selectionId
         AND m.ts_min=o.snapshot_ts
    """, (start, end)).fetchall()

    # hi/lo per (mid,sid)
    hilo = con.execute("""
        SELECT marketId, selectionId,
               MIN(odd) AS odd_lo,
               MAX(odd) AS odd_hi
        FROM oc_series
        WHERE snapshot_ts >= ? AND snapshot_ts < ?
        GROUP BY marketId, selectionId
    """, (start, end)).fetchall()

    # stitch in SQL (one more join keeps it in-SQL)
    rows = con.execute("""
        WITH anchor AS (
          SELECT o.marketId, o.selectionId, o.odd AS anchor_odd
          FROM oc_series o
          JOIN (
            SELECT marketId, selectionId, MIN(snapshot_ts) AS ts_min
            FROM oc_series
            WHERE snapshot_ts >= ? AND snapshot_ts < ?
            GROUP BY marketId, selectionId
          ) m
            ON m.marketId=o.marketId AND m.selectionId=o.selectionId AND m.ts_min=o.snapshot_ts
        ),
        hilo AS (
          SELECT marketId, selectionId, MIN(odd) AS odd_lo, MAX(odd) AS odd_hi
          FROM oc_series
          WHERE snapshot_ts >= ? AND snapshot_ts < ?
          GROUP BY marketId, selectionId
        )
        SELECT h.marketId, h.selectionId, h.odd_lo, h.odd_hi, a.anchor_odd
        FROM hilo h JOIN anchor a
          ON a.marketId=h.marketId AND a.selectionId=h.selectionId
        ORDER BY h.marketId, CAST(h.selectionId AS INTEGER)
    """, (start, end, start, end)).fetchall()

    return rows, ["marketId","selectionId","odd_lo","odd_hi","anchor_odd"]

# 2) Strategy story (last decision per runner/letter) — FAST: range filter
def q_strategy_story():
    rows = con.execute("""
        WITH last AS (
          SELECT marketId, selectionId, notes AS letter,
                 MAX(datetime(decided_at)) AS last_decided
          FROM decisions
          WHERE decided_at >= ? AND decided_at < ?
          GROUP BY marketId, selectionId, letter
        )
        SELECT l.marketId, l.selectionId, l.letter, l.last_decided,
               COALESCE(json_extract(d.meta_json,'$.why'), d.why) AS reason
        FROM last l
        JOIN decisions d
          ON d.marketId=l.marketId AND d.selectionId=l.selectionId
         AND d.notes=l.letter AND datetime(d.decided_at)=l.last_decided
        ORDER BY l.marketId, CAST(l.selectionId AS INTEGER), l.letter
    """, (start, end)).fetchall()
    return rows, ["marketId","selectionId","letter","last_decided","reason"]

# 3) Scalp summary (counts) — FAST: range filter, no physical 'outcome' column
def q_scalp_summary():
    rows = con.execute("""
        SELECT
          notes AS letter,
          COUNT(*) AS total,
          SUM(CASE WHEN COALESCE(json_extract(meta_json,'$.placement_outcome'),'not_placed')='placed' THEN 1 ELSE 0 END) AS placed,
          SUM(CASE WHEN COALESCE(json_extract(meta_json,'$.placement_outcome'),'not_placed')!='placed' THEN 1 ELSE 0 END) AS not_placed
        FROM decisions
        WHERE decided_at >= ? AND decided_at < ?
        GROUP BY letter
        ORDER BY letter
    """, (start, end)).fetchall()
    return rows, ["letter","total","placed","not_placed"]


# 4) Opportunity story (ticks available) — FAST: reuse anchor + hilo and compute tick deltas
def q_opportunity_story():
    rows = con.execute("""
        WITH anchor AS (
          SELECT o.marketId, o.selectionId, o.odd AS anchor
          FROM oc_series o
          JOIN (
            SELECT marketId, selectionId, MIN(snapshot_ts) AS ts_min
            FROM oc_series
            WHERE snapshot_ts >= ? AND snapshot_ts < ?
            GROUP BY marketId, selectionId
          ) m
            ON m.marketId=o.marketId AND m.selectionId=o.selectionId AND m.ts_min=o.snapshot_ts
        ),
        hilo AS (
          SELECT marketId, selectionId, MIN(odd) AS odd_lo, MAX(odd) AS odd_hi
          FROM oc_series
          WHERE snapshot_ts >= ? AND snapshot_ts < ?
          GROUP BY marketId, selectionId
        )
        SELECT h.marketId, h.selectionId, a.anchor,
               h.odd_lo, h.odd_hi,
               CAST((h.odd_hi - a.anchor)/0.01 AS INT) AS ticks_up,
               CAST((a.anchor - h.odd_lo)/0.01 AS INT) AS ticks_dn
        FROM hilo h JOIN anchor a
          ON a.marketId=h.marketId AND a.selectionId=h.selectionId
        ORDER BY h.marketId, CAST(h.selectionId AS INTEGER)
    """, (start, end, start, end)).fetchall()
    return rows, ["marketId","selectionId","anchor","odd_lo","odd_hi","ticks_up","ticks_dn"]

# Run all four, print timings, write CSVs
rows, hdr = timed("runner_story", q_runner_story);        export_csv(rows, hdr, "reports/runner_story.csv")
rows, hdr = timed("strategy_story", q_strategy_story);    export_csv(rows, hdr, "reports/strategy_story.csv")
rows, hdr = timed("scalp_summary", q_scalp_summary);      export_csv(rows, hdr, "reports/scalp_summary.csv")
rows, hdr = timed("opportunity_story", q_opportunity_story); export_csv(rows, hdr, "reports/opportunity_story.csv")

con.close()
print("[OK] make_daily_review wrote 4 CSVs into reports/")
