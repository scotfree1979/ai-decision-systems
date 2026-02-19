# 06 — MASTERY CONTEXT BUILDER TRACE

## File
engines/mastery/context_builder.py

## Entry
build_context(source)

## Mode Handling
• LIVE
• TEST
• LEARNING override

## Context Fields Populated
• marketId
• selectionId
• odds / ltp
• slope_ppm
• oc_momentum_ticks
• bias, bias_dir, bias_conf
• bank
• used_exposure
• legacy_parent linkage

## DB Reads
• inbound_oc_cache
• markets_schedule
• BankState

## DB Writes
None

## Invariants
• Always returns (ctx, meta)
• ctx fields may be None unless normalized
• Parent-driven override applies only if legacy parents exist

