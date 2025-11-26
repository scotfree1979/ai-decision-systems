# SETTLEMENTS Schema
```sql
CREATE TABLE bf_cleared_orders_raw(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,                 -- 'CSV' | 'API'
  ingested_at TEXT NOT NULL,
  betId TEXT,
  payload_json TEXT NOT NULL
);
CREATE TABLE sqlite_sequence(name,seq);
CREATE INDEX idx_bfco_raw_betid ON bf_cleared_orders_raw(betId);
CREATE TABLE bf_cleared_orders(
  betId TEXT PRIMARY KEY,
  marketId TEXT,
  selectionId TEXT,
  side TEXT,                            -- BACK|LAY
  priceRequested REAL,
  averagePriceMatched REAL,
  priceMatched REAL,
  sizeSettled REAL,
  profit REAL,                          -- net of commission (Betfair)
  commission REAL,
  settledDate TEXT,
  placedDate TEXT,
  customerOrderRef TEXT,
  customerStrategyRef TEXT,
  persistenceType TEXT,
  orderType TEXT,
  bspLiability REAL,
  eventTypeId TEXT,
  handicap REAL,
  json_raw TEXT
);
CREATE INDEX idx_bfco_mkt ON bf_cleared_orders(marketId);
CREATE INDEX idx_bfco_sel ON bf_cleared_orders(selectionId);
CREATE INDEX idx_bfco_settled ON bf_cleared_orders(settledDate);
CREATE TABLE bf_market_catalogue(
  marketId TEXT PRIMARY KEY,
  marketName TEXT,
  eventName TEXT,
  competition TEXT,
  countryCode TEXT,
  venue TEXT,
  marketStartTime TEXT,
  totalMatched REAL,
  raceType TEXT,
  distanceMeters REAL,
  going TEXT,
  class TEXT,
  runnersJson TEXT,          -- runners metadata (ids, names)
  raw_json TEXT
);
CREATE TABLE bf_market_book(
  marketId TEXT PRIMARY KEY,
  isInplay INTEGER,
  status TEXT,               -- OPEN|SUSPENDED|CLOSED
  betDelay INTEGER,
  totalMatched REAL,
  lastMatchTime TEXT,
  resultJson TEXT,           -- runners status/ltp/etc.
  raw_json TEXT
);
CREATE TABLE bf_runner_info(
  marketId TEXT,
  selectionId TEXT,
  runnerName TEXT,
  stallDraw INTEGER,
  jockeyName TEXT,
  trainerName TEXT,
  age INTEGER,
  weightCarried REAL,
  officialRating INTEGER,
  raw_json TEXT,
  PRIMARY KEY(marketId, selectionId)
);
CREATE TABLE bf_settlement_runner_day(
  day TEXT,
  marketId TEXT,
  selectionId TEXT,
  trades INTEGER NOT NULL DEFAULT 0,
  net REAL NOT NULL DEFAULT 0.0,
  commission REAL NOT NULL DEFAULT 0.0,
  win_rate REAL NOT NULL DEFAULT 0.0,
  PRIMARY KEY(day, marketId, selectionId)
);
CREATE VIEW v_settle_mkt_day AS
                  SELECT date(settledDate)         AS day,
                         marketId                  AS marketId,
                         SUM(COALESCE(profit,0.0)) AS net,
                         COUNT(*)                  AS bets
                    FROM bf_cleared_orders
                   WHERE settledDate IS NOT NULL
                GROUP BY 1,2
/* v_settle_mkt_day(day,marketId,net,bets) */;
CREATE VIEW v_settle_day AS
                  SELECT day,
                         SUM(net)  AS net,
                         COUNT(*)  AS mkts
                    FROM v_settle_mkt_day
                GROUP BY 1
/* v_settle_day(day,net,mkts) */;
CREATE TABLE st_runner_day_totals(
            day TEXT,
            marketId TEXT,
            selectionId TEXT,
            parents_placed INTEGER NOT NULL DEFAULT 0,
            children_placed INTEGER NOT NULL DEFAULT 0,
            exits INTEGER NOT NULL DEFAULT 0,
            cancels INTEGER NOT NULL DEFAULT 0,
            fails INTEGER NOT NULL DEFAULT 0,
            hedges_matched INTEGER NOT NULL DEFAULT 0,
            successes INTEGER NOT NULL DEFAULT 0,
            net REAL NOT NULL DEFAULT 0.0,
            commission REAL NOT NULL DEFAULT 0.0,
            PRIMARY KEY(day, marketId, selectionId)
          );
CREATE TABLE st_order_ledger(
            betId TEXT PRIMARY KEY,
            source TEXT, mode TEXT,
            marketId TEXT, selectionId TEXT, side TEXT,
            price REAL, size REAL, status TEXT,
            opened_at TEXT, placed_at TEXT, settled_at TEXT,
            customerOrderRef TEXT, customerStrategyRef TEXT,
            net_pl REAL, commission REAL, json_raw TEXT
          );
```
