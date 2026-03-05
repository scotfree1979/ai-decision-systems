# 📘 Doctrine: Bus ↔ BusRoute Architectural Contract

## 1️⃣ Purpose

This doctrine defines the strict contract between **Bus (driver)** and **BusRoute (builder)**.

It exists to eliminate ambiguity about:
- Who builds identity
- Who drives lifecycle
- When rebuilds occur
- What state must persist
- What state must refresh

This doctrine is architectural. It is not situational.

---

## 2️⃣ Role Separation

### BusRoute — The Builder
BusRoute is responsible for:
- Selecting eligible markets
- Selecting eligible runners
- Constructing the runner_pool
- Partitioning runner_pool into bus_stops
- Maintaining ctx_map (accumulative, static context)
- Refreshing dynamic price fields

BusRoute must be able to:
> Build a correct route at any time of day when asked.

BusRoute does not:
- Decide when to rebuild
- Manage tick progression
- Manage execution cadence
- Perform lifecycle timing decisions

It builds when called.

---

### Bus — The Driver
Bus is responsible for:
- Tick progression
- Bus stop progression
- Route boundaries
- Calling build_route()
- Replacing active route surfaces
- Driving engines
- Driving execution

Bus does not:
- Construct identity internally
- Modify runner_pool logic
- Re-partition bus stops manually
- Reconstruct route selection logic

Bus asks.
BusRoute builds.
Bus drives what was built.

---

## 3️⃣ Route Lifecycle Invariant

The following invariant must always hold:

```
At every route boundary:
    Bus calls build_route()
    BusRoute constructs new runner_pool
    BusRoute partitions into bus_stops
    Bus replaces identity surfaces
    CTX map persists
```

If this invariant fails, the system will degrade over time.

---

## 4️⃣ CTX Persistence Law

CTX is not Route.

### CTX
- Accumulative
- Expensive to build
- Built once per runner
- Persists across route rebuilds

### Route
- Identity surface only
- Determines which mids/sids are active
- Fully rebuildable
- Time-dependent

Route rebuild must NEVER wipe CTX.
Route rebuild must ALWAYS refresh identity.

---

## 5️⃣ Cold Start Equivalence Rule

Cold start and live rebuild must be equivalent.

If:
- Cold start produces correct markets

Then:
- Live rebuild must produce identical logic when invoked.

There is no separate “live logic.”
There is only build_route().

---

## 6️⃣ Route Replacement Rule

When a new route is built:
- runner_pool must be replaced
- bus_stops must be replaced
- ctx_map must be preserved
- route_id must increment

Old route identity must not leak forward.

---

## 7️⃣ Builder Independence

BusRoute must be:
- Stateless relative to driver timing
- Deterministic when called
- Independent of previous route identity

BusRoute must not rely on:
- Prior runner_pool state
- Prior partition state
- Implicit incremental mutation

Each build must stand alone.

---

## 8️⃣ Driver Responsibility

If rebuild behaviour differs between startup and live execution:
- The builder is correct.
- The driver is incorrect.

Lifecycle transitions are always the driver’s responsibility.

---

## 9️⃣ Architectural Summary

Bus drives time.
BusRoute builds identity.
CTX persists knowledge.
Route defines presence.

The system remains stable only if this separation is preserved.

---

# End of Doctrine

