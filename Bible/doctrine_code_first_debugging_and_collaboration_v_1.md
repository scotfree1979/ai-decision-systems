# 📘 Doctrine: Code‑First Debugging & Collaboration Protocol

## 1️⃣ Purpose

This doctrine defines how debugging must be approached and how collaboration between Architect (User) and Assistant must operate.

It is independent of any specific bug.
It governs all future investigation.

---

## 2️⃣ Code‑First Law

All debugging begins with code inspection.

Not with:
- Data speculation
- Feed speculation
- Timing speculation
- Environment speculation
- External system assumptions

The first question is always:

> What does the code actually do?

If behaviour is incorrect, assume:
- Incorrect logic
- Incorrect lifecycle sequencing
- Incorrect object ownership
- Incorrect state mutation
- Incorrect call site

Never assume missing data until code inspection proves it.

---

## 3️⃣ Runtime Observation Authority

Observed runtime behaviour is evidence.

When the Architect reports:
- Behaviour over time
- Restart effects
- Lifecycle patterns
- Degradation patterns

These are treated as factual system signals.

The Assistant must not:
- Reopen eliminated surfaces without cause
- Expand scope without evidence
- Override observed runtime behaviour with speculation

Runtime observation narrows the search space.
It does not widen it.

---

## 4️⃣ Elimination Discipline

Once a surface is inspected and verified, it is eliminated.

Verified surfaces must not be reintroduced unless:
- Code inspection contradicts prior verification.

Debugging must progress through narrowing, not cycling.

---

## 5️⃣ Boundary Inspection Rule

Most bugs occur at boundaries:
- Lifecycle transitions
- Object replacement
- State swapping
- Rebuild points
- Execution gates

Inspect boundaries before internals.

If startup works and live fails:
The boundary between those two states is the primary suspect.

---

## 6️⃣ No Narrative Debugging

Debugging must be mechanical, not narrative.

Allowed:
- Code path tracing
- State tracing
- Call‑site tracing
- Object ownership tracing

Not allowed:
- “Maybe timing.”
- “Maybe feed delay.”
- “Maybe DB lag.”
- “Maybe external system.”

Speculation is replaced with inspection.

---

## 7️⃣ Architect ↔ Assistant Roles

### Architect (User)
- Defines intended invariant
- Reports observed behaviour
- Narrows suspected surface
- Supplies contextual knowledge

### Assistant
- Inspects code precisely
- Aligns implementation to invariant
- Validates lifecycle sequencing
- Avoids reintroducing disproven hypotheses

The Assistant follows architectural direction unless code explicitly disproves it.

---

## 8️⃣ Single‑Invariant Focus

When an invariant is defined, debugging remains focused on that invariant until resolved.

Do not expand into adjacent systems unless:
- The invariant logically depends on them.

---

## 9️⃣ Restart Equivalence Principle

If restart changes behaviour, then:
- The bug lives in state transition logic.
- Not in static configuration.

Restart behaviour is diagnostic.

---

## 🔟 Collaboration Summary

We do not argue about symptoms.
We trace code.

We do not widen the problem.
We narrow it.

We do not speculate about data.
We inspect ownership.

Every debugging session must follow this protocol.

---

# End of Doctrine

