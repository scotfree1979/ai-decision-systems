# AUTOSCALP FINAL DOCTRINE — v7.9.15

---

## 🔵 CORE PRINCIPLE

The system evaluates the FULL WORLD every tick.

- No pruning
- No slicing
- No filtering (except px=None)

The system is always aware of every runner for the day.

---

## 🌍 WORLD MODEL

- Runners enter when PX appears
- Runners remain until:
  - Market completes
  - PX disappears

This creates a continuously evolving world:

BUILD → FULL → DECAY → EMPTY

---

## 🧠 ENGINE ARCHITECTURE (FINAL)

### MSC_UNIFIED
- Core signal engine
- Handles:
  - Exploratory (>20m)
  - Risk (20m–5m)
  - In-play (≤5m)
  - Structural breakdown
- Owns stoploss

### MSC_BLUEPRINT
- Trades known blueprint patterns

### MSC_CONTEXT
- Discovers new structures

### MSC_STRUCTURE
- Trades only profitable structures

### MSC_META
- Trades best combined signals
- Highest conviction
- Highest stake

---

## 🔁 LEARNING LOOP

CONTEXT → STRUCTURE → META

- Context discovers
- Structure filters by PnL
- Meta exploits best combinations

Fully automatic. No manual promotion.

---

## 💰 CAPITAL SYSTEM

- Profile-based allocation (P1–P5)
- Daily rebalance
- Engine-level pots
- Safety net (5%)

No engine can exceed allocation.

---

## 🎯 STAKE SYSTEM

Dynamic Stake v7:

- 30-point scoring model
- Deterministic
- No bank dependency

Hierarchy:

Meta > Structure > Context > Blueprint > Unified

---

## 🔁 EXECUTION FLOW

WORLD → BUS → ENGINES → PLANS → CADENCE → ROUTER → BETFAIR

BUS responsibilities:

- Routing
- Stake authority
- Cadence control
- No strategy logic

---

## ⏱️ TIMING MODEL

- >20m → Exploratory
- 20m–5m → Risk
- ≤5m → In-play

---

## 🚦 CADENCE SYSTEM

- 60s window
- 3s tick
- 240 plans per tick
- 2400 per window

Ensures controlled throughput.

---

## 🔒 SAFETY SYSTEMS

1. Stoploss (Unified)
2. Lane 6 DB repair
3. Capital gating

---

## 🚫 REMOVED SYSTEMS

- Legacy routing
- Window slicing
- OC dependency
- Manual strategy promotion

---

## ⚠️ KNOWN LIMITATIONS

- Duplicate PX trades allowed
- No per-runner exposure cap
- No throughput optimisation layer

Non-blocking.

---

## 🟢 STATUS

- Architecture complete
- Execution ready
- Learning active

---

## 🎯 FINAL TRUTH

This is no longer a system.

It is a self-evolving trading engine.

---

## 🔥 NEXT STEP

Run.
Observe.
Only fix what breaks.

