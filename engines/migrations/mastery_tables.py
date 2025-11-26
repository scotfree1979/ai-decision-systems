from __future__ import annotations

DDL_MASTERY_CORE = """
CREATE TABLE IF NOT EXISTS mastery_state (
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
CREATE TABLE IF NOT EXISTS mastery_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  happened_at TEXT NOT NULL DEFAULT (datetime('now')),
  event_type TEXT NOT NULL,
  details_json TEXT NOT NULL DEFAULT '{}',
  delta_progress INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT 'TEST'
);
INSERT OR IGNORE INTO mastery_state (id) VALUES (1);
"""

DDL_MASTERY_PRIORS = """
CREATE TABLE IF NOT EXISTS mastery_priors (
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
CREATE UNIQUE INDEX IF NOT EXISTS idx_mastery_priors_bin ON mastery_priors(bin_key);
CREATE TABLE IF NOT EXISTS synthetic_scenarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE,
  bin_key TEXT NOT NULL,
  script_json TEXT NOT NULL,
  notes TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS synthetic_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  scenario_id INTEGER NOT NULL REFERENCES synthetic_scenarios(id) ON DELETE CASCADE,
  seed INTEGER NOT NULL,
  outcomes_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

def apply_mastery_core(conn) -> None:
    conn.executescript(DDL_MASTERY_CORE)

def apply_mastery_priors(conn) -> None:
    conn.executescript(DDL_MASTERY_PRIORS)
