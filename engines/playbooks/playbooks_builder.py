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
    # === PATCH START ===
    # 📍 TARGET: engines/playbooks/playbooks_builder.py:_verify_schema
    # 🔎 SEARCH: con = sqlite3.connect(db_path)
    # 📆 PATCHED: 2025-11-21

    from engines.config_paths import auto_conn as _auto_conn

    if os.path.abspath(db_path) == os.path.abspath(AUTO_DB):
        con = _auto_conn(rw=False)
    else:
        con = sqlite3.connect(db_path)
    # === PATCH END ===

    cur = con.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = {r["name"] for r in cur.fetchall()}
    missing = [t for t in required if t not in tables]
    if missing:
        raise RuntimeError(f"{os.path.basename(db_path)} missing tables: {missing}")
    _print(f"[SCHEMA VERIFIED] {os.path.basename(db_path)} — {len(required)} tables OK")
    con.close()

# ───────────────────────────────────────────────────────────────────────────────
# Core Builder
# ───────────────────────────────────────────────────────────────────────────────
def build_playbooks():
    start = datetime.datetime.utcnow()
    _print(f"Starting Playbooks build at {_now()}")
    for path, req in [
        (AUTO_DB, ["orders","decisions","plan_ledger","markets_schedule"]),
        (BETS_DB, ["oc_series","markets_schedule"]),
        (SETTLE_DB, ["bf_cleared_orders","bf_market_catalogue","bf_runner_info"]),
    ]:
        _verify_schema(path, req)

    # Step 1 — close open parents/children
    try:
        n_closed = _close_parents_children()
        n_mkts   = close_settled_markets()
        _print(f"closed parents/children={n_closed}, markets={n_mkts}")
    except Exception as e:
        _print(f"warn: settlement closures failed → {e}")

    # === PATCH START ===
    # 📍 TARGET: engines/playbooks/playbooks_builder.py:build_playbooks (Step 2)
    # 🔎 SEARCH: con = sqlite3.connect(AUTO_DB)
    # 📆 PATCHED: 2025-11-21

    from engines.config_paths import auto_conn as _auto_conn

    # Step 2 — create tables
    con = _auto_conn(rw=True)
    con.row_factory = sqlite3.Row   # keep original behaviour
    cur = con.cursor()
    # === PATCH END ===

    _q(cur, """CREATE TABLE IF NOT EXISTS playbooks_settled(
        day TEXT, betId TEXT PRIMARY KEY, marketId TEXT, selectionId INTEGER,
        letter TEXT, strategy TEXT, side TEXT,
        entry_odds REAL, entry_stake REAL, exit_odds REAL, exit_stake REAL,
        profit REAL, commission REAL, net_pl REAL, pnl_pct REAL,
        odds_diff REAL, odds_ticks INTEGER, mto_minutes REAL,
        oc_stage_entry TEXT, oc_stage_exit TEXT,
        band_low_entry REAL, band_high_entry REAL,
        fav_rank_entry INTEGER, volatility_band TEXT,
        venue TEXT, going TEXT, distance TEXT, class_band TEXT,
        trainer TEXT, jockey TEXT, exit_kind TEXT,
        confidence REAL, slippage REAL, latency_ms REAL,
        pair_id INTEGER, child_id INTEGER, pair_complete INTEGER,
        hedge_ratio REAL, green_up_pnl REAL, liability_released REAL,
        risk_state TEXT, outcome_bin TEXT, meta_json TEXT,
        created_at TEXT DEFAULT (datetime('now','utc'))
    )""")
    _q(cur, """CREATE TABLE IF NOT EXISTS playbooks_diagnostics(
        day TEXT, plan_id TEXT, letter TEXT, strategy TEXT,
        marketId TEXT, selectionId INTEGER, pair_complete INTEGER,
        green_up_before_off INTEGER, time_to_match_s REAL,
        expected_ticks REAL, realized_ticks REAL, net_pl REAL,
        cause_tag TEXT, meta_json TEXT,
        created_at TEXT DEFAULT (datetime('now','utc')),
        PRIMARY KEY(day, plan_id)
    )""")
    _q(cur, """CREATE TABLE IF NOT EXISTS playbooks_insights(
        day TEXT, letter TEXT, strategy TEXT, trades INTEGER,
        pair_complete_rate REAL, green_rate REAL, win_rate REAL,
        avg_pnl REAL, total_pnl REAL, avg_slippage REAL,
        avg_latency_ms REAL, avg_confidence REAL, avg_volatility REAL,
        recommendation_json TEXT,
        updated_at TEXT DEFAULT (datetime('now','utc')),
        PRIMARY KEY(day, letter, strategy)
    )""")
    con.commit()

# === PATCH START: tag live playbooks with source=PLAYBOOKS_LIVE ================
# 📍 TARGET: engines/playbooks/playbooks_builder.py (Step 3 population)
# 📆 PATCHED: 2025-10-17T15:25Z — ensure live Playbooks source tagging
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 3 — populate playbooks_settled
    _print("Populating playbooks_settled …")
    q = """
    INSERT OR REPLACE INTO playbooks_settled
    (day, betId, marketId, selectionId, letter, strategy, side,
     entry_odds, entry_stake, exit_odds, exit_stake,
     profit, commission, net_pl, pnl_pct,
     odds_diff, mto_minutes, exit_kind, confidence,
     risk_state, outcome_bin, meta_json, source)
    SELECT
      date(s.settledDate,'utc'),
      s.betId, s.marketId, s.selectionId,
      SUBSTR(o.source,1,1), o.source, s.side,
      o.entry_odds, o.entry_stake,
      s.priceMatched, s.sizeSettled,
      s.profit, s.commission,
      (s.profit - COALESCE(s.commission,0.0)),
      CASE WHEN o.entry_stake>0
           THEN (s.profit - COALESCE(s.commission,0.0))/o.entry_stake ELSE 0 END,
      (s.priceMatched - o.entry_odds),
      (julianday(ms.off_at_utc)-julianday(o.opened_at))*1440.0,
      o.exit_kind,
      d.confidence,
      CASE WHEN c.entry_status='MATCHED' THEN 'CLOSED' ELSE 'OPEN' END,
      CASE WHEN (s.profit-COALESCE(s.commission,0.0))>0 THEN 'WIN'
           WHEN (s.profit-COALESCE(s.commission,0.0))<0 THEN 'LOSS' ELSE 'NEUTRAL' END,
      json_object('settledDate',s.settledDate,'customerOrderRef',s.customerOrderRef),
      'PLAYBOOKS_LIVE'
    FROM bf_cleared_orders s
      LEFT JOIN orders o ON s.customerOrderRef=o.customerOrderRef
      LEFT JOIN orders c ON c.hedge_of=o.id
      LEFT JOIN decisions d ON d.marketId=o.marketId AND d.selectionId=o.selectionId
      LEFT JOIN markets_schedule ms ON ms.marketId=o.marketId;
    """
# === PATCH END =================================================================
# === PATCH START ===
# 📍 TARGET: engines/playbooks/playbooks_builder.py: build_playbooks()
# 📆 PATCHED: 2025-10-19T15:05Z — map blueprint segmentation to valid playbooks columns
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Step 3b — Integrate segmentation from latest Blueprints
    try:
        import glob
        bp_dir = os.path.join(DATA_DIR, "blueprints")
        bp_files = sorted(glob.glob(os.path.join(bp_dir, "blueprint_signals_*.json")), reverse=True)
        if bp_files:
            latest_bp = bp_files[0]
            _print(f"[blueprints→playbooks] loading segments from {os.path.basename(latest_bp)}")
            with open(latest_bp, "r") as f:
                bp_data = json.load(f)

            segmap = {}
            for pattern_key, meta in bp_data.items():
                for t in meta.get("trades", []):
                    mid = str(t.get("market_id") or t.get("marketId") or "")
                    seg = (
                        t.get("segment_key")
                        or t.get("surface_key")
                        or t.get("surface_type")
                        or "unknown"
                    )
                    if mid:
                        segmap[mid] = seg

            if segmap:
                from engines.config_paths import auto_conn as _auto_conn

                con2 = _auto_conn(rw=True)
                cur2 = con2.cursor()
                for mid, seg in segmap.items():
                    parts = seg.split("_")
                    dist = parts[1] if len(parts) > 1 else "unknown"
                    rtype = parts[2] if len(parts) > 2 else "other"
                    cur2.execute("""
                        UPDATE playbooks_settled
                           SET segment_key=?,
                               distance=?,
                               class_band=?
                         WHERE marketId=?;
                    """, (seg, dist, rtype, mid))
                con2.commit()
                con2.close()
                _print(f"[blueprints→playbooks] applied segment_key/distance/class_band for {len(segmap)} markets.")
            else:
                _print("[blueprints→playbooks] no segment data found in latest blueprint JSON.")
        else:
            _print("[blueprints→playbooks] no blueprint files found.")
    except Exception as e:
        _print(f"[blueprints→playbooks] warn: {e}")
# === PATCH END ===




    # Step 4 — build diagnostics
    _print("Building playbooks_diagnostics …")
    _q(cur, """
    INSERT OR REPLACE INTO playbooks_diagnostics
    (day, plan_id, letter, strategy, marketId, selectionId, pair_complete,
     green_up_before_off, time_to_match_s, realized_ticks, net_pl, cause_tag, meta_json)
    SELECT
      date(p.day,'utc'), pl.plan_id, p.letter, p.strategy, p.marketId, p.selectionId,
      p.pair_complete,
      CASE WHEN datetime(o.closed_at)<=datetime(ms.off_at_utc) THEN 1 ELSE 0 END,
      (julianday(o.closed_at)-julianday(o.opened_at))*86400,
      p.odds_ticks, p.net_pl,
      CASE
        WHEN p.pair_complete=0 THEN 'no_child'
        WHEN p.net_pl<0 THEN 'loss'
        ELSE 'ok'
      END,
      json_object('exit_kind',o.exit_kind,'off_at',ms.off_at_utc)
    FROM playbooks_settled p
      LEFT JOIN plan_ledger pl ON pl.marketId=p.marketId AND pl.selectionId=p.selectionId
      LEFT JOIN orders o ON o.id=p.pair_id
      LEFT JOIN markets_schedule ms ON ms.marketId=p.marketId;
    """)
    con.commit()

    # Step 5 — aggregate insights
    _print("Aggregating playbooks_insights …")
    _q(cur, """
    INSERT OR REPLACE INTO playbooks_insights
    (day, letter, strategy, trades, pair_complete_rate, green_rate, win_rate,
     avg_pnl, total_pnl, avg_slippage, avg_latency_ms,
     avg_confidence, recommendation_json)
    SELECT
      p.day, p.letter, p.strategy,
      COUNT(*),
      AVG(p.pair_complete),
      AVG(CASE WHEN d.green_up_before_off=1 THEN 1 ELSE 0 END),
      AVG(CASE WHEN p.outcome_bin='WIN' THEN 1 ELSE 0 END),
      AVG(p.net_pl),
      SUM(p.net_pl),
      AVG(p.slippage),
      AVG(p.latency_ms),
      AVG(p.confidence),
      json_object('cap_adj',ROUND(AVG(p.pair_complete)*2,2),'conf_thresh',ROUND(AVG(p.confidence),2))
    FROM playbooks_settled p
      LEFT JOIN playbooks_diagnostics d ON d.marketId=p.marketId AND d.selectionId=p.selectionId
    GROUP BY p.day, p.letter, p.strategy;
    """)
    con.commit()

    # Step 6 — verification
    rows = cur.execute("SELECT COUNT(*) FROM playbooks_settled").fetchone()[0]
    diags = cur.execute("SELECT COUNT(*) FROM playbooks_diagnostics").fetchone()[0]
    ins = cur.execute("SELECT COUNT(*) FROM playbooks_insights").fetchone()[0]
    open_orders = cur.execute("SELECT COUNT(*) FROM orders WHERE exit_status NOT IN ('MATCHED','SETTLED')").fetchone()[0]
    total_pnl = cur.execute("SELECT ROUND(SUM(net_pl),2) FROM playbooks_settled").fetchone()[0]
    con.close()
    # Step 7 — ingest into mastery_outcomes_raw
    _final_ingest_pipeline()

    _print(f"settled={rows} diagnostics={diags} insights={ins}")
    _print(f"open_orders={open_orders}  total_pnl={total_pnl}")
    if open_orders==0: _print("✅ All orders closed, build complete")
    else: _print("⚠️ Some orders remain open — review manually")

    end = datetime.datetime.utcnow()
    _print(f"Completed Playbooks build in {(end-start).total_seconds():.1f}s")

    # --- NEW: replay learning generation ---
    try:
        from engines.config_paths import auto_conn as _auto_conn

        _generate_replay_variants(_auto_conn(rw=True))
    except Exception as e:
        _print(f"[playbooks] replay-generation warn: {e}")


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
    """Ensure mastery_outcomes_raw exists with source tagging."""
    _q(con, """
        CREATE TABLE IF NOT EXISTS mastery_outcomes_raw (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT, marketId TEXT, selectionId TEXT,
            letter TEXT, class_band TEXT,
            target_ticks INTEGER, realized_ticks INTEGER,
            success INTEGER, stoploss_hit INTEGER, hedge_hit INTEGER,
            entry_px REAL, pnl REAL, weight REAL, priority INTEGER,
            created_at TEXT DEFAULT (datetime('now','utc')),
            source TEXT DEFAULT 'PLAYBOOKS_LIVE'
        )
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
