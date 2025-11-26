#!/usr/bin/env python3
"""
scripts/build_story_shape.py
───────────────────────────────────────────────────────────────────────
Builds Shape & Timing narratives (layer 2) using v7_shape_summary +
v7_intelligence.  These describe race flow patterns, runner shapes,
and turning points through the race.

Usage:
    python3 scripts/build_story_shape.py [YYYY-MM-DD]
"""

import os, sys, sqlite3, time
from datetime import datetime

# ── sys.path injection FIRST ───────────────────────────────────────────
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── FORCE LOCAL CONTEXT (no cloud redirects) ───────────────────────────
def autoscalp_db():
    """Force local autoscalp_gui.db path for offline builds."""
    return os.path.join(ROOT, "data", "autoscalp_gui.db")

def mastery_v7_db():
    """Write shape narratives directly to the cloud Mastery v7 DB."""
    return os.path.expanduser(
        "~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCloud/data/mastery_v7.db"
    )


# optional debug
print(f"[local-paths] GUI={autoscalp_db()}  MASTERY={mastery_v7_db()}")

# ───────────────────────────────────────────────────────────────────────
def _safe_float(x, d=0.0):
    try:
        return float(x)
    except Exception:
        return float(d)

def _safe_text(x):
    return str(x or "").strip().upper()

def _ensure_shape_table(con):
    cur = con.cursor()
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
    con.commit()

# ───────────────────────────────────────────────────────────────────────
def build_shape_narratives(day: str | None = None):
    gui_db = autoscalp_db()
    out_db = mastery_v7_db()
    con_gui = sqlite3.connect(gui_db)
    con_gui.row_factory = sqlite3.Row
    con_out = sqlite3.connect(out_db)
    con_out.row_factory = sqlite3.Row
    _ensure_shape_table(con_out)

    print(f"[SHAPE] using autoscalp_gui.db → {gui_db}")
    print(f"[SHAPE] writing to → {out_db}")

    # Force the link in case the bash script didn’t persist the view
    cur = con_gui.cursor()
    cur.execute("DROP VIEW IF EXISTS v7_shape_summary;")
    cur.execute("CREATE VIEW v7_shape_summary AS SELECT * FROM v7_shape_summary_cache;")
    con_gui.commit()



    sql = """
        SELECT
            s.marketId, s.selectionId,
            s.race_pattern, s.avg_drift, s.drift_speed AS shape_drift_speed,
            s.inplay_progress, s.expected_race_mins,
            v.momentum_class, v.tick_vel_3s_up, v.slope_ppm
        FROM v7_shape_summary_cache AS s
        LEFT JOIN v7_intelligence AS v
              USING (marketId, selectionId)
    """

    if day:
        sql += " WHERE date(v.day)=date(?)"
        rows = con_gui.execute(sql, (day,)).fetchall()
    else:
        rows = con_gui.execute(sql).fetchall()

    t0 = time.time()
    batch = []
    for r in rows:
        pattern = _safe_text(r["race_pattern"])
        drift_speed = _safe_float(r["shape_drift_speed"])
        momentum = _safe_text(r["momentum_class"])
        tick_up = _safe_float(r["tick_vel_3s_up"])
        slope = _safe_float(r["slope_ppm"])

        parts = []
        if "FRONT" in pattern:
            parts.append("Led early; front-runner bias evident."); conf = 0.6
        elif "CLOSE" in pattern or "FINISH" in pattern:
            parts.append("Closed strongly from mid-pack; late surge."); conf = 0.7
        elif "FADE" in pattern:
            parts.append("Tired mid-race and faded late."); conf = 0.4
        else:
            parts.append("Neutral pattern; traded in rhythm."); conf = 0.5

        if momentum == "SURGE": parts.append("Showed distinct momentum spike in-race."); conf += 0.05
        elif momentum == "STALL": parts.append("Lost momentum during middle phase."); conf -= 0.05
        if drift_speed > 0.10: parts.append("Odds drifted noticeably as pace eased.")
        elif drift_speed < -0.10: parts.append("Odds collapsed — strong finishing move.")
        if tick_up > 3: parts.append("Sustained upward tick velocity detected.")
        if slope > 0.5: parts.append("Positive slope confirmed sustained rally.")
        story = " ".join(parts)
        conf = max(0.0, min(1.0, conf))
        batch.append((r["marketId"], r["selectionId"], pattern, drift_speed, momentum, story, conf))

    if batch:
        con_out.executemany("""
            INSERT INTO shape_narratives(
                marketId, selectionId, race_pattern, drift_speed,
                momentum_class, shape_story, confidence_shape
            ) VALUES (?,?,?,?,?,?,?)
        """, batch)
        con_out.commit()

    print(f"[SHAPE] ✅ inserted {len(rows)} shape_narratives rows in {time.time()-t0:.2f}s")
    con_gui.close(); con_out.close()

# ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    day = sys.argv[1] if len(sys.argv) > 1 else None
    t0 = time.time()
    build_shape_narratives(day)
    print(f"[DONE] shape narratives built in {time.time()-t0:.2f}s")
