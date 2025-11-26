# analytics_beta/engines/db_migrations.py
from __future__ import annotations
import logging, sqlite3
import engines.config_paths as cp

from engines.path_guard import ensure_parent

SCHEMA_VERSION = 8  # <- bumped

# ──────────────────────────────────────────────────────────────────────────────
# 📍 TARGET: engines/db_migrations.py
# 🔎 SEARCH: ^def ensure_tables\(\).*?:\n(?:.*\n)+?
# --- PATCH START: ensure_tables adds Mastery migrations -----------------------
def ensure_tables() -> int:
    """Create/upgrade SQLite schema in DB_PATH."""
    from engines.config_paths import DB_PATH, connect_db
    from engines.migrations.mastery_tables import apply_mastery_core, apply_mastery_priors

    with connect_db(ro=False) as conn:
        conn.execute("PRAGMA busy_timeout=8000")

        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        _bootstrap_meta(conn)
        ver = _get_version(conn)

        if ver < 1:
            _apply_v1(conn); _set_version(conn, 1); ver = 1
        if ver < 2:
            _apply_v2(conn); _set_version(conn, 2); ver = 2
        if ver < 3:
            _apply_v3(conn); _set_version(conn, 3); ver = 3
        if ver < 4:
            _apply_v4(conn); _set_version(conn, 4); ver = 4
        if ver < 5:
            _apply_v5(conn); _set_version(conn, 5); ver = 5
        if ver < 6:
            apply_mastery_core(conn); _set_version(conn, 6); ver = 6
        if ver < 7:
            apply_mastery_priors(conn); _set_version(conn, 7); ver = 7

        conn.commit()
        return ver
# --- PATCH END ----------------------------------------------------------------


    # --- Phase-1 compatibility: add 'note' columns if missing (safe, idempotent) ---
    def _has_col(conn, table, col):
        return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})"))

    try:
        if _has_col(conn, "stories", "id") and not _has_col(conn, "stories", "note"):
            conn.execute("ALTER TABLE stories ADD COLUMN note TEXT")
        if _has_col(conn, "chapters", "id") and not _has_col(conn, "chapters", "note"):
            conn.execute("ALTER TABLE chapters ADD COLUMN note TEXT")
    except Exception as e:
        logging.warning(f"[migrations] note columns add failed (may already exist): {e}")


# ---------------- internals ----------------

def _bootstrap_meta(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS schema_meta(
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)
    if conn.execute("SELECT 1 FROM schema_meta WHERE key='schema_version'").fetchone() is None:
        conn.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version','0')")

def _get_version(conn) -> int:
    row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
    try: return int(row[0]) if row else 0
    except Exception: return 0

def _set_version(conn, n: int) -> None:
    conn.execute("UPDATE schema_meta SET value=? WHERE key='schema_version'", (str(n),))

def _table_has_column(conn, table: str, col: str) -> bool:
    for _, name, *_ in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if name == col: return True
    return False

def _ensure_column(conn, table: str, col: str, decl: str) -> None:
    if not _table_has_column(conn, table, col):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

# v1: core bets table (minimal for runner seeding + anchors)
def _apply_v1(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bets(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId        TEXT    NOT NULL,
            selectionId     INTEGER NOT NULL,
            horse_name      TEXT,
            race_name       TEXT,
            market_name     TEXT,
            event_name      TEXT,
            marketStartTime TEXT,     -- ISO8601
            date            TEXT,     -- YYYY-MM-DD
            timestamp       TEXT,     -- ISO8601
            meta_json       TEXT,

            -- anchor/OC0
            anchor_odd      REAL,
            placed_at       TEXT,     -- ISO8601 when anchor set
            OC0             REAL,
            OC0_band        TEXT,     -- JSON string

            status          TEXT,
            test_mode       INTEGER DEFAULT 0,

            UNIQUE(marketId, selectionId)
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bets_date ON bets(date)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bets_market ON bets(marketId)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bets_mkt_sel ON bets(marketId, selectionId)")

# v2: time-series used by the GUI code (_record_oc_series writes here)
def _apply_v2(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS oc_series(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId    TEXT    NOT NULL,
            selectionId INTEGER NOT NULL,
            stage       TEXT    NOT NULL,      -- 'OC0', 'OC1', etc
            snapshot_ts TEXT    NOT NULL,      -- ISO8601
            odd         REAL,
            band_low    REAL,
            band_high   REAL,
            band_json   TEXT,
            meta_json   TEXT,
            source      TEXT,
            UNIQUE(marketId, selectionId, stage, snapshot_ts) ON CONFLICT IGNORE
        );
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oc_series_mkt_time ON oc_series(marketId, snapshot_ts)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_oc_series_sel_time ON oc_series(selectionId, snapshot_ts)")

# v3: simple key/value flags
def _apply_v3(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_flags(
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
    """)

# v4: (placeholder for previous tweaks; keep for forward-compat)
def _apply_v4(conn) -> None:
    pass

# v5: add customerOrderRef expected by hijack monitor
def _apply_v5(conn) -> None:
    _ensure_column(conn, "bets", "customerOrderRef", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bets_custref ON bets(customerOrderRef)")
# --- Phase-1 autoscalp tables (created when needed by GUI/engine) -------------
def ensure_autoscalp_tables() -> None:
    """Create core autoscalp tables (runs/orders/events/stories/chapters/inbound_*)."""
    with cp.connect_autoscalp_db(timeout=15.0, ro=False) as auto:
        auto.execute("PRAGMA foreign_keys=ON;")
        auto.execute("""CREATE TABLE IF NOT EXISTS runs(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            mode TEXT NOT NULL CHECK (mode IN ('TEST','LEARNING','LIVE')),
            blueprint_file TEXT,
            notes TEXT
        );""")
        auto.execute("CREATE INDEX IF NOT EXISTS idx_runs_started_at ON runs(started_at);")

        auto.execute("""CREATE TABLE IF NOT EXISTS orders(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            decision_id INTEGER,
            customerOrderRef TEXT UNIQUE,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            mode TEXT NOT NULL CHECK (mode IN ('TEST','LEARNING','LIVE')),
            side TEXT CHECK (side IN ('BACK','LAY')),
            entry_odds REAL,
            entry_stake REAL,
            entry_status TEXT,
            entry_bet_id TEXT,
            exit_odds REAL,
            exit_stake REAL,
            exit_status TEXT,
            exit_bet_id TEXT,
            unrealized_pnl REAL,
            realized_pnl REAL,
            opened_at TEXT,
            closed_at TEXT,
            error TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );""")
        auto.execute("CREATE INDEX IF NOT EXISTS idx_orders_time ON orders(opened_at,closed_at);")
        auto.execute("CREATE INDEX IF NOT EXISTS idx_orders_key  ON orders(marketId,selectionId);")
        auto.execute("CREATE INDEX IF NOT EXISTS idx_orders_mode ON orders(mode);")

        auto.execute("""CREATE TABLE IF NOT EXISTS events(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            ts TEXT NOT NULL,
            level TEXT,
            source TEXT,
            message TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );""")
        auto.execute("CREATE INDEX IF NOT EXISTS idx_events_time ON events(ts);")

        auto.execute("""CREATE TABLE IF NOT EXISTS inbound_bets_min(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            placed_at TEXT,
            anchor_odd REAL,
            horse_name TEXT,
            meta_json TEXT
        );""")

        auto.execute("""CREATE TABLE IF NOT EXISTS inbound_oc_cache(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            anchor_odd REAL,
            oc1 REAL, oc2 REAL, oc3 REAL, oc4 REAL, oc5 REAL,
            oc6 REAL, oc7 REAL, oc8 REAL, oc9 REAL, oc10 REAL,
            oc11 REAL, oc12 REAL, oc13 REAL, oc14 REAL, oc15 REAL,
            oc16 REAL, oc17 REAL, oc18 REAL, oc19 REAL, oc20 REAL,
            oc1_band_json  TEXT, oc2_band_json  TEXT, oc3_band_json  TEXT, oc4_band_json  TEXT, oc5_band_json  TEXT,
            oc6_band_json  TEXT, oc7_band_json  TEXT, oc8_band_json  TEXT, oc9_band_json  TEXT, oc10_band_json TEXT,
            oc11_band_json TEXT, oc12_band_json TEXT, oc13_band_json TEXT, oc14_band_json TEXT, oc15_band_json TEXT,
            oc16_band_json TEXT, oc17_band_json TEXT, oc18_band_json TEXT, oc19_band_json TEXT, oc20_band_json TEXT,
            last_sync_ts TEXT
        );""")

        auto.execute("""CREATE TABLE IF NOT EXISTS stories(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            story_start_oc TEXT,
            created_at TEXT
        );""")
        auto.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_story_key ON stories(marketId,selectionId);")

        auto.execute("""CREATE TABLE IF NOT EXISTS chapters(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            story_id INTEGER NOT NULL,
            oc_label TEXT NOT NULL,
            entry_odds REAL,
            exit_odds REAL,
            oc_band_json TEXT,
            tick_pattern TEXT,
            direction_bias TEXT,
            position_ratio REAL,
            volatility REAL,
            minutes_to_post REAL,
            opened_at TEXT,
            closed_at TEXT,
            UNIQUE(story_id, oc_label),
            FOREIGN KEY(story_id) REFERENCES stories(id)
        );""")

        auto.execute("""CREATE TABLE IF NOT EXISTS decisions(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL,
            marketId TEXT NOT NULL,
            selectionId TEXT NOT NULL,
            decided_at TEXT NOT NULL,
            signal_type TEXT,
            blueprint_match TEXT,
            confidence REAL,
            scalp_direction TEXT,
            proposed_odds REAL,
            proposed_stake REAL,
            notes TEXT,
            FOREIGN KEY(run_id) REFERENCES runs(id)
        );""")

