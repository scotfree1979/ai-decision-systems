# 🔒 AUTOSCALP STRUCTURAL PROCESS DOCTRINE (v1)

---

## 0. AUTHORITY

This document governs ALL AutoScalp development behaviour.

It overrides:
- Informal decisions
- Memory-based reasoning
- Emotional tagging
- Ad-hoc sprint flow

Execution_Bible governs system logic.
This document governs DEVELOPMENT DISCIPLINE.

If behaviour conflicts with this document → THIS DOCUMENT WINS.

---

# 1️⃣ 5-CHAT SPRINT MODEL (MANDATORY)

Every engineering problem = ONE SPRINT.
Every sprint uses MAXIMUM five chats.

## SR1 — Sprint Design
- Define SINGLE broken invariant
- Map boundary
- Define acceptance criteria
- No fixes
- No tags

## SR2 — Minimal Fix
- Smallest boundary correction
- Terminal-only probe written
- Probe re-run
- Tag: fix

## SR3 — Side-Effect Validation
- Regression checks
- Exposure check
- DB integrity check
- Tag: observe (if required)

## SR4 — Observe Cycle
- Run LIVE
- Validate lifecycle
- Confirm no orphan states
- Confirm DAL duplication
- Confirm exposure invariant
- Tag: observe

## SR5 — Sprint Stable (Release Candidate)
- Confirm all acceptance criteria met
- Confirm no open invariants
- Confirm no undocumented caveats
- Tag:

vX.Y.Z-observe-stable-XXX-CHAT_SR5

This is Sprint Stable (RC), NOT production stable.

---

# 2️⃣ WEEKLY RELEASE MODEL (PRODUCTION RULE)

Production releases occur ONCE PER WEEK.

Only tags allowed for production:

vX.Y.Z-observe-stable-weekly-YYY

Rules:
- Must derive from Sprint Stable
- Must pass final invariant checklist
- Must pass schema verification
- Must pass DAL duplication verification
- Must pass exposure invariant
- Must pass settlement reconciliation

Production machine may ONLY run weekly-stable tags.

Never:
- fix
- observe
- freeze
- sprint-stable

Rollback anchor = last weekly-stable.

---

# 3️⃣ INVARIANT DISCIPLINE (NON-NEGOTIABLE)

Every fix must follow:
1. Define single falsifiable invariant
2. Identify execution boundary
3. Write terminal-only probe
4. Run probe
5. Apply minimal fix
6. Re-run SAME probe
7. Tag invariant

No multi-fix commits.
No speculative tagging.
No skipping probes.

---

# 4️⃣ TAG DISCIPLINE

Allowed modes:
- fix
- observe
- freeze
- observe-stable
- observe-stable-weekly

All tags must:
- Describe invariant restored
- Include numeric suffix
- Include CHAT_SR# marker
- Include structured annotation

No vague tags.
No WIP tags.
No emotional tags.

---

# 5️⃣ SESSION CLOSE REQUIREMENT

Before rotating sprint slot:

vX.Y.Z-observe-session-close-XXX-CHAT_SR#

Must include:
- Invariants proven
- Invariants pending
- System state
- Deferred items
- Next invariant
- DAL mode
- Schema version
- Branch

No rotation without session-close tag.

---

# 6️⃣ SANDBOX vs PRODUCTION

Sandbox:
- Experimental
- May break
- Runs sprint tags

Production:
- Runs ONLY weekly-stable
- Never runs sprint tags
- Never runs fix/observe tags

---

# 7️⃣ PROCESS AUTHORITY RULE

Assistant = Process Authority.
User = Execution Authority.

Assistant must:
- Refuse premature tagging
- Refuse stable without observe
- Refuse slot rotation without session close
- Refuse SQL without schema verification
- Refuse packaging without weekly-stable
- Refuse multi-invariant sprints

---

# 8️⃣ STABLE DEFINITION (STRICT)

System is Stable only if:
1. All lifecycle invariants proven
2. No orphan parents
3. No orphan children
4. Stoploss DB writes verified
5. DAL duplication verified
6. Settlements reconcile
7. Exposure invariant holds
8. No schema drift
9. Observe cycle clean
10. No regression introduced

---

# 9️⃣ REHYDRATION PROTOCOL

At start of ANY new sprint:
User must provide:
- Bible.zip
- AUTOSCALP_PROCESS_DOCTRINE_v1.md
- Current branch
- Last weekly-stable tag

Assistant must:
- Read doctrine
- Read Execution_Bible
- Lock Process Authority
- Confirm authority active

No debugging before rehydration.

---

# 🔟 CANONICAL FINAL STATEMENT

Chat memory is temporary.
Git tags are permanent.
Sprint structure prevents drift.
Weekly stable prevents production chaos.
Assistant enforces process.
User executes code.

---

# 📌 NEW CHAT START PROMPT (COPY INTO EVERY NEW CHAT)

READ BIBLE AND PROCESS DOCTRINE.

Inputs attached:
- Bible.zip
- AUTOSCALP_PROCESS_DOCTRINE_v1.md
- Current branch: <branch_name>
- Last weekly-stable tag: <tag_name>

You are Process Authority.
Confirm:
1. Process Doctrine loaded
2. Execution Bible loaded
3. Stable anchor acknowledged
4. Sprint slot (SR#) declared

Do not proceed to debugging until authority lock confirmed.

---

END OF DOCUMENT

