# AutoScalp Doctrine — World‑Driven Execution Model (v1)

## Core Architectural Rule

The AutoScalp system operates on a **world‑driven execution model**.

The complete trading world for the day is constructed once and then evaluated continuously.

**Principle:**

> "The world never changes. Only PX does."

This rule defines how data flows through the system and how engines reason about markets.

---

# 1. World Construction

At the beginning of the trading session the system constructs the **full trading world**.

The world contains:

- All markets scheduled for the trading day
- All runners within those markets
- All static attributes (IDs, start times, venue, etc.)

Once built:

- The world **remains fixed for the entire session**
- Markets are **never removed**
- Runners are **never pruned**
- No sliding windows are applied

The system does **not** rebuild or prune the world during the day.

---

# 2. Tick Processing Model

Every BUS tick performs the same action:

1. Refresh live market data
2. Update PX values
3. Send the **entire world snapshot** to the engines

Engines therefore always receive:

```
WORLD[t]
```

Where only **price values (PX)** have changed.

Everything else is structurally identical.

---

# 3. The Only Exclusion Rule

Markets and runners are **never removed** from the world.

The **only exclusion rule** allowed in the system is:

```
PX is None
```

If PX is `None`, the runner cannot be evaluated by engines.

This ensures engines only operate on **tradable data**.

No other pruning or filtering rules exist.

---

# 4. BUS Responsibilities

The BUS is now responsible only for:

### 1. Cadence

Maintaining the system tick rate.

Example:

```
1Hz tick
```

### 2. Data Refresh

Refreshing price information from the exchange.

### 3. World Broadcast

Sending the full world snapshot to engines each tick.

The BUS **does not perform**:

- Market filtering
- Runner pruning
- Strategy logic

The BUS is purely an **orchestration layer**.

---

# 5. BusRoute Responsibilities

BusRoute determines **which engines receive the world**.

Responsibilities:

- Engine scheduling
- Engine routing
- Execution order

BusRoute **does not modify the world**.

It only controls where the world is sent.

---

# 6. Engine Input Contract

Every engine receives the same input structure:

```
EngineInput
{
    world_snapshot
    timestamp
}
```

Key properties:

- Deterministic
- Stateless input
- Complete market visibility

Engines must assume:

```
The entire world is always present.
```

No engine should rely on partial views of the market.

---

# 7. Market Lifecycle

Markets move through phases during the day but remain present in the world:

```
PRE → OFF → INPLAY → COMPLETE
```

Even after completion the market remains in the world snapshot.

Engines decide whether to act based on phase.

The system **never removes markets**.

---

# 8. Performance Assumptions

The architecture assumes:

- The world size is manageable
- Engines are responsible for filtering internally
- Broadcast is cheaper than rebuild

Benefits:

- Deterministic behaviour
- No hidden filtering
- Simpler debugging

---

# 9. Architectural Invariants

The following rules must never be violated.

### Invariant 1

The world is constructed once per day.

### Invariant 2

The BUS broadcasts the full world every tick.

### Invariant 3

Markets and runners are never removed.

### Invariant 4

The only exclusion rule is:

```
PX is None
```

### Invariant 5

BUS does not contain strategy logic.

### Invariant 6

BusRoute does not mutate the world.

### Invariant 7

Engines must handle filtering internally.

---

# 10. System Doctrine

The governing doctrine of the system is therefore:

> **"The world never changes. Only PX does."**

Everything in the architecture must align with this rule.

If a component attempts to:

- prune markets
- remove runners
- create sliding windows
- partially construct the world

then the system is violating the doctrine.

---

# Final Rule

**AutoScalp operates on a world‑broadcast architecture.**

Every tick distributes the complete world.

Engines observe price changes within that world and decide whether to act.

Nothing else in the architecture modifies the world.

