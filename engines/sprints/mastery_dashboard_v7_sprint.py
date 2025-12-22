#!/usr/bin/env python3
"""
AutoScalp Mastery Dashboard v7.8 — Master Sprint Plan
(Phase 7.8 – Dashboard Intelligence & Brain-Integrated Feedback)
====================================================================================================

All core phases complete (0–6).
Phase 7.7 through 7.8 delivers unified feedback loops, brain coherence integration,
and wiring of live/replay Playbooks into the training + dashboard ecosystem.
"""

SPRINT = {
    "version": "7.9.9.2",
    "current_phase": "Phase 7.9.9.2 – Dashboard Intelligence & GUI Sync",


        "Phase 0 – Foundation": {"status": "✅"},
        "Phase 1 – Wireframes": {"status": "✅"},
        "Phase 2 – Data Wiring": {"status": "✅"},
        "Phase 3 – Intelligence Layer": {"status": "✅"},
        "Phase 4 – Horse Form Learning": {"status": "✅"},
        "Phase 5 – Replay / Consolidation": {"status": "✅"},
        "Phase 6 – Advanced Intelligence": {"status": "✅"},

        # ───────────────────────────────────────────────────────────────
        # PHASE 7 — SYSTEM / CONTROL HUB (7.0 → 7.7 RANGE)
        # ───────────────────────────────────────────────────────────────
        "Phase 7 – System / Control Hub": {
            "status": "✅",
            "tasks": [

                # PHASE 7.1
                "✅ Rebuilt Playbooks pipeline end-to-end (orders → settlements → playbooks_settled)",
                "✅ Verified schema creation for playbooks_settled / diagnostics / insights / learning",
                "✅ Added blueprint segmentation via segment_key (distance / class / surface mapping)",
                "✅ Implemented automatic tagging (PLAYBOOKS_LIVE / PLAYBOOKS_REPLAY / TRAIN / LIVE)",
                "✅ Created v_playbooks_all (unified live + replay source of truth)",
                "✅ Integrated replay variant generator + replay JSON backfill for synthetic learning",
                "✅ Added _ensure_live_view() → v_mastery_live for clean real-time feeds",
                "✅ Confirmed ingestion pipeline into mastery_outcomes_raw with validated column structure",

                # PHASE 7.2
                "✅ Rebuilt mastery_outcomes_raw using unified tagging and source integrity checks",
                "✅ Added schema extensions (confidence, class_band, distance_band, surface, segment_key)",
                "✅ Added automatic PRAGMA-driven schema migration for missing columns",
                "✅ Implemented v_mastery_live for dashboard-grade real-time signals",
                "✅ Added v_playbooks_feed_trace for replay vs live validation",

                # PHASE 7.3
                "✅ Validated v_mastery_training_input across inbound_oc_cache + trends + form + playbooks",
                "✅ Rebuilt v_mastery_intel_v7 with complete auto-healing and schema guard integration",
                "✅ Integrated v7_timing_features_fixed for drift_speed + in-play progress modelling",
                "✅ Ensured cache_mastery_outcomes rebuild stays schema-consistent",
                "✅ Added fallback to mastery_outcomes_raw when intelligent cache is thin (<100 rows)",

                # PHASE 7.4
                "✅ Integrated Forest–River hybrid model with categorical encoding and smoothing",
                "✅ Built 90-day / 25-epoch training pipeline with heartbeat + progress tracking",
                "✅ Added adaptive bucket weighting (v7Q question-driven epochs)",
                "✅ Integrated goal_alignment sample weighting and form-bias modulation",
                "✅ Snapshot pipeline writing → mastery_snapshot_v7.json & model artifacts (.pkl)",
                "✅ Added stable model versioning and metadata tagging",

                # PHASE 7.5
                "✅ Rebuilt goal_adapter with PnL-driven decay + loss penalty model",
                "✅ Added 19,014-row historical H/S/M backfill for trade quality tagging",
                "✅ Added real-time Good/Bad trade summary + alignment proxy output",
                "✅ feedback_scheduler now emits goal_alignment_tick with unified payload",
                "✅ Added persistent brain_history_v7 for extended time-series tracking",

                # PHASE 7.6
                "✅ Added brain_trainer (GLOBAL + MACRO coherence modelling)",
                "✅ Added Δ-coherence calculations and divergence detection",
                "✅ Persisted brain_state_v7 across global/macro buckets with full history",
                "✅ Added brain_core.py entropy + divergence snapshots to brain_prints",
                "✅ Enhanced training output with full hierarchical GLOBAL / MACRO / MICRO breakdowns",
                "✅ Standardised coherence scale + removed duplicate MACRO entries",

                # PHASE 7.7
                "✅ Deployed unified _view auto-validation guard to all dashboard + training views",
                "✅ Verified closed training loop (brain → goal → training → playbooks → back to brain)",
                "✅ Ensured schema consistency across autoscalp_gui.db ↔ mastery_v7.db",
                "✅ Performed full end-to-end validation with coherence ~0.99 / alignment ~0.76",
                "✅ Prepared sprint realignment for Phase 7.7.x → 7.9.x restructuring",

                # PHASE 7.7.1
                "✅ Implemented full v7 dashboard intelligence base (schema-verified metrics)",
                "✅ Added dynamic date filtering (Today / Yesterday / Last 7 Days / Custom)",
                "✅ Integrated v7_timing_features_fixed for drift & in-play timing metrics",
                "✅ Validated historical metrics (P&L / Confidence / Win% / Strategy mix)",
                "⚙️ Built race visualisation scaffold (real-time rendering + scroll state)",
                "⚙️ Designed two-tier display (LIVE metrics + historical replays)",
                "⚙️ Set up dashboard metric card set (P&L, Liability, Confidence, Blueprint Match, GA Index)",
                "⚙️ Added prewiring for training-health visuals (form bias, heatmaps, coherence trend)",
                "⚙️ Deferred final v7Q drivers for after larger live dataset",

                # PHASE 7.7.3
                "✅ Implemented Auto-Heal Schema Guard across LIVE + CACHE databases",
                "✅ Added drift detection between autoscalp_gui.db ↔ autoscalp_gui_cache.db",
                "✅ Added compat views: orders_compat, dashboard_runners_compat, mastery_feedback_compat",
                "✅ Bootstrapped missing critical tables (plan_ledger, markets_schedule, runner_form_canonical)",
                "✅ Restored 36-column orders schema (customer_ref + matched_odds etc.)",
                "✅ Repaired LIVE placement error [PLACE][ERR] customer_ref missing",
                "✅ Added W.A.L. restoration for main DB (100GB lock-free rebuild architecture)",
                "✅ Ensured blueprint builder + Overwatcher boot with no malformed pages",
                "✅ Added automatic cache integrity verification + fallback clone system",
                "✅ Clean end-to-end live loop: anchors → OC timeline → lanes → dashboard → stable training"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.7.2 — BRAIN QUESTIONS & COHERENCE EXPLORATION
        # ───────────────────────────────────────────────────────────────
        "Phase 7.7.2 – Brain Questions & Coherence Exploration": {
            "status": "✅",
            "tasks": [
                "✅ Defined unified brain coherence model (p1_mean + drift deltas)",
                "✅ Connected macro buckets (Bias, Timing, Execution, Market Conditions, Stability)",
                "✅ Added cross-metric diagnostics (brain_global → training_unified)",
                "✅ Persisted '18 Brain Questions' for long-term coherence + decision insight",
                "🧠 These questions now drive coherence detection, drift tracking, training feedback, and dashboard health metrics."
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.3 — LIVE SYSTEM REBUILD & CLOUD–LOCAL UNIFICATION
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.3 – Live System Rebuild & Cloud–Local Unification": {
            "status": "✅",
            "tasks": [
                "✅ Implemented LIVE-mode override in DAL to prevent fallback to SETUP mode",
                "✅ Ensured DAL_MODE flips only at Step 4 (never downgraded mid-run)",
                "✅ Unified semantics: READ → local, WRITE → cloud",
                "✅ Background cloud→local syncing re-established (fast-sync writer)",
                "✅ Deterministic GUI boot (preflight → seeds → anchors → LIVE → timeline)",
                "✅ Eliminated writer contention + cross-DB lock sources",
                "✅ Corrected event_sink goal_alignment persistence + removed stale column logic",
                "✅ Confirmed stable LIVE loop with no schema drift or malformed pages"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.4 — CLOUD DB REPAIR & ZERO-LOSS CLONE SYSTEM
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.4 – Cloud Database Repair & Clone System": {
            "status": "✅",
            "tasks": [
                "✅ Built zero-loss SQLite clone engine (repairs WAL/SHM/corrupt pages safely)",
                "✅ Repaired autoscalp_gui_cache.db, bets_cache.db, settlements_cache.db, mastery_cache.db",
                "✅ Rebuilt all indexes, triggers, and views using canonical definitions",
                "✅ Verified integrity_check on all cloud DBs (0 pages malformed)",
                "📌 Added nightly or emergency repair utility for long-term resilience"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.5 — EXECUTION ARCHITECTURE REBUILD (LEGACY UNCOUPLED)
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.5 – Execution Architecture Rebuild (Legacy Uncoupled)": {
            "status": "✅",
            "tasks": [
                "✅ Fully removed Legacy routing from all live paths — Legacy now ONLY opens the parent order",
                "✅ Installed unified execution chain: CTX v7 → MicroScalper v7 → Overwatcher → LiveRouter → orders",
                "✅ Rebuilt LiveRouter to be MSC-aware (parent-first, child-safe, SLEQ + TSL compatible)",
                "✅ Removed all legacy stop-loss code paths — router now listens purely to TSL + RiskEngine signals",
                "✅ Normalised execution inputs across engines (marketId, selectionId, oc_phase, current_price)",
                "✅ Installed new MSC folder structure with complete isolation from legacy exec",
                "✅ Ensured router, Overwatcher, MicroScalper and TSL all use the same modern ctx schema",
                "✅ Established architecture foundation for remaining 7.9.x phases (router stability + ctx unification)"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.6 — CTX v7 REBUILD + LANES NORMALISATION
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.6 – CTX v7 Rebuild + Lanes Normalisation": {
            "status": "✅",
            "tasks": [
                "✅ Rebuilt CTX layer to use authoritative schema-only data: odds_current, inbound_oc_cache, oc_series, band state",
                "✅ Added v7 intelligence fields: slope_ppm, drift_speed, momentum_class, bias_conf, blueprint stats, form",
                "✅ Implemented OC-phase model (OC0–OC7 pre-off, OC7+ in-play) with consistent interpretation across engines",
                "✅ Completely cleaned Lanes: removed deprecated ctx fields, wrong lookups, stale market_data references",
                "✅ Normalised plan schema: family, subtype, direction, px, size, target_ticks, stop_ticks, msc multipliers",
                "✅ Integrated MasteryPolicy for confidence, sizing, behavioural constraints, and engine gating",
                "✅ Rebuilt PlanLedger to store plan_json, plan_why, confidence vectors, and MSC metadata",
                "✅ Installed strict price resolvers: odds_current → inbound_oc_cache → oc_series → bets",
                "✅ Ensured Lanes no longer hit live API anywhere — pure database-driven decision flow",
                "✅ Verified Lanes run-through with live OC timeline, monitor bands, and MSC ctx injection"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.7 — OVERWATCHER v7, EVENT SINK v7 & MICROSCALPER v7
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.7 – Overwatcher v7, EventSink v7 & MicroScalper v7": {
            "status": "✅",
            "tasks": [
                "✅ Rebuilt Overwatcher as full guardian layer (microstructure, WOM, volatility, range tracking, cooling windows)",
                "✅ Added Overwatcher → LiveRouterBridge → hedge/close/stoploss relays (drop-in replacements for legacy paths)",
                "✅ EventSink v7 fully rewritten with WAL-safe async writer, queueing, mastery_v7.db routing, and caching",
                "✅ Integrated BrainPulse system: Overwatcher → MicroScalper brain_coherence injection",
                "✅ Installed Trailing Stop-Loss (TSL) bridge: TSL events trigger immediate RiskEngine cleanup",
                "✅ Integrated MicroScalper v7: Exploratory Engine A, Risk Engine B per parent, In-Play Engine C",
                "✅ MSC ctx injection: sleq, volatility_state, micro_opportunity, drift/steam direction, bias, win_prob",
                "✅ Full MSC multiplier model + SLEQ multiplier + direction engine (AGG/MOD/CON, ticks/stop_ticks)",
                "✅ Added In-Play MSC Engine (Engine C) with collapse detection, safe liability limits, canonical pnl use",
                "✅ Upgraded price_math: full Betfair ladder (353 ticks), tick-diff, walk_ticks(), accurate snap_to_tick()",
                "✅ Verified MSC outputs route cleanly into LiveRouter and Orders with correct family/subtype",
                "✅ Installed complete diagnostic MSC engine for non-trading telemetry and router debugging"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.7.1 — ROUTER, LANES, SETTLEMENTS & CANDIDATES REPAIR
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.7.1 – Router, Lanes, Settlements & Candidates Repair": {
            "status": "✅",
            "tasks": [
                "✅ Fixed LiveRouter startup ordering so no thread starves or blocks Step-4 initialisation",
                "✅ Corrected Lanes → Candidates integration (Candidates rebuilt entirely to be API-free)",
                "✅ Removed all hidden live API fallbacks in Candidates and replaced with pure DB chain",
                "✅ Rebuilt Candidates priority: in_play → near20 → near60 → next5 using scope buckets",
                "✅ Added Candidates source tagging: [PRICE:odds_current], [PRICE:inbound], [PRICE:oc_series], [PRICE:bets]",
                "✅ Eliminated rogue fallback paths in Candidates that triggered fetch_live_odds or old patches",
                "✅ Repaired Settlements infinite-loop lock by isolating connections and decoupling DAL",
                "✅ Reintegrated credential reload path using safe DB lookups (no stale env paths)",
                "✅ Repaired Step-4 warm-up → LiveLoop ordering (monitor refresh, scope preload, OC-timeline prep)",
                "✅ Removed all cloud-attach issues and direct sqlite3 misuse (dedicated connectors only)",
                "✅ Verified absolutely no engine calls Betfair API except the explicit Feeder/Step-1",
                "✅ Validated end-to-end: MSC → Lanes → Candidates → LiveRouter → Orders with no fallback noise"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.8 — DYNAMIC TRAILING STOP-LOSS ENGINE (TSL) v1.0
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.8 – Dynamic Trailing Stop-Loss (TSL) Engine v1.0": {
            "status": "✅",
            "tasks": [
                "✅ Installed unified TSL Engine (trailing, boundary, recovery, SLEQ-widened stops)",
                "✅ Added TSL classification for Mastery training (trailing_positive, trailing_negative)",
                "✅ Implemented real SLEQ reinforcement learning (positive stops strengthen, negative stops weaken)",
                "✅ Integrated TSL into Overwatcher: boundary, reversal & pullback exits detected in real-time",
                "✅ Integrated TSL into RiskEngine: TSL hits cleanly detach parent-specific MSC engines",
                "✅ Installed TSL → Overwatcher → LiveRouter → orders bridge (realized_pnl, exit_kind)",
                "✅ Removed every legacy stop-loss path from router, Overwatcher, and Candidates",
                "✅ Verified SLEQ flows through ctx → RiskEngine → multiplier → plan → execution",
                "✅ Completed scenario suite: boundary-low, boundary-high, favourable-trend, pullback-reversal"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.8.1 — FOREST–RIVER LIVE SYNC & REINTEGRATION (ARCHIVED)
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.8.1 – Forest–River Live Sync & Reintegration": {
            "status": "❌ ARCHIVED",
            "tasks": [
                "➡ Tasks migrated to Phase 7.9.9.1"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.8.2 — LIVE MASTERY → CTX v7 INTEGRATION (ARCHIVED)
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.8.2 – Live Mastery Integration Into CTX v7": {
            "status": "❌ ARCHIVED",
            "tasks": [
                "➡ Tasks migrated to Phase 7.9.9.1"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.8.3 — FULL MICROSTRUCTURE → MSC (RENAMED → 7.9.9) (ARCHIVED)
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.9 – Full Microstructure Integration for MSC v7 (Renamed from 7.9.8.3)": {
            "status": "❌ ARCHIVED",
            "tasks": [
                "➡ Tasks migrated to Phase 7.9.9.1"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.9.1 — POST-LIVE VALIDATION & CONTINUATION (v7.2 GATE)
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.9.1 – Post-Live Validation & Continuation (v7.2 Gate)": {
            "status": "✅",

            "tasks": [

                # ================================================================
                # MIGRATED INCOMPLETE TASKS FROM 7.9.8.1 — FOREST–RIVER
                # ================================================================
                "⬜ Ensure River (LIVE) events persist cleanly from Overwatcher and MSC",
                "    → Verify STOPLOSS events (S-child creation) appear in river_bucket_state",
                "    → Validate Overwatcher → EventSink → River ingestion without dropped or duplicated rows",
                "    → Confirm DAL Writer (dual-write) preserves row order deterministically",

                "⬜ Fix Forest→River merge path for next-day training consistency",
                "    → Validate mastery_outcomes_raw rolling windows after live ingestion",
                "    → Confirm CTX v7 fields align with Forest feature expectations",
                "    → Ensure nightly consolidation merges RIVER slices with historical data correctly",

                "⬜ Guarantee schema alignment across mastery_outcomes_raw, river_bucket_state, mastery_posteriors",
                "    → Check column parity after STOPLOSS, H-child cancellation, and parent exit_kind stamping",
                "    → Ensure schema guards detect drift between mastery DBs and cache DBs",
                "    → Validate auto-heal logic handles Forest/River schema divergence",

                "⬜ Add live reinforcement (on_settlement_event) reliability checks",
                "    → settlement_writer must fire consistently now DAL Writer is repaired",
                "    → Validate that realized_pnl + net_pl flows into mastery_outcomes_raw",
                "    → Confirm reinforcement deltas feed correctly into mastery_posteriors",

                "⬜ Ensure nightly Forest run combines River shifts with historical windows",
                "    → Validate v7.1 training inputs after refactor",
                "    → Ensure no missing slices due to cloud-writer corruption (now fixed)",
                "    → Confirm stable consolidation of RIVER deltas into Forest epoch windows",

                "⬜ Add checksum validation for mid/sid/betId to prevent partial sync failures",
                "    → Ensure LiveRouter now stamps bet_id for every parent and child",
                "    → Add optional sync checksum before nightly Forest run",
                "    → Detect partial STOPLOSS ingestion or broken River rows",

                # ================================================================
                # MIGRATED INCOMPLETE TASKS FROM 7.9.8.2 — MASTERY → CTX
                # ================================================================
                "⬜ Inject mastery-smoothed win_prob into ctx",
                "    → CTXv7 now exposes win_prob placeholder field with numeric hardening",
                "    → Inject smoothed mastery deltas after live tests",

                "⬜ Inject brain_global and macro bucket deltas into ctx",
                "    → Validate MasteryState export for GLOBAL/MACRO coherence",
                "    → Add mapping layer for ctx injection",

                "⬜ Derive cluster features from v_mastery_intel_v7 for MSC direction and mode",
                "    → Validate intel stability after LiveCache fix",
                "    → MSC engines must read intel consistently before versioning",

                "⬜ Add mastery deltas into MSC multiplier (confidence & volatility balancing)",
                "    → Confirm multiplier engine receives mastery-feedback deltas",
                "    → Ensure deltas modulate aggression safely (not over-leveraging)",

                "⬜ Ensure ctx captures mastery feedback for training loops",
                "    → Validate STOPLOSS updates modify ctx history",
                "    → Confirm ctx → Mastery pipeline passes deltas unmodified",

                # ================================================================
                # MIGRATED INCOMPLETE TASKS FROM 7.9.8.3 — MICROSTRUCTURE → MSC
                # ================================================================
                "⬜ Add full MarketMonitor microstructure fields into ctx",
                "    → Integrate breakout, range, momentum-class, micro-variance fields",
                "    → Confirm stable market monitor snapshots",

                "⬜ Add volatility clusters, micro-zones, WOM-derived states, acceleration curves",
                "    → Use Overwatcher diagnostic feed to populate advanced microstructure",
                "    → Integrate into ctx for MSC direction decisions",

                "⬜ Feed OC-timeline deltas into ctx",
                "    → Validate oc_series correctness after DAL rewrite",
                "    → Ensure no missing OC deltas from cloud corruption",

                "⬜ Integrate microstructure into direction_engine and multiplier logic",
                "    → Confirm MSC direction stability under microstructure influence",
                "    → Test EX/RISK/IP responses to micro-zones and volatility clusters",

                "⬜ Build MSC telemetry and diagnostics for tracking microstructure performance",
                "    → Expand Overwatcher diagnostic engine output",
                "    → Add visualization-friendly MSC telemetry hooks",

                # ================================================================
                # FULL DOCUMENTATION OF COMPLETED v7.1 ARCHITECTURE REBUILD
                # (Moved here so Phase 7.9.9.1 becomes the canonical v7.1 record)
                # ================================================================
                "✅ Fixed LiveCache corruption (root cause of entire v7 system instability)",
                "    → Identified cloud DB as single-point-of-failure",
                "    → Rewrote DAL Writer to dual-write (local + cloud)",
                "    → Ensured real-time WAL sync ordering and crash safety",
                "    → Eliminated all silent write failures",

                "✅ Rebuilt LiveRouter with STOPLOSS-aware execution chain",
                "    → Added S-child creation with immediate flattening",
                "    → Added SL_Cancel_H to cancel hedge children on STOPLOSS",
                "    → Added parent exit_kind propagation + pnl stamping",
                "    → Ensured router no longer hits stale cloud paths",

                "✅ Rebuilt Lanes routing (v7 canonical engine model)",
                "    → Removed legacy ORDER loop entirely",
                "    → Normalised plan schema (engine, strategy, direction, px, size)",
                "    → Ensured Lanes never infers engine identity from letter",
                "    → Added MSC EX/RISK/IP independence and correct routing",
                "    → Ensured Overwatcher plans route via v7 plan normalisation",

                "✅ Rebuilt CTX v7 with authoritative, DB-only data",
                "    → odds_current → inbound_oc_cache → oc_series → bets ordering",
                "    → Hard numeric defaults + NaN protection",
                "    → Added OC-phase model (OC0–OC7 pre-off, OC7+ in-play)",
                "    → Added volatility_state, drift_speed, slope_ppm, bias metrics",

                "✅ Rebuilt MicroScalper v7 (EX, RISK, INPLAY)",
                "    → MSC Exploratory inherits Legacy letter",
                "    → MSC Risk uses J, MSC InPlay uses V",
                "    → Added unified multiplier (AGG/MOD/CON, SLEQ-aware)",
                "    → Ensured RiskEngine detaches on STOPLOSS",
                "    → Ensured EX/RISK/IP engines never collide or suppress each other",

                "✅ Rebuilt Overwatcher v7 (guardian layer)",
                "    → Live microstructure monitoring",
                "    → Range tracking + WOM signals",
                "    → cooling windows, volatility detection",
                "    → Integrated TSL engine",
                "    → Bridge: Overwatcher → LiveRouter STOPLOSS",

                "✅ Repaired Settlements pipeline",
                "    → SettlementWriter isolated from cloud corruption",
                "    → DAL Writer integration ensures clean writes",
                "    → PnL correctness restored",
                "    → STOPLOSS exits now settle exactly once",

                "✅ Repaired BankState + BudgetManager",
                "    → Ensured pot mutation correctness post-STOPLOSS",
                "    → Verified engine pots reflect live exposure",
                "    → Ensured BankState no longer reads stale cloud DB rows",

                "✅ Ensured end-to-end LIVE execution path is clean",
                "    → anchors → OC timeline → CTX v7 → Overwatcher → MSC → Lanes → Router → Orders",
                "    → ZERO API fallback calls anywhere in execution path",

                "✅ Fixed MSC direction contract (root-cause execution blocker)",
                "    → Identified missing / invalid direction as cause of zero MSC exposure",
                "    → Enforced direction_engine as single source of truth",
                "    → EX / RISK / INPLAY plans now emit valid BACK->LAY / LAY->BACK",

                "✅ Repaired Placement execution ownership",
                "    → Relocated execution worker to placement.py",
                "    → Enforced non-blocking BUS enqueue",
                "    → Stabilised execution thread lifecycle",

                "✅ Added definitive live execution pipeline verifier",
                "    → scripts/verify_live_execution_pipeline.py",
                "    → Hard PASS/FAIL validation of BUS → Placement → Router → Orders",
                "    → Direction contract validated against live schema",

                "✅ Verified end-to-end MSC execution path",
                "    → MSC plans fire, route, and preclaim correctly",
                "    → Zero exposure confirmed as budget-gated, not logic failure",


                # ================================================================
                # NEXT-STAGE REQUIREMENTS BEFORE v7.2
                # ================================================================
                "⬜ Live STOPLOSS test suite (parent → S-child → settle → pot mutation)",
                "⬜ End-to-end settlements replay test",
                "⬜ River→Forest data consistency test",
                "⬜ Mastery feedback-injection test (win_prob, deltas)",
                "⬜ Microstructure-on executions stability test",
                "⬜ Dashboard integration readiness check",

                "🏁 Once all tasks above are complete → branch 7.9.9.1 closes and 7.9.9.2 becomes active"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.9.2 — DASHBOARD INTELLIGENCE & GUI SYNC (RENAMED FROM 7.9.9)
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.9.2 – Dashboard Intelligence & GUI Sync": {
            "status": "🔜",
            "tasks": [
                "🖥️ Wire dashboard data-sources to live brain/goal metrics (v_mastery_brain_input, v_mastery_brain_global)",
                "🧠 Add Brain Summary panel (Global / Macro / Micro coherence + delta)",
                "🧩 Add Good/Bad Trade metrics tile (% hedged / % stop-loss / unmatched)",
                "📈 Add goal alignment index trend visualisation",
                "🧩 Display model snapshot metadata (version, epochs, alignment index)",
                "🧠 Add Training Health tab (brain_history_v7 time series)",
                "⚙️ Add GUI Control Center automation (Playbooks rebuild, nightly training)",
                "🧾 Auto-log training completion summaries to mastery_state",
                "🧩 Dashboard health tests (KPI refresh, zero blank cards)",
                "🏁 Prepare final matrix for Phase 7.9.10"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 7.9.10 — FINAL VALIDATION & RELEASE PREP (old 7.9.6)
        # ───────────────────────────────────────────────────────────────
        "Phase 7.9.10 – Final Validation & Release Prep": {
            "status": "🔜",
            "tasks": [
                "📊 Full system regression (7.9.3 → 7.9.8 pipeline continuity)",
                "📦 Freeze schemas (mastery_outcomes_raw / brain_state_v7 / cache_mastery_outcomes)",
                "🧠 Coherence stability check vs brain_history_v7 (deltas < ±0.02)",
                "🧾 Generate release report + dashboard screenshots",
                "🏁 Tag v7.9.x ‘Stabilised Brain-Integrated AutoScalp’ and merge branch → main"
            ]
        },

        # ───────────────────────────────────────────────────────────────
        # PHASE 8 – PUBLIC RELEASE
        # ───────────────────────────────────────────────────────────────
        "Phase 8 – Validation & Public Release": {
            "status": "🔜",
            "tasks": [
                "Final QA + regression tests",
                "Public release notes & technical documentation",
                "Freeze schema + tag v8.0.0"
            ]
        }
    }

def show_progress():
    print(f"\nAUTO-SCALP MASTERY DASHBOARD v{SPRINT['version']}")
    print("=" * 60)
    for name, data in SPRINT["phases"].items():
        print(f"{name}: {data['status']}")
        if "tasks" in data:
            for t in data["tasks"]:
                print(f"   - {t}")
    print("\nCurrent phase:", SPRINT["current_phase"])


# ──────────────────────────────────────────────────────────────────────────────
# ─── DATA FLOW MAP (AUTO-READABLE FOR SYSTEM SELF-AWARENESS)
# ──────────────────────────────────────────────────────────────────────────────

"""
This section is machine-readable and forms part of the system’s long-term memory.
It describes how AutoScalp’s training, playbooks, and dashboards are connected.
"""

DATA_FLOW_MAP = {
    "live_inputs": ["orders", "odds_current", "inbound_oc_cache", "events"],
    "core_pipelines": [
        {"name": "Playbooks Builder",
         "inputs": ["orders", "decisions", "plan_ledger", "settlements.db"],
         "outputs": ["playbooks_settled", "playbooks_diagnostics",
                     "playbooks_insights", "playbooks_learning"],
         "next": "Mastery Outcomes Raw"},

        {"name": "Mastery Outcomes Raw",
         "inputs": ["playbooks_*", "replay_reports_ingested", "live outcomes"],
         "outputs": ["mastery_outcomes_raw"],
         "tags": ["PLAYBOOKS_LIVE", "PLAYBOOKS_REPLAY", "V7_TRAIN", "LIVE"],
         "next": "Mastery Posteriors"},

        {"name": "Mastery Posteriors",
         "inputs": ["mastery_outcomes_raw"],
         "outputs": ["mastery_posteriors"],
         "next": "Training Sandbox"},

        {"name": "Training Sandbox",
         "inputs": ["v_mastery_v7", "v7_timing_features_fixed",
                    "v_market_sentiment", "trend_features",
                    "runner_form_canonical"],
         "outputs": ["mastery_v7_training"],
         "next": "Model Train"},

        {"name": "Model Train",
         "inputs": ["mastery_v7_training"],
         "outputs": ["models/mastery_v7_policy.pkl",
                     "data/posteriors/mastery_snapshot_v7.json"],
         "next": "Mastery State"},

        {"name": "Mastery State",
         "inputs": ["model metadata", "training metrics"],
         "outputs": ["mastery_state"],
         "next": "Dashboard / Control Center"}
    ],

    "consumers": {
        "Dashboard": ["v_mastery_intel_v7", "v_mastery_live",
                      "v_playbooks_all", "mastery_state"],
        "Control Center": ["build_playbooks()", "train_mastery_v7()", "emit_snapshot()"]
    },

    "last_updated": "2025-10-30Z",
    "version": "7.1-dataflow"
}


# ──────────────────────────────────────────────────────────────────────────────
# ─── FUTURE-PACING NOTES
# ──────────────────────────────────────────────────────────────────────────────

"""
When the system runs a self-audit:
  • Reads SPRINT['phases'] + DATA_FLOW_MAP
  • Compares expected vs actual tables/views/models
  • Outputs a new sprint delta:
        - missing_views
        - schema_drift
        - stale_models
  • Drafts next sprint (v7.2) automatically for human approval.
"""


if __name__ == "__main__":
    show_progress()
