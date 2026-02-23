# 📜 AUTOSCALP TRACE MODE DOCTRINE (v1)

## Purpose
This doctrine enforces strict **code-first tracing discipline** during debugging, auditing, and accounting reviews.

This exists to permanently eliminate:
- Structural reasoning
- Architectural projection
- Pattern completion from memory
- Behaviour narration without extraction
- Assumption-based debugging

Trace mode must always be mechanical, not interpretive.

---

# 🔒 CORE RULE: CODE DEFINES REALITY

During trace mode:

> The narrative must emerge from the code.
> The code must never be inferred from the narrative.

If a function has not been extracted, it does not exist for reasoning purposes.

---

# 🚫 ABSOLUTE PROHIBITIONS

During trace mode, the assistant must NOT:

1. Describe behaviour without first extracting the exact function.
2. Infer implementation based on prior versions or memory.
3. Project architectural patterns onto the current branch.
4. Defend a statement after a challenge without re-extracting the file.
5. Complete structural gaps using "likely", "probably", or pattern reasoning.
6. Reference earlier system designs unless re-verified in the current mounted files.

If any of the above occurs, the trace is invalid.

---

# ✅ MANDATORY TRACE PROCEDURE

For every function discussed:

1. Locate the file path.
2. Extract the full function definition.
3. Search the entire codebase for all call sites.
4. Confirm the execution path with line references.
5. Only then describe behaviour.

If challenged:

→ Immediately re-search and re-extract.
→ No defence.
→ No explanation.
→ Just verification.

---

# 🧠 NO STRUCTURAL REASONING RULE

Structural reasoning is explicitly disabled during trace mode.

This includes:
- Pattern recognition from earlier versions
- Architectural assumptions
- Expected lifecycle behaviour
- "Standard model" thinking

Trace mode is not architecture mode.
Trace mode is mechanical inspection only.

---

# 🧩 TRACE MODE VS ARCHITECTURE MODE

| Mode              | Allowed                              | Forbidden                          |
|-------------------|---------------------------------------|------------------------------------|
| Trace Mode        | Extraction, verification, call graph  | Structural reasoning, projection   |
| Architecture Mode | High-level reasoning                  | Unverified code claims             |

The assistant must explicitly know which mode it is in.

SR1, SR2, accounting reviews, and execution audits are ALWAYS Trace Mode.

---

# ⚠️ CHALLENGE RESPONSE RULE

If the user says:

- "That’s not in the file"
- "You didn’t look at the code"
- "That function doesn’t exist"

The assistant must:

1. Stop immediately.
2. Re-extract the relevant file.
3. Search the full file for the function.
4. Show the result.
5. Continue only after verification.

No defence is permitted.

---

# 🎯 ACCOUNTING-SPECIFIC TRACE RULE

When debugging BankState, placement, exposure, or routing:

- All gating logic must be shown.
- All mutation points must be shown.
- All ledger writes must be shown.
- All reconciliation logic must be shown.
- No lifecycle narrative allowed without call-site proof.

Accounting must never be reasoned from memory.

---

# 🔁 ENFORCEMENT

If this doctrine is violated:

The user may state:

> "Trace Mode Violation"

Upon hearing this, the assistant must:

- Reset to extraction-only mode.
- Remove narrative.
- Restart the trace at the last verified function.

---

# 🏁 FINAL PRINCIPLE

> You cannot reason about code you have not extracted.
> You cannot describe behaviour you have not verified.
> You cannot fix what you have not proven.

Trace mode is mechanical.
Not interpretive.
Not memory-based.
Not structural.

---

**Status: ACTIVE — Mandatory for all future tracing sessions**

