#!/usr/bin/env python3
"""
engines/mastery/cache_day_builder.py
─────────────────────────────────────────────
Phase 3A — Chronological Day Cache Builder (LOCAL ONLY)

Builds `cache_mastery_day` inside:
  data/mastery_v7.db

There is NO cloud / iCloud mirror.
This DB is the single source of truth for:
  - training
  - River
  - Forest
"""

import os, sqlite3
from datetime import datetime, timezone



# ───────────────────────────────────────────────
# Force LOCAL DB paths (no cloud mirrors)
# ───────────────────────────────────────────────
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
BETS_LOCAL   = os.path.join(PROJECT_ROOT, "data", "bets.db")
GUI_LOCAL    = os.path.join(PROJECT_ROOT, "data", "autoscalp_gui.db")
SETTLE_LOCAL = os.path.join(PROJECT_ROOT, "data", "settlements.db")
MASTERY_LOCAL = os.path.join(PROJECT_ROOT, "data", "mastery_v7.db")


def build_mastery_cache(days: int = 90):
    """Build `cache_mastery_day` table in mastery_v7.db and mirror to cloud."""

    print(f"[cache-day] 🧩 building {days}-day day-level cache …")
    print(f"         BETS     → {BETS_LOCAL}")
    print(f"         GUI      → {GUI_LOCAL}")
    print(f"         FORM     → {SETTLE_LOCAL}")
    print(f"         LOCAL    → {MASTERY_LOCAL}")


    # sanity checks
    for path in (BETS_LOCAL, GUI_LOCAL, SETTLE_LOCAL):
        if not os.path.exists(path):
            raise FileNotFoundError(f"❌ Missing required DB: {path}")

    con = sqlite3.connect(MASTERY_LOCAL, timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # attach source DBs
    cur.execute(f"ATTACH DATABASE '{BETS_LOCAL}' AS bets;")
    cur.execute(f"ATTACH DATABASE '{GUI_LOCAL}' AS gui;")
    cur.execute(f"ATTACH DATABASE '{SETTLE_LOCAL}' AS form;")

    # build unified cache
    cur.executescript(f"""
    DROP TABLE IF EXISTS cache_mastery_day;

    CREATE TABLE cache_mastery_day AS
    SELECT
        b.date                          AS day,
        b.marketId,
        b.selectionId,
        ps.letter,                      -- ✅ take letter from playbooks_settled
        b.horse_name,
        b.event_name,
        b.market_name,
        b.anchor_odd,
        b.OC0                           AS oc0,
        b.OC0_band                      AS oc0_band,
        i.last_sync_ts,
        i.oc1, i.oc2, i.oc3, i.oc4, i.oc5,
        i.oc6, i.oc7, i.oc8, i.oc9, i.oc10,
        i.oc11, i.oc12, i.oc13, i.oc14, i.oc15,
        i.oc16, i.oc17, i.oc18, i.oc19, i.oc20,
        t.drift_speed,
        t.inplay_progress,
        t.expected_race_mins,
        t.pre_vol, t.inplay_vol, t.post_vol,
        o.side,
        o.entry_odds,
        o.exit_odds,
        o.entry_stake,
        o.exit_stake,
        o.realized_pnl,
        o.net_pl,
        o.role,
        o.exit_kind,
        f.runner_name,
        f.win_rate,
        f.avg_pnl            AS form_avg_pnl,
        f.trainer,
        f.jockey,
        f.distance_band,
        f.class_band,
        f.surface,
        datetime('now','utc') AS created_at
    FROM bets.bets AS b
    LEFT JOIN gui.playbooks_settled AS ps
       ON b.marketId = ps.marketId AND b.selectionId = ps.selectionId
    LEFT JOIN gui.inbound_oc_cache AS i
           ON b.marketId=i.marketId AND b.selectionId=i.selectionId
    LEFT JOIN gui.v_timing_features_v7 AS t
           ON b.marketId=t.marketId AND b.selectionId=t.selectionId
    LEFT JOIN gui.orders AS o
           ON b.marketId=o.marketId AND b.selectionId=o.selectionId
    LEFT JOIN form.runner_form_canonical AS f
           ON b.marketId=f.marketId AND b.selectionId=f.selectionId
    WHERE date(b.date) >= date('now','-{days} day');
    """)

    # index for performance
    cur.executescript("""
    CREATE INDEX IF NOT EXISTS idx_day_mid_sid ON cache_mastery_day(day,marketId,selectionId);
    CREATE INDEX IF NOT EXISTS idx_day_mid ON cache_mastery_day(day,marketId);
    """)

    rows = cur.execute("SELECT COUNT(*) FROM cache_mastery_day;").fetchone()[0]
    con.commit()
    print(f"[cache-day] ✅ built cache_mastery_day — {rows:,} rows.")




# --- keep this at the bottom ---
if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    args = ap.parse_args()
    build_mastery_cache(days=args.days)
