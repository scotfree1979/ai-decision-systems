# 02 — BACKGROUND THREADS TRACE

## Threads Started by start_live_loop

1. StorageHousekeeper (WAL/SHM cleanup)
2. Settlement loop (start_settlement_loop)
3. Placement worker (decide_once/placement)
4. Router child worker (live_router)
5. Rehedge loop (_start_rehedge_loop)
6. Brain listener
7. Context observer v7
8. Feedback assimilator
9. ExposureGuardian
10. Execution surface loop
11. BUSLoop

## Thread Ownership
• Placement worker consumes queued placement instructions
• Router child worker polls Betfair and updates order status
• Execution surface loop updates market surfaces
• ExposureGuardian monitors exposure

## DB Tables Touched by Threads
• orders
• odds_current / surface tables
• pnl_trades
• mastery_events
• markets_schedule

## Invariants
• Placement worker must run before router child worker
• Execution surface must update before BUS tick reads state
• Settlement loop runs independently of BUS timing

