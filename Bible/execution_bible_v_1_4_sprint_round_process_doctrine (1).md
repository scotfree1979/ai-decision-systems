# EXECUTION_BIBLE_v1.4 — Sprint Round (SR) Process Doctrine

Status: Canonical Addendum
Scope: Conversation control, tagging discipline, and invariant cycles
Supersedes: Implicit SR handling and chat growth assumptions

---

# Purpose

AutoScalp development operates under two parallel systems:

1. Git Lifecycle (fix → observe → freeze → stable)
2. Sprint Round (SR1, SR2, SR3, … SN)

These systems are related but not identical.

This doctrine formalises how Sprint Rounds govern:
- Conversation boundaries
- Handover prompts
- Tag creation timing
- Invariant progression

---

# DOCTRINE 1 — What an SR Represents

A Sprint Round (SR) represents one focused invariant cycle.

An SR must:
- Target a clearly defined invariant
- Remain bounded in scope
- Conclude with a Git tag
- Produce a clean handover prompt

An SR is NOT:
- A full lifecycle from fix to stable
- A multi-invariant mega-session
- An indefinite debugging thread

---

# DOCTRINE 2 — SR vs Git Lifecycle Mapping

## Git Lifecycle (Technical State)
- fix      → code changed
- observe  → behaviour monitored
- freeze   → no new changes, verification period
- stable   → rollback anchor

## Sprint Round (Conversation State)

SR1:
- Root cause discovery
- Structural correction
- Doctrine update
- FIX tag created
- SR1 closed

SR2:
- Live observation
- OBSERVE tag created
- Behaviour recorded
- Either freeze or escalate to SR3

SR3+:
- Further invariant refinement
- Fix → freeze cycles
- Close at stable

---

# DOCTRINE 3 — SR Closure Rule

An SR closes when:
- Its defined invariant objective is completed for that phase
- A Git tag is created
- A handover prompt is generated
- The Bible is updated

An SR does NOT remain open through freeze and stable unless explicitly scoped that way.

SR boundaries exist to prevent:
- Chat bloat
- Context drift
- Performance degradation
- Unbounded reasoning loops

---

# DOCTRINE 4 — Handover Protocol

At the end of every SR:

1. Bible must contain:
   - Any new doctrine
   - Any replaced doctrine
   - Updated invariants

2. A handover prompt must be generated containing:
   - Current branch
   - Last tag
   - Current invariant state
   - Objectives for next SR

3. New chat must:
   - Include SR number in title
   - Begin by loading Bible + engines.zip

---

# DOCTRINE 5 — SR Escalation Rule

If within an SR:
- Fix → freeze cycles grow complex
- Multiple unrelated invariants appear
- Performance degradation occurs
- Context becomes heavy

Then:
- Generate continuation prompt
- Increment SR number
- Open fresh chat

SR numbers reflect conversation segmentation, not Git mode.

---

# DOCTRINE 6 — Tag Naming Discipline

All tags must include:
- Version prefix
- Git lifecycle mode (fix / observe / freeze / stable)
- Short human-readable summary
- Three-digit unique suffix
- SR number in annotation body

Example:

v7.9.15-fix-temporal-startup-sr1-104

SR number must appear in annotation message, not necessarily in tag name.

---

# DOCTRINE 7 — SR Completion Signal

An SR is considered complete when:
- Its invariant objective is achieved
- A tag is created
- Handover prompt is delivered
- New SR begins in fresh chat

No SR may silently transition into another without formal closure.

---

# Summary

Sprint Rounds control:
- Scope
- Conversation size
- Invariant focus
- Clean state transitions

Git lifecycle controls:
- Technical state
- Rollback safety
- Release anchors

They must remain conceptually separate.

---

# End of EXECUTION_BIBLE_v1.4

