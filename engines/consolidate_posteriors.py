#!/usr/bin/env python3
# consolidate_posteriors.py — crunch outcomes → posteriors with checkpointing

import os, sys, sqlite3, json

# --- ensure repo root is importable ---
_HERE = os.path.dirname(os.path.abspath(__file__))      # .../engines
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))      # repo root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from engines.config_paths import autoscalp_db
from engines.mastery import posteriors
from engines.mastery.policy_lookup import get_bin_key

CHUNK_SIZE = 100_000

def ensure_id_column(db: sqlite3.Connection):
    """Ensure mastery_outcomes_raw has an 'id' column and it's populated + indexed."""
    cur = db.cursor()
    cols = {r[1] for r in cur.execute("PRAGMA table_info(mastery_outcomes_raw)")}
    if "id" not in cols:
        cur.execute("ALTER TABLE mastery_outcomes_raw ADD COLUMN id INTEGER;")
        db.commit()

    # Populate any missing ids
    cur.execute("SELECT COUNT(*) FROM mastery_outcomes_raw WHERE id IS NULL")
    if cur.fetchone()[0] > 0:
        cur.execute("""
        WITH numbered AS (
          SELECT rowid AS rid, ROW_NUMBER() OVER (ORDER BY rowid) AS n
          FROM mastery_outcomes_raw
        )
        UPDATE mastery_outcomes_raw
        SET id = (SELECT n FROM numbered WHERE numbered.rid = mastery_outcomes_raw.rowid)
        WHERE id IS NULL;
        """)
        db.commit()

    cur.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_mastery_outcomes_id
        ON mastery_outcomes_raw(id)
    """)
    db.commit()


# === PATCH START ===
# 📍 TARGET: engines/consolidate_posteriors.py
# 🔎 SEARCH: def consolidate():
# 📆 PATCHED: 2025-10-15T01:05Z
# ───────────────────────────────────────────────────────────────
def consolidate():
    # === PATCH START: automatic replay-JSON bridge =========================
    # 📍 TARGET: engines/consolidate_posteriors.py
    # 📆 PATCHED: 2025-10-17T20:10Z — auto-import pending replay_report_*.json
    # ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    import glob, time

    try:
        print("[consolidate] pre-flight check for unimported replay JSONs …")
        db_path = autoscalp_db()
        con_chk = sqlite3.connect(db_path)
        cur_chk = con_chk.cursor()
        cur_chk.execute("""
            CREATE TABLE IF NOT EXISTS mastery_outcomes_raw(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                day TEXT,
                marketId TEXT,
                selectionId TEXT,
                pnl REAL
            );
        """)
        # newest file in /data
        replay_files = sorted(
            glob.glob(os.path.join(os.path.dirname(db_path), "replay_report_*.json")),
            key=os.path.getmtime,
            reverse=True
        )
        if replay_files:
            latest = replay_files[0]
            last_id = cur_chk.execute(
                "SELECT MAX(id) FROM mastery_outcomes_raw WHERE source='REPLAY'"
            ).fetchone()[0] or 0
            file_mtime = os.path.getmtime(latest)
            if last_id == 0 or (time.time() - file_mtime < 3600*48):
                with open(latest, "r") as f:
                    data = json.load(f)
                print(f"[consolidate] importing {len(data):,} rows from {os.path.basename(latest)}")
                for i, o in enumerate(data, 1):
                    cur_chk.execute("""
                        INSERT INTO mastery_outcomes_raw(source, day, marketId, selectionId, pnl)
                        VALUES('REPLAY', ?, ?, ?, ?)
                    """, (
                        o.get("day") or "",
                        o.get("mid") or "",
                        o.get("sid") or "",
                        float(o.get("pnl") or 0.0)
                    ))
                    if i % 500000 == 0:
                        con_chk.commit()
                        print(f"[consolidate] committed {i:,} replay rows …")
                con_chk.commit()
                print(f"[consolidate] ✅ imported {len(data):,} replay rows into mastery_outcomes_raw")
            else:
                print("[consolidate] replay data already up-to-date.")
        con_chk.close()
    except Exception as e:
        print(f"[consolidate] warn replay bridge: {e}")
    # === PATCH END =========================================================

    # ── Step 0: merge contextual digest into mastery_posteriors ────────────
    try:
        print("[consolidate] syncing contextual digest → mastery_posteriors …")
        from engines.mastery import posteriors
        n = posteriors.update_from_digest(auto_commit=True)
        print(f"[consolidate] contextual bins updated: {n}")
    except Exception as e:
        print(f"[consolidate] warn: digest sync failed → {e}")


    # ── Step 1: open autoscalp as main ─────────────────────────────────────
    db = sqlite3.connect(autoscalp_db()); db.row_factory = sqlite3.Row
    # attach bets + settlements for cross-queries
    db.execute("ATTACH 'data/bets.db' AS betsdb")
    db.execute("ATTACH 'data/settlements.db' AS setdb")
    cur = db.cursor()

    posteriors.ensure_schema(db)
# === PATCH END ===


# === PATCH START: integrate liability_signals into posteriors consolidation ===
# 📍 TARGET: engines/consolidate_posteriors.py
# 📆 PATCHED: 2025-10-23T07:45Z — join liability telemetry with outcomes (non-breaking)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        print("[consolidate] ensuring v_liability_context view …")
        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_liability_context AS
            SELECT m.*,
                   COALESCE(ls.liability, 0.0) AS liab_value,
                   CASE WHEN ls.liability > 250.0 THEN 1 ELSE 0 END AS liab_over250
              FROM mastery_outcomes_raw m
              LEFT JOIN liability_signals ls
                ON m.marketId = ls.marketId
               AND m.selectionId = ls.selectionId
               AND m.day = ls.day;
        """)
        print("[consolidate] liability context view ready.")
    except Exception as e:
        print(f"[consolidate] warn: could not create liability context view → {e}")
# === PATCH END ===


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/consolidate_posteriors.py
# 🔎 SEARCH: # Find last processed rowid
# 📆 PATCHED: 2025-10-07
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # Find last processed rowid
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mastery_posteriors_progress(
            processed_outcome_id INTEGER
        )
    """)
    row = cur.execute("SELECT processed_outcome_id FROM mastery_posteriors_progress").fetchone()
    last_rowid = row["processed_outcome_id"] if row and row["processed_outcome_id"] else 0
    print(f"[consolidate] resuming from rowid>{last_rowid}")




    total = cur.execute("SELECT COUNT(*) FROM mastery_outcomes_raw").fetchone()[0]
    processed = 0

    while True:
        rows = cur.execute(f"""
            SELECT id, day, marketId, selectionId,
                   letter, distance_band, fav_rank,
                   day_of_week, surface, class_band,
                   target_ticks, realized_ticks, success,
                   stoploss_hit, hedge_hit, entry_px,
                   pnl, weight, priority
              FROM v_liability_context
             WHERE id > ?
             ORDER BY id
             LIMIT {CHUNK_SIZE}
        """, (last_rowid,)).fetchall()


        if not rows:
            break

        for r in rows:
            try:
                bin_key = get_bin_key(
                    r["distance_band"] or "unk",
                    "STEAM" if (r["letter"] or "").startswith("B") else "DRIFT",
                    "30-10",
                    r["day_of_week"] or "unk",
                    r["surface"] or "unk",
                    r["fav_rank"] or "unk"
                )

                # --- lookup wins/losses + winner names ---
                rr = db.execute("""
                    WITH outcomes AS (
                      SELECT 
                        b.horse_name,
                        b.selectionId,
                        b.marketId,
                        SUM(s.profit) AS net_pnl,
                        CASE WHEN SUM(s.profit) > 0 THEN 1 ELSE 0 END AS win
                      FROM betsdb.bets b
                      JOIN setdb.bf_cleared_orders s
                        ON b.marketId=s.marketId AND b.selectionId=s.selectionId
                      WHERE b.marketId=?
                      GROUP BY b.marketId, b.selectionId, b.horse_name
                    )
                    SELECT 
                      SUM(win) AS wins,
                      COUNT(*)-SUM(win) AS losses,
                      SUM(net_pnl) AS net_pnl,
                      GROUP_CONCAT(CASE WHEN win=1 THEN horse_name END) AS winner_names
                    FROM outcomes
                """, (r["marketId"],)).fetchone()

                wins    = rr["wins"]   if rr and rr["wins"]   is not None else 0
                losses  = rr["losses"] if rr and rr["losses"] is not None else 0
                winners = rr["winner_names"].split(",") if rr and rr["winner_names"] else []

                posteriors.update_after_outcome(
                    db,
                    bin_key=bin_key,
                    target_ticks=int(r["target_ticks"] or 1),
                    success=bool(r["success"]),
                    fill_observed=True,
                    mae_ticks=float(r["realized_ticks"] or 0.0),
                    letter=r["letter"] or "",
                    band=r["class_band"] or "",
                    epic_stories=0,
                    stoploss_hit=bool(r["stoploss_hit"]),
                    hedge_hit=bool(r["hedge_hit"]),
                    net_pnl=float(r["pnl"] or 0.0) * float(r["weight"] or 1.0),
                    source="REPLAY",
                    source_run=r["day"] or "REPLAY",
                    priority=int(r["priority"] or 99),
                    weight_applied=float(r["weight"] or 1.0),
                    day_of_week=r["day_of_week"] or "unk",
                    surface_type=r["surface"] or "unk",
                    winners=json.dumps(winners) if winners else None
                )

                last_rowid = r["id"]

            except Exception as e:
                print(f"[consolidate] warn update_after_outcome: {e}")
                continue

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/consolidate_posteriors.py
# 🔎 SEARCH: # Persist checkpoint every batch
# 📆 PATCHED: 2025-10-07
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
        # Persist checkpoint every batch
        cur.execute("DELETE FROM mastery_posteriors_progress")
        cur.execute(
            "INSERT INTO mastery_posteriors_progress(processed_outcome_id) VALUES(?)",
            (last_rowid,)
        )
        db.commit()


        processed += len(rows)
        print(f"[consolidate] processed {processed:,}/{total:,} (checkpoint={last_rowid})")


    db.close()
    print(f"[consolidate] done → mastery_posteriors updated up to rowid={last_rowid}")

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 📍 TARGET: engines/consolidate_posteriors.py
# 🔎 SEARCH: print\(f"\[consolidate\] done → mastery_posteriors updated up to rowid=
# 📆 PATCHED: 2025-10-14T23:30Z
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- PATCH START: trigger mastery snapshot rebuild ---
    try:
        from engines.mastery import snapshot_ledger
        snapshot_ledger.update()
        print("[consolidate] mastery snapshots refreshed via snapshot_ledger")
    except Exception as e:
        print(f"[consolidate] mastery snapshot warn: {e}")
    # --- PATCH END ---

# === PATCH START: Live Overwatcher + Feedback integration ======================
# 📍 TARGET: engines/consolidate_posteriors.py (append before multi-source pass)
# 📆 PATCHED: 2025-10-26Z — fold Overwatcher / Feedback outcomes into posteriors
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Live Overwatcher / Feedback Integration --------------------------------
    try:
        print("\n[consolidate] scanning mastery_outcomes_raw for LIVE_FEEDBACK rows …")
        pdb = sqlite3.connect(autoscalp_db()); pdb.row_factory = sqlite3.Row
        cur = pdb.cursor()

        live_rows = cur.execute("""
            SELECT id, day, marketId, selectionId,
                   letter, class_band AS band,
                   target_ticks, realized_ticks, success,
                   stoploss_hit, hedge_hit, entry_px, pnl,
                   weight, priority
              FROM mastery_outcomes_raw
             WHERE COALESCE(source,'LIVE_FEEDBACK') IN ('LIVE_FEEDBACK','OVERWATCHER','MASTERYPOLICY')
               AND date(day) >= date('now','-3 day')
        """).fetchall()

        if not live_rows:
            print("[consolidate] no LIVE_FEEDBACK rows found (nothing new to fold in).")
        else:
            print(f"[consolidate] merging {len(live_rows):,} LIVE_FEEDBACK rows → mastery_posteriors …")
            merged_live = 0
            for r in live_rows:
                try:
                    bin_key = get_bin_key(
                        r["band"] or "unk",
                        "STEAM" if (r["letter"] or "").startswith("B") else "DRIFT",
                        "30-10", "unk", "unk", "unk"
                    )
                    posteriors.update_after_outcome(
                        pdb,
                        bin_key=bin_key,
                        target_ticks=int(r["target_ticks"] or 1),
                        success=bool(r["success"]),
                        fill_observed=True,
                        mae_ticks=float(r["realized_ticks"] or 0.0),
                        letter=r["letter"] or "",
                        band=r["band"] or "",
                        epic_stories=0,
                        stoploss_hit=bool(r["stoploss_hit"]),
                        hedge_hit=bool(r["hedge_hit"]),
                        net_pnl=float(r["pnl"] or 0.0),
                        source="LIVE_FEEDBACK",
                        source_run=r["day"] or "LIVE_FEEDBACK",
                        priority=int(r["priority"] or 99),
                        weight_applied=float(r["weight"] or 1.0),
                        day_of_week="unk",
                        surface_type="unk",
                        winners=None
                    )
                    merged_live += 1
                except Exception as e:
                    print(f"[consolidate] warn LIVE_FEEDBACK id={r['id']}: {e}")
                    continue

            pdb.commit()
            print(f"[consolidate] ✅ LIVE_FEEDBACK rows merged: {merged_live:,}")
        pdb.close()
    except Exception as e:
        print(f"[consolidate] warn LIVE_FEEDBACK integration: {e}")
# === PATCH END =================================================================
# === PATCH START: create v_mastery_training_view ==========================
# 📍 TARGET: engines/consolidate_posteriors.py (end of consolidate)
# 📆 PATCHED: 2025-10-26Z — training dataset materialization
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        print("[consolidate] building v_mastery_training_view …")
        pdb = sqlite3.connect(autoscalp_db())
        cur = pdb.cursor()
        cur.execute("""
            CREATE VIEW IF NOT EXISTS v_mastery_training AS
            SELECT mc.marketId,
                   mc.pnl_total,
                   mc.liability_total,
                   CASE WHEN mc.liability_total>0
                        THEN mc.pnl_total/mc.liability_total ELSE 0 END AS ratio,
                   mc.slope_ppm,
                   mc.tick_vel_3s_up,
                   mc.mto_minutes,
                   me.event_type AS action,
                   COALESCE(sr.net,0.0) AS reward
              FROM mastery_cache mc
              JOIN mastery_events me ON me.details_json LIKE '%'||mc.marketId||'%'
              LEFT JOIN st_runner_day_totals sr ON sr.marketId=mc.marketId
             WHERE date(mc.ts)=date('now','utc')
        """)
        pdb.commit(); pdb.close()
        print("[consolidate] ✅ v_mastery_training_view ready.")
    except Exception as e:
        print(f"[consolidate] warn training view: {e}")
# === PATCH END ===========================================================

# === PATCH START: goal-weighted posterior updates ===============================
# 📍 TARGET: engines/consolidate_posteriors.py (after LIVE_FEEDBACK integration)
# 📆 PATCHED: 2025-10-26Z — weight posterior updates by Core Value proximity
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    try:
        print("[consolidate] applying Core-Value goal weighting …")
        from engines.config_core_values import get_core_values
        vals = get_core_values()
        profit_tgt = vals["profit_target"]
        max_loss   = vals["max_loss"]

        pdb = sqlite3.connect(autoscalp_db()); pdb.row_factory = sqlite3.Row
        cur = pdb.cursor()

        rows = cur.execute("""
            SELECT rowid AS id, day, marketId, selectionId,
                   letter, class_band AS band,
                   pnl, weight, priority
              FROM mastery_outcomes_raw
             WHERE date(day)=date('now','utc')
               AND COALESCE(source,'LIVE_FEEDBACK') IN
                   ('LIVE_FEEDBACK','OVERWATCHER','MASTERYPOLICY','PLAYBOOKS_LIVE')
        """).fetchall()

        if not rows:
            print("[consolidate] no goal-weighted rows today.")
        else:
            adj = 0
            for r in rows:
                pnl = float(r["pnl"] or 0.0)
                # Scale by distance to profit/loss band: 1.0 at £32, 0 at |pnl|≥|£90|
                if pnl >= 0:
                    align = min(1.0, pnl / profit_tgt)
                else:
                    align = max(0.0, 1 - abs(pnl / max_loss))
                weight_adj = float(r["weight"] or 1.0) * align

                bin_key = get_bin_key(
                    r["band"] or "unk",
                    "STEAM" if (r["letter"] or "").startswith("B") else "DRIFT",
                    "30-10", "unk", "unk", "unk"
                )
                posteriors.update_after_outcome(
                    pdb,
                    bin_key=bin_key,
                    target_ticks=1,
                    success=(pnl > 0),
                    fill_observed=True,
                    mae_ticks=0.0,
                    letter=r["letter"] or "",
                    band=r["band"] or "",
                    epic_stories=0,
                    stoploss_hit=(pnl <= max_loss),
                    hedge_hit=False,
                    net_pnl=pnl,
                    source="GOAL_WEIGHTED",
                    source_run=r["day"] or "LIVE",
                    priority=int(r["priority"] or 99),
                    weight_applied=weight_adj,
                    day_of_week="unk",
                    surface_type="unk",
                    winners=None
                )
                adj += 1
            pdb.commit(); pdb.close()
            print(f"[consolidate] ✅ goal-weighted updates applied: {adj:,}")
    except Exception as e:
        print(f"[consolidate] warn goal-weighted updates: {e}")
# === PATCH END =================================================================

# === PATCH START: Playbooks Integration (schema-aligned, 7d window) ============
# 📍 TARGET: engines/consolidate_posteriors.py
# 📆 PATCHED: 2025-10-19T10:20Z — Playbooks merge using live schema (7-day window)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Playbooks Integration Pass (own connection) ---------------------------
    try:
        print("\n[consolidate] scanning mastery_outcomes_raw for recent PLAYBOOKS rows …")

        # open a fresh connection dedicated to this merge
        pdb = sqlite3.connect(autoscalp_db())
        pdb.row_factory = sqlite3.Row
        cur = pdb.cursor()

        play_rows = cur.execute("""
            SELECT rowid AS id, day, marketId, selectionId,
                   letter,
                   COALESCE(distance_band,'unk') AS distance_band,
                   COALESCE(fav_rank,'unk')      AS fav_rank_bin,
                   COALESCE(day_of_week,'unk')   AS day_of_week,
                   COALESCE(surface,'unk')       AS surface_type,
                   COALESCE(class_band,'unk')    AS class_band,
                   target_ticks, realized_ticks, success,
                   stoploss_hit, hedge_hit, entry_px, pnl,
                   weight, priority
              FROM mastery_outcomes_raw
             WHERE date(day) >= date('now','-7 day')
               AND COALESCE(source,'PLAYBOOKS_LIVE') IN ('PLAYBOOKS','PLAYBOOKS_LIVE')
        """).fetchall()

        if not play_rows:
            print("[consolidate] no recent Playbooks rows found (nothing to merge).")
        else:
            print(f"[consolidate] merging {len(play_rows):,} Playbooks rows → mastery_posteriors …")
            processed_play = 0

            for r in play_rows:
                try:
                    bin_key = get_bin_key(
                        r["distance_band"] or "unk",
                        "STEAM" if (r["letter"] or "").startswith("B") else "DRIFT",
                        "30-10",
                        r["day_of_week"] or "unk",
                        r["surface_type"] or "unk",
                        r["fav_rank_bin"] or "unk"
                    )

                    posteriors.update_after_outcome(
                        pdb,
                        bin_key=bin_key,
                        target_ticks=int(r["target_ticks"] or 1),
                        success=bool(r["success"]),
                        fill_observed=True,
                        mae_ticks=float(r["realized_ticks"] or 0.0),
                        letter=r["letter"] or "",
                        band=r["class_band"] or "",
                        epic_stories=0,
                        stoploss_hit=bool(r["stoploss_hit"]),
                        hedge_hit=bool(r["hedge_hit"]),
                        net_pnl=float(r["pnl"] or 0.0) * float(r["weight"] or 1.0),
                        source="PLAYBOOKS_LIVE",
                        source_run=r["day"] or "PLAYBOOKS_LIVE",
                        priority=int(r["priority"] or 99),
                        weight_applied=float(r["weight"] or 1.0),
                        day_of_week=r["day_of_week"] or "unk",
                        surface_type=r["surface_type"] or "unk",
                        winners=None
                    )
                    processed_play += 1
                except Exception as e:
                    print(f"[consolidate] warn playbooks update_after_outcome id={r['id']}: {e}")
                    continue

            pdb.commit()
            print(f"[consolidate] ✅ Playbooks rows merged: {processed_play:,}")
        pdb.close()

    except Exception as e:
        print(f"[consolidate] warn Playbooks integration: {e}")
# === PATCH END =================================================================

# === PATCH START: adaptive weighting logic ================================
# 📍 TARGET: engines/consolidate_posteriors.py
# 📆 PATCHED: 2025-10-17T21:15Z — auto-balance replay weight based on live volume
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Adaptive Replay Weight Computation --------------------------------
    try:
        pdb = sqlite3.connect(autoscalp_db()); pdb.row_factory = sqlite3.Row
        cur = pdb.cursor()
        live_rows = cur.execute("""
            SELECT COUNT(*) FROM mastery_outcomes_raw
             WHERE COALESCE(source,'PLAYBOOKS')='PLAYBOOKS'
        """).fetchone()[0] or 0
        replay_rows = cur.execute("""
            SELECT COUNT(*) FROM mastery_outcomes_raw
             WHERE COALESCE(source,'REPLAY')='REPLAY'
        """).fetchone()[0] or 0
        pdb.close()

        if live_rows == 0:
            adaptive_replay_weight = 0.40
        else:
            adaptive_replay_weight = round(
                min(0.40, max(0.15, 0.25 * (10000 / max(3000, live_rows)))),
                2
            )

        print(f"[consolidate] adaptive weight: live_rows={live_rows:,} "
              f"replay_rows={replay_rows:,} → replay_weight={adaptive_replay_weight}")
    except Exception as e:
        adaptive_replay_weight = 0.25
        print(f"[consolidate] warn adaptive weight fallback: {e}")
# === PATCH END =============================================================

# === PATCH START: Multi-source consolidation (PLAYBOOKS_LIVE + PLAYBOOKS_REPLAY) ===
# 📍 TARGET: engines/consolidate_posteriors.py (append after Playbooks Integration)
# 📆 PATCHED: 2025-10-17T14:45Z — safe layered merge, source-weighted
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    # --- Extended multi-source pass --------------------------------------------
    try:
        print("\n[consolidate] scanning mastery_outcomes_raw for multi-source rows …")
        pdb = sqlite3.connect(autoscalp_db()); pdb.row_factory = sqlite3.Row
        cur = pdb.cursor()

        # detect all distinct sources (excluding the base 'PLAYBOOKS' handled above)
        srcs = [r[0] for r in cur.execute("""
            SELECT DISTINCT source FROM mastery_outcomes_raw
             WHERE source IS NOT NULL AND source NOT IN ('PLAYBOOKS','REPLAY')
        """).fetchall()]
        if not srcs:
            print("[consolidate] no additional sources found.")
        else:
            for src in srcs:
                weight_mult = 1.0 if "LIVE" in src else 0.25
                print(f"[consolidate] processing source={src} weight={weight_mult}")

                rows = cur.execute("""
                    SELECT rowid AS id, day, marketId, selectionId,
                           letter, class_band AS band,
                           target_ticks, realized_ticks, success,
                           stoploss_hit, hedge_hit, entry_px, pnl,
                           weight, priority
                      FROM mastery_outcomes_raw
                     WHERE COALESCE(source,'PLAYBOOKS')=?
                """, (src,)).fetchall()

                if not rows:
                    print(f"[consolidate] no rows found for {src}")
                    continue

                processed_src = 0
                for r in rows:
                    try:
                        bin_key = get_bin_key(
                            r["band"] or "unk",
                            "STEAM" if (r["letter"] or "").startswith("B") else "DRIFT",
                            "30-10", "unk", "unk", "unk"
                        )
                        posteriors.update_after_outcome(
                            pdb,
                            bin_key=bin_key,
                            target_ticks=int(r["target_ticks"] or 1),
                            success=bool(r["success"]),
                            fill_observed=True,
                            mae_ticks=float(r["realized_ticks"] or 0.0),
                            letter=r["letter"] or "",
                            band=r["band"] or "",
                            epic_stories=0,
                            stoploss_hit=bool(r["stoploss_hit"]),
                            hedge_hit=bool(r["hedge_hit"]),
                            net_pnl=float(r["pnl"] or 0.0),
                            source=src,
                            source_run=r["day"] or src,
                            priority=int(r["priority"] or 99),
                            weight_applied=float(r["weight"] or 1.0) * weight_mult,
                            day_of_week="unk",
                            surface_type="unk",
                            winners=None
                        )
                        processed_src += 1
                    except Exception as e:
                        print(f"[consolidate] warn {src} id={r['id']}: {e}")
                        continue

                pdb.commit()
                print(f"[consolidate] ✅ {src} merged → {processed_src:,} rows")

        pdb.close()
    except Exception as e:
        print(f"[consolidate] warn multi-source pass: {e}")
# === PATCH END =================================================================
# === PATCH START: Goal Adapter Integration =====================================
# 📍 TARGET: engines/consolidate_posteriors.py (end of __main__)
# 📆 PATCHED: 2025-10-27Z — integrate goal_adapter live metrics & persistence
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    consolidate()

    try:
        import sqlite3, json
        from engines.config_paths import autoscalp_db
        from engines.mastery.goal_adapter import as_feedback_dict
        from engines.mastery import event_sink

        con = sqlite3.connect(autoscalp_db())
        con.row_factory = sqlite3.Row

        # --- Core live aggregates -------------------------------------------
        row = con.execute("""
            SELECT 
                AVG(pnl) AS avg_pnl,
                SUM(CASE WHEN pnl>0 THEN 1 ELSE 0 END)*1.0/NULLIF(COUNT(*),0) AS win_rate
              FROM mastery_outcomes_raw
             WHERE date(day)=date('now','utc')
        """).fetchone()
        live_pnl  = float(row["avg_pnl"] or 0.0)
        win_rate  = float(row["win_rate"] or 0.0)

        # matched ratio from live cache (parents vs children)
        match_row = con.execute("""
            SELECT 
                SUM(CASE WHEN event_type='cashout_tick' THEN 1 ELSE 0 END)*1.0/
                NULLIF(COUNT(*),0)
              FROM mastery_cache
             WHERE date(ts)=date('now','utc')
        """).fetchone()
        matched_ratio = float(match_row[0] or 0.0)
        con.close()

        # --- Evaluate goal alignment ----------------------------------------
        payload = as_feedback_dict(
            live_pnl     = live_pnl,
            win_rate     = win_rate,
            matched_ratio= matched_ratio
        )

        # Emit to mastery_cache & log
        event_sink.emit("goal_alignment_tick", payload)
        print(f"[goal_adapter] ✅ Alignment={payload['goal_alignment']:.3f}  "
              f"PnL={live_pnl:.2f}  Win={win_rate:.2%}  Match={matched_ratio:.2%}")

        # Optional: persist to mastery_state for reference
        try:
            con2 = sqlite3.connect(autoscalp_db())
            con2.execute("""
                CREATE TABLE IF NOT EXISTS mastery_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT DEFAULT (datetime('now','utc')),
                    goal_alignment REAL DEFAULT 0.0,
                    meta_json TEXT
                )
            """)
            con2.execute("""
                INSERT INTO mastery_state(goal_alignment, meta_json)
                VALUES (?, ?)
            """, (payload["goal_alignment"], json.dumps(payload)))
            con2.commit(); con2.close()
        except Exception as e:
            print(f"[goal_adapter] warn persistence: {e}")

    except Exception as e:
        print(f"[goal_adapter] warn final integration: {e}")
# === PATCH END =================================================================

