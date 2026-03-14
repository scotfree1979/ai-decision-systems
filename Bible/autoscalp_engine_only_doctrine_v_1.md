# AUTOSCALP ENGINE‑ONLY DOCTRINE (FINAL)

## STATUS
The core system architecture is now considered **complete and locked**.

The following system layers are **finalised infrastructure** and must not be modified except to fix a clear runtime defect.

Locked system components:

- BUS
- BUS Route
- Market Monitor
- Context Map (ctx_map)
- Unified Engine signal surfaces
- Snapshot writers
- Router
- Bet placement pipeline
- Dashboard snapshot readers
- Database schemas

These components form the **execution backbone** of AutoScalp.

From this point forward **all development effort moves to trading engines only.**

---

# SYSTEM PRINCIPLE

The system now follows a strict separation of concerns.

## Infrastructure Layer (LOCKED)
Responsible for:

- building the world
- providing runner context
- computing signal surfaces
- writing snapshots
- routing plans
- executing trades

This layer is **stable and complete**.

Changes here are only allowed when:

1. A runtime error occurs
2. A wiring defect is proven
3. A snapshot surface is incorrect

No optimisation or experimentation occurs in infrastructure.

---

# ENGINE LAYER (ACTIVE DEVELOPMENT)

All experimentation and strategy development occurs here.

Engines are responsible for:

- interpreting signals
- generating plans
- exploiting market behaviour

Engines must:

- read ctx_map
- read Unified signal surfaces
- emit plans

Engines must NOT:

- rebuild world state
- query databases directly
- modify router behaviour
- change execution rules

---

# UNIFIED ENGINE ROLE

Unified is now the **signal authority**.

Unified provides:

- Layer1 signal surfaces
- Layer2 candidate ranking
- exploratory anchors
- risk harvesting
- structural in‑play triggers

Unified is **not a strategy engine**.

It is a **signal generator**.

---

# DEVELOPMENT RULE

From this point forward:

> If something is not working the only valid question is:

**"Why is this engine not working?"**

Not:

- "Maybe BUS is wrong"
- "Maybe the router should change"
- "Maybe the dashboard needs rewriting"

Infrastructure is assumed correct unless a runtime failure proves otherwise.

---

# FIVE ENGINE SYSTEM

The final architecture consists of:

1. MSC_UNIFIED
2. MSC_MOMENTUM
3. MSC_BREAKOUT
4. MSC_MEAN_REVERSION
5. MSC_LIQUIDITY

Unified produces signals.

The remaining engines exploit those signals using different trading behaviours.

---

# ENGINE DEVELOPMENT MODEL

Each engine must be:

- isolated
- deterministic
- observable

Each engine must emit:

```
enter
engine
bet_type
role
marketId
selectionId
px
why
```

Engines do not handle stake sizing or routing.

They only generate **plans**.

---

# DASHBOARD CONTRACT

The dashboard reads only:

- unified_runtime_snapshot
- bus_runtime_snapshot
- bankstate_runtime_snapshot

No dashboard panel may compute logic.

The dashboard is **display only**.

---

# COMPLETION CONDITION

The system is considered complete when:

- engines produce plans
- router executes bets
- dashboard surfaces signals and execution

At that point the project enters **engine iteration mode only**.

---

# FINAL RULE

Infrastructure is frozen.

All innovation occurs in engines.

If behaviour changes are required they must be implemented as **new engines**, not infrastructure changes.

---

# AUTOSCALP DEVELOPMENT PHILOSOPHY

Build stable infrastructure once.

Then evolve trading intelligence forever.

Infrastructure is architecture.

Engines are edge.

Edge is where profit is created.

