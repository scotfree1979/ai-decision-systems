# UNIFIED ENGINE DOCTRINE v1

## PURPOSE
Design a single, capital-scalable execution engine that unifies the structural intelligence of:
- LEGACY
- MSC_EXPLORATORY
- MSC_RISK
- MSC_INPLAY

Without rewriting core logic.
Without breaking route invariants.
Without modifying existing engines.

This engine will be introduced as a new BUS lane and capital-allocated gradually.

---

# CORE PRINCIPLES

## 1. No Rewrite Philosophy
The Unified Engine is NOT a replacement rewrite.
It is a composition layer built from:
- Existing surface builders
- Existing engine signal logic
- Existing CTX structures
- Existing PX refresh mechanisms

Existing engines remain untouched.
Unified consumes their logic as components.

---

## 2. Architectural Position

BUS Layer
├── LEGACY
├── MSC_EXPLORATORY
├── MSC_RISK
├── MSC_INPLAY
└── UNIFIED  ← New lane

UNIFIED must:
- Register like any other engine
- Appear in reporting
- Respect pot allocation rules
- Respect BankState invariants
- Never bypass exposure controls

---

## 3. Capital Evolution Model

Phase 1: 20% pot allocation
Phase 2: 40% pot allocation (if performance stable)
Phase 3: 60–80% allocation (if structurally superior)

Legacy engines remain live during evaluation.
This is capital evolution, not engine replacement.

---

# STRUCTURAL COMPONENT EXTRACTION

Unified Engine will be constructed from extracted modules:

## A. Surface Layer
- MarketMonitor band state
- DayRunnerSurface PX map
- V7 in-play snapshot
- Parent anchor bindings
- Volatility & drift measures

## B. Signal Components
- Drift detection
- Collapse / crossover detection
- Anchor bias logic
- In-play quartile logic
- Risk scaling logic

## C. Decision Planner
Resolves:
- Conflicting signals
- Exposure limits
- Engine bias
- Pot constraints
- Market time phase

Planner outputs execution plan.

---

# INVARIANTS

1. Route identity remains separate from Unified logic.
2. CTX build surface remains DB-first.
3. Window logic remains authoritative.
4. No duplicated PX fetch layers.
5. No cross-engine mutation.

Unified reads. It does not mutate global state.

---

# WHAT THIS ENGINE MUST DO

- Trade full market lifecycle (pre-off + in-play)
- Detect structural crossovers
- Lay outgoing strength
- Back incoming strength
- Scale position by volatility
- Adapt bias by anchor + drift
- Continue post-off via CTX grace surface

---

# WHAT WE ARE NOT DOING YET

- No coding
- No engine registration
- No BUS modification
- No pot changes

SR2 is ANALYSIS + DESIGN ONLY.

---

# SR2 OBJECTIVE

Mount project files.
Extract logic from existing engines.
Map all decision components.
Design Unified skeleton.

Only after full architecture clarity do we move to build phase.

---

END OF DOCTRINE v1

