# EXECUTION_BIBLE_v1.2 — Temporal & Startup Ownership Doctrine

Status: Canonical Addendum
Scope: LIVE runtime execution ordering & service ownership
Supersedes: Implicit startup and timing assumptions in EXECUTION_BIBLE_v1

---

# Purpose

EXECUTION_BIBLE_v1 defined lifecycle ownership.
EXECUTION_BIBLE_v1.1 defined temporal guarantees.

This addendum formalises:

1) Temporal execution boundaries
2) Service startup ownership
3) Separation between execution, reconciliation, and orchestration

AutoScalp must be:
- State-correct
- Time-correct
- Ownership-correct

---

# DOCTRINE 1 — Temporal Execution Guarantee

## 1.1 MATCHED Is an Event

When a PARENT transitions to `MATCHED`, this is a lifecycle event.

This event MUST:
- Occur inside Router ownership
- Trigger child creation immediately
- Trigger child enqueue immediately
- Not depend on reconciliation loops
- Not depend on rescue loops

MATCHED is not a passive DB state.
It is an execution boundary.

---

## 1.2 Child Promotion Requirement

Upon parent MATCHED:

Router must:
1. Ensure child row exists
2. Immediately enqueue child into Router worker queue
3. Promote child QUEUED → PLACING in same worker cycle
4. Submit child to Betfair without delay

Rescue is safety net only.
Rescue must never be primary lifecycle driver.

---

## 1.3 Temporal Bound

The time between:

parent MATCHED → child PLACED

Must be < 1 second under normal LIVE conditions.

If this bound is violated:
- Hedge completion probability collapses
- Floor instability increases
- Economic integrity degrades

Temporal correctness is mandatory.

---

# DOCTRINE 2 — Reconciliation Is Not Execution

Reconciliation loops (Surface / Settlement / Rescue) exist to:
- Confirm external truth
- Repair drift
- Detect anomalies

They must NOT:
- Drive lifecycle progression
- Initiate child creation
- Initiate execution logic

Execution logic belongs exclusively to Router.

If reconciliation becomes primary execution path,
architecture is inverted.

---

# DOCTRINE 3 — Service Startup Ownership (LIVE)

## 3.1 Startup Must Be Explicit

Every background service must be started explicitly in LIVE mode.

No service may rely on:
- Import side effects
- __main__ blocks
- CLI-only entrypoints

LIVE startup path must define:
- Which services run
- In what order
- Under what preconditions

---

## 3.2 Settlement Service Ownership

There are two settlement execution paths:

A) CLI path (manual execution)
B) LIVE daemon path (background thread)

LIVE must use the full reconciliation cycle:

fetch → reconcile → bank_state sync → close markets → KPI refresh

LIVE must not use partial cycles.

If CLI path and LIVE path diverge,
the system is startup-inconsistent.

---

## 3.3 Orchestrator Is the Authority

The Orchestrator is the sole authority for starting:
- Router workers
- Settlement services
- Exposure guardian
- Surface loops
- BankState reporter

All service start calls must be traceable from start_live_loop().

No background service may exist that is not visible in startup.

---

# DOCTRINE 4 — CLI vs LIVE Parity

If a process works in standalone CLI but not in LIVE runtime,
the cause must be one of:

- Different startup path
- Different DB routing
- Different thread lifecycle
- Different execution order

CLI success does not prove LIVE correctness.
LIVE correctness must be verified in LIVE startup chain.

---

# DOCTRINE 5 — Invariant-First Debugging

When diagnosing issues:

1. Define the invariant being violated
2. Identify owner of that invariant
3. Trace startup path
4. Compare CLI vs LIVE path
5. Apply minimal ownership correction
6. Re-measure

Never patch symptoms.
Always correct ownership boundary.

---

# SR1 Completion Criteria (Temporal + Startup)

SR1 is complete when:

- Parent MATCHED → child PLACED < 1 second
- Child enqueue occurs inside MATCHED handler
- Settlement service started via explicit LIVE startup
- CLI and LIVE settlement paths are functionally equivalent
- Rescue no longer primary lifecycle driver

Only after these are proven may SR2 begin.

---

# End of EXECUTION_BIBLE_v1.2

