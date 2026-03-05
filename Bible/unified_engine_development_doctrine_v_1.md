# AutoScalp Platform Doctrine — Post‑SR4 Architecture Lock

## Status
SR4 completed on branch **v7.9.14**.

The platform architecture is now considered **STABLE INFRASTRUCTURE**. Future development will focus exclusively on **engine evolution**, not system rewrites.

---

# 1. Stable System Layer (Frozen Infrastructure)

The following components are **platform invariants** and must not be modified except for critical bug fixes.

## Core Execution
- BUS
- BUS_ROUTE
- ROUTER
- BANKSTATE
- CTX Builder

## Data Layer
- DAL / LiveCache
- bets.db
- autoscalp.db

## Runtime Snapshots
- `bus_runtime_snapshot`
- `router_runtime_snapshot`
- `bankstate_runtime_snapshot`
- `unified_runtime_snapshot`

## Dashboard System
- Execution Intelligence dashboard
- Router report cards
- BankState capital cards
- Live View runner grid
- Next Bus Stop runners
- Sweet Spot runners

## Identity Surface
The identity surface of the system is:

```
(mid, sid)
marketId, selectionId
```

All engines operate on this identity.

## CTX Surface

CTX is built by **BusRouteSnapshot** and is the authoritative execution state.

Important CTX fields include:

- px
- odds
- band
- fav_rank
- anchor_parent_id
- drift_pct
- pos_inplay

The dashboard reads this state **directly from BUS memory**.

---

# 2. Architectural Rule

From this point forward:

> **Infrastructure is frozen. Engines evolve.**

The system layer must remain stable while trading logic iterates.

Allowed changes:

- Engine logic
- Strategy models
- Evaluation algorithms
- Stake sizing
- Runner selection

Forbidden changes unless bug fix:

- BUS lifecycle
- BUS_ROUTE scheduling
- Router lifecycle
- BankState accounting
- Snapshot schema
- CTX structure

---

# 3. Development Model Going Forward

Future work occurs through **versioned engine evolution**.

Example lifecycle:

```
Unified Engine V1   (current baseline)
Unified Engine V2
Unified Engine V3
Unified Engine V4
...
```

Each version must deliver a **measurable improvement**.

---

# 4. Core Improvement Dimensions

Every engine version must improve at least one of the following.

### 1. Tick Speed
Reduce BUS cycle latency.

Methods:
- reduce DB polling
- optimise CTX refresh
- batch odds requests
- memory‑first evaluation

### 2. Decision Quality
Improve entry/exit signals.

Methods:
- better sweet‑spot logic
- drift prediction
- collapse detection

### 3. Capital Efficiency
Improve BankState utilisation.

Methods:
- dynamic stake sizing
- faster capital recycling
- adaptive headroom

### 4. Liquidity Capture
Capture more matched volume.

Methods:
- ladder positioning
- spread detection
- fill‑rate optimisation

### 5. Market Coverage
Expand runner opportunities without degrading signal quality.

Methods:
- adaptive window size
- race prioritisation

---

# 5. Engine Evolution Roadmap

The following roadmap defines the next **10 engine versions**.

## Unified Engine V2 — Tick Speed Optimisation
Goal:
- Increase BUS tick speed.

Improvements:
- reduce CTX refresh overhead
- eliminate unnecessary DB reads
- optimise odds refresh batching

Target:
- **2‑3x faster tick rate**

---

## Unified Engine V3 — Sweet Spot Intelligence
Goal:
- Improve runner ranking around sweet‑spot price bands.

Improvements:
- enhanced delta calculations
- weighted odds proximity
- improved favourite detection

Target:
- **higher signal precision**

---

## Unified Engine V4 — Liquidity‑Aware Entry
Goal:
- Improve fill probability.

Improvements:
- spread width detection
- liquidity band weighting
- queue depth awareness

Target:
- **improved plan routing success**

---

## Unified Engine V5 — Adaptive Stake Model
Goal:
- Improve capital deployment.

Improvements:
- volatility‑scaled stake sizing
- adaptive exposure allocation

Target:
- **higher ROI per trade**

---

## Unified Engine V6 — Drift Prediction Layer
Goal:
- Predict price movement direction.

Improvements:
- drift slope modelling
- momentum filters

Target:
- **better entry timing**

---

## Unified Engine V7 — Market Context Engine
Goal:
- Evaluate runners within full race context.

Improvements:
- favourite pressure
- pack behaviour modelling

Target:
- **better race‑level awareness**

---

## Unified Engine V8 — Execution Efficiency
Goal:
- Improve fill‑rate and reduce cancellations.

Improvements:
- ladder placement logic
- price improvement rules

Target:
- **higher matched ratio**

---

## Unified Engine V9 — Capital Recycling
Goal:
- Reduce idle capital time.

Improvements:
- faster child order resolution
- dynamic headroom adjustments

Target:
- **higher utilisation of BankState pots**

---

## Unified Engine V10 — Cross‑Race Opportunity Engine
Goal:
- Improve multi‑race opportunity selection.

Improvements:
- race prioritisation model
- liquidity scoring

Target:
- **higher trade frequency with maintained quality**

---

## Unified Engine V11 — Performance Convergence
Goal:
- Final optimisation pass.

Improvements:
- micro‑latency reduction
- model tuning

Target:
- **maximum system efficiency**

---

# 6. Measurement Requirement

Every engine version must produce measurable metrics.

Example telemetry fields:

```
plans_generated
plans_routed
plans_matched
fill_rate
capital_utilisation
avg_trade_latency
```

These metrics must be visible in runtime snapshots.

---

# 7. Strategic Principle

The platform now follows this rule:

> **Infrastructure stability enables engine innovation.**

The system layer should remain constant while trading logic improves continuously.

---

# 8. Final Architecture

The final system architecture is:

```
Dashboard
    ↑
Runtime Snapshots
    ↑
BUS
    ↑
BUS_ROUTE
    ↑
CTX Surface
    ↑
Unified Engine Versions
```

Infrastructure remains fixed.

Engines evolve.

---

# 9. Platform Status

AutoScalp is now operating as a **stable trading platform** rather than a prototype system.

Future development will focus entirely on:

- performance
- profitability
- market efficiency
- execution optimisation

not architectural reconstruction.

---

End of Doctrine.

