# APPENDIX C — SETTLEMENTS.DB SCHEMA (AUTHORITATIVE)

Source: live extraction via sqlite3
Database: data/settlements.db

---

## CORE TABLE

### bf_cleared_orders
betId TEXT PRIMARY KEY
marketId TEXT
selectionId TEXT
side TEXT
priceRequested REAL
averagePriceMatched REAL
priceMatched REAL
sizeSettled REAL
profit REAL
commission REAL
settledDate TEXT
placedDate TEXT
customerOrderRef TEXT
customerStrategyRef TEXT
persistenceType TEXT
orderType TEXT
bspLiability REAL
eventTypeId TEXT
handicap REAL
json_raw TEXT

---

## SUPPORT TABLES

bf_market_book
bf_market_catalogue
bf_runner_info
bf_settlement_runner_day
runner_form
runner_form_canonical
st_order_ledger
st_runner_day_totals

---

## VIEWS

v_settle_day
v_settle_mkt_day

---

⚠ Settlement authority:
• profit and commission fields must be sourced from bf_cleared_orders
• orders.bf_bet_id must align with bf_cleared_orders.betId
• No PnL recalculation outside this mapping

This appendix locks settlement column naming permanently.

