-- ✅ 1. RAM.db – Real-time Access Memory
-- Holds current active runner states and live signal payloads
CREATE TABLE IF NOT EXISTS ram_snapshots (
    marketId TEXT NOT NULL,
    selectionId INTEGER NOT NULL,
    odds REAL,
    tick_pattern TEXT,
    direction_bias TEXT,
    anchor_odd REAL,
    range_low REAL,
    range_high REAL,
    position_ratio REAL,
    volatility REAL,
    tick_trail TEXT, -- JSON array of odds
    oc_snapshots TEXT, -- JSON of OC labels to odds
    position INTEGER,
    minutes_to_post REAL,
    tier TEXT,
    last_updated TEXT,
    snapshot TEXT, -- Full JSON snapshot if needed
    PRIMARY KEY (marketId, selectionId)
);


-- ✅ 2. STM.db – Short-Term Memory
-- Holds today’s signals, matches, trades (live signals)
CREATE TABLE IF NOT EXISTS stm_live_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    marketId TEXT,
    selectionId INTEGER,
    odds REAL,
    range_low REAL,
    range_high REAL,
    tick_pattern TEXT,
    direction_bias TEXT,
    confidence REAL,
    drift REAL,
    spread REAL,
    position_ratio REAL,
    volatility REAL,
    signal_type TEXT,
    stake REAL,
    session_token TEXT,
    timestamp TEXT,
    customerOrderRef TEXT,
    blueprint_match TEXT,
    scalp_direction TEXT,
    scalp_ticks INTEGER,
    forced INTEGER DEFAULT 0,
    status TEXT
);


-- ✅ 3. LTM.db – Long-Term Memory
-- Finalised playbooks and historical outcomes
CREATE TABLE IF NOT EXISTS ltm_playbooks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playbook_group_id TEXT,
    marketId TEXT,
    selectionId INTEGER,
    pattern_key TEXT,
    blueprint_match TEXT,
    scalp_direction TEXT,
    tick_pattern TEXT,
    confidence REAL,
    scalp_ticks INTEGER,
    entry_odds REAL,
    hedge_odds REAL,
    entry_side TEXT,
    hedge_side TEXT,
    entry_stake REAL,
    hedge_stake REAL,
    entry_bet_id TEXT,
    hedge_bet_id TEXT,
    pnl REAL,
    result TEXT,
    opened_at TEXT,
    closed_at TEXT,
    customer_order_ref TEXT,
    ladder_matched INTEGER,
    ladder_total INTEGER
);
