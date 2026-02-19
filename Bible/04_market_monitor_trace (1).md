# 04 — MARKET MONITOR TRACE

## File
engines/market_monitor/monitor.py

## Key Functions
• refresh(market_ids)
• get_market_state(marketId)

## Responsibility
• Classify runners into bands:
  - ACTIVE
  - PASSIVE
  - EXTENDED
  - IGNORED

## Inputs
• Execution surface prices
• inbound_oc_cache fallback

## Outputs
• In-memory state map: { marketId → runner states }

## DB Interaction
• Reads from odds_current
• No writes to orders

## Consumers
• BUS (via ctx normalization)
• Mastery context builder

## Invariants
• Classification based on odds thresholds
• No direct stake or exposure logic here
• Pure classification authority

