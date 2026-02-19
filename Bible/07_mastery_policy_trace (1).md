# 07 — MASTERY POLICY TRACE

## File
engines/mastery/mastery_policy.py

## Entry
plan_for_strategy(strategy_name, ctx)

## Responsibilities
• Evaluate priors
• Apply bias adjustments
• Determine direction
• Determine target_ticks
• Suggest size (pre dynamic_stake)

## DB Interaction
• Reads mastery_priors
• Writes mastery_events (via record_outcome)

## LEARNING Mode
• Commission applied in _finalize_parent_on_hedge
• Outcome fed back via record_outcome

## Invariants
• Does not place orders
• Does not reserve exposure
• Pure plan generation layer

## Observed Gap
• No explicit exposure-aware sizing inside policy (no BankState reference here)

