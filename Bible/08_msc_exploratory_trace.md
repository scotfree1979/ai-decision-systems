# 08 — MSC EXPLORATORY TRACE

## File
engines/micro_scalper_v7/exploratory_engine.py

## Entry
ExploratoryEngine.tick(ctx)

## Responsibilities
• Evaluate exploratory scalp opportunities
• Read ctx (price, slope, bias, band)
• Produce plan dict (enter, direction, target_ticks, size)

## Inputs
• ctx from mastery context builder
• MarketMonitor band state

## DB Interaction
None (pure plan generation)

## Invariants
• Does not write orders
• Does not reserve exposure
• Delegates stake sizing to dynamic_stake_v7

