#!/usr/bin/env python3
# ===============================================================
# AutoScalp Schema Guard v3 — Full Self-Recovery
# ===============================================================
# Auto-repairs missing columns, bootstraps critical tables,
# and fully rebuilds cache DB schema if it’s missing or corrupt.
# ===============================================================

import sqlite3, os, shutil
from pathlib import Path

DATA = Path("data")
CACHE = Path("~/Library/Mobile Documents/com~apple~CloudDocs/AutoScalpCache").expanduser()

DBS = {
    "AUTO_MAIN": DATA / "autoscalp_gui.db",
    "AUTO_CACHE": CACHE / "autoscalp_gui_cache.db",
}

# -----------------------------------------------------------------
# Utility: safe column introspection
# -----------------------------------------------------------------
def list_columns(db_path):
    if not os.path.exists(db_path):
        return {}
    schema = {}
    try:
        with sqlite3.connect(db_path) as con:
            tables = con.execute(
                "SELECT name FROM sqlite_master WHERE type='table';"
            ).fetchall()
            for (name,) in tables:
                try:
                    cols = [r[1] for r in con.execute(f"PRAGMA table_info({name});").fetchall()]
                    schema[name] = cols
                except Exception:
                    pass
    except Exception as e:
        print(f"[schema_guard] ⚠️ Could not read schema from {db_path}: {e}")
    return schema

# -----------------------------------------------------------------
# Utility: generate CREATE VIEW for _compat
# -----------------------------------------------------------------
def create_compat_sql(table, src_cols, missing):
    parts = [f"{c}" for c in src_cols]
    for c in missing:
        default = (
            "0 AS " + c
            if any(x in c for x in ("id", "stake", "pnl", "ticks"))
            else "NULL AS " + c
        )
        parts.append(default)
    return f"""
    DROP VIEW IF EXISTS _{table}_compat;
    CREATE VIEW _{table}_compat AS
    SELECT {', '.join(parts)}
    FROM {table};
    """

# -----------------------------------------------------------------
# Phase 0 — full rebuild if cache DB is missing or corrupt
# -----------------------------------------------------------------
def rebuild_cache_from_main(main_db, cache_db):
    try:
        print(f"[schema_guard] 🏗️ Rebuilding cache DB schema → {cache_db}")
        if os.path.exists(cache_db):
            os.remove(cache_db)
        os.makedirs(cache_db.parent, exist_ok=True)
        with sqlite3.connect(main_db) as src, sqlite3.connect(cache_db) as dst:
            tables = src.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%';"
            ).fetchall()
            for name, ddl in tables:
                if ddl:
                    try:
                        dst.execute(ddl)
                        print(f"  • cloned table {name}")
                    except Exception as e:
                        print(f"  • skipped {name}: {e}")
            dst.commit()
        print("[schema_guard] ✅ Cache DB schema rebuilt successfully.")
    except Exception as e:
        print(f"[schema_guard] ❌ Failed to rebuild cache DB: {e}")

# -----------------------------------------------------------------
# Phase 1 — heal missing columns via _compat
# -----------------------------------------------------------------
def repair_column_drift(main_db, cache_db):
    main_schema = list_columns(main_db)
    cache_schema = list_columns(cache_db)
    core = set(main_schema) & set(cache_schema)
    for t in core:
        cols_main = main_schema[t]
        cols_cache = cache_schema[t]
        missing = [c for c in cols_main if c not in cols_cache]
        if missing:
            sql = create_compat_sql(t, cols_cache, missing)
            with sqlite3.connect(cache_db) as con:
                con.executescript(sql)
            print(f"[schema_guard] ✅ Created _{t}_compat (added {len(missing)} cols).")

# -----------------------------------------------------------------
# Phase 2 — bootstrap critical tables for live execution
# -----------------------------------------------------------------
def bootstrap_core_tables(main_db, cache_db):
    CORE_TABLES = ["plan_ledger", "markets_schedule", "runner_form_canonical"]
    with sqlite3.connect(cache_db) as con:
        alias = "core"
        try:
            con.execute(f"ATTACH DATABASE '{main_db}' AS {alias};")
        except sqlite3.OperationalError:
            # already attached, safe to continue
            pass

        # ── Core base tables ──────────────────────────────────────────────
        for t in CORE_TABLES:
            try:
                con.execute(
                    f"CREATE TABLE IF NOT EXISTS {t} AS SELECT * FROM {alias}.{t} WHERE 0;"
                )
                print(f"[schema_guard] 🩹 Bootstrapped empty {t} into cache.")
            except Exception as e:
                print(f"[schema_guard] ⚠️ Skipped {t}: {e}")

        # ── Phase 2.5 — auxiliary safety tables ───────────────────────────
        con.execute("""
        CREATE TABLE IF NOT EXISTS decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            strategy TEXT,
            letter TEXT,
            side TEXT,
            entry_odds REAL,
            entry_stake REAL,
            confidence REAL,
            status TEXT,
            created_at TEXT DEFAULT (datetime('now','utc'))
        );
        """)

        con.execute("""
        CREATE TABLE IF NOT EXISTS mastery_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_type TEXT,
            marketId TEXT,
            selectionId TEXT,
            json_payload TEXT,
            created_at TEXT DEFAULT (datetime('now','utc'))
        );
        """)

        # --- Phase 2.7 — ensure mastery_state table exists for brain feedback ---
        con.execute("""
        CREATE TABLE IF NOT EXISTS mastery_state (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT,
            run_id TEXT,
            goal_alignment REAL DEFAULT 0,
            pnl REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            match_rate REAL DEFAULT 0,
            progress REAL DEFAULT 0
        );
        """)


        try:
            con.execute("ALTER TABLE mastery_state ADD COLUMN progress REAL DEFAULT 0;")
        except sqlite3.OperationalError:
            # column already exists
            pass

        con.commit()


# -----------------------------------------------------------------
# Phase 3 — orchestrator
# -----------------------------------------------------------------
def auto_heal():
    main_db = str(DBS["AUTO_MAIN"])
    cache_db = str(DBS["AUTO_CACHE"])

    if not os.path.exists(main_db):
        print("[schema_guard] ❌ Main DB missing — cannot auto-heal.")
        return

    if not os.path.exists(cache_db) or os.path.getsize(cache_db) < 1024:
        rebuild_cache_from_main(main_db, cache_db)

    try:
        sqlite3.connect(cache_db).close()
    except Exception:
        print("[schema_guard] ⚠️ Cache DB seems corrupt — rebuilding.")
        rebuild_cache_from_main(main_db, cache_db)

    repair_column_drift(main_db, cache_db)
    bootstrap_core_tables(main_db, cache_db)
    sync_views_from_main(main_db, cache_db)
    apply_verified_schema(cache_db, "/tmp/verified_schema.sql")
    print("[schema_guard] ✅ Auto-heal + self-recovery complete.")

# inside schema_guard.py, after bootstrap_core_tables()

def sync_views_from_main(main_db, cache_db):
    """Ensure all views (v_*) exist in cache and match main DB."""
    try:
        with sqlite3.connect(main_db) as src, sqlite3.connect(cache_db) as dst:
            views = src.execute("SELECT name, sql FROM sqlite_master WHERE type='view' AND name LIKE 'v%';").fetchall()
            for name, sql in views:
                if sql:
                    dst.execute(f"DROP VIEW IF EXISTS {name};")
                    dst.execute(sql)
            dst.commit()
        print("[schema_guard] ✅ Synced all v_* views from main → cache")
    except Exception as e:
        print(f"[schema_guard] ⚠️ view sync failed: {e}")



# -----------------------------------------------------------------
# Phase 4 — full verified schema bootstrap
# -----------------------------------------------------------------
def apply_verified_schema(cache_db, verified_schema_path="/tmp/verified_schema.sql"):
    if not os.path.exists(verified_schema_path):
        print(f"[schema_guard] ❌ Verified schema file not found: {verified_schema_path}")
        return
    try:
        with open(verified_schema_path, "r") as f:
            schema_sql = f.read()
        with sqlite3.connect(cache_db) as con:
            con.executescript(schema_sql.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS"))

        print(f"[schema_guard] ✅ Applied verified schema → {cache_db}")
    except Exception as e:
        print(f"[schema_guard] ❌ Failed to apply verified schema: {e}")


# -----------------------------------------------------------------
# Script entrypoint
# -----------------------------------------------------------------
if __name__ == "__main__":
    auto_heal()
