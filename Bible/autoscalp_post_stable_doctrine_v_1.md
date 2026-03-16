# AutoScalp System Doctrine — Post‑Stable Architecture

## Stable Baseline

Stable anchor tag:

v7.9.14-observe-stable-context-engine-baseline-381

This tag represents the first complete operational baseline where:

- BUS → ROUTER → EXECUTION pipeline works end‑to‑end
- Unified + Context engines operate correctly
- No blocking errors remain
- System can run continuously without intervention

This tag is the **permanent rollback anchor**.

Any future regression can revert to this version.

---

# Core System Doctrine

## Continuous World Model

The system now operates on a **continuous world evaluation model**.

Rule:

Every tick → send the entire world

Meaning:

- Every runner evaluated each tick
- No window slicing
- No route pruning
- No bus‑stop gating

Only exclusion rule:

If PX == None → skip evaluation

All other runners remain visible to the engines throughout the race day.

---

# Tick Lifecycle

Each tick performs the following pipeline:

1. BUS builds world snapshot
2. Engines evaluate world
3. Plans emitted
4. Router executes
5. Snapshot persisted
6. Next tick begins

Evaluation occurs across the **entire race lifecycle**.

---

# Engine State

Active engines:

- MSC_CONTEXT
- MSC_UNIFIED
- MSC_BLUEPRINT
- STOPLOSS
- ROUTER
- BUS

Deprecated (kept for compatibility):

- MSC_RISK
- MSC_INPLAY
- MSC_EXPLORATORY
- LEGACY

Unified + Context now perform their responsibilities.

---

# Context Engine Responsibilities

The Context Engine provides three capabilities.

## 1 — Market Structure Classification

Each runner is classified by:

- band
- fav_gap_bucket
- field_bucket
- fav_strength

These represent the structural shape of a race.

---

## 2 — Context Profit Learning

Historical settlements are aggregated by structure.

Example structure:

ACTIVE + tight_gap + small_field

Produces:

average pnl per structure

The system therefore learns **which race structures are profitable**.

---

## 3 — Strategy Discovery

Context structures with sufficient history are ranked by:

- sample size
- average pnl

These structures become candidates for future engines.

This means the platform can now **discover strategies automatically**.

---

# Development Doctrine

Development is no longer debugging.

Three change categories now exist:

## FIX

Used only for defects.

Example:

v7.9.15-fix-router-plan-lock-412

---

## UPGRADE

Enhancement of existing components.

Examples:

- scoring improvements
- signal optimisation
- engine refinements

Example tag:

v7.9.15-upgrade-layer2-confidence-surface-421

---

## IDEA

Experimental research work.

Examples:

- new engines
- experimental signals
- new strategy models

Example:

v7.9.15-idea-context-breakout-engine-430

Idea branches are allowed to fail.

---

# Tag Lifecycle

All development follows:

fix → observe → freeze → stable
upgrade → observe → freeze → stable
idea → observe → freeze → stable

Stable tags follow:

vX.X.X-observe-stable-description-NNN

Only **stable tags** are rollback anchors.

---

# Branch Doctrine

Stable branch foundation:

v7.9.15-STABLE

Rules:

- Stable branch contains only stable code
- Development occurs in feature branches

Example:

v7.9.15-STABLE
    ├ dev-context-engine
    ├ dev-strategy-discovery
    ├ dev-layer2-upgrade
    └ dev-new-engine

Branches merge only after reaching their own stable tag.

---

# Merge Doctrine

A branch may merge into STABLE only when:

1. It has a stable tag
2. System runs without regression
3. Observe cycle confirms stability

---

# System Identity

The platform is now:

A continuous signal evaluation system
with automated strategy discovery
and modular engine evolution.

The system:

- observes the market continuously
- learns profitable contexts
- evolves new engines from discovered structures

---

# Engine Evolution Ladder

Future development follows a structured sequence.

## Engine 1 — Context Engine

Purpose:

Discover profitable race structures.

Status:

Complete.

---

## Engine 2 — Strategy Discovery Engine

Purpose:

Convert profitable contexts into explicit strategies.

Example output:

"ACTIVE + tight_gap + small_field"

→ candidate strategy.

---

## Engine 3 — Strategy Engine

Purpose:

Execute discovered strategies as independent engines.

Each strategy becomes:

MSC_STRATEGY_<structure>

---

## Engine 4 — Portfolio Engine

Purpose:

Allocate capital dynamically across strategy engines.

Responsibilities:

- risk balancing
- engine weighting
- exposure control

---

## Engine 5 — Adaptive Engine

Purpose:

Automatically disable weak strategies and promote strong ones.

Responsibilities:

- monitor strategy pnl
- retire failing engines
- promote profitable engines

---

# Final Platform Vision

The system evolves from:

build → debug → stabilise

into:

observe → discover → evolve

The platform now improves itself by:

- analysing structural contexts
- discovering profitable environments
- generating new engines automatically.

