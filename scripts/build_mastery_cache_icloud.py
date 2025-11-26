#!/usr/bin/env python3
"""
build_mastery_cache_icloud.py
─────────────────────────────────────────────────────────────
Builds a resumable Mastery cache in iCloud Drive.
Optimised for small file size and resumable batch commits.
"""

import os, sqlite3, time, statistics, sys
from datetime import datetime

# ── sys.path injection so engines.* imports resolve ────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engines.config_paths import autoscalp_db, bets_db, cloud_data_dir

ICLOUD_DIR = cloud_data_dir()
os.makedirs(ICLOUD_DIR, exist_ok=True)
CACHE_PATH = os.path.join(ICLOUD_DIR, "mastery_cache.db")

# ─────────────────────────────────────────────────────────────
def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con

# ─────────────────────────────────────────────────────────────
def ensure_schema(con):
    """Create destination cache schema dynamically based on source columns."""
    src = _connect(autoscalp_db())
    cols = [r[1] for r in src.execute("PRAGMA table_info(inbound_oc_cache);").fetchall()]
    src.close()

    # Filter only base OCs and key numeric/meta fields
    keep = [c for c in cols if c.startswith("oc") or c in (
        "marketId", "selectionId", "anchor_odd", "last_sync_ts"
    )]
    keep = [c for c in keep if not c.endswith("_band_json")]  # drop heavy JSONs

    ddl_cols = ", ".join([f"{c} REAL" if c.startswith("oc") or c == "anchor_odd" else f"{c} TEXT" for c in keep])
    ddl = f"""
        CREATE TABLE IF NOT EXISTS cache_mastery_outcomes (
            {ddl_cols},
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY(marketId, selectionId)
        );
    """
    con.execute(ddl)
    con.execute("""
        CREATE TABLE IF NOT EXISTS _resume_state(
            last_market TEXT PRIMARY KEY,
            updated_at TEXT DEFAULT (datetime('now','utc'))
        );
    """)
    con.commit()
    return keep

# ─────────────────────────────────────────────────────────────
def build_cache(batch_size=100, vacuum_every=200):
    src_gui = _connect(autoscalp_db())
    src_bets = _connect(bets_db())
    dst = _connect(CACHE_PATH)
    keep_cols = ensure_schema(dst)

    done = dst.execute("SELECT last_market FROM _resume_state").fetchone()
    resume_after = done["last_market"] if done else None

    markets = [r[0] for r in src_bets.execute(
        "SELECT DISTINCT marketId FROM bets WHERE marketId IS NOT NULL ORDER BY marketId;"
    )]
    if resume_after and resume_after in markets:
        idx = markets.index(resume_after) + 1
        markets = markets[idx:]

    print(f"[CACHE] starting build — {len(markets)} markets to process …")
    t0 = time.time()
    batch, processed = [], 0
    col_placeholder = ",".join(["?"] * len(keep_cols))
    insert_sql = f"INSERT OR REPLACE INTO cache_mastery_outcomes ({','.join(keep_cols)}) VALUES ({col_placeholder})"

    for i, mid in enumerate(markets, 1):
        rows = src_gui.execute("SELECT * FROM inbound_oc_cache WHERE marketId=?;", (mid,)).fetchall()
        for r in rows:
            batch.append(tuple(r[c] if c in r.keys() else None for c in keep_cols))
        if len(batch) >= batch_size:
            dst.executemany(insert_sql, batch)
            dst.execute("INSERT OR REPLACE INTO _resume_state(last_market) VALUES(?)", (mid,))
            dst.commit()
            processed += len(batch)
            batch.clear()
            print(f"[CACHE] committed {processed:,} rows ({i}/{len(markets)}) …")
        if i % vacuum_every == 0:
            dst.execute("VACUUM;")

    if batch:
        dst.executemany(insert_sql, batch)
        dst.commit()
        processed += len(batch)

    dst.close()
    print(f"[CACHE] ✅ build complete — {processed:,} rows in {time.time()-t0:.1f}s")

# ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    build_cache(batch_size=200, vacuum_every=50)
