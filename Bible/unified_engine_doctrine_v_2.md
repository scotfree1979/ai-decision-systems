# UNIFIED ENGINE DOCTRINE v2
## (SR2 Architectural Lock — Visibility-First Spine Model)

---

# STATUS: ARCHITECTURE LOCKED

This document formalises the Unified Engine design following SR2 analysis.

No execution logic is defined here.
No activation logic is defined here.
No thresholds are finalised here.

This doctrine defines:
- What the Unified Engine IS
- What it SEES
- How it REPORTS
- How it CONNECTS to BUS
- How TIME is controlled
- What MUST remain invariant

After this document, architecture discussion is closed.
SR3 moves into controlled implementation only.

---

# 1. PURPOSE

The Unified Engine is the autonomous decision spine of the system.

It:
- Runs on every BUS tick
- Maintains a full-day world model
- Builds CTX for all runners
- Reads all authoritative surfaces
- Classifies runners into structured buckets
- Returns plans OR structured narrative every tick

It does NOT:
- Depend on window logic
- Depend on Betfair inPlay flags
- Mutate DB state directly
- Rely on legacy engines

BUS calls it.
Unified Engine responds.

---

# 2. LIFECYCLE STATES

Unified Engine operates under four internal states:

INITIALISING
BUILDING_CTX
CLASSIFICATION_ONLY
ACTIVE

SR2 / Phase 0 operates strictly in CLASSIFICATION_ONLY.

No plans fire in this phase.

---

# 3. WORLD MODEL

Unified Engine owns a full-day world.

It enumerates ALL markets from BETS for the current UTC day.

Markets are never dropped.
Markets transition through states:

PRE_COUNTDOWN
POST_ZERO_WAITING
OFF_DETECTED
LIVE_RUNNING
COMPLETE
ARCHIVED

Markets remain in the world after COMPLETE.
They are only rotated out of the "Next Five" timing panel.

---

# 4. PHASE AUTHORITY (VOLATILITY-DRIVEN TIME)

Time is NOT controlled by:
- Betfair inPlay flag
- OC stage
- External schedule triggers

Time IS controlled by:
- BETS.marketStartTime (for countdown only)
- PX volatility impulse
- Structural collapse signature

## Phase Rules

If minutes_to_off > 5 → PRE
If minutes_to_off ≤ 5 → LIVE_PHASE

OFF is detected when:
- ≥ 3 runners move ≥ 1 tick within short window

COMPLETE is detected when:
- Multiple runners drift to ~1000
- Volatility collapses
- Structural movement ceases

Time is market-defined, not flag-defined.

---

# 5. SURFACE STACK (AUTHORITATIVE VISIBILITY)

Unified Engine reads the following surfaces:

1. MarketMonitor (BETS schedule)
2. Direction Engine surface
3. Match Surface (execution truth)
4. Liability Surface (worst-case simulation)
5. Capital Surface (BankState snapshots)
6. Stop Surface (SLEQ + trail state)
7. CTX Surface (context_builder)

No DayRunner surface.
No Betfair inPlay reliance.

---

# 6. VISIBILITY REPORT STRUCTURE (PHASE 0)

Each tick returns:

- Phase Summary
- Drift Surface
- Rank/Crossover Surface
- Sweet Spot Surface
- Volatility Surface
- Liability Surface
- Capital Surface
- Stop Surface
- Classification Buckets
- Suppression Counters
- Plans (empty in Phase 0)

Engine never returns silence.

---

# 7. CLASSIFICATION MODEL

Every runner belongs to one of:

PRIMED
BUILDING
CONTEXT_ACTIVE
INACTIVE

Classification is independent from activation.

Activation logic is introduced only after Phase 0 validation.

---

# 8. TIMING PANEL (VALIDATION INSTRUMENT)

The Unified Engine must output a Timing Surface:

- Next five markets by schedule
- Countdown to zero
- Post-zero count-up
- OFF detection (turn green)
- LIVE duration counter
- COMPLETE detection (turn red)
- Persist:
  - Scheduled off time
  - Volatility-off time
  - Collapse time
  - Actual race duration

This validates:
- Schedule correctness
- Volatility impulse detection
- Completion detection
- Timing accuracy

---

# 9. BUS CONTRACT

Every BUS tick:

response = UnifiedEngine.tick()

Response includes:
- plans (possibly empty)
- buckets
- suppression summary
- surface health
- lifecycle state

BUS is never blind.

---

# 10. INVARIANTS

1. Unified Engine ticks every BUS tick.
2. It never returns None.
3. It never mutates DB directly.
4. It fails open.
5. Markets never silently disappear.
6. Phase is volatility-driven.
7. Classification is separate from activation.
8. Phase 0 emits zero plans.
9. All surfaces must be visible simultaneously.

---

# 11. STRATEGIC ROADMAP

Phase 0 — Visibility Only
Phase 1 — Controlled Activation (PRIMED → 1 plan)
Phase 2 — Segment Modulation
Phase 3 — Capital Dominance
Future — 100% budget allocation
Future — Competitive Engine (50/50 split model)

---

# ARCHITECTURE STATUS

All structural decisions are now locked.

SR2 is complete.

Next step:

SR3 — Controlled Build Sequencing

No further architectural redesign permitted without formal revision of this doctrine.

---

END OF DOCTRINE v2

