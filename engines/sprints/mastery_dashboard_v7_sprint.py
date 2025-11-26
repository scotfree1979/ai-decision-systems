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
    "version": "7.8",
    "current_phase": "Phase 7.8 – Dashboard Intelligence & Brain Integration",
    "phases": {

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
    # PHASE 7.9.5 — EXECUTION ARCHITECTURE (LEGACY + OVERWATCHER)
    # ───────────────────────────────────────────────────────────────
    "Phase 7.9.5 – Brain-Integrated Execution Architecture": {
        "status": "🧭",
        "tasks": [
            "🧠 Lock in unified architecture: Legacy + Overwatcher + MicroScalper + StopLoss + v7 Brain",
            "📚 Finalise contracts: ctx → Plan → PlanLedger → LiveRouter → orders",
            "🔤 Finalise letter / subtype model (A/B/G… and AA/BB/SS overlays)",
            "📌 Define per-runner/per-letter caps and slot model (A1/A2/A3, 3 parents max)",
            "📌 Define per-trade tick-based StopLoss (entry_odds + stop_ticks via price_math)",
            "📌 Define MicroScalper behaviour inside stop corridor (drift/steam ebbs and flows)",
            "📚 Freeze this spec as the reference for all 7.9.5–7.9.8 work"
        ]
    },

    # ───────────────────────────────────────────────────────────────
    # PHASE 7.9.6 — LEGACY DECISION ENGINE & CTX REBUILD
    # ───────────────────────────────────────────────────────────────
    "Phase 7.9.6 – Legacy Decision Engine & CTX v7 Integration": {
        "status": "🧭",
        "tasks": [
            "🧱 Clean up context_builder/context_builder_next to use live schema only",
            "🧱 Ensure ctx includes v7 features (v_mastery_intel_v7, v_timing_features_v7, v_mastery_brain_input)",
            "🧠 Prune/adjust lanes so they only reference real ctx fields and views",
            "📋 Normalise plans: letter, family, direction, px, size, target_ticks, stop_ticks, confidence",
            "📚 Wire mastery_policy to enforce per-letter stake rules and caps (max_open_parents per letter/runner)",
            "📁 Ensure PlanLedger writes complete plan rows (plan_json, plan_why, letter, family, target_ticks…)",
            "📦 Ensure placement.py picks up READY plans and calls place_parent_and_hedge() with _plan/_ctx"
        ]
    },

    # ───────────────────────────────────────────────────────────────
    # PHASE 7.9.7 — OVERWATCHER, EVENTSYNC & MICROSCALPER
    # ───────────────────────────────────────────────────────────────
    "Phase 7.9.7 – Overwatcher, EventSync & MicroScalper": {
        "status": "🧭",
        "tasks": [
            "🧠 Audit Overwatcher event types vs live_router_bridge handlers (type → plan mapping)",
            "📡 Standardise decision event shape: type, marketId, selectionId, side/direction, stake, odds, letter, ts",
            "📶 Wire MicroScalper to drift/steam signals (trend_features, v7_intelligence, microscope_feed)",
            "📉 Implement micro_scalp events: stack/hedge scalps within stop corridor",
            "🔤 Ensure MicroScalper emits correct letters/subtypes (AA/BB overlays on base letters)",
            "📡 Ensure Overwatcher Guardian events (loss_cut_signal, greenup, mlm_cap_hit) are cleanly separated from per-trade StopLoss",
            "📚 Wire EventSync so actionable events always hit live_router_bridge.handle_mastery_event()"
        ]
    },

    # ───────────────────────────────────────────────────────────────
    # PHASE 7.9.8 — STOPLOSS ENGINE & EXECUTION
    # ───────────────────────────────────────────────────────────────
    "Phase 7.9.8 – Per-Trade StopLoss & Risk Execution": {
        "status": "🧭",
        "tasks": [
            "🧮 Implement per-parent StopLoss loop (ticks_moved_against_entry ≥ stop_ticks via price_math)",
            "📡 Emit stoploss decision events with parent_ref, parent_side, entry_odds, entry_stake, stop_ticks, trigger_ticks",
            "🧠 Map stoploss events through live_router_bridge to place STOPLOSS children via place_parent_and_hedge()",
            "📋 Ensure exit_kind='STOPLOSS' flows through orders, playbooks_settled, training views",
            "⚖️ Ensure MicroScalper operates inside stop corridor while StopLoss is hard boundary",
            "🔍 Confirm separation between per-trade StopLoss and global/budget risk (BudgetManager/Guardian)",
        ]
    },

    # ───────────────────────────────────────────────────────────────
    # PHASE 7.9.9 — DASHBOARD INTELLIGENCE & GUI SYNC (old 7.9.5)
    # ───────────────────────────────────────────────────────────────
    "Phase 7.9.9 – Dashboard Intelligence & GUI Sync": {
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
