# APPENDIX B — BETS.DB SCHEMA (AUTHORITATIVE)

Source: live extraction via sqlite3
Database: data/bets.db

---

## CORE TABLES

### bets
(Anchor authority table for route/window layer)

id INTEGER PRIMARY KEY AUTOINCREMENT
marketId TEXT NOT NULL
selectionId INTEGER NOT NULL
horse_name TEXT
race_name TEXT
market_name TEXT
event_name TEXT
marketStartTime TEXT
date TEXT
timestamp TEXT
meta_json TEXT
anchor_odd REAL
placed_at TEXT
OC0 REAL
OC0_band TEXT
status TEXT
test_mode INTEGER
customerOrderRef TEXT
runner_name TEXT

---

### inbound_oc_cache
(OC1–OC20 surface authority)

Includes oc1–oc20 REAL
Includes oc1_band_json–oc20_band_json TEXT

Primary UNIQUE(marketId, selectionId)

---

### oc_series
Full stage snapshots
UNIQUE(marketId, selectionId, stage, snapshot_ts)

---

### engine_pots
(day, engine) PRIMARY KEY
pot REAL

---

### pnl_trades / pnl_daily
Settlement tracking tables

---

⚠ Route + Window layer must only reference:
• bets
• markets_schedule
• inbound_oc_cache
• oc_series

No schema drift permitted beyond this definition.

