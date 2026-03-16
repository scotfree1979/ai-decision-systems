# AutoScalp Engine Evolution Doctrine

## System Stage

Current baseline:

v7.9.14-observe-stable-context-engine-baseline

Branch foundation:

v7.9.15-STABLE

At this point the platform is **stable and operational**. Development is no longer about fixing architecture but about evolving the trading engines themselves.

---

# Core Rule: Engine Count Limit

The system intentionally limits the number of trading engines.

Maximum engines = **5**

Reason:

- Avoid fragmentation
- Maintain capital efficiency
- Prevent signal dilution
- Keep system explainable

Each engine must represent a **distinct trading philosophy**.

---

# Current Engine Set

The platform currently operates with three engines.

1. MSC_UNIFIED
2. MSC_BLUEPRINT
3. MSC_CONTEXT

These represent the first generation of engines.

---

# Engine Roles

## 1 — MSC_UNIFIED

Primary execution engine.

Responsibilities:

- Core exploratory trading
- Risk harvesting
- In-play ladder logic
- Structural collapse detection

Unified is the **core capital deployment engine**.

---

## 2 — MSC_BLUEPRINT

Structural trading engine.

Responsibilities:

- Pattern-driven signals
- Strategy templates
- Known profitable shapes

Blueprint acts as the **strategy template engine**.

---

## 3 — MSC_CONTEXT

Market structure engine.

Responsibilities:

- Race structure classification
- Context learning
- Strategy discovery

Context observes the market and learns which structures produce profit.

---

# Engine Evolution Plan

Two additional engines complete the architecture.

This creates a **5 engine system**.

---

## 4 — MSC_STRATEGY

Strategy execution engine.

Purpose:

Convert discovered context structures into executable strategies.

Example:

ACTIVE + tight_gap + small_field

→ becomes a tradable strategy.

Strategy engine responsibilities:

- implement discovered strategies
- test strategies in live environment
- measure PnL performance

This engine acts as the **strategy testing environment**.

---

## 5 — MSC_PORTFOLIO

Portfolio aggregation engine.

Purpose:

Combine signals from all engines into a unified strategy layer.

Responsibilities:

- capital allocation
- signal blending
- portfolio optimisation

Portfolio becomes the **final execution authority**.

---

# Engine Lifecycle

Once the five-engine architecture exists, the system begins a cycle.

1. Context discovers new structures
2. Strategy engine tests them
3. Portfolio integrates the best ones
4. Weak engines are deprecated

This creates continuous system evolution.

---

# Deprecation Rule

When the fifth engine is added, one of the following engines may be removed:

- MSC_BLUEPRINT
- MSC_CONTEXT
- MSC_STRATEGY

The engine with the weakest PnL or signal value is retired.

The slot is then reused for:

- a new experimental engine
- an upgraded version of an existing engine

The system therefore **never exceeds five engines**.

---

# Permanent Engine Roles

Two engines are permanent.

MSC_UNIFIED
MSC_PORTFOLIO

These represent:

execution + capital management.

The remaining three slots are evolutionary.

---

# Evolutionary Slots

The three dynamic slots are:

1. Discovery
2. Strategy Testing
3. Experimental

Possible occupants include:

- Blueprint
- Context
- Strategy
- New experimental engines

---

# Engine Evolution Cycle

The long-term cycle becomes:

observe → discover → test → integrate → retire

Example cycle:

Context discovers profitable structure.

Strategy engine trades it.

Portfolio integrates it.

Blueprint becomes redundant.

Blueprint slot is reused for new experimentation.

---

# Final Architecture

The mature platform becomes:

MSC_UNIFIED
MSC_PORTFOLIO
MSC_CONTEXT
MSC_STRATEGY
MSC_EXPERIMENTAL

Where the final three evolve continuously.

---

# Development Philosophy

Development now follows this rule:

Do not split engines unnecessarily.

Instead:

- upgrade existing engines
- replace weak engines
- evolve the system slowly

This maintains stability while allowing continuous improvement.

---

# System Identity

AutoScalp is now:

A continuous market observation engine
that evolves trading strategies over time.

The system improves by:

- observing market structure
- discovering profitable environments
- testing strategies
- integrating the best strategies
- retiring weak approaches

This architecture ensures the system remains both stable and adaptive.

