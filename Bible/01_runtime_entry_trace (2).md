# 01 — RUNTIME ENTRY TRACE

## File
engines/decision_engine/orchestrator.py

## Entry
start_live_loop(*args, **kwargs)

## Call Origin
GUI → orchestrator.start_live_loop()

## Mode Enforcement
_set_current_source_override("LIVE")

## Ordered Startup Sequence (Code Order)
1. atexit shutdown hook (_clean_shutdown_handler)
2. StorageHousekeeper thread
3. enable_live_dal()
4. set_db_paths(mode="live")
5. BudgetManager.init_budget_manager()
6. BankState.init_bank_state()
7. Module reload (writers, router, monitor, etc.)
8. _dal_verify_and_repair_schema()
9. load_blueprints()
10. start_settlement_loop()
11. start_placement_worker()
12. _router_child_recovery_sweep()
13. start_router_child_worker()
14. start_brain_listener()
15. start_context_observer()
16. start_feedback_assimilator()
17. ExposureGuardian.start()
18. start_execution_surface_loop()
19. BUS.run_live(hz) (thread)

## Threads Spawned Here
• StorageHousekeeper
• BUSLoop

## Authority
Orchestrator owns process lifecycle only. Execution authority transfers to BUS.

## DB Writes During Startup
• markets_schedule (via upsert)
• schema repair via _dal_verify_and_repair_schema
• app_kv (blueprints)

## Invariants
• DAL must be LIVE before workers start
• BankState initialised after BudgetManager
• Execution surface starts before BUS thread

