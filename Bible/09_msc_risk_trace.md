# 09 — MSC RISK TRACE

## File
engines/micro_scalper_v7/risk_engine.py

## Entry
RiskEngine.tick(ctx)

## Responsibilities
• Parent-driven logic
• Evaluate legacy_parent linkage
• Decide defensive or scaling actions

## Inputs
• ctx with legacy_parent fields
• get_legacy_parent_odds_snapshot()

## DB Interaction
None directly

## Invariants
• Operates only when parent positions exist
• Does not write orders directly

