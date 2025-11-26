# BETS Schema
```sql
CREATE TABLE app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE order_events(
          id INTEGER PRIMARY KEY,
          order_id INTEGER,
          oco_group_id TEXT,
          market_id TEXT,
          runner_id TEXT,
          side TEXT,
          role TEXT,
          event TEXT,
          price REAL, size REAL,
          ts TEXT DEFAULT (datetime('now')),
          FOREIGN KEY(order_id) REFERENCES orders(id)
        );
CREATE TABLE __gui_probe(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE schema_meta(
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
CREATE TABLE bets(
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
            test_mode       INTEGER DEFAULT 0, customerOrderRef TEXT,

            UNIQUE(marketId, selectionId)
        );
CREATE TABLE sqlite_sequence(name,seq);
CREATE INDEX idx_bets_date ON bets(date);
CREATE INDEX idx_bets_market ON bets(marketId);
CREATE INDEX idx_bets_mkt_sel ON bets(marketId, selectionId);
CREATE TABLE oc_series(
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
CREATE INDEX idx_oc_series_mkt_time ON oc_series(marketId, snapshot_ts);
CREATE INDEX idx_oc_series_sel_time ON oc_series(selectionId, snapshot_ts);
CREATE TABLE system_flags(
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
CREATE INDEX idx_bets_custref ON bets(customerOrderRef);
CREATE TABLE mastery_state (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  progress INTEGER NOT NULL DEFAULT 0,
  thresholds_json TEXT NOT NULL DEFAULT '{}',
  volatility_json TEXT NOT NULL DEFAULT '{}',
  liquidity_json TEXT NOT NULL DEFAULT '{}',
  time_windows_json TEXT NOT NULL DEFAULT '{}',
  exit_policy_json TEXT NOT NULL DEFAULT '{}',
  confidence_json TEXT NOT NULL DEFAULT '{}',
  version INTEGER NOT NULL DEFAULT 1,
  source TEXT NOT NULL DEFAULT 'TEST',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE mastery_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  happened_at TEXT NOT NULL DEFAULT (datetime('now')),
  event_type TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  delta_progress INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'TEST'
);
CREATE TABLE mastery_priors (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  bin_key TEXT NOT NULL,
  prior_p1_alpha REAL NOT NULL,
  prior_p1_beta  REAL NOT NULL,
  prior_p2_alpha REAL NOT NULL,
  prior_p2_beta  REAL NOT NULL,
  prior_p3_alpha REAL NOT NULL,
  prior_p3_beta  REAL NOT NULL,
  prior_fill_alpha REAL NOT NULL,
  prior_fill_beta  REAL NOT NULL,
  prior_mae_mean REAL NOT NULL,
  prior_mae_std  REAL NOT NULL,
  source TEXT NOT NULL DEFAULT 'PRIOR',
  updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX idx_mastery_priors_bin ON mastery_priors(bin_key);
CREATE TABLE synthetic_scenarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE,
  bin_key TEXT NOT NULL,
  script_json TEXT NOT NULL,
  notes TEXT DEFAULT ''
);
CREATE TABLE synthetic_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario_id INTEGER NOT NULL REFERENCES synthetic_scenarios(id) ON DELETE CASCADE,
  seed INTEGER NOT NULL,
  outcomes_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_bets_oc_series_market_time ON oc_series(marketId, snapshot_ts);
CREATE INDEX idx_bets_oc_series_selection_time ON oc_series(selectionId, snapshot_ts);
CREATE TABLE inbound_oc_cache(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        anchor_odd REAL,
        last_sync_ts TEXT
      , oc1 REAL, oc1_band_json TEXT, oc2 REAL, oc2_band_json TEXT, oc3 REAL, oc3_band_json TEXT, oc4 REAL, oc4_band_json TEXT, oc5 REAL, oc5_band_json TEXT, oc6 REAL, oc6_band_json TEXT, oc7 REAL, oc7_band_json TEXT, oc8 REAL, oc8_band_json TEXT, oc9 REAL, oc9_band_json TEXT, oc10 REAL, oc10_band_json TEXT, oc11 REAL, oc11_band_json TEXT, oc12 REAL, oc12_band_json TEXT, oc13 REAL, oc13_band_json TEXT, oc14 REAL, oc14_band_json TEXT, oc15 REAL, oc15_band_json TEXT, oc16 REAL, oc16_band_json TEXT, oc17 REAL, oc17_band_json TEXT, oc18 REAL, oc18_band_json TEXT, oc19 REAL, oc19_band_json TEXT, oc20 REAL, oc20_band_json TEXT);
CREATE UNIQUE INDEX ux_bets_inbound_mid_sid ON inbound_oc_cache(marketId, selectionId);
CREATE TABLE odds_snapshots(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ts TEXT NOT NULL,
        marketId TEXT NOT NULL, selectionId TEXT NOT NULL,
        ltp REAL, back1 REAL, lay1 REAL,
        slope_ppm REAL, tick_vel_1s_up INTEGER, tick_vel_3s_up INTEGER,
        fav_rank_now INTEGER, fav_rank_30s INTEGER,
        mto_minutes REAL,
        source TEXT
      );
CREATE INDEX idx_bets_odds_ts ON odds_snapshots(marketId, selectionId, ts);
CREATE TABLE blueprint_state (
        day TEXT NOT NULL,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        blueprint_key TEXT,
        score REAL,
        features_json TEXT NOT NULL,
        PRIMARY KEY(day, marketId, selectionId)
      );
CREATE TABLE dashboard_runners(
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
      );
CREATE TABLE markets_schedule (
  marketId     TEXT PRIMARY KEY,
  off_at_utc   TEXT NOT NULL,               -- ISO UTC, e.g. 2025-09-21T13:13:00Z
  venue        TEXT,
  course       TEXT,
  event_name   TEXT,
  market_name  TEXT,
  source       TEXT,
  discovered_ts TEXT NOT NULL DEFAULT (datetime('now','utc'))
);
CREATE INDEX idx_ms_offutc ON markets_schedule(off_at_utc);
CREATE TABLE pnl_daily (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_day INTEGER NOT NULL,
      month_index INTEGER NOT NULL,
      amount REAL NOT NULL,
      created_at TEXT NOT NULL,
      run_id TEXT,
      source TEXT
    );
CREATE TABLE sim_runs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      run_day INTEGER NOT NULL,
      month_index INTEGER NOT NULL,
      run_id TEXT NOT NULL UNIQUE,
      started_at TEXT NOT NULL,
      ended_at TEXT
    );
CREATE TABLE pnl_trades (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      amount REAL,
      created_at TEXT,
      settled_at TEXT,
      source TEXT
    );
CREATE TABLE mastery_posteriors (
  bin_key TEXT PRIMARY KEY,
  p1_alpha REAL NOT NULL DEFAULT 0, p1_beta REAL NOT NULL DEFAULT 0,
  p2_alpha REAL NOT NULL DEFAULT 0, p2_beta REAL NOT NULL DEFAULT 0,
  p3_alpha REAL NOT NULL DEFAULT 0, p3_beta REAL NOT NULL DEFAULT 0,
  fill_alpha REAL NOT NULL DEFAULT 0, fill_beta REAL NOT NULL DEFAULT 0,
  mae_n INTEGER NOT NULL DEFAULT 0,
  mae_mean REAL NOT NULL DEFAULT 0.0,
  mae_m2 REAL NOT NULL DEFAULT 0.0,
  -- NEW enrichment
  letter TEXT DEFAULT '',
  band   TEXT DEFAULT '',
  epic_stories INT DEFAULT 0,
  stop_losses  INT DEFAULT 0,
  hedges       INT DEFAULT 0,
  net_pnl      REAL DEFAULT 0.0,
  source_run   TEXT DEFAULT 'LIVE',
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT NOT NULL DEFAULT 'TEST'
, priority INTEGER NOT NULL DEFAULT 99, weight_applied REAL NOT NULL DEFAULT 1.0, day_of_week TEXT DEFAULT 'unk', surface_type TEXT DEFAULT 'unk');
CREATE TABLE mastery_consolidator_progress(
            run_id TEXT PRIMARY KEY,
            last_id INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL DEFAULT (datetime('now','utc'))
        );
CREATE TABLE replay_reports_ingested(
                    filename TEXT PRIMARY KEY,
                    outcomes INTEGER,
                    ingested_at TEXT
                );
```
