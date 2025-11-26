#!/usr/bin/env python3
import os, sys, sqlite3, time
from datetime import datetime

# ── sys.path injection so engines.* imports resolve ────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ✅ import AFTER sys.path setup
from engines.config_paths import (
    autoscalp_db as _auto_default,
    mastery_v7_db,
    connect_mastery_v7_cache
)

# ✅ override AFTER imports (so it actually takes effect)
def autoscalp_db():
    """Force local autoscalp_gui.db path for offline builds."""
    return os.path.join(ROOT, "data/autoscalp_gui.db")

con_out = connect_mastery_v7_cache()



# ───────────────────────────────────────────────────────────────────────
def _connect(db_path):
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    return con

def _safe_float(x, d=0.0):
    try: return float(x)
    except Exception: return float(d)
def _safe_text(x):
    return str(x or "").strip().upper()

# ───────────────────────────────────────────────────────────────────────
# Schema preflight — verify required tables/views exist or create them
# ───────────────────────────────────────────────────────────────────────
def ensure_all_required_structures():
    """
    Verify all tables/views required by the narrative pipeline exist.
    Creates lightweight stubs if missing to prevent runtime errors.
    """
    auto = autoscalp_db()
    v7   = mastery_v7_db()

    con_auto = sqlite3.connect(auto)
    con_auto.row_factory = sqlite3.Row
    con_v7 = sqlite3.connect(v7)
    con_v7.row_factory = sqlite3.Row

    print("[SCHEMA] verifying required tables and views…")

    # 1️⃣ Required VIEWS in autoscalp_gui.db
    required_views = {
        "v7_intelligence": "CREATE VIEW v7_intelligence AS SELECT NULL AS marketId;",
        "v7_shape_summary": "CREATE VIEW v7_shape_summary AS SELECT NULL AS marketId;",
        "v7_timing_features_fixed": "CREATE VIEW v7_timing_features_fixed AS SELECT NULL AS marketId;",
    }
    for name, ddl in required_views.items():
        ok = con_auto.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
        ).fetchone()
        if not ok:
            print(f"  [CREATE] {name} (view)")
            con_auto.executescript(ddl)

    # 2️⃣ Required TABLES in mastery_v7.db
    required_tables = {
        "story_enriched_cache": """
            CREATE TABLE story_enriched_cache (
                day TEXT, marketId TEXT, selectionId TEXT,
                narrative TEXT, conf REAL,
                drift_speed REAL, momentum_class TEXT,
                race_pattern TEXT, success_flag INTEGER,
                inplay_progress REAL, expected_race_mins REAL
            );
        """,
        "probability_summary": """
            CREATE TABLE probability_summary (
                day TEXT, marketId TEXT, selectionId TEXT,
                p_min REAL, p_max REAL, p_avg REAL, p_range REAL, p_last REAL
            );
        """,
        "opportunity_map": """
            CREATE TABLE opportunity_map (
                marketId TEXT, selectionId TEXT,
                oscillations INTEGER, avg_move_ticks REAL, micro_zone_flag INTEGER
            );
        """
    }
    for name, ddl in required_tables.items():
        ok = con_v7.execute(
            "SELECT 1 FROM sqlite_master WHERE type IN ('table','view') AND name=?", (name,)
        ).fetchone()
        if not ok:
            print(f"  [CREATE] {name} (table)")
            con_v7.executescript(ddl)

    con_auto.commit(); con_v7.commit()
    con_auto.close(); con_v7.close()
    print("[SCHEMA] ✅ verified / created all required structures.")

def _verify_db_sources():
    """Ensure all required tables exist in their expected DBs."""
    for name, dbf in {
        "story_enriched_cache": mastery_v7_db(),
        "probability_summary": mastery_v7_db(),
        "opportunity_map": mastery_v7_db(),
        "v7_intelligence": autoscalp_db(),
        "v7_shape_summary": autoscalp_db(),
    }.items():
        con = sqlite3.connect(dbf)
        ok = con.execute("SELECT 1 FROM sqlite_master WHERE name=?;", (name,)).fetchone()
        con.close()
        print(f"[CHECK] {name:<24} → {'✅' if ok else '❌'} in {os.path.basename(dbf)}")

# ───────────────────────────────────────────────────────────────────────
def build_shape_from_cache(con_out, day):
    """Ultra-fast shape layer using story_enriched_cache."""
    t0 = time.time()
    cur = con_out.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS shape_narratives (
            marketId TEXT,
            selectionId TEXT,
            race_pattern TEXT,
            drift_speed REAL,
            momentum_class TEXT,
            shape_story TEXT,
            confidence_shape REAL,
            created_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)
    cur.execute("DELETE FROM shape_narratives WHERE 1;")

    rows = con_out.execute("""
        SELECT marketId, selectionId, race_pattern, drift_speed,
               momentum_class, conf AS confidence_shape
          FROM story_enriched_cache
         WHERE day=?;
    """, (day,)).fetchall()

    batch = []
    for r in rows:
        pat = _safe_text(r["race_pattern"])
        mom = _safe_text(r["momentum_class"])
        drift = _safe_float(r["drift_speed"])
        conf = _safe_float(r["confidence_shape"], 0.5)
        parts = []

        if "FRONT" in pat: parts.append("Front-runner bias early."); conf += 0.05
        elif "CLOSE" in pat or "FINISH" in pat: parts.append("Closed strongly; late surge."); conf += 0.1
        elif "FADE" in pat: parts.append("Faded late as pace increased."); conf -= 0.1
        else: parts.append("Neutral run pattern.")
        if mom in ("SURGE","RALLY"): parts.append("Momentum spike mid-race.")
        elif mom in ("STALL","FADE"): parts.append("Momentum loss mid-race.")
        if drift > 0.1: parts.append("Odds drifted late.")
        if drift < -0.1: parts.append("Odds collapsed; strong finish.")

        story = " ".join(parts)
        batch.append((r["marketId"], r["selectionId"], pat, drift, mom, story, conf))

    cur.executemany("""
        INSERT INTO shape_narratives(
            marketId, selectionId, race_pattern, drift_speed,
            momentum_class, shape_story, confidence_shape
        ) VALUES (?,?,?,?,?,?,?)
    """, batch)
    con_out.commit()
    print(f"[SHAPE] ✅ {len(batch):,} cached shape rows in {time.time()-t0:.2f}s")

# ───────────────────────────────────────────────────────────────────────
def build_opportunity_from_cache(con_out):
    """Summarised opportunity statistics using inbound_oc_cache snapshot."""
    t0 = time.time()
    cur = con_out.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS opportunity_summary(
            total_runners INTEGER, avg_osc REAL, avg_move REAL,
            micro_zones INTEGER, created_at TEXT DEFAULT (datetime('now','utc'))
        )
    """)
    cur.execute("DELETE FROM opportunity_summary;")

    # 🔧 FIX — read from mastery_v7.db instead of autoscalp_gui.db
    src = mastery_v7_db()
    con_src = _connect(src)

    row = con_src.execute("""
        SELECT COUNT(*) AS n,
               AVG(oscillations) AS avg_osc,
               AVG(avg_move_ticks) AS avg_move,
               SUM(micro_zone_flag) AS micro_zones
          FROM opportunity_map;
    """).fetchone()

    cur.execute("""
        INSERT INTO opportunity_summary VALUES(?,?,?, ?,datetime('now','utc'))
    """, (row["n"], row["avg_osc"], row["avg_move"], row["micro_zones"]))
    con_out.commit(); con_src.close()
    print(f"[OPP] ✅ opportunity summary cached in {time.time()-t0:.2f}s")


# ───────────────────────────────────────────────────────────────────────
def build_prob_summary(con_out):
    """Copy probability_summary → prob_cache (single-table join for trainer)."""
    t0 = time.time()
    cur = con_out.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prob_cache AS
        SELECT * FROM probability_summary;
    """)
    con_out.commit()
    print(f"[PROB] ✅ probability cache copied in {time.time()-t0:.2f}s")

# ───────────────────────────────────────────────────────────────────────
def build_trainer_view(con_out, day):
    """Lightweight join of all caches into story_training_view."""
    t0 = time.time()
    cur = con_out.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_training_view AS
        SELECT e.day, e.marketId, e.selectionId,
               e.narrative, e.conf, e.drift_speed, e.momentum_class,
               e.race_pattern, e.success_flag,
               s.shape_story, s.confidence_shape,
               p.p_avg, p.p_range
          FROM story_enriched_cache e
          LEFT JOIN shape_narratives s USING(marketId,selectionId)
          LEFT JOIN prob_cache p USING(marketId,selectionId)
         WHERE e.day=?;
    """, (day,))
    con_out.commit()
    print(f"[TRAIN] ✅ trainer view built in {time.time()-t0:.2f}s")

# ───────────────────────────────────────────────────────────────────────
#  View Bootstrap — ensure v7 intelligence / timing / shape views exist
# ───────────────────────────────────────────────────────────────────────
def create_views():
    """(Re)build critical v7 views inside autoscalp_gui.db."""
    auto = autoscalp_db()
    con = sqlite3.connect(auto)
    con.row_factory = sqlite3.Row
    print(f"[VIEWS] bootstrapping core views in {auto}…")

    con.executescript("""
    DROP VIEW IF EXISTS v7_intelligence;
    CREATE VIEW v7_intelligence AS
    SELECT
        m.day,
        m.marketId,
        m.selectionId,
        m.letter,
        m.pnl,
        m.success,
        t.slope_ppm,
        t.tick_vel_3s_up,
        t.momentum_class,
        tf.drift_speed,
        tf.inplay_progress,
        tf.expected_race_mins,
        r.trainerName,
        r.jockeyName,
        r.distance,
        r.going
      FROM mastery_outcomes_raw m
      LEFT JOIN trend_features t
            USING (marketId,selectionId)
      LEFT JOIN v7_timing_features_fixed tf
            USING (marketId,selectionId)
      LEFT JOIN market_data r
            USING (marketId,selectionId);

    DROP VIEW IF EXISTS v7_shape_summary;
    CREATE VIEW v7_shape_summary AS
    SELECT
        marketId,
        selectionId,
        AVG(drift_speed) AS avg_drift,
        MAX(drift_speed) AS drift_speed,
        MAX(inplay_progress) AS inplay_progress,
        MAX(expected_race_mins) AS expected_race_mins,
        CASE
          WHEN MAX(drift_speed) > 0.15 THEN 'FADE'
          WHEN MIN(drift_speed) < -0.10 THEN 'CLOSE_FINISH'
          ELSE 'STEADY'
        END AS race_pattern
      FROM v7_timing_features_fixed
     GROUP BY marketId, selectionId;

    DROP VIEW IF EXISTS v7_timing_features_fixed;
    CREATE VIEW v7_timing_features_fixed AS
    SELECT
        marketId, selectionId,
        pre_avg_odds, inplay_avg_odds, post_avg_odds,
        drift_speed, inplay_progress, expected_race_mins
      FROM v_timing_features_v7;
    """)

    con.commit(); con.close()
    print("[VIEWS] ✅ core v7 views ready.")


# ───────────────────────────────────────────────────────────────────────
def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "2025-10-17"
    print(f"[ALL] full narrative + cache pipeline for {day}")

    # 0️⃣ Schema verification
    ensure_all_required_structures()

    # 1️⃣ Rebuild SQL views
    create_views()

    # 2️⃣ Run cache pipeline
    con_out = _connect(mastery_v7_db())
    build_shape_from_cache(con_out, day)
    build_opportunity_from_cache(con_out)
    build_prob_summary(con_out)
    build_trainer_view(con_out, day)
    con_out.close()

    print(f"[ALL] ✅ pipeline + views built successfully.")



# ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
