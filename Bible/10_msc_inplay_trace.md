# 10 — MSC INPLAY TRACE

## File
engines/micro_scalper_v7/inplay_engine.py

## Entry
InPlayEngine.tick(ctx)

## Responsibilities
• In-play specific scalping logic
• Use ctx.phase == IN_PLAY
• Evaluate elapsed time since off

## Inputs
• ctx minutes_to_off (negative when in-play)
• MarketMonitor classification

## DB Interaction
None

## Invariants
• Only active when market in-play
• Does not place orders directly

