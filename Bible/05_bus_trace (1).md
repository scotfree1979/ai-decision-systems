# 05 — BUS TRACE

## File
engines/bus/bus.py

## Entry
BUS.run_live(hz)

## Tick Loop
while True:
  build_bus_route_tick()
  build_context()
  mastery_policy.plan_for_strategy()
  dynamic_stake_v7.compute_dynamic_stake()
  _apply_bus_stake_gate()
  place_from_bus()

## Dependencies
• build_full_cycle / build_bus_route_tick (engines/bus_route)
• build_context (engines/mastery/context_builder)
• dynamic_stake_v7
• place_from_bus (live_router)

## DB Reads
• engine availability via BankState
• route market list

## DB Writes
None directly (delegates to placement/router)

## Invariants
• Stake bounded by ENGINE_MIN / ENGINE_MAX
• Route split based on ROUTE_SPLIT
• Does not directly modify exposure

## Observed Gap
• No per-runner exposure cap enforced in BUS (no such logic present in file)

