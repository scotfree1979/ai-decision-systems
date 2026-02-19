# 16 — DAL PATHS TRACE

## Files
engines/config_paths.py
engines/db/*

## DB Separation
• AUTO_DB (autoscalp_gui.db)
• BETS_DB (bets.db)
• settlements.db
• mastery_v7.db

## Write Paths
• orders → AUTO_DB
• pnl_trades → BETS_DB
• pnl_daily → BETS_DB
• mastery_events → AUTO_DB
• markets_schedule → BETS_DB
• odds_current → AUTO_DB

## Invariants
• connect_db() wrapped to enforce row_factory
• WAL mode enabled
• Schema repaired on startup via _dal_verify_and_repair_schema

## Observed Gap
• No global schema version hash enforcement present

