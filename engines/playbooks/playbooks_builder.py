#!/usr/bin/env python3
"""
Playbooks Builder — Stage-1 Data Consolidator
...
"""
from __future__ import annotations
import os, sys, sqlite3, json, datetime, traceback

# --- PATH FIX (allow direct execution) -----------------------------------------
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
# -------------------------------------------------------------------------------

from engines.config_paths import autoscalp_db, connect_db, q_retry as _q
from engines.live.settlements import _close_parents_children, close_settled_markets
from engines.playbooks import __name__ as _pkg
import glob


AUTO_DB = autoscalp_db()
DATA_DIR = os.path.dirname(AUTO_DB)
BETS_DB = os.path.join(DATA_DIR, "bets.db")
SETTLE_DB = os.path.join(DATA_DIR, "settlements.db")

# ───────────────────────────────────────────────────────────────────────────────
# Helpers
# ───────────────────────────────────────────────────────────────────────────────
def _now(): return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")

def _print(msg: str): print(f"[playbooks] {msg}", flush=True)

def _verify_schema(db_path: str, required: list[str]):
    from engines.config_paths import auto_conn as _auto_conn

    if os.path.abspath(db_path) == os.path.abspath(AUTO_DB):
        con = _auto_conn(rw=False)
    else:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row   # ✅ FIX

    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r[0] for r in cur.fetchall()}

    missing = [t for t in required if t not in tables]
    if missing:
        raise RuntimeError(f"{os.path.basename(db_path)} missing tables: {missing}")

    _print(f"[SCHEMA VERIFIED] {os.path.basename(db_path)} — {len(required)} tables OK")
    con.close()

def build_playbooks(days: int = 90):
    """
    PLAYBOOKS — settlement-truth (training-ready)

    Produces one row per:
      (day, marketId, selectionId, letter)

    This is the canonical input for cache_mastery_day.
    """

    import sqlite3
    from engines.config_paths import open_auto_db

    print(f"[playbooks] building settlement-truth playbooks ({days} days)…")

    con = open_auto_db(rw=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # --------------------------------------------------
    # Table (training-facing grain)
    # --------------------------------------------------
    cur.execute("""
        CREATE TABLE IF NOT EXISTS playbooks_runner_day (
            day TEXT NOT NULL,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            letter TEXT NOT NULL,
            pnl REAL NOT NULL,
            success INTEGER NOT NULL,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY (day, marketId, selectionId, letter)
        );
    """)

    # --------------------------------------------------
    # Attach settlements
    # --------------------------------------------------
    cur.execute("ATTACH DATABASE 'data/settlements.db' AS settle;")

    # --------------------------------------------------
    # Build winners per market
    # --------------------------------------------------
    cur.execute("""
        WITH winners AS (
            SELECT
                mb.marketId,
                json_extract(r.value,'$.selectionId') AS selectionId
            FROM settle.bf_market_book mb,
                 json_each(mb.resultJson) r
            WHERE
                mb.status='CLOSED'
                AND json_extract(r.value,'$.status')='WINNER'
        ),

        parent_orders AS (
            SELECT
                date(COALESCE(o.closed_at,o.opened_at)) AS day,
                o.marketId,
                o.selectionId,
                UPPER(SUBSTR(COALESCE(o.source,'S'),1,1)) AS letter,
                UPPER(o.side) AS side,
                o.entry_odds AS odds,
                o.entry_stake AS stake
            FROM orders o
            WHERE
                o.mode='LIVE'
                AND (o.role IS NULL OR o.role='PARENT')
                AND o.entry_status='MATCHED'
                AND date(COALESCE(o.closed_at,o.opened_at))
                    >= date('now', ?)
        ),

        runner_pnl AS (
            SELECT
                p.day,
                p.marketId,
                p.selectionId,
                p.letter,
                CASE
                    WHEN p.side='BACK' AND w.selectionId IS NOT NULL
                        THEN (p.odds-1.0)*p.stake
                    WHEN p.side='BACK' AND w.selectionId IS NULL
                        THEN -p.stake
                    WHEN p.side='LAY' AND w.selectionId IS NOT NULL
                        THEN -(p.odds-1.0)*p.stake
                    WHEN p.side='LAY' AND w.selectionId IS NULL
                        THEN p.stake
                    ELSE 0.0
                END AS pnl
            FROM parent_orders p
            LEFT JOIN winners w
              ON w.marketId=p.marketId
             AND w.selectionId=p.selectionId
        )

        INSERT OR REPLACE INTO playbooks_runner_day
        (day, marketId, selectionId, letter, pnl, success)

        SELECT
            day,
            marketId,
            selectionId,
            letter,
            ROUND(SUM(pnl),2) AS pnl,
            CASE WHEN SUM(pnl) > 0 THEN 1 ELSE 0 END AS success
        FROM runner_pnl
        GROUP BY day, marketId, selectionId, letter;
    """, (f"-{int(days)} day",))

    con.commit()

    # --------------------------------------------------
    # Verify
    # --------------------------------------------------
    row = cur.execute("""
        SELECT COUNT(*) AS n,
               ROUND(SUM(pnl),2) AS total_pnl
        FROM playbooks_runner_day;
    """).fetchone()

    print(f"[playbooks] rows={row['n']} total_pnl={row['total_pnl']}")
    print("[playbooks] ✅ settlement-truth playbooks built")

    con.close()



# ───────────────────────────────────────────────────────────────────────────────
# === PATCH START ===
# 📍 TARGET: engines/playbooks/playbooks_builder.py
# 📆 PATCHED: 2025-10-17T12:30Z — integrated replay learning generation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _ensure_learning_schema(con):
    """Create playbooks_learning + unified view if missing."""
    _q(con, """
        CREATE TABLE IF NOT EXISTS playbooks_learning (
            day TEXT, marketId TEXT, selectionId TEXT,
            letter TEXT, strategy TEXT, side TEXT,
            entry_odds REAL, entry_stake REAL,
            exit_odds REAL, exit_stake REAL,
            net_pl REAL, confidence REAL,
            oc_stage_entry TEXT, class_band TEXT,
            source TEXT DEFAULT 'REPLAY',
            replay_run_id TEXT,
            meta_json TEXT,
            created_at TEXT DEFAULT (datetime('now','utc')),
            PRIMARY KEY(day, marketId, selectionId, replay_run_id)
        )
    """)
    _q(con, """
        CREATE VIEW IF NOT EXISTS v_playbooks_all AS
        SELECT * FROM playbooks_settled
        UNION ALL
        SELECT * FROM playbooks_learning;
    """)
    con.commit()


def _generate_replay_variants(con):
    """Create synthetic replay playbooks from the live set."""
    _ensure_learning_schema(con)
    rows = _q(con, "SELECT * FROM playbooks_settled WHERE date(day)=date('now','utc')").fetchall()
    if not rows:
        _print("[playbooks] no base rows for replay generation today.")
        return
    run_tag = datetime.datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    cur = con.cursor()
    import random, json
    for r in rows:
        base = dict(r)
        for _ in range(random.randint(3,5)):
            tick_var = float(base.get("odds_ticks") or 1.0) * random.uniform(0.8,1.2)
            conf_var = float(base.get("confidence") or 0.5) * random.uniform(0.9,1.1)
            pnl_var  = float(base.get("net_pl") or 0.0) * random.uniform(0.9,1.1)
            _q(cur, """
                INSERT OR IGNORE INTO playbooks_learning(
                    day, marketId, selectionId, letter, strategy, side,
                    entry_odds, entry_stake, exit_odds, exit_stake,
                    net_pl, confidence, oc_stage_entry, class_band,
                    replay_run_id, meta_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                base["day"], base["marketId"], base["selectionId"],
                base["letter"], base["strategy"], base["side"],
                base["entry_odds"], base["entry_stake"],
                base["exit_odds"], base["exit_stake"],
                pnl_var, conf_var,
                base.get("oc_stage_entry"), base.get("class_band"),
                run_tag,
                json.dumps({"variant":"replay","tick_var":tick_var}, separators=(',',':'))
            ))
    con.commit()
    _print(f"[playbooks] replay-mode variants generated for {len(rows)} base trades → run_id={run_tag}")
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/playbooks/playbooks_builder.py:_ensure_outcomes_schema / _ingest_playbooks_to_raw
# 📆 PATCHED: 2025-10-19T14:25Z — add segment_key to mastery_outcomes_raw ingestion
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _ensure_outcomes_schema(con):
    """Ensure mastery_outcomes_raw exists with source tagging."""
    _q(con, """
        CREATE TABLE IF NOT EXISTS mastery_outcomes_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT, marketId TEXT, selectionId TEXT,
            letter TEXT, class_band TEXT, segment_key TEXT,
            target_ticks INTEGER, realized_ticks INTEGER,
            success INTEGER, stoploss_hit INTEGER, hedge_hit INTEGER,
            entry_px REAL, pnl REAL, weight REAL, priority INTEGER,
            created_at TEXT DEFAULT (datetime('now','utc')),
            source TEXT DEFAULT 'PLAYBOOKS'
        )
    """)
    con.commit()

# === PATCH START ===
# 📍 TARGET: engines/playbooks/playbooks_builder.py:_ensure_outcomes_schema()
# 📆 PATCHED: 2025-10-19T15:15Z — add segment_key column for blueprint linkage
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(mastery_outcomes_raw)")
        cols = [r[1] for r in cur.fetchall()]
        if "segment_key" not in cols:
            cur.execute("ALTER TABLE mastery_outcomes_raw ADD COLUMN segment_key TEXT")
            con.commit()
            _print("[playbooks] added segment_key column to mastery_outcomes_raw")
    except Exception as e:
        _print(f"[playbooks] segment_key ensure warn: {e}")
# === PATCH END ===

# === PATCH START ===
# 📍 TARGET: engines/playbooks/playbooks_builder.py
# 📆 PATCHED: 2025-10-19T15:10Z — live-only view + correct source tagging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _ensure_live_view(con):
    """Create v_mastery_live view (filters out replay data)."""
    _q(con, """
        CREATE VIEW IF NOT EXISTS v_mastery_live AS
        SELECT *
          FROM mastery_outcomes_raw
         WHERE source IN ('PLAYBOOKS_LIVE','LIVE');
    """)
    con.commit()

def _ensure_outcomes_schema(con):
    _q(con, """
        CREATE TABLE IF NOT EXISTS mastery_outcomes_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT, marketId TEXT, selectionId TEXT,
            letter TEXT, class_band TEXT, segment_key TEXT,
            target_ticks INTEGER, realized_ticks INTEGER,
            success INTEGER, stoploss_hit INTEGER, hedge_hit INTEGER,
            entry_px REAL, pnl REAL, weight REAL, priority INTEGER,
            created_at TEXT DEFAULT (datetime('now','utc')),
            source TEXT DEFAULT 'PLAYBOOKS_LIVE',
            confidence REAL
        )
    """)
    con.commit()

    cols = [r[1] for r in _q(con, "PRAGMA table_info(mastery_outcomes_raw)").fetchall()]
    if "segment_key" not in cols:
        _q(con, "ALTER TABLE mastery_outcomes_raw ADD COLUMN segment_key TEXT")
    if "confidence" not in cols:
        _q(con, "ALTER TABLE mastery_outcomes_raw ADD COLUMN confidence REAL")

    _q(con, """
        CREATE VIEW IF NOT EXISTS v_mastery_live AS
        SELECT *
        FROM mastery_outcomes_raw
        WHERE source IN ('PLAYBOOKS_LIVE','LIVE')
    """)
    con.commit()

    _ensure_live_view(con)
# === PATCH END ===


# === PATCH START ===
# 📍 TARGET: engines/playbooks/playbooks_builder.py:_ingest_playbooks_to_raw
# 📆 PATCHED: 2025-10-19T15:25Z — split segment_key → surface/distance/class for Mastery ingestion
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _ingest_playbooks_to_raw(con, table_name: str, source_tag: str):
    """Move playbooks_[settled|learning] into mastery_outcomes_raw, parsing segment_key."""
    _ensure_outcomes_schema(con)
    cur = con.cursor()

    # skip if this source already processed today
    already = cur.execute("""
        SELECT COUNT(*) FROM mastery_outcomes_raw
         WHERE date(day)=date('now','utc') AND source=?
    """, (source_tag,)).fetchone()[0]
    if already:
        _print(f"[playbooks] {source_tag} already ingested today ({already} rows) — skipping.")
        return 0

    # Load source rows including segment_key
    q = f"""
        SELECT day, marketId, selectionId,
               letter, segment_key, odds_ticks, net_pl,
               pair_complete, entry_odds, confidence
          FROM {table_name}
         WHERE net_pl IS NOT NULL
    """
    rows = cur.execute(q).fetchall()
    if not rows:
        _print(f"[playbooks] no rows found in {table_name} for ingestion.")
        return 0

    cur2 = con.cursor()
    count = 0
    for r in rows:
        seg = str(r["segment_key"] or "flat_unknown_other")
        parts = seg.split("_")
        surface = parts[0] if len(parts) > 0 else "flat"
        distance = parts[1] if len(parts) > 1 else "unknown"
        rtype = parts[2] if len(parts) > 2 else "other"

        cur2.execute("""
            INSERT INTO mastery_outcomes_raw(
                day, marketId, selectionId, letter,
                distance_band, fav_rank, day_of_week, surface, class_band,
                target_ticks, realized_ticks, success, stoploss_hit, hedge_hit,
                entry_px, pnl, weight, priority, created_at, source, confidence
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now','utc'), ?, ?
            )
        """, (
            r["day"], r["marketId"], r["selectionId"], r["letter"],
            distance, "", "", surface, rtype,
            int(r["odds_ticks"] or 0), int(r["odds_ticks"] or 0),
            1 if (r["net_pl"] or 0) > 0 else 0,
            1 if (r["net_pl"] or 0) < 0 else 0,
            int(r["pair_complete"] or 1),
            float(r["entry_odds"] or 0.0),
            float(r["net_pl"] or 0.0),
            1.0, 99, source_tag, float(r["confidence"] or 0.0)
        ))
        count += 1

    con.commit()
    _print(f"[playbooks] inserted {count} rows into mastery_outcomes_raw ({source_tag})")
    return count
# === PATCH END ===




def _backfill_replay_jsons(con):
    """Scan replay_report_*.json and backfill into playbooks_learning if missing."""
    _print("[playbooks] scanning for replay JSONs …")
    paths = sorted(glob.glob(os.path.join(DATA_DIR, "replay_report_*.json")))
    if not paths:
        _print("[playbooks] no replay JSONs found.")
        return 0

    cur = con.cursor()
    added = 0
    for pth in paths:
        try:
            day = os.path.basename(pth).split("_")[-1].split(".")[0]
            exists = cur.execute("SELECT 1 FROM playbooks_learning WHERE day=? LIMIT 1", (day,)).fetchone()
            if exists:
                continue

            with open(pth, "r") as f:
                data = json.load(f)
            if not data:
                continue

            for o in data:
                cur.execute("""
                    INSERT OR IGNORE INTO playbooks_learning(
                        day, marketId, selectionId, letter, strategy, side,
                        entry_odds, entry_stake, exit_odds, exit_stake,
                        net_pl, confidence, oc_stage_entry, class_band,
                        replay_run_id, meta_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    o.get("day"),
                    o.get("mid") or o.get("marketId"),
                    o.get("sid") or o.get("selectionId"),
                    o.get("letter"), o.get("strategy") or "",
                    o.get("side") or "",
                    o.get("entry_px") or 0.0,
                    o.get("stake") or 0.0,
                    o.get("exit_px") or 0.0,
                    o.get("exit_stake") or 0.0,
                    o.get("pnl") or 0.0,
                    o.get("confidence") or 0.5,
                    o.get("oc_stage") or "OC?",
                    o.get("class_band") or "",
                    day,
                    json.dumps(o, separators=(',',':'))
                ))
                added += 1
            con.commit()
            _print(f"[playbooks] imported {added} rows from {os.path.basename(pth)} → playbooks_learning")
        except Exception as e:
            _print(f"[playbooks] warn backfill {pth}: {e}")
            continue
    return added


def _final_ingest_pipeline():
    """Main loader that runs at end of build_playbooks."""
    try:
        from engines.config_paths import auto_conn as _auto_conn

        con = _auto_conn(rw=True)
        _backfill_replay_jsons(con)
        live_n   = _ingest_playbooks_to_raw(con, "playbooks_settled", "PLAYBOOKS_LIVE")
        replay_n = _ingest_playbooks_to_raw(con, "playbooks_learning", "PLAYBOOKS_REPLAY")
        con.close()
        _print(f"[playbooks] ✅ ingestion complete → live={live_n} replay={replay_n}")
    except Exception as e:
        _print(f"[playbooks] ingestion warn: {e}")
# === PATCH END ===

def main():
    try:
        build_playbooks()
    except Exception as e:
        _print(f"❌ error: {e}")
        traceback.print_exc()
        sys.exit(1)
    sys.exit(0)

if __name__ == "__main__":
    main()
# === PATCH END =================================================================
