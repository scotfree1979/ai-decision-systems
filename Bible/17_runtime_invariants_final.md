# 17 — RUNTIME INVARIANTS (FINAL)

## Execution Authority
• Only BUS initiates trade decisions
• Only Placement Worker inserts parent rows
• Only Live Router interacts with Betfair
• Only _finalize_parent_on_hedge computes realized PnL
• Only Settlement loop writes pnl_daily

## Exposure Authority
• BankState reserves exposure
• ExposureGuardian monitors exposure
• No exposure logic inside MSC engines

## Stake Authority
• Mastery policy proposes
• dynamic_stake_v7 computes
• BUS clamps to ENGINE_MIN/MAX

## Classification Authority
• MarketMonitor owns band state
• No classification logic inside BUS

## Surface Authority
• Execution surface loop writes live state
• No direct Betfair calls outside router

## Observed Structural Gaps
• No explicit absolute liability threshold trigger observed in Overwatcher code
• No explicit per-runner global exposure cap observed

SYSTEM FULLY REHYDRATED — READY FOR PATCH PLANNING.

