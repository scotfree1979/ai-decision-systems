#!/usr/bin/env python3
"""
Populate mastery_posteriors from ALL saved replay reports (data/replay_report_*.json),
oldest → newest. Safe to re-run; updates are Bayesian (idempotent in spirit).
"""

import os, sys, glob, json, sqlite3

# 1) Repo-root import shim so imports resolve like Mastery/replay do
_HERE = os.path.dirname(os.path.abspath(__file__))      # .../engines
_ROOT = os.path.abspath(os.path.join(_HERE, ".."))      # repo root
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# 2) Primary imports (same primitives Mastery uses)
from engines.config_paths import connect_db
from engines.mastery import posteriors                  # ensure_schema, update_after_outcome
from engines.mastery.policy_lookup import get_bin_key   # MUST be called with 5 args

# --- one-time runtime sanity for the exact get_bin_key being used
if __name__ == "__main__":
    try:
        import inspect, engines.mastery.policy_lookup as _pl
        print(f"[loader] policy_lookup module  : {_pl.__file__}")
        print(f"[loader] get_bin_key signature: {inspect.signature(get_bin_key)}")
    except Exception as e:
        print("[loader] signature probe warn:", e)


# 3) Helper: list all reports sorted oldest → newest
def _list_reports() -> list[str]:
    files = sorted(glob.glob(os.path.join("data", "replay_report_*.json")))
    if not files:
        raise FileNotFoundError("No replay_report_*.json found in data/")
    return files

# 4) Helper: normalize fields from an outcome dict with safe defaults
def _extract_bin_dims(o: dict) -> tuple[str,str,str,str,str,str]:
    """
    Returns the 6 strings required by get_bin_key:
      distance_band, code, tto_window, day_of_week, surface_type, fav_rank_bin
    """
    distance_band = str(o.get("distance_band", "1m-1m2") or "1m-1m2")
    code          = str(o.get("code",          "FLAT")   or "FLAT")
    tto_window    = str(o.get("tto_window",    "30-10")  or "30-10")
    day_of_week   = str(o.get("day_of_week",   "unk")    or "unk")
    surface_type  = str(o.get("surface_type",  "unk")    or "unk")
    fav_rank_bin  = str(o.get("fav_rank_bin",  "fav")    or "fav")

    return distance_band, code, tto_window, day_of_week, surface_type, fav_rank_bin

# 5) Main loader
def populate_from_reports() -> None:
    files = _list_reports()
    print(f"[loader] Found {len(files)} replay reports to import")

    # Open autoscalp_gui.db directly (contains mastery_outcomes_raw)
    from engines.config_paths import autoscalp_db
    db = sqlite3.connect(autoscalp_db())
    db.row_factory = sqlite3.Row


    # Make sure table exists
    posteriors.ensure_schema(db)

    total_outcomes = 0
    total_commits  = 0
    bins_touched   = set()

    # Improve write throughput a bit
    try:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=NORMAL")
        db.execute("PRAGMA temp_store=MEMORY")
    except Exception:
        pass

    for idx, path in enumerate(files, 1):
        base = os.path.basename(path)
        print(f"\n[loader] [{idx}/{len(files)}] Processing {base} …")

        try:
            with open(path, "r") as f:
                outcomes = json.load(f)
        except Exception as e:
            print(f"[loader]   skip {base}: {e}")
            continue

        print(f"[loader]   loaded {len(outcomes):,} outcomes")

        applied = 0
        for i, o in enumerate(outcomes, 1):
            # 5a) Pull bin dimensions (5) and build bin_key
            dist, code, tto, dow, surface, fav = _extract_bin_dims(o)
            try:
                bin_key = get_bin_key(dist, code, tto, dow, surface, fav)
            except Exception as e:
                if i <= 5:
                    print(f"[loader]   FAIL get_bin_key on record #{i}: {e}")
                    print(f"[loader]   args -> distance_band={dist!r} code={code!r} tto_window={tto!r} class_band={klass!r} fav_rank_bin={fav!r}")
                continue

            # 5b) Insert each outcome into mastery_outcomes_raw
            try:
                cur = db.cursor()
                cur.execute("""
                    INSERT OR IGNORE INTO mastery_outcomes_raw(
                        day, marketId, selectionId, letter, distance_band,
                        fav_rank, day_of_week, surface, class_band,
                        target_ticks, realized_ticks, success,
                        stoploss_hit, hedge_hit, entry_px,
                        pnl, weight, priority, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','utc'))
                """, (
                    o.get("day"),
                    o.get("mid"),
                    o.get("sid"),
                    o.get("letter",""),
                    o.get("distance_band","unk"),
                    o.get("fav_rank_bin","unk"),
                    o.get("day_of_week","unk"),
                    o.get("surface_type","unk"),
                    o.get("class_band",""),
                    int(o.get("target_ticks",0)),
                    int(o.get("realized_ticks",0)),
                    int(o.get("success",0)),
                    int(o.get("stoploss",0)),
                    int(o.get("hedge",0)),
                    float(o.get("entry_px",0.0)),
                    float(o.get("pnl",0.0)),
                    float(o.get("weight",1.0)),
                    int(o.get("priority",99)),
                ))
            except Exception as e:
                if i <= 5:
                    print(f"[loader]   warn insert mastery_outcomes_raw on record #{i}: {e}")
                continue


            applied += 1
            total_outcomes += 1
            bins_touched.add(bin_key)

            # 5c) Periodic commits for safety + visibility
            if applied % 10000 == 0:
                db.commit()
                total_commits += 1
                print(f"[loader]   committed {applied:,} in {base} (total {total_outcomes:,})")

        # Mark file as ingested
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS replay_reports_ingested(
                    filename TEXT PRIMARY KEY,
                    outcomes INTEGER,
                    ingested_at TEXT
                )
            """)
            cur.execute(
                "INSERT OR REPLACE INTO replay_reports_ingested(filename,outcomes,ingested_at) VALUES (?,?,datetime('now','utc'))",
                (base, applied)
            )
            db.commit()
        except Exception as e:
            print(f"[loader] warn marking file {base} ingested: {e}")



        # 5d) Commit end-of-file
        db.commit()
        print(f"[loader]   finished {base}: applied {applied:,} / {len(outcomes):,}")

    # 6) Final close + summary
    db.close()
    print("\n[loader] All reports processed.")
    print(f"[loader] Total outcomes applied: {total_outcomes:,}")
    print(f"[loader] Total commits:          {total_commits:,}")
    print(f"[loader] Distinct bins touched:  {len(bins_touched):,}")

    # Optional: quick verification query (bin count)
    try:
        db2 = connect_db(ro=True); db2.row_factory = sqlite3.Row
        c = db2.execute("SELECT COUNT(*) AS c FROM mastery_posteriors").fetchone()["c"]
        d = db2.execute("SELECT COUNT(DISTINCT bin_key) AS d FROM mastery_posteriors").fetchone()["d"]
        print(f"[loader] DB now has rows={c:,}, distinct bin_key={d:,}")
        db2.close()
    except Exception:
        pass


if __name__ == "__main__":
    populate_from_reports()
