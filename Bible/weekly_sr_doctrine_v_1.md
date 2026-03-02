# AutoScalp Weekly SR Doctrine (Canonical)

## PURPOSE
This doctrine formalises the weekly SR lifecycle for AutoScalp.
It standardises how each trading week begins, how previous SR phases are rehydrated, and how stability is evaluated.

---

# 1️⃣ Weekly Structure

Each trading week begins in **SR1 (Observe Mode)**.

Lifecycle per week:

fix → freeze → run → observe → stabilise → freeze-stable

No structural changes are allowed during Observe unless a confirmed invariant breaks.

---

# 2️⃣ End-of-SR Handover Rule

At the end of each SR phase (SR1–SR5), the following must be produced:

• What was observed
• What was fixed
• What logic changed
• What invariants were introduced
• What remains unstable
• What is considered stable
• Current branch + tag
• Known risk areas

This summary becomes the input to the next SR.

---

# 3️⃣ Monday Rehydration Protocol

Every Monday:

1. Rehydrate prior SR summaries (SR1–SR5 if applicable)
2. Merge them into a single Weekly State Brief
3. Confirm:
   - Window logic integrity
   - InPlay trigger integrity
   - BankState integrity
   - Dashboard correctness
   - PX refresh correctness
   - Parent/child lifecycle integrity
4. Declare system state:
   - Stable
   - Near-stable
   - Needs fix

SR1 of the week is always Observe-first.

---

# 4️⃣ Stability Criteria

The system is considered stable when:

• Route window fills deterministically (seed 5 → entry to 10)
• Sliding behaviour holds after off+grace
• InPlay triggers correctly at 3-runner market volatility
• Dashboard reflects live state accurately
• PX never wipes
• No silent engine starvation
• No unexpected resets of active_slots

Only then can a "stable" tag be issued.

---

# 5️⃣ Weekly Opening Workflow

Each new SR1 chat must begin with:

• Current branch
• Latest freeze tag
• Previous SR summary
• Goals for the week
• Explicit observe checklist

No code changes before confirmation of observed behaviour.

---

# 6️⃣ Core Principle

We do not patch reactively.
We freeze structural improvements.
We observe behaviour.
We promote only proven logic.

This doctrine is permanent and part of the AutoScalp Bible.

