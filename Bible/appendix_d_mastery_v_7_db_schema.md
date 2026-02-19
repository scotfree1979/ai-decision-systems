# APPENDIX D — MASTERY_V7.DB SCHEMA (AUTHORITATIVE)

Source: live extraction via sqlite3
Database: data/mastery_v7.db

---

## CORE TABLES

### mastery_posteriors
bin_key TEXT PRIMARY KEY
marketId TEXT
selectionId TEXT
letter TEXT
bucket TEXT
confidence REAL
bucket_confidence REAL
live_pnl_ratio REAL
weight_applied REAL
updated_at TEXT
weight REAL

---

### context_observations_raw
ctx_ref TEXT UNIQUE
marketId TEXT
selectionId TEXT
engine TEXT
engine_family TEXT
letter TEXT
side TEXT
odds_at_decision REAL
stake_at_decision REAL
band TEXT
fav_rank INTEGER
minutes_to_off REAL
in_play INTEGER
ts_created TEXT
ctx_json TEXT

---

### context_outcomes
ctx_ref TEXT
exit_kind TEXT
pnl REAL
success INTEGER
exit_ts TEXT
meta_json TEXT

---

### cache_mastery_day
(Full surface + trade + volatility fields as extracted)

---

### brain_* tables
brain_history_v7
brain_state_v7
brain_prints

---

⚠ Mastery authority rules:
• bucket + letter must align with mastery_posteriors
• context_observations_raw → context_outcomes join on ctx_ref
• No field assumptions beyond this definition

Schema locked.

