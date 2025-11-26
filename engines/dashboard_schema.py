# 📍 TARGET: engines/dashboard_schema.py  (NEW FILE)
from __future__ import annotations
import sqlite3
from engines.config_paths import autoscalp_db

def ensure_gui_schema() -> None:
    """
    Ensure autoscalp_gui.db has the tables/columns the dashboard expects:
      - strategies_performance
      - orders.ts (generic timestamp for panels/tools)
    Safe to call many times.
    """
    db = autoscalp_db()
    con = sqlite3.connect(db, timeout=15, isolation_level=None)
    try:
        con.execute("PRAGMA journal_mode=WAL;")
        con.execute("PRAGMA foreign_keys=ON;")

        # 1) strategies_performance
        con.execute("""
        CREATE TABLE IF NOT EXISTS strategies_performance(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_id     TEXT,
          ts         TEXT NOT NULL,      -- UTC timestamp
          mode       TEXT,               -- LIVE | SIM | TEST | LEARNING
          strategy   TEXT NOT NULL,      -- human/readable strategy key
          marketId   TEXT,
          selectionId TEXT,
          action     TEXT,               -- ENTER/LAY/BACK/EXIT...
          stake      REAL,
          price      REAL,
          pnl        REAL DEFAULT 0.0,   -- realized contribution (+/-)
          source     TEXT                -- optional tag (e.g., X3 / B1, or same as strategy)
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_sp_mode_ts       ON strategies_performance(mode, ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_sp_strategy_ts   ON strategies_performance(strategy, ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_sp_runner_ts     ON strategies_performance(marketId, selectionId, ts)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_sp_runid_ts      ON strategies_performance(run_id, ts)")

        # 2) orders.ts (generic event time so panels don't rely only on opened_at/closed_at)
        cols = {r[1] for r in con.execute("PRAGMA table_info(orders)")}
        if "ts" not in cols:
            con.execute("ALTER TABLE orders ADD COLUMN ts TEXT")
            # backfill once
            con.execute("UPDATE orders SET ts = COALESCE(ts, opened_at, datetime('now','utc'))")

    finally:
        try: con.commit()
        except Exception: pass
        con.close()

# ─────────────────────────────────────────────────────────────────────────────
# Additive schema helpers + superset entrypoint
# (keeps your existing ensure_gui_schema() intact)
# ─────────────────────────────────────────────────────────────────────────────
from typing import List
from engines.config_paths import connect_db as _connect_bets

def _pragma_sane(con: sqlite3.Connection) -> None:
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    con.execute("PRAGMA busy_timeout=8000")
    con.execute("PRAGMA synchronous=NORMAL")

def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()

def _has_table(con: sqlite3.Connection, table: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone()
    return bool(row)

def _ensure_table(con: sqlite3.Connection, ddl: str) -> None:
    con.execute(ddl)

def _ensure_cols(con: sqlite3.Connection, table: str, spec: dict[str, str]) -> None:
    existing = _cols(con, table)
    for col, ddl in spec.items():
        if col not in existing:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")

def _ensure_index(con: sqlite3.Connection, name: str, ddl: str) -> None:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=? LIMIT 1", (name,)
    ).fetchone()
    if not row:
        con.execute(ddl)

def _ensure_autoscalp_full(con: sqlite3.Connection) -> List[str]:
    """Full GUI DB surface used by writers/health/feeder (AUTOSCALP DB)."""
    out: List[str] = []
    _pragma_sane(con)

    # inbound_oc_cache with oc1..oc20 + oc*_band_json
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS inbound_oc_cache(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        anchor_odd REAL,
        last_sync_ts TEXT
      )""")
    add_cols = {}
    for n in range(1, 21):
        add_cols[f"oc{n}"] = "REAL"
        add_cols[f"oc{n}_band_json"] = "TEXT"
    _ensure_cols(con, "inbound_oc_cache", add_cols)
    _ensure_index(con, "ux_inbound_oc_cache_mid_sid",
                  "CREATE UNIQUE INDEX IF NOT EXISTS ux_inbound_oc_cache_mid_sid "
                  "ON inbound_oc_cache(marketId, selectionId)")
    out.append("autoscalp.inbound_oc_cache OK")

    # odds_snapshots
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS odds_snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        marketId TEXT NOT NULL, selectionId TEXT NOT NULL,
        ltp REAL, back1 REAL, lay1 REAL,
        slope_ppm REAL, tick_vel_1s_up INTEGER, tick_vel_3s_up INTEGER,
        fav_rank_now INTEGER, fav_rank_30s INTEGER,
        mto_minutes REAL,
        source TEXT
      )""")
    _ensure_index(con, "idx_odds_ts",
                  "CREATE INDEX IF NOT EXISTS idx_odds_ts "
                  "ON odds_snapshots(marketId, selectionId, ts)")
    out.append("autoscalp.odds_snapshots OK")

    # odds_current (add missing columns if table already exists)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS odds_current(
        day TEXT NOT NULL,
        marketId TEXT NOT NULL, selectionId TEXT NOT NULL,
        updated_ts TEXT NOT NULL
      )""")
    _ensure_cols(con, "odds_current", {
        "ltp": "REAL", "back1": "REAL", "lay1": "REAL",
        "fav_rank_now": "INTEGER", "mto_minutes": "REAL",
        "slope_ppm": "REAL", "tick_vel_1s_up": "INTEGER", "tick_vel_3s_up": "INTEGER"
    })
    _ensure_index(con, "idx_odds_cur_mid",
                  "CREATE INDEX IF NOT EXISTS idx_odds_cur_mid ON odds_current(day, marketId)")
    out.append("autoscalp.odds_current OK")

    # blueprint_state / blueprint_events (health writers/readers expect these)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS blueprint_state (
        day TEXT NOT NULL,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        blueprint_key TEXT,
        score REAL,
        features_json TEXT NOT NULL,
        PRIMARY KEY(day, marketId, selectionId)
      )""")
    out.append("autoscalp.blueprint_state OK")

    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS blueprint_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        marketId TEXT NOT NULL,
        selectionId TEXT,
        blueprint_key TEXT NOT NULL,
        strength REAL NOT NULL,
        detected_at TEXT NOT NULL,
        window_tag TEXT,
        context_json TEXT NOT NULL
      )""")
    _ensure_index(con, "idx_bp_ev_day",
                  "CREATE INDEX IF NOT EXISTS idx_bp_ev_day "
                  "ON blueprint_events(day, marketId, detected_at)")
    out.append("autoscalp.blueprint_events OK")

    # dashboard tables
    _ensure_table(con, """
        day TEXT PRIMARY KEY,
        window_start_ts TEXT,
        runners_scanned INTEGER,
        decisions INTEGER,
        proposals INTEGER,
        parents_placed INTEGER,
        hedges_matched INTEGER,
        cancels INTEGER,
        timeouts INTEGER,
        open_parents INTEGER,
        recent_events_json TEXT,
        gate_reasons_json TEXT,
        last_refreshed_ts TEXT
      )""")

    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS dashboard_runners(
        day TEXT, marketId TEXT, selectionId TEXT,
        runner_name TEXT,
        odd REAL, implied_prob REAL,
        band_low REAL, band_high REAL,
        last_snapshot_ts TEXT, source TEXT,
        top6_rank INTEGER, first_seen_rank INTEGER, first_seen_ts TEXT,
        in_top6 INTEGER,
        bar_pre_norm REAL, bar_post_norm REAL, bar_display_norm REAL, bar_color TEXT,
        flow_ppm REAL, vol_1m REAL, trend TEXT,
        PRIMARY KEY(day, marketId, selectionId)
      )""")
    _ensure_index(con, "ux_dash_run_day_mid_sid",
                  "CREATE UNIQUE INDEX IF NOT EXISTS ux_dash_run_day_mid_sid "
                  "ON dashboard_runners(day, marketId, selectionId)")
    out.append("autoscalp.dashboard_runners OK")
    # Ensure older DBs get the new column too
    _ensure_cols(con, "dashboard_runners", {"runner_name": "TEXT"})

    # Ensure older DBs get the full column set (ALTER only if missing)
    _expected_runner_cols = {
      "runner_name":       "TEXT",
      "odd":               "REAL",
      "implied_prob":      "REAL",
      "band_low":          "REAL",
      "band_high":         "REAL",
      "last_snapshot_ts":  "TEXT",
      "source":            "TEXT",
      "top6_rank":         "INTEGER",
      "first_seen_rank":   "INTEGER",
      "first_seen_ts":     "TEXT",
      "in_top6":           "INTEGER",
      "bar_pre_norm":      "REAL",
      "bar_post_norm":     "REAL",
      "bar_display_norm":  "REAL",
      "bar_color":         "TEXT",
      "flow_ppm":          "REAL",
      "vol_1m":            "REAL",
      "trend":             "TEXT",
    }
    _ensure_cols(con, "dashboard_runners", _expected_runner_cols)



    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS dashboard_markets(
        day TEXT, marketId TEXT,
        course TEXT, market_name TEXT, off_at_utc TEXT,
        status TEXT, t_minus_sec INTEGER,
        is_next INTEGER, runners_total INTEGER,
        source TEXT,
        betfair_status TEXT, betfair_status_mapped TEXT, betfair_status_age_sec INTEGER,
        status_source TEXT, last_api_ok_ts TEXT, last_api_err TEXT,
        discovered_ts TEXT, last_refreshed_ts TEXT,
        t0_phase TEXT, t0_color TEXT, t0_sec INTEGER,
        inplay_start_ts TEXT, late_since_ts TEXT,
        is_concurrent INTEGER, concurrent_rank INTEGER,
        PRIMARY KEY(day, marketId)
      )""")
    out.append("autoscalp.dashboard_markets OK")

    # minimal markets_schedule (queried by multiple components)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS markets_schedule(
        marketId TEXT PRIMARY KEY,
        venue TEXT, course TEXT, event_name TEXT,
        market_name TEXT, off_at_utc TEXT, country_code TEXT
      )""")
    out.append("autoscalp.markets_schedule OK")

    # runs / orders shells (+ columns that live_router expects)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        mode TEXT,
        blueprint_file TEXT,
        notes TEXT
      )""")
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS orders(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        customerOrderRef TEXT UNIQUE,
        mode TEXT, run_id INTEGER,
        marketId TEXT, selectionId TEXT, side TEXT,
        entry_odds REAL, entry_stake REAL,
        entry_status TEXT, entry_bet_id TEXT, opened_at TEXT,
        exit_status TEXT, exit_bet_id TEXT, exit_odds REAL, exit_stake REAL,
        closed_at TEXT,
        realized_pnl REAL, net_pl REAL, error TEXT,
        role TEXT, hedge_of INTEGER, source TEXT,
        ts TEXT
      )""")
    _ensure_cols(con, "orders", {
        # router & sync helpers rely on these
        "bf_bet_id": "TEXT",
        "customer_ref": "TEXT",
        "entry_matched_odds": "REAL",
        "entry_matched_stake": "REAL",
        "exit_matched_odds": "REAL",
        "exit_matched_stake": "REAL",
        "child_bf_bet_id": "TEXT"
    })
    _ensure_index(con, "idx_orders_mode_opened",
                  "CREATE INDEX IF NOT EXISTS idx_orders_mode_opened ON orders(mode, opened_at)")
    _ensure_index(con, "idx_orders_status",
                  "CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(entry_status, exit_status)")
    _ensure_index(con, "idx_orders_role",
                  "CREATE INDEX IF NOT EXISTS idx_orders_role ON orders(role)")
    _ensure_index(con, "idx_orders_link",
                  "CREATE INDEX IF NOT EXISTS idx_orders_link ON orders(hedge_of)")
    _ensure_index(con, "idx_orders_bfid",
                  "CREATE INDEX IF NOT EXISTS idx_orders_bfid ON orders(bf_bet_id)")
    out.append("autoscalp.orders/runs OK")

    # app_kv (feature flags / once-per-day gates)
    _ensure_table(con, "CREATE TABLE IF NOT EXISTS app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT)")
    out.append("autoscalp.app_kv OK")

    # decisions (placement audit; lanes summary reads from here)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS decisions(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id          TEXT,
        marketId        TEXT,
        selectionId     TEXT,
        decided_at      TEXT,     -- UTC
        signal_type     TEXT,
        blueprint_match TEXT,
        confidence      REAL,
        scalp_direction TEXT,
        proposed_odds   REAL,
        proposed_stake  REAL,
        notes           TEXT,
        meta_json       TEXT,     -- JSON with {"why": "...", ...}
        order_id        INTEGER
      )
    """)
    _ensure_index(con, "idx_decisions_run_time",
                  "CREATE INDEX IF NOT EXISTS idx_decisions_run_time "
                  "ON decisions(run_id, decided_at)")
    out.append("autoscalp.decisions OK")


    # keep your original extension
    try:
        ensure_gui_schema()
    except Exception:
        pass

    return out

def _ensure_bets_full(con: sqlite3.Connection) -> List[str]:
    """
    BETS DB: create analytics/timeline tables and a *safety* shadow of a few
    GUI tables some legacy writers accidentally target (to avoid hard errors).
    """
    out: List[str] = []
    _pragma_sane(con)

    # oc_series used by OC timeline / analytics
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS oc_series(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT, selectionId INTEGER,
        stage TEXT, snapshot_ts TEXT,
        odd REAL, band_low REAL, band_high REAL, band_json TEXT,
        meta_json TEXT, source TEXT
      )""")
    _ensure_index(con, "idx_bets_oc_series_market_time",
                  "CREATE INDEX IF NOT EXISTS idx_bets_oc_series_market_time ON oc_series(marketId, snapshot_ts)")
    _ensure_index(con, "idx_bets_oc_series_selection_time",
                  "CREATE INDEX IF NOT EXISTS idx_bets_oc_series_selection_time ON oc_series(selectionId, snapshot_ts)")
    out.append("bets.oc_series OK")

    # minimal bets table (if fresh DB)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS bets(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId INTEGER NOT NULL,
        stake TEXT, odds INTEGER, bet_type TEXT,
        placed_at TEXT, status TEXT,
        horse_name TEXT, race_name TEXT,
        market_name TEXT, event_name TEXT,
        marketStartTime TEXT,
        date TEXT, timestamp TEXT,
        meta_json TEXT,
        anchor_odd REAL
      )""")
    out.append("bets.bets OK")

    # ── SAFETY SHADOWS (avoid 'no such table' if a writer misroutes) ─────────
    # Some legacy paths sometimes write inbound_oc_cache/odds_snapshots to BETS.
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS inbound_oc_cache(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        anchor_odd REAL,
        last_sync_ts TEXT
      )""")
    add_cols = {}
    for n in range(1, 21):
      add_cols[f"oc{n}"] = "REAL"
      add_cols[f"oc{n}_band_json"] = "TEXT"
    _ensure_cols(con, "inbound_oc_cache", add_cols)
    _ensure_index(con, "ux_bets_inbound_mid_sid",
                  "CREATE UNIQUE INDEX IF NOT EXISTS ux_bets_inbound_mid_sid "
                  "ON inbound_oc_cache(marketId, selectionId)")
    out.append("bets.inbound_oc_cache (shadow) OK")

    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS odds_snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        marketId TEXT NOT NULL, selectionId TEXT NOT NULL,
        ltp REAL, back1 REAL, lay1 REAL,
        slope_ppm REAL, tick_vel_1s_up INTEGER, tick_vel_3s_up INTEGER,
        fav_rank_now INTEGER, fav_rank_30s INTEGER,
        mto_minutes REAL,
        source TEXT
      )""")
    _ensure_index(con, "idx_bets_odds_ts",
                  "CREATE INDEX IF NOT EXISTS idx_bets_odds_ts "
                  "ON odds_snapshots(marketId, selectionId, ts)")

    out.append("bets.odds_snapshots (shadow) OK")

    # blueprint_state shadow (health/odds sometimes resolve into BETS)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS blueprint_state (
        day TEXT NOT NULL,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        blueprint_key TEXT,
        score REAL,
        features_json TEXT NOT NULL,
        PRIMARY KEY(day, marketId, selectionId)
      )""")
    out.append("bets.blueprint_state (shadow) OK")

    # dashboard_runners shadow (some catalogue writers may target BETS)
    _ensure_table(con, """
      CREATE TABLE IF NOT EXISTS dashboard_runners(
        day TEXT, marketId TEXT, selectionId TEXT,
        runner_name TEXT,
        odd REAL, implied_prob REAL,
        band_low REAL, band_high REAL,
        last_snapshot_ts TEXT, source TEXT,
        top6_rank INTEGER, first_seen_rank INTEGER, first_seen_ts TEXT,
        in_top6 INTEGER,
        bar_pre_norm REAL, bar_post_norm REAL, bar_display_norm REAL, bar_color TEXT,
        flow_ppm REAL, vol_1m REAL, trend TEXT,
        PRIMARY KEY(day, marketId, selectionId)
      )""")
    _ensure_cols(con, "dashboard_runners", {
      "runner_name":       "TEXT",
      "odd":               "REAL",
      "implied_prob":      "REAL",
      "band_low":          "REAL",
      "band_high":         "REAL",
      "last_snapshot_ts":  "TEXT",
      "source":            "TEXT",
      "top6_rank":         "INTEGER",
      "first_seen_rank":   "INTEGER",
      "first_seen_ts":     "TEXT",
      "in_top6":           "INTEGER",
      "bar_pre_norm":      "REAL",
      "bar_post_norm":     "REAL",
      "bar_display_norm":  "REAL",
      "bar_color":         "TEXT",
      "flow_ppm":          "REAL",
      "vol_1m":            "REAL",
      "trend":             "TEXT",
    })
    out.append("bets.dashboard_runners (shadow) OK")

    return out




def ensure_dashboard_schema(verbose: bool = True) -> list[str]:
    """
    Superset schema creator/patcher:
      • AUTOSCALP (GUI) DB: inbound_oc_cache (+oc1..oc20), odds_snapshots, odds_current (+fav_rank_now…),
      • BETS DB: oc_series, minimal bets
    Calls your existing ensure_gui_schema() as part of AUTOSCALP setup.
    """
    msgs: List[str] = []

    # AUTOSCALP
    a_path = autoscalp_db()
    con_a = sqlite3.connect(a_path, timeout=15, isolation_level=None)
    try:
        msgs += _ensure_autoscalp_full(con_a)
        con_a.commit()
    finally:
        try: con_a.close()
        except Exception: pass

    # BETS
    con_b = _connect_bets(ro=False)
    try:
        msgs += _ensure_bets_full(con_b)
        con_b.commit()
    finally:
        try: con_b.close()
        except Exception: pass

    if verbose:
        for m in msgs:
            print(f"[schema] {m}")
    return msgs

    # ─── Cash-Out Intelligence System table ─────────────────────────────────────
    cur.execute("""
        CREATE TABLE IF NOT EXISTS cashout_series (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            day TEXT NOT NULL,
            marketId TEXT NOT NULL,
            selectionId INTEGER NOT NULL,
            order_id INTEGER,
            oc_stage TEXT,
            snapshot_ts TEXT,
            ltp REAL,
            cashout_pnl REAL,
            total_pnl_now REAL,
            source TEXT DEFAULT 'SIM',
            created_at TEXT DEFAULT (datetime('now','utc'))
        );
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_cashout_day_mid_sid
            ON cashout_series(day, marketId, selectionId, snapshot_ts);
    """)

    con.commit()
# === PATCH END ===

