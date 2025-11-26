# AUTOSCALP_GUI Schema
```sql
CREATE TABLE app_kv(key TEXT PRIMARY KEY, value TEXT, updated_at TEXT);
CREATE TABLE __gui_probe(k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE inbound_oc_cache(
                      id INTEGER PRIMARY KEY AUTOINCREMENT,
                      marketId TEXT NOT NULL,
                      selectionId TEXT NOT NULL,
                      anchor_odd REAL,
                      last_sync_ts TEXT
                      -- OCn columns can already exist; we don't redeclare them here
                    , oc1 REAL, oc1_band_json TEXT, oc2 REAL, oc2_band_json TEXT, oc3 REAL, oc3_band_json TEXT, oc4 REAL, oc4_band_json TEXT, oc5 REAL, oc5_band_json TEXT, oc6 REAL, oc6_band_json TEXT, oc7 REAL, oc7_band_json TEXT, oc8 REAL, oc8_band_json TEXT, oc9 REAL, oc9_band_json TEXT, oc10 REAL, oc10_band_json TEXT, oc11 REAL, oc11_band_json TEXT, oc12 REAL, oc12_band_json TEXT, oc13 REAL, oc13_band_json TEXT, oc14 REAL, oc14_band_json TEXT, oc15 REAL, oc15_band_json TEXT, oc16 REAL, oc16_band_json TEXT, oc17 REAL, oc17_band_json TEXT, oc18 REAL, oc18_band_json TEXT, oc19 REAL, oc19_band_json TEXT, oc20 REAL, oc20_band_json TEXT);
CREATE TABLE sqlite_sequence(name,seq);
CREATE UNIQUE INDEX ux_inbound_oc_cache_mid_sid ON inbound_oc_cache(marketId, selectionId);
CREATE TABLE dashboard_runners(
                      day TEXT,
                      marketId TEXT,
                      selectionId TEXT, runner_name TEXT, odd REAL, implied_prob REAL, band_low REAL, band_high REAL, last_snapshot_ts TEXT, source TEXT, top6_rank INTEGER, first_seen_rank INTEGER, first_seen_ts TEXT, in_top6 INTEGER, bar_pre_norm REAL, bar_post_norm REAL, bar_display_norm REAL, bar_color TEXT, flow_ppm REAL, vol_1m REAL, trend TEXT,
                      -- other columns…
                      PRIMARY KEY(day, marketId, selectionId)
                    );
CREATE UNIQUE INDEX ux_dash_run_day_mid_sid ON dashboard_runners(day, marketId, selectionId);
CREATE TABLE inbound_bets_min (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        placed_at TEXT,
        anchor_odd REAL,
        odds_check_1 REAL, odds_check_2 REAL, odds_check_3 REAL,
        odds_check_4 REAL, odds_check_5 REAL, odds_check_6 REAL,
        horse_name TEXT,
        meta_json TEXT
    );
CREATE TABLE oc_series(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        marketId TEXT NOT NULL,
        selectionId TEXT NOT NULL,
        stage TEXT NOT NULL,
        snapshot_ts TEXT NOT NULL,
        odd REAL,
        band_low REAL,
        band_high REAL,
        band_json TEXT,
        meta_json TEXT,
        source TEXT
      );
CREATE INDEX idx_oc_series_mkt_time ON oc_series(marketId, snapshot_ts);
CREATE INDEX idx_oc_series_sel_time ON oc_series(selectionId, snapshot_ts);
CREATE TABLE strategies_performance(
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
        );
CREATE INDEX idx_sp_mode_ts       ON strategies_performance(mode, ts);
CREATE INDEX idx_sp_strategy_ts   ON strategies_performance(strategy, ts);
CREATE INDEX idx_sp_runner_ts     ON strategies_performance(marketId, selectionId, ts);
CREATE INDEX idx_sp_runid_ts      ON strategies_performance(run_id, ts);
CREATE TABLE odds_current(
      day TEXT NOT NULL,
      marketId TEXT NOT NULL,
      selectionId TEXT NOT NULL,
      updated_ts TEXT,
      ltp REAL, back1 REAL, lay1 REAL, fav_rank_now INTEGER, mto_minutes REAL, slope_ppm REAL, tick_vel_1s_up INTEGER, tick_vel_3s_up INTEGER,
      PRIMARY KEY(day, marketId, selectionId)
    );
CREATE INDEX idx_inbound_oc_cache_mid_sid_id ON inbound_oc_cache(marketId, selectionId, id);
CREATE INDEX idx_oc_series_mid_sid_ts ON oc_series(marketId, selectionId, snapshot_ts);
CREATE TABLE sqlite_stat1(tbl,idx,stat);
CREATE TABLE sqlite_stat4(tbl,idx,neq,nlt,ndlt,sample);
CREATE TABLE dashboard_runs(
              run_id TEXT PRIMARY KEY,
              mode TEXT,
              source_mode TEXT,
              day TEXT,
              speed_x REAL,
              now_ts TEXT,
              status TEXT,
              last_heartbeat_ts TEXT
            );
CREATE TABLE dashboard_markets(
              day TEXT,
              marketId TEXT,
              course TEXT,
              market_name TEXT,
              off_at_utc TEXT,
              status TEXT,
              t_minus_sec INTEGER,
              is_next INTEGER,
              runners_total INTEGER,
              source TEXT,
              betfair_status TEXT,
              betfair_status_mapped TEXT,
              betfair_status_age_sec INTEGER,
              status_source TEXT,
              last_api_ok_ts TEXT,
              last_api_err TEXT,
              discovered_ts TEXT,
              last_refreshed_ts TEXT, t0_phase TEXT, t0_color TEXT, t0_sec INTEGER, inplay_start_ts TEXT, late_since_ts TEXT, is_concurrent INTEGER, concurrent_rank INTEGER,
              PRIMARY KEY(day, marketId)
            );
CREATE TABLE dashboard_tiles(
              day TEXT,
              mode TEXT,
              total REAL,
              yesterday REAL,
              today REAL,
              d7 REAL,
              d30 REAL,
              hit_rate REAL,
              pnl_per_hour REAL,
              avg_gain REAL,
              win_pct_mkt REAL,
              bank REAL,
              used_exposure REAL,
              last_refreshed_ts TEXT,
              PRIMARY KEY(day, mode)
            );
CREATE TABLE dashboard_orders_tape(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              ts TEXT,
              marketId TEXT,
              selectionId TEXT,
              runner_name TEXT,
              side TEXT,
              status TEXT,
              price REAL,
              amount REAL,
              realized_pnl REAL,
              order_id TEXT,
              mode TEXT,
              day TEXT
            );
CREATE INDEX idx_dash_orders_tape_day_ts
              ON dashboard_orders_tape(day, ts DESC);
CREATE TABLE markets_schedule(
          marketId TEXT PRIMARY KEY,
          venue TEXT,
          course TEXT,
          event_name TEXT,
          market_name TEXT,
          off_at_utc TEXT,
          country_code TEXT
        , off_ts TEXT, source TEXT, eventId TEXT, marketTypeCode TEXT);
CREATE TABLE runners(
          marketId TEXT,
          selectionId TEXT,
          runner_name TEXT,
          PRIMARY KEY (marketId, selectionId)
        );
CREATE TABLE dashboard_activity(
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
, id INTEGER);
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
CREATE INDEX idx_odds_ts ON odds_snapshots(marketId, selectionId, ts);
CREATE INDEX idx_odds_cur_mid ON odds_current(day, marketId);
CREATE TABLE runs(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        mode TEXT,
        blueprint_file TEXT,
        notes TEXT
      );
CREATE TABLE orders(
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
      , bf_bet_id TEXT, customer_ref TEXT, entry_matched_odds REAL, entry_matched_stake REAL, exit_matched_odds REAL, exit_matched_stake REAL, child_bf_bet_id TEXT, notes TEXT, stop_ticks INTEGER DEFAULT 4, stop_loss_triggered INTEGER DEFAULT 0, greened_up INTEGER DEFAULT 0, exit_kind TEXT);
CREATE INDEX idx_orders_mode_opened ON orders(mode, opened_at);
CREATE INDEX idx_orders_status ON orders(entry_status, exit_status);
CREATE INDEX idx_orders_role ON orders(role);
CREATE INDEX idx_orders_link ON orders(hedge_of);
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
CREATE TABLE blueprint_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        day TEXT NOT NULL,
        marketId TEXT NOT NULL,
        selectionId TEXT,
        blueprint_key TEXT NOT NULL,
        strength REAL NOT NULL,
        detected_at TEXT NOT NULL,
        window_tag TEXT,
        context_json TEXT NOT NULL
      );
CREATE INDEX idx_bp_ev_day ON blueprint_events(day, marketId, detected_at);
CREATE INDEX idx_orders_bfid ON orders(bf_bet_id);
CREATE TABLE decisions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id          TEXT,
  marketId        TEXT,
  selectionId     TEXT,
  decided_at      TEXT,
  signal_type     TEXT,
  blueprint_match TEXT,
  confidence      REAL,
  scalp_direction TEXT,
  proposed_odds   REAL,
  proposed_stake  REAL,
  notes           TEXT,
  meta_json       TEXT,
  order_id        INTEGER
, why TEXT);
CREATE INDEX idx_decisions_run_time ON decisions(run_id, decided_at);
CREATE INDEX idx_oc_series_day ON oc_series(date(snapshot_ts));
CREATE INDEX idx_oc_series_ts  ON oc_series(snapshot_ts);
CREATE INDEX idx_decisions_day ON decisions(date(decided_at));
CREATE INDEX idx_decisions_mid_sid_notes_dt ON decisions(marketId, selectionId, notes, decided_at);
CREATE TABLE events(
              ts TEXT, level TEXT, source TEXT, message TEXT
            );
CREATE TABLE runner_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketId TEXT NOT NULL,
    selectionId TEXT NOT NULL,
    status TEXT NOT NULL,
    ts TEXT DEFAULT (datetime('now','utc'))
);
CREATE INDEX idx_runner_activity_mid_sid
    ON runner_activity(marketId, selectionId);
CREATE TABLE plan_ledger (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  day TEXT NOT NULL,
  run_id TEXT,
  plan_id TEXT UNIQUE NOT NULL,
  marketId TEXT NOT NULL,
  selectionId TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'field',
  allow_letters TEXT NOT NULL DEFAULT '[]',
  budget_units INTEGER NOT NULL DEFAULT 1,
  max_open_parents INTEGER NOT NULL DEFAULT 1,
  letter TEXT NOT NULL DEFAULT '',
  note TEXT NOT NULL DEFAULT '',
  scalp_direction TEXT,
  proposed_odds REAL,
  proposed_stake REAL,
  confidence REAL,
  blueprint_match TEXT,
  window_tag TEXT,
  anchor_odd REAL,
  oc_stage TEXT,
  decided_at TEXT NOT NULL DEFAULT (datetime('now','utc')),
  status TEXT NOT NULL,
  why TEXT,
  parent_order_id INTEGER,
  child_order_id INTEGER,
  updated_at TEXT NOT NULL DEFAULT (datetime('now','utc'))
);
CREATE INDEX idx_pl_day_runner
  ON plan_ledger(day, marketId, selectionId, decided_at);
CREATE INDEX idx_pl_status_day
  ON plan_ledger(status, day);
CREATE INDEX idx_pl_letter_day
  ON plan_ledger(letter, day);
CREATE INDEX idx_plan_ledger_midsid ON plan_ledger(marketId, selectionId);
CREATE INDEX idx_plan_ledger_status ON plan_ledger(status);
CREATE INDEX idx_plan_ledger_parent ON plan_ledger(parent_order_id);
CREATE INDEX idx_plan_ledger_child  ON plan_ledger(child_order_id);
CREATE VIEW orders_effective AS
SELECT *
  FROM orders
 WHERE UPPER(COALESCE(entry_status,''))='PLACED'
   AND entry_bet_id IS NOT NULL
/* orders_effective(id,customerOrderRef,mode,run_id,marketId,selectionId,side,entry_odds,entry_stake,entry_status,entry_bet_id,opened_at,exit_status,exit_bet_id,exit_odds,exit_stake,closed_at,realized_pnl,net_pl,error,role,hedge_of,source,ts,bf_bet_id,customer_ref,entry_matched_odds,entry_matched_stake,exit_matched_odds,exit_matched_stake,child_bf_bet_id,notes,stop_ticks,stop_loss_triggered,greened_up,exit_kind) */;
CREATE VIEW v_odds_latest AS
WITH latest AS (
  SELECT marketId, selectionId, MAX(snapshot_ts) AS max_ts
  FROM oc_series
  GROUP BY marketId, selectionId
),
stage_ordered AS (
  SELECT o.*,
         CASE WHEN o.stage GLOB 'OC[0-9]*' THEN CAST(SUBSTR(o.stage,3) AS INT) ELSE 0 END AS stage_n
  FROM oc_series o
  JOIN latest l
    ON o.marketId=l.marketId AND o.selectionId=l.selectionId AND o.snapshot_ts=l.max_ts
),
one_per_sid AS (
  SELECT marketId, selectionId, odd AS ltp, snapshot_ts,
         ROW_NUMBER() OVER (
            PARTITION BY marketId, selectionId, snapshot_ts
            ORDER BY stage_n DESC, id DESC
         ) AS rn
  FROM stage_ordered
)
SELECT marketId, selectionId, CAST(ltp AS REAL) AS ltp, snapshot_ts
FROM one_per_sid
WHERE rn=1
/* v_odds_latest(marketId,selectionId,ltp,snapshot_ts) */;
CREATE VIEW v_odds_first_last_today AS
WITH raw AS (
  SELECT marketId, selectionId, snapshot_ts, odd, stage, id,
         CASE WHEN stage GLOB 'OC[0-9]*' THEN CAST(SUBSTR(stage,3) AS INT) ELSE 0 END AS stage_n
  FROM oc_series
  WHERE date(snapshot_ts)=date('now','utc')
),
first_ts AS ( SELECT marketId, selectionId, MIN(snapshot_ts) AS first_ts FROM raw GROUP BY marketId, selectionId ),
last_ts  AS ( SELECT marketId, selectionId, MAX(snapshot_ts) AS last_ts  FROM raw GROUP BY marketId, selectionId ),
first_pick AS (
  SELECT r.marketId, r.selectionId, r.snapshot_ts AS ts, r.odd AS ltp,
         ROW_NUMBER() OVER (PARTITION BY r.marketId, r.selectionId, r.snapshot_ts ORDER BY r.stage_n DESC, r.id DESC) AS rn
  FROM raw r JOIN first_ts f ON r.marketId=f.marketId AND r.selectionId=f.selectionId AND r.snapshot_ts=f.first_ts
),
last_pick AS (
  SELECT r.marketId, r.selectionId, r.snapshot_ts AS ts, r.odd AS ltp,
         ROW_NUMBER() OVER (PARTITION BY r.marketId, r.selectionId, r.snapshot_ts ORDER BY r.stage_n DESC, r.id DESC) AS rn
  FROM raw r JOIN last_ts l ON r.marketId=l.marketId AND r.selectionId=l.selectionId AND r.snapshot_ts=l.last_ts
)
SELECT f.marketId, f.selectionId, CAST(f.ltp AS REAL) AS ltp_first, CAST(l.ltp AS REAL) AS ltp_last
FROM first_pick f JOIN last_pick l ON f.marketId=l.marketId AND f.selectionId=l.selectionId
WHERE f.rn=1 AND l.rn=1
/* v_odds_first_last_today(marketId,selectionId,ltp_first,ltp_last) */;
CREATE TABLE dashboard_strategy_perf(
    strategy TEXT,
    p_q INTEGER, p_p INTEGER, p_m INTEGER,
    c_q INTEGER, c_p INTEGER, c_m INTEGER,
    open_parents INTEGER,
    matched_liab REAL, unmatched_liab REAL,
    pnl_today REAL,
    last_trade_ts TEXT,
    mode TEXT
);
CREATE TABLE book_state(
              day TEXT NOT NULL,
              run_id TEXT NOT NULL,
              letter TEXT NOT NULL,
              open_parents INTEGER DEFAULT 0,
              matched_children INTEGER DEFAULT 0,
              open_liability REAL DEFAULT 0.0,
              net_pl REAL DEFAULT 0.0,
              updated_at TEXT,
              PRIMARY KEY(day, run_id, letter)
            );
CREATE TABLE internal_bank(
            day TEXT PRIMARY KEY,
            start_balance REAL NOT NULL,
            current_balance REAL NOT NULL,
            updated_at TEXT NOT NULL
        );
CREATE TABLE indicators_opportunities(
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            runner_name TEXT,
            opportunities INTEGER DEFAULT 0,
            taken INTEGER DEFAULT 0,
            conversion INTEGER DEFAULT 0,
            last_ts TEXT,
            PRIMARY KEY(day, marketId, selectionId)
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
  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
  source TEXT NOT NULL DEFAULT 'TEST'
, day_of_week TEXT DEFAULT 'unk', surface_type TEXT DEFAULT 'unk', priority INTEGER NOT NULL DEFAULT 99, weight_applied REAL NOT NULL DEFAULT 1.0, net_pnl REAL NOT NULL DEFAULT 0.0, distance_band TEXT DEFAULT 'unk', fav_rank_bin TEXT DEFAULT 'unk', trainer TEXT, jockey TEXT, venue TEXT, going TEXT, race_type TEXT, distance REAL, market_liquidity REAL, age INTEGER, stall INTEGER, official_rating INTEGER, weight REAL, trainer_form REAL, jockey_form REAL, winners TEXT DEFAULT '[]');
CREATE TABLE loss_tags(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    marketId TEXT NOT NULL,
    selectionId TEXT NOT NULL,
    tag TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now','utc'))
, dow TEXT DEFAULT '');
CREATE INDEX idx_loss_tags_day_mid_sid
    ON loss_tags(day, marketId, selectionId);
CREATE UNIQUE INDEX ux_loss_tags_day_mid_sid_tag
            ON loss_tags(day, marketId, selectionId, tag)
        ;
CREATE TABLE mastery_outcomes_raw(
        day TEXT,
        marketId TEXT,
        selectionId TEXT,
        letter TEXT,
        distance_band TEXT,
        fav_rank TEXT,
        day_of_week TEXT,
        surface TEXT,
        class_band TEXT,
        target_ticks INTEGER,
        realized_ticks INTEGER,
        success INTEGER,
        stoploss_hit INTEGER,
        hedge_hit INTEGER,
        entry_px REAL,
        pnl REAL,
        weight REAL,
        priority INTEGER,
        created_at TEXT
    , id INTEGER);
CREATE VIEW bank_state AS
 SELECT day,
        current_balance AS balance,
        start_balance,
        updated_at
 FROM internal_bank
/* bank_state(day,balance,start_balance,updated_at) */;
CREATE UNIQUE INDEX idx_mastery_outcomes_id
ON mastery_outcomes_raw(id);
CREATE TABLE replay_reports_ingested(
                    filename TEXT PRIMARY KEY,
                    outcomes INTEGER,
                    ingested_at TEXT
                );
CREATE VIEW v_strategy_perf AS
        WITH base AS (
          SELECT
            id, role, hedge_of,
            date(COALESCE(opened_at,ts))                 AS d,
            UPPER(COALESCE(entry_status,''))             AS es,
            UPPER(COALESCE(exit_status ,''))             AS xs,
            UPPER(COALESCE(side,''))                     AS side,
            COALESCE(entry_matched_odds,  entry_odds,  0.0) AS odds,
            COALESCE(entry_matched_stake, entry_stake, 0.0) AS stake,
            COALESCE(mode,'')                            AS mode,
            CASE WHEN COALESCE(source,'')<>'' THEN UPPER(SUBSTR(source,1,1)) ELSE 'S' END AS letter,
            CASE
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='A' THEN 'ALWAYS_ON'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='B' THEN 'BTL_SCOUT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='G' THEN 'BTL_AGGR'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='X' THEN 'S4_CROSSOVER'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='R' THEN 'S5_BREAKOUT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='F' THEN 'S6_STEAM_FADE'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='M' THEN 'LADDER_STRATEGY'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='Z' THEN 'OG_BIAS'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='I' THEN 'IP1_SHOCK_DRIFT'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='T' THEN 'IP2_TIRED_LEADER'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='C' THEN 'IP3_CLOSE_FINISH'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='E' THEN 'IP4_FENCE_ERROR'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='K' THEN 'IP5_COLLAPSE_FADE'
              WHEN UPPER(SUBSTR(COALESCE(source,''),1,1))='S' THEN 'LEGACY_S'
              ELSE 'UNKNOWN'
            END AS strategy_name,
            COALESCE(net_pl, realized_pnl, 0.0)          AS pnl,
            COALESCE(opened_at,ts)                       AS opened_ts,
            COALESCE(closed_at,opened_at,ts)             AS closed_ts
          FROM orders
          WHERE date(COALESCE(opened_at,ts)) = date('now')  -- today-only (unchanged)
        ),
        mode_sel AS (SELECT * FROM base WHERE UPPER(mode)=UPPER('TEST') ),
        open_parents AS (
          SELECT id FROM mode_sel
           WHERE (role IS NULL OR role='PARENT')
             AND es='MATCHED' AND xs<>'MATCHED'
        ),
        liab_unmatched AS (
          SELECT letter,
                 SUM(CASE WHEN side='LAY' THEN (odds-1.0)*stake ELSE stake END) AS v
          FROM mode_sel
          WHERE (role IS NULL OR role='PARENT') AND es IN ('QUEUED','PLACED')
          GROUP BY letter
        ),
        liab_matched_open AS (
          SELECT letter,
                 SUM(CASE WHEN side='LAY' THEN (odds-1.0)*stake ELSE stake END) AS v
          FROM mode_sel
          WHERE (role IS NULL OR role='PARENT') AND es='MATCHED' AND xs NOT IN ('MATCHED','SETTLED')
          GROUP BY letter
        ),
        pnl_today AS (
          SELECT letter, ROUND(SUM(pnl),2) AS pnl
          FROM mode_sel
          WHERE (role IS NULL OR role='PARENT') AND xs='MATCHED'
          GROUP BY letter
        ),
        last_trade AS (
          SELECT letter, MAX(opened_ts) AS last_ts
          FROM mode_sel WHERE (role IS NULL OR role='PARENT')
          GROUP BY letter
        )
        SELECT
          COALESCE(bl.strategy_name, bl.letter) AS strategy,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='QUEUED')                                  AS p_q,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='PLACED')                                  AS p_p,
          SUM((bl.role IS NULL OR bl.role='PARENT') AND bl.es='MATCHED')                                 AS p_m,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND bl.es='QUEUED')                                    AS c_q,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND bl.es='PLACED')                                    AS c_p,
          SUM(UPPER(COALESCE(bl.role,''))='CHILD' AND (bl.es='MATCHED' OR bl.xs='MATCHED'))              AS c_m,
          (SELECT COUNT(*) FROM open_parents op JOIN mode_sel b2 ON b2.id=op.id AND b2.letter=bl.letter) AS open_parents,
          ROUND(COALESCE((SELECT v FROM liab_matched_open WHERE letter=bl.letter),0),2)                  AS matched_liab,
          ROUND(COALESCE((SELECT v FROM liab_unmatched   WHERE letter=bl.letter),0),2)                    AS unmatched_liab,
          ROUND(COALESCE((SELECT pnl FROM pnl_today      WHERE letter=bl.letter),0),2)                    AS pnl_today,
          COALESCE((SELECT last_ts FROM last_trade       WHERE letter=bl.letter),'')                      AS last_trade_ts
        FROM mode_sel bl
        GROUP BY bl.letter, bl.strategy_name
        ORDER BY strategy
/* v_strategy_perf(strategy,p_q,p_p,p_m,c_q,c_p,c_m,open_parents,matched_liab,unmatched_liab,pnl_today,last_trade_ts) */;
CREATE TABLE market_data (
  marketId        TEXT NOT NULL,
  selectionId     INTEGER NOT NULL,
  runnerName      TEXT,
  trainerName     TEXT,
  jockeyName      TEXT,
  age             TEXT,
  stallDraw       TEXT,
  officialRating  TEXT,
  weightValue     TEXT,
  venue           TEXT,
  eventName       TEXT,
  marketName      TEXT,
  marketStartTime TEXT,    -- ISO8601 UTC
  distance        TEXT,
  going           TEXT,
  raw_json        TEXT,
  PRIMARY KEY(marketId, selectionId)
);
CREATE INDEX idx_market_data_venue
  ON market_data(venue);
CREATE INDEX idx_market_data_trainer
  ON market_data(trainerName);
CREATE INDEX idx_market_data_jockey
  ON market_data(jockeyName);
CREATE INDEX idx_market_data_date
  ON market_data(marketStartTime);
CREATE TABLE runner_history (
        runner_name     TEXT NOT NULL,
        selectionId     INTEGER NOT NULL,
        marketId        TEXT NOT NULL,
        runs            INTEGER DEFAULT 0,
        wins            INTEGER DEFAULT 0,
        losses          INTEGER DEFAULT 0,
        net_pnl         REAL DEFAULT 0.0,
        last_run_date   TEXT,
        last_win_date   TEXT,
        form_string     TEXT,
        trainerName     TEXT,
        jockeyName      TEXT,
        age             INTEGER,
        stallDraw       INTEGER,
        officialRating  INTEGER,
        weightValue     REAL,
        updated_at      TEXT DEFAULT (datetime('now','utc')),
        PRIMARY KEY (runner_name, selectionId)
    );
CREATE TABLE replay_resume_progress(
        id INTEGER PRIMARY KEY CHECK (id=1),
        last_epoch INTEGER,
        last_day_idx INTEGER,
        updated_at TEXT
    );
CREATE TABLE mastery_posteriors_progress(
            processed_outcome_id INTEGER
        );
```
