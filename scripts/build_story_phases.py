#!/usr/bin/env python3
"""
scripts/build_story_phases.py
───────────────────────────────────────────────────────────────────────
Builds pre-off and in-play race narratives from OC data across
bets.db + autoscalp_gui.db → mastery_v7.db.
Usage:
    python3 scripts/build_story_phases.py 2025-08-13
If no date is provided → latest bets.date is used automatically.
"""

import sys, os, sqlite3, math, statistics, time
from datetime import datetime

# ── sys.path injection so engines.* imports resolve ────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engines.config_paths import connect_mastery_v7_cache
con_out = connect_mastery_v7_cache()



# ───────────────────────────────────────────────────────────────────────
# Safe helpers
# ───────────────────────────────────────────────────────────────────────
def _safe_float(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return float(d)


def _band_mean(bjson: str | None):
    if not bjson:
        return None
    try:
        import json
        b = json.loads(bjson)
        vals = [b.get("low"), b.get("high"), b.get("mid")]
        vals = [float(v) for v in vals if v is not None]
        return statistics.mean(vals) if vals else None
    except Exception:
        return None


# ───────────────────────────────────────────────────────────────────────
# Schema bootstrap
# ───────────────────────────────────────────────────────────────────────
def _ensure_tables(con):
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS preoff_narratives (
            marketId TEXT,
            selectionId TEXT,
            fav_rank INTEGER,
            anchor_odd REAL,
            preoff_story TEXT,
            confidence_preoff REAL,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS inplay_narratives (
            marketId TEXT,
            selectionId TEXT,
            fav_rank INTEGER,
            drift_speed REAL,
            inplay_story TEXT,
            confidence_inplay REAL,
            created_at TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_alignment (
            marketId TEXT,
            selectionId TEXT,
            fav_rank INTEGER,
            pre_conf REAL,
            inplay_conf REAL,
            story_divergence REAL,
            winner_flag INTEGER,
            created_at TEXT
        )
    """)
    con.commit()


def _row_val(row, key):
    """Return float value or 0.0 if missing/null."""
    try:
        return float(row[key]) if row[key] is not None else 0.0
    except Exception:
        return 0.0


# ───────────────────────────────────────────────────────────────────────
# Phase 1 — Pre-off Narratives (anchor → OC7)
# ───────────────────────────────────────────────────────────────────────
def build_preoff(con_anchor, con_inbound, con_out, day):
    print("[PREOFF] deriving pre-off narratives…")
    cur_out = con_out.cursor()
    inserted = 0

    try:
        con_anchor.execute(f"ATTACH DATABASE '{autoscalp_db()}' AS gui")
    except Exception as e:
        print(f"[PREOFF] attach warn: {e}")

    rows = con_anchor.execute("""
        SELECT
            b.marketId,
            b.selectionId,
            b.anchor_odd,
            b.OC0,
            b.OC0_band,
            g.oc1, g.oc2, g.oc3, g.oc4, g.oc5, g.oc6, g.oc7,
            t.expected_race_mins,
            t.inplay_progress
        FROM bets AS b
        LEFT JOIN gui.inbound_oc_cache AS g
              ON b.marketId = g.marketId AND b.selectionId = g.selectionId
        LEFT JOIN gui.v7_timing_features_fixed AS t
              ON b.marketId = t.marketId AND b.selectionId = t.selectionId
        WHERE b.date = ? AND b.anchor_odd IS NOT NULL
    """, (day,)).fetchall()

    for r in rows:
        anchor = _row_val(r, "anchor_odd")
        sel = str(r["selectionId"])  # ✅ normalize type
        oc_vals = [_row_val(r, f"oc{i}") for i in range(1, 8)]
        valid = [v for v in oc_vals if v > 0]

        if not valid:
            story, conf = "Tool failure — no pre-off odds.", 0.0
        else:
            first, last = valid[0], valid[-1]
            drift = (last - anchor) / max(1e-9, anchor)
            parts = []
            if any(v <= 0 for v in oc_vals[:2]):
                parts.append("Tool down early.")
            elif len(valid) < 7:
                parts.append("Tool recovered mid-session.")
            if drift < -0.05:
                parts.append("Money arrived early; odds shortened before the off."); conf = 0.7
            elif drift > 0.10:
                parts.append("Odds drifted steadily; weak early backing."); conf = 0.4
            else:
                parts.append("Stable market before the off."); conf = 0.5
            story = " ".join(parts)

        cur_out.execute("""
            INSERT INTO preoff_narratives
              (marketId, selectionId, anchor_odd, preoff_story,
               confidence_preoff, created_at)
            VALUES (?, ?, ?, ?, ?, datetime('now','utc'))
        """, (r["marketId"], sel, anchor, story, conf))
        inserted += 1

    con_out.commit()
    con_anchor.execute("DETACH DATABASE gui;")
    print(f"[PREOFF] ✅ inserted {inserted} rows.")

# ───────────────────────────────────────────────────────────────────────
# Phase 2 — In-play Narratives (OC7 → OC20)
# ───────────────────────────────────────────────────────────────────────
def build_inplay(con_anchor, con_inbound, con_out):
    print("[INPLAY] deriving in-play narratives…")
    cur_out = con_out.cursor()
    inserted = 0

    try:
        cur_inbound = con_inbound.cursor()
        cur_inbound.execute(f"ATTACH DATABASE '{autoscalp_db()}' AS auto")
    except Exception as e:
        print(f"[INPLAY] attach warn: {e}")

    rows = con_inbound.execute("""
        SELECT i.marketId, i.selectionId,
               i.oc7, i.oc8, i.oc9, i.oc10, i.oc11, i.oc12,
               i.oc13, i.oc14, i.oc15, i.oc16, i.oc17, i.oc18, i.oc19, i.oc20,
               t.expected_race_mins, t.inplay_progress, t.drift_speed
          FROM inbound_oc_cache AS i
          LEFT JOIN auto.v7_timing_features_fixed AS t USING (marketId, selectionId)
    """).fetchall()

    for r in rows:
        sel = str(r["selectionId"])  # ✅ normalize type
        oc_vals = [_row_val(r, f"oc{i}") for i in range(7, 21)]
        valid = [v for v in oc_vals if v > 0]
        parts = []
        if not valid:
            parts.append("Tool failure — no in-play data."); conf = 0.0
        else:
            if any(v <= 0 for v in oc_vals[:2]): parts.append("Tool down entering race.")
            elif len(valid) < len(oc_vals): parts.append("Tool recovered mid-race.")
            momentum = []
            for i in range(1, len(valid)):
                drift = (valid[i] - valid[i-1]) / max(1e-9, valid[i-1])
                if abs(drift) > 0.15: momentum.append("race_changed")
                elif abs(drift) < 0.03: momentum.append("race_stable")
            if "race_changed" in momentum: parts.append("Race complexion changed mid-race.")
            elif "race_stable" in momentum[-3:]: parts.append("Race remained stable in final stages.")
            first, last = valid[0], valid[-1]
            drift = (last - first) / max(1e-9, first)
            if drift < -0.10: parts.append("Odds collapsed — likely winner emerged."); conf = 0.9
            elif drift > 0.15: parts.append("Runner weakened — drifted out late."); conf = 0.3
            else: parts.append("Traded steadily through race."); conf = 0.5
        story = " ".join(parts)
        cur_out.execute("""
            INSERT INTO inplay_narratives
              (marketId, selectionId, inplay_story, confidence_inplay, created_at)
            VALUES (?, ?, ?, ?, datetime('now','utc'))
        """, (r["marketId"], sel, story, conf))
        inserted += 1

    con_out.commit()
    print(f"[INPLAY] ✅ inserted {inserted} rows.")


def ensure_story_alignment_schema(con_out):
    """Make sure story_alignment table exists and has all required columns."""
    cur = con_out.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS story_alignment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT,
            selectionId TEXT,
            fav_rank INTEGER,
            pre_conf REAL,
            inplay_conf REAL,
            story_divergence REAL,
            winner_flag INTEGER,
            momentum_class TEXT,
            race_pattern TEXT,
            drift_speed REAL,
            created_at TEXT
        )
    """)
    # add any missing columns (safe idempotent patch)
    cols = {r[1] for r in cur.execute("PRAGMA table_info(story_alignment)")}
    def _add(col, ddl):
        if col not in cols:
            cur.execute(f"ALTER TABLE story_alignment ADD COLUMN {col} {ddl}")
    _add("fav_rank", "INTEGER")
    _add("pre_conf", "REAL")
    _add("inplay_conf", "REAL")
    _add("story_divergence", "REAL")
    _add("winner_flag", "INTEGER")
    _add("momentum_class", "TEXT")
    _add("race_pattern", "TEXT")
    _add("drift_speed", "REAL")
    _add("created_at", "TEXT")
    con_out.commit()


# ───────────────────────────────────────────────────────────────────────
# Phase 3 — Alignment → Cached Single-Market Builder
# ───────────────────────────────────────────────────────────────────────
def build_alignment(con_out, con_auto):
    print("[ALIGN] joining narratives (single-runner cache mode)…")
    ensure_story_alignment_schema(con_out)
    cur = con_out.cursor()
    try:
        cur.execute(f"ATTACH DATABASE '{autoscalp_db()}' AS auto")
    except Exception as e:
        print(f"[ALIGN] attach warn: {e}")

    # Pull all matching marketIds that have both preoff + inplay rows
    mids = con_out.execute("""
        SELECT DISTINCT p.marketId
          FROM preoff_narratives p
          JOIN inplay_narratives i USING (marketId, selectionId)
    """).fetchall()

    print(f"[ALIGN] joining {len(mids)} markets…")
    for (mid,) in mids:
        rows = con_out.execute("""
            SELECT
                p.marketId, p.selectionId,
                p.confidence_preoff AS pre_conf,
                i.confidence_inplay AS inplay_conf,
                (p.confidence_preoff - i.confidence_inplay) AS story_divergence,
                0 AS winner_flag
            FROM preoff_narratives AS p
            JOIN inplay_narratives AS i USING (marketId, selectionId)
            WHERE p.marketId=?
        """, (mid,)).fetchall()

        for r in rows:
            cur.execute("""
                INSERT OR REPLACE INTO story_alignment_cache(
                    day, marketId, selectionId,
                    pre_conf, inplay_conf, story_divergence, winner_flag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now','utc'))
            """, ("2025-10-17", r["marketId"], r["selectionId"],
                  r["pre_conf"], r["inplay_conf"], r["story_divergence"], r["winner_flag"]))
    con_out.commit()
    print("[ALIGN] ✅ inserted alignment cache for all markets.")

# ───────────────────────────────────────────────────────────────────────
# Phase 4 — Console Narratives
# ───────────────────────────────────────────────────────────────────────
def print_narratives(con_out, n_markets=10, n_runners=5):
    print("\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print("[STORY] Sample Market Narratives")
    rows = con_out.execute("""
        SELECT marketId, COUNT(*) AS n, AVG(story_divergence) AS div
          FROM story_alignment GROUP BY marketId
          ORDER BY RANDOM() LIMIT ?
    """, (n_markets,)).fetchall()
    for i, r in enumerate(rows, 1):
        mid = r["marketId"]
        print(f"🏇 {i}. {mid} — Δ={r['div']:+.2f}")
        subs = con_out.execute("""
            SELECT p.preoff_story, i.inplay_story
              FROM preoff_narratives p
              JOIN inplay_narratives i USING(marketId,selectionId)
             WHERE p.marketId=? LIMIT 1
        """, (mid,)).fetchone()
        if subs:
            print(f"   Pre-off: {subs['preoff_story']}")
            print(f"   In-play: {subs['inplay_story']}")
            print("   ───────────────────────────────")

    print("\n[STORY] Runner Narratives")
    rows = con_out.execute("""
        SELECT marketId, selectionId, preoff_story, inplay_story
          FROM preoff_narratives
          JOIN inplay_narratives USING (marketId,selectionId)
         ORDER BY RANDOM() LIMIT ?
    """, (n_runners,)).fetchall()
    for r in rows:
        print(f"🏇 {r['marketId']} | sid={r['selectionId']}")
        print(f"   Pre-off: {r['preoff_story']}")
        print(f"   In-play: {r['inplay_story']}")
        print("   ───────────────────────────────")


# ───────────────────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────────────────
def main():
    day = sys.argv[1] if len(sys.argv) > 1 else None
    con_anchor = sqlite3.connect(bets_db()); con_anchor.row_factory = sqlite3.Row
    con_inbound = sqlite3.connect(autoscalp_db()); con_inbound.row_factory = sqlite3.Row
    con_out = sqlite3.connect(mastery_v7_db()); con_out.row_factory = sqlite3.Row
    _ensure_tables(con_out)

    if not day:
        day = con_anchor.execute(
            "SELECT MAX(date) FROM bets WHERE date IS NOT NULL"
        ).fetchone()[0]
    print(f"[BUILDER] using date={day}")

    t0 = time.time()
    build_preoff(con_anchor, con_inbound, con_out, day)
    build_inplay(con_inbound, con_inbound, con_out)
    build_alignment(con_out, con_inbound)
    print_narratives(con_out)
    print(f"[DONE] all phases completed in {time.time()-t0:.2f}s")

    con_anchor.close(); con_inbound.close(); con_out.close()


if __name__ == "__main__":
    main()
