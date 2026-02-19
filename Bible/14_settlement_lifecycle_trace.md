# 14 — SETTLEMENT LIFECYCLE TRACE

## File
engines/live/settlements.py

## Entry
start_settlement_daemon(interval_s)

## Responsibilities
• Fetch cleared orders from Betfair API
• Reconcile orders table
• Update pnl_trades
• Update pnl_daily
• Expire closed markets
• Write mastery_events

## DB Tables
• bf_cleared_orders
• orders
• pnl_trades
• pnl_daily
• mastery_events

## Invariants
• Reconciliation unconditional per cycle
• Settlement does not place trades
• BankState reconciles realized PnL

