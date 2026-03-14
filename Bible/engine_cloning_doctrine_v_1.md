# ENGINE CLONING DOCTRINE — AutoScalp V7

## Purpose

This doctrine defines the **canonical process for cloning a trading engine** inside the AutoScalp architecture. The goal is to safely create additional independent engines (bots) that share the same infrastructure while maintaining:

- deterministic BUS routing
- capital isolation
- independent signal logic
- reproducible deployment

This process allows the system to scale from **one engine → many coordinated engines** without modifying core BUS behaviour.

---

# Core Principle

An engine clone is **not a modification of an existing engine**.

It is a **new independent engine instance** that:

- has its own file
- has its own engine name
- has its own BUS lane
- has its own capital budget
- has its own runtime context label

The clone inherits logic but **never shares runtime identity**.

---

# Canonical Engine Clone Workflow

## Step 1 — Duplicate the Engine File

Create a copy of the base engine.

Example:

Base engine:

```
engines/micro_scalper_v7/unified_engine.py
```

Clone:

```
engines/micro_scalper_v7/msc_blueprint_engine.py
```

Rules:

- never modify the original engine
- cloned file becomes the **new engine authority**

---

## Step 2 — Rename the Engine Class

Inside the cloned file change the class name.

Example:

```
class BlueprintEngine:
```

And define the new engine identity.

Example:

```
ENGINE_NAME = "MSC_BLUEPRINT"
LANE_ID = 8
```

Each engine must have:

- unique `ENGINE_NAME`
- unique `LANE_ID`

---

## Step 3 — Register Engine in Engine Registry

File:

```
engines/bus/engine_registry.py
```

Add the engine to the registry.

Example:

```
"MSC_BLUEPRINT": BlueprintEngine,
```

This allows BUS to instantiate the engine at runtime.

---

## Step 4 — Add BUS Lane

Inside `DecisionBus` create a new lane handler.

Example:

```
def _lane8_msc_blueprint(self, base_ctx, engine_report):
```

Responsibilities:

- build ctx for engine
- call engine.tick()
- collect plans
- update engine_report

The lane must:

- never mutate execution
- only emit plans

---

## Step 5 — Add Lane Execution Call

Inside `tick()` integrate the lane.

Example:

```
lane8_plans = self._lane8_msc_blueprint(base_ctx, engine_report)

if lane8_plans:
    generated_plans.extend(lane8_plans)
    lane_counts[8] += len(lane8_plans)
```

This connects the engine to the BUS execution cycle.

---

## Step 6 — Add Slot Control

Update slot tracking so the BUS can regulate plan flow.

Example:

```
slot_seen = {
    ...
    "MSC_BLUEPRINT": set(),
}
```

And ensure the lane is included in slot budgets.

---

## Step 7 — Add Budget Allocation

File:

```
engines/live/budget_manager.py
```

Add the engine budget.

Example:

```
"MSC_BLUEPRINT": <capital allocation>
```

This ensures the engine participates in **BankState capital control**.

---

## Step 8 — Add Dashboard Context Label

The engine must expose its own context namespace.

Example:

```
CTX_BLUEPRINT
```

This prevents overlap with:

```
CTX_UNIFIED
```

Each engine must own its own reporting surface.

---

## Step 9 — Add Scoring Logic

After cloning, the engine normally receives **custom scoring logic**.

Examples:

- candidate ranking
- signal weighting
- structural filters

This step differentiates cloned engines.

---

# Engine Clone Checklist

When cloning an engine confirm the following are complete:

| Component | Status |
|--------|--------|
| Engine file duplicated | ✔ |
| Class renamed | ✔ |
| ENGINE_NAME defined | ✔ |
| LANE_ID assigned | ✔ |
| Engine registry updated | ✔ |
| BUS lane added | ✔ |
| Lane added to tick() | ✔ |
| Slot tracking updated | ✔ |
| Budget manager updated | ✔ |
| Context namespace added | ✔ |
| Scoring logic implemented | ✔ |

---

# Architectural Guarantees

Cloning engines under this doctrine guarantees:

- BUS behaviour remains deterministic
- execution routing remains unchanged
- capital governance remains centralised
- engines remain independently deployable

This architecture allows AutoScalp to evolve into a **multi‑engine trading platform** where each engine represents a specialised strategy bot.

---

# Strategic Significance

Engine cloning marks the transition from:

```
Single trading engine
```

to

```
Multi‑engine autonomous trading system
```

Each engine becomes a **modular trading bot** that can:

- operate independently
- run different signal models
- share infrastructure
- compete for capital

This capability is a foundational milestone in the AutoScalp architecture.

---

# Doctrine Status

Status: **ACTIVE**

This process is now the **official method for creating new engines** in AutoScalp.

