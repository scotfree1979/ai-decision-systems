# AutoScalp Architecture Doctrine
## Snapshot Execution Model & Router Lifecycle Fix

Date: 2026
Branch Context: v7.9.14
System: AutoScalp BUS v13

---

# 1. Purpose of this Doctrine

This doctrine records the architectural direction of the system after discovering that the current BUS + Router interaction is too slow and overly dependent on database reads.

The goal of the new architecture is:

• Eliminate repeated database lookups during BUS ticks
• Move execution truth into runtime snapshots
• Preserve execution truth permanently
• Separate execution state from lifecycle state
• Ensure BUS becomes a pure routing engine

This doctrine also records the immediate work for:

• Today (BUS performance fixes)
• Next week (Router lifecycle corrections)

This document becomes part of the system "Bible" and will evolve as fixes are implemented.

---

# 2. Core Architectural Direction

The system is moving toward a **Snapshot Execution Model**.

Principle:

"Everything required for execution should exist in runtime memory."

After the system boots and CTX is built, the database should not be consulted for operational logic.

The only repeating runtime operation should be:

• Odds refresh

Everything else should already exist in memory.

---

# 3. The Snapshot Model

The router already maintains runtime execution surfaces:

ROUTER_PARENT_SURFACE
ROUTER_CHILD_SURFACE

These surfaces provide instant access to:

Parent execution state
Child lifecycle state
Parent anchors
Matched prices
Matched stakes

These surfaces must become the **primary source of execution truth** for BUS.

The database becomes a persistence layer only.

BUS should never need to query orders tables during execution.

---

# 4. Router Execution Truth

When a child hedge executes, the router currently stamps:

entry_status = MATCHED
exit_status  = MATCHED

This represents **execution truth**.

This state must never be mutated afterwards.

Execution state must be immutable once confirmed.

---

# 5. Current Lifecycle Problem

Router sweeps later mutate exit_status to:

SETTLED
CANCELLED
EXPIRED

This destroys execution truth.

As a result:

• Router reports lose matched evidence
• BUS diagnostics become misleading
• Risk engines lose anchor certainty

This mutation must be removed.

---

# 6. Correct State Separation

Execution State (permanent)

entry_status = MATCHED
exit_status  = MATCHED

Lifecycle State (mutable)

parent_closed
closed_at
exposure_released

Settlement State (future field)

settlement_status

Execution truth must never change.

Lifecycle state handles settlement and cleanup.

---

# 7. Target Router Behaviour

Once a hedge child matches:

The router must:

• mark parent_closed
• release exposure
• mark closed_at

But must **NOT change exit_status from MATCHED**.

This ensures execution history remains intact.

---

# 8. BUS Performance Problem

BUS ticks are currently slow because:

• DB lookups occur repeatedly
• Parent anchors are re-derived
• Child state is re-queried

Even though the router already knows these values.

The result is unnecessary IO overhead.

---

# 9. BUS Performance Solution

BUS should rely on runtime snapshots instead of database queries.

Required execution information:

Parent id
Parent price
Parent stake
Parent side
Parent engine
Child state

All of this is already available inside the router surfaces.

Therefore:

BUS should read from the router snapshot instead of querying orders.

---

# 10. Router Snapshot Extension

Router surfaces should contain full parent rows.

Example snapshot structure:

Parent Snapshot

parent_id
marketId
selectionId
entry_odds
entry_stake
side
engine
customerOrderRef

Child Snapshot

child_id
entry_status
exit_status

These objects should be accessible in O(1).

---

# 11. BUS Execution Philosophy

BUS should become a pure coordinator.

Responsibilities:

• iterate route
• refresh odds
• call engines
• route plans

BUS must not perform:

• database lifecycle analysis
• order reconstruction
• anchor discovery

Those belong to the router snapshot layer.

---

# 12. Immediate Work (Today)

Today we focus on **BUS performance improvements**.

Actions:

1. Stop BUS reading parent anchors from DB

2. Use router parent snapshot instead

3. Use router child snapshot for hedge state

4. Ensure CTX building only occurs once

5. Ensure odds refresh is the only repeated operation

This should dramatically reduce BUS tick latency.

---

# 13. Next Week Work

Next week we fix Router lifecycle correctness.

Required changes:

1. Prevent exit_status mutation after MATCHED

2. Introduce settlement_status field if needed

3. Ensure router reports count MATCHED children correctly

4. Ensure parent lifecycle uses parent_closed only

5. Align parent and child lifecycle semantics

---

# 14. Expected System Outcome

After these changes:

BUS will become extremely fast.

Router will maintain permanent execution truth.

Reports will correctly show matched hedges.

Risk engines will have stable parent anchors.

Database IO will be drastically reduced.

---

# 15. Doctrine Evolution

This doctrine will be extended as additional architectural discoveries occur.

Future doctrines may include:

Unified Engine Architecture
BUS Cadence Control
Router Lifecycle Invariants
BankState Exposure Model

Each doctrine becomes part of the system Bible.

---

END OF DOCTRINE

