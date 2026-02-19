# 12 — PLACEMENT WORKER TRACE

## File
engines/decision_engine/decide_once/placement.py

## Entry
start_placement_worker(run_id)

## Responsibilities
• Consume queued plans
• Insert parent order rows (queue_order)
• Attach strategy letter to orders.source
• Call place_companion_hedge()
• Call set_order_status()

## DB Writes
• orders (parent rows)
• decisions table

## Invariants
• Does not talk to Betfair
• Delegates execution to live_router
• Enforces per-runner cap via _can_open_scalp

