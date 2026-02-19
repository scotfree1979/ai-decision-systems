# EXECUTION_BIBLE_v1.1 — Temporal Execution Doctrine Addition

Status: Canonical Addendum
Scope: LIVE execution timing guarantees
Supersedes: Implicit timing assumptions in EXECUTION_BIBLE_v1

---

# Purpose

EXECUTION_BIBLE_v1 defines lifecycle ownership and state correctness.
This addendum defines temporal correctness.

AutoScalp must be both:
- State-correct
- Time-correct

Execution that is logically valid but temporally inverted is doctrinally invalid.

---

# 1. MATCHED Is an Event, Not Just a State

When a parent transitions to `MATCHED`, this is a lifecycle event.

This event must:
- Trigger synchronous downstream actions
- Execute inside Router ownership
- Not rely on reconciliation loops
- Not rely on rescue loops

MATCHED is not a passive database value.
It is an execution boundary.

---

# 2. Immediate Child Insertion Requirement

Upon parent `MATCHED`:

Router must:
1. Insert a child row immediately
2. Promote child to PLACING within the same worker cycle
3. Submit child to Betfair without delay

Child placement must NOT:
- Wait for surface reconciliation
- Wait for rescue detection
- Depend on later polling loops

Rescue exists only as a safety net.

---

# 3. Temporal Bound Guarantee

The time between:

parent MATCHED
→ child PLACED

must be bounded.

Canonical bound:

< 1 second under normal operation

If latency exceeds bound:
- Hedge completion probability collapses
- Economic integrity degrades
- System is temporally incorrect

Temporal correctness is mandatory.

---

# 4. Rescue Doctrine Clarification

Rescue exists to:
- Detect anomalies
- Repair missing lifecycle steps
- Correct inconsistent state

Rescue must never:
- Be the primary driver of child insertion
- Be required for normal hedge creation

If rescue is required for normal flow,
execution ordering is inverted.

---

# 5. Matched Timestamp Requirement

Parent rows must record:

`matched_at`

This timestamp:
- Defines event boundary
- Enables latency measurement
- Enables doctrine verification
- Enables regression detection

Without `matched_at`, temporal invariants cannot be proven.

---

# 6. Surface Reconciliation Doctrine

Surface loops may:
- Reconcile external truth
- Update betIds
- Repair drift

Surface loops must NOT:
- Originate child insertion
- Drive lifecycle progression

Lifecycle progression belongs exclusively to Router.

---

# 7. Temporal Integrity Is Part of Execution Integrity

AutoScalp is not only:
- State-correct

It must be:
- Time-correct

Any execution path where:
- Parent MATCHED
- Child inserted minutes later

is doctrinally invalid, even if logically consistent.

---

# 8. Regression Detection Requirement

All future verification suites must include:

Measurement of:

child_opened_at - parent_matched_at

If average latency exceeds bound:
- Execution regression is declared
- Fix required before next tag

---

# 9. SR2 Eligibility Condition

SR2 may only begin once:

- Parent MATCHED → child PLACED latency < 1 second
- Rescue no longer primary insertion path
- Hedge completion rate improves materially

Until then, system remains in Temporal Correction phase.

---

# End of EXECUTION_BIBLE_v1.1 Temporal Addendum

