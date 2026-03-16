# AutoScalp Doctrine — World‑Driven Execution Model (v1)

## Purpose

This doctrine defines the **new core execution model** for AutoScalp. It replaces route‑driven evaluation with a **world‑driven evaluation loop** while keeping BUS as the cadence driver.

The rule is simple:

> **Every tick, every runner in the world is evaluated.**

Nothing is pruned, windowed, or slid out of scope.

The only exclusion rule is:

> **If a runner has no PX (price), it is ignored.**

Everything else remains in the system for the entire trading day.

---

# 1. Core Principle — The World

The **World** is the complete set of runners for the trading day.

It is built once at startup and persists for the entire session.

World contents:

- All markets scheduled for the day
- All runners in those markets
- All context fields required by engines

The world **never shrinks or slides**.

Markets are not removed. Runners are not removed.

Instead:

- Finished markets naturally stop producing PX
- Engines simply ignore runners without PX

This removes the need for pruning logic.

---

# 2. PX Rule (Global Invariant)

The **only gating condition** for evaluation is PX.

Rule:

```
if ctx.px is None:
    skip
```

Meaning:

- Markets with no prices are effectively finished
- Runners without PX are ignored
- No structural world mutation occurs

This keeps the execution loop deterministic and safe.

---

# 3. BUS Responsibilities

BUS is **only the cadence driver**.

BUS does **not build the world**. BUS does **not prune the world**. BUS does **not manage evaluation scope**.

BUS responsibilities are strictly:

1. Maintain execution cadence
2. Refresh dynamic fields (PX updates)
3. Call engines
4. Route plans

Execution loop:

```
Tick
  → refresh PX
  → send world to engines
  → collect plans
  → route plans
```

BUS never decides which runners exist.

---

# 4. BusRoute Responsibilities

BusRoute provides **world construction and context surfaces**.

Responsibilities:

- Build route snapshot (initial identity map)
- Build CTX for runners
- Refresh dynamic runner fields

BusRoute no longer controls evaluation scope.

It only supplies the **world state**.

---

# 5. Engine Input Contract

Engines receive the **entire world** every tick.

Input surface:

```
_route_ctx_map
```

Which contains:

```
(marketId, selectionId) → ctx
```

Engines decide internally:

- which runners matter
- which signals fire
- which plans emit

The BUS does not filter runners for engines.

---

# 6. Engine Evaluation Loop

Every engine operates on the same structure.

Example model:

```
for (mid, sid), ctx in world:

    if ctx.px is None:
        continue

    evaluate signals

    if trade condition:
        emit plan
```

The engine decides what is relevant.

---

# 7. Why This Model Exists

The previous system used:

- route pruning
- window slicing
- market rotation

These mechanisms introduced several problems:

1. Missing runners
2. Engines receiving incomplete context
3. BUS stalls when routes break
4. Complex synchronization bugs

The world‑driven model eliminates these entirely.

Advantages:

- deterministic execution
- identical world view for all engines
- simpler architecture
- zero pruning bugs

---

# 8. Market Lifecycle

Markets progress naturally through the world.

Lifecycle:

```
PRE → LIVE → COMPLETE
```

However, the world does **not change**.

Instead:

- PX disappears when a market finishes
- engines stop evaluating those runners

Thus the world remains stable.

---

# 9. Performance Model

The world model is safe because evaluation is lightweight.

Typical scale:

```
~200 runners
2 ticks per second
```

Which results in:

```
~400 evaluations per second
```

This is trivial computational load.

The complexity removed from pruning logic is worth the trade‑off.

---

# 10. Architectural Invariants

These rules must never be violated:

1. BUS does not construct CTX
2. BUS does not prune runners
3. BUS sends the full world to engines
4. Engines ignore runners with no PX
5. World persists for the entire trading day

If these invariants hold, the system cannot stall.

---

# 11. Engine Structure Going Forward

The system now has **two primary engines**:

1. Unified Engine
2. Blueprint Engine

Both operate on the same world surface.

Next stage of architecture will introduce a **third engine** designed to operate within this world‑driven model.

Design discussion for that engine will occur separately.

---

# Final Rule

The most important rule of the system is now:

> **The world never changes. Only PX does.**

Everything else is derived from that truth.

