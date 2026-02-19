# 03 — EXECUTION SURFACE TRACE

## File
(Started via start_execution_surface_loop in tools.betfair_match_surface)

## Entry
start_execution_surface_loop(period_s=2.0)

## Thread Context
Daemon thread started in orchestrator before BUSLoop

## Responsibilities (Observed)
• Poll Betfair match/execution data
• Update AUTO_DB surface tables
• Provide live price + execution truth

## DB Tables Observed
• odds_current (ltp, back1, lay1, updated_ts)
• inbound_oc_cache (read fallback)

## Consumers
• MarketMonitor.refresh()
• Mastery context builder (price acquisition)
• Router hedge evaluation

## Invariants
• updated_ts must refresh at ~2s interval
• Surface must be non-null before BUS decision
• No direct writes to orders table from surface loop

