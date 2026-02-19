# 13 — LIVE ROUTER WORKER TRACE

## File
engines/live/live_router.py

## Entry Points
• start_router_child_worker()
• _start_rehedge_loop()
• place_from_bus()

## Responsibilities
• Place Betfair orders
• Poll bet status (get_bet_status)
• Mark entry_status
• Create child hedge rows
• Call _finalize_parent_on_hedge on match

## DB Writes
• orders (entry_status, exit_status, closed_at)
• realized_pnl
• net_pl

## Invariants
• Only router interacts with Betfair
• Only router marks child matched
• Parent P&L finalized via _finalize_parent_on_hedge

