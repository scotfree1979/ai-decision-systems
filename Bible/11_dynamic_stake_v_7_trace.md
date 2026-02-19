# 11 — DYNAMIC STAKE V7 TRACE

## File
engines/math/dynamic_stake_v7.py

## Entry
compute_dynamic_stake(ctx)
calc_dynamic_stake(...)

## Responsibilities
• Compute position size
• Apply volatility or bias modifiers

## Inputs
• ctx (odds, slope, band, bias)
• ENGINE_MIN / ENGINE_MAX

## DB Interaction
None

## Invariants
• Returns float stake
• BUS applies final min/max clamp

