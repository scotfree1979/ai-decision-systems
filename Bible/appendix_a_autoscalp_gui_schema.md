# APPENDIX A — AUTOSCALP_GUI.DB SCHEMA (AUTHORITATIVE)

Source: live extraction via sqlite3
Database: data/autoscalp_gui.db

---

## CORE EXECUTION TABLE

### orders
(Full column list preserved exactly as extracted)

id INTEGER PRIMARY KEY AUTOINCREMENT
customerOrderRef TEXT UNIQUE
mode TEXT
run_id INTEGER
marketId TEXT
selectionId TEXT
side TEXT
entry_odds REAL
entry_stake REAL
entry_status TEXT
entry_bet_id TEXT
opened_at TEXT
exit_status TEXT
exit_bet_id TEXT
exit_odds REAL
exit_stake REAL
closed_at TEXT
realized_pnl REAL
net_pl REAL
error TEXT
role TEXT
hedge_of INTEGER
source TEXT
ts TEXT
bf_bet_id TEXT
customer_ref TEXT
entry_matched_odds REAL
entry_matched_stake REAL
exit_matched_odds REAL
exit_matched_stake REAL
child_bf_bet_id TEXT
notes TEXT
stop_ticks INTEGER DEFAULT 4
stop_loss_triggered INTEGER DEFAULT 0
greened_up INTEGER DEFAULT 0
exit_kind TEXT
bank REAL DEFAULT 0.0
stoploss_mode TEXT DEFAULT 'BALANCED'
trail_floor REAL
trail_ceil REAL
engine TEXT
stop_loss_px REAL
bank_reconciled INTEGER DEFAULT 0
target_ticks INTEGER
exposure_released INTEGER DEFAULT 0
parent_closed INTEGER DEFAULT 0
current_px REAL
current_px_ts TEXT
required_exposure REAL
route_id INTEGER
bus_stop INTEGER
tick_id INTEGER
exposure_released_amount REAL
lane INTEGER

---

## CORE MARKET DATA TABLES

bets
inbound_oc_cache
oc_series
odds_current
odds_snapshots
markets_schedule

(Full definitions preserved in raw extraction above — this appendix acts as locked column authority.)

---

## EXPOSURE / LIABILITY TABLES

engine_pots
internal_bank
live_state
liability_signals
exposure_log
book_state

---

## EXECUTION SURFACES

betfair_execution_surface
execution_events
order_events
parent_child_flow (VIEW)
engine_exposure_live (VIEW)

---

## INTELLIGENCE / V7 TABLES

v7_intelligence_inferred
v7_intelligence_priors
v7_trade_index
v7_shape_summary_cache
v7_mastery_* views

---

## DASHBOARD / ANALYTICS TABLES

All dashboard_* tables and v_dash_* views are preserved exactly as extracted.

---

⚠ This appendix locks column names for:
• orders
• exposure logic
• pnl logic
• route fields (route_id, bus_stop, tick_id)
• engine tracking

No assumptions permitted outside this definition.

