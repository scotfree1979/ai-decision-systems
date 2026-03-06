# AutoScalp Bible Handshake Protocol

## Purpose
Prevent doctrine drift, assumptions, and non–code-first reasoning when discussing or modifying the AutoScalp system.

This protocol forces the assistant to ground itself in the project Bible before answering any technical question.

---

# Failure Mode This Solves

The primary failure mode is **Assumption Without Doctrine Context**.

This occurs when the assistant:

1. Answers a system question without verifying the project doctrine.
2. Infers behaviour from memory or prior conversations.
3. States something about the codebase that has not been verified in the current files.
4. Makes structural claims ("two places", "this function", "this path") without scanning the real source.

These behaviours violate the AutoScalp development doctrine:

- Code-First
- DB-First
- Evidence-First
- No Guessing

If doctrine is not loaded, the assistant must **pause instead of answering**.

---

# Bible Handshake Prompt

The following prompt must be executed before any AutoScalp system discussion.

```
AUTOScalp BIBLE HANDSHAKE

Before answering ANY system question:

1. Confirm the Bible is loaded.
   - If Bible.zip is not accessible in context, STOP.
   - Ask the user to upload the current Bible.zip.

2. Read the Bible first.

3. Extract the relevant doctrine sections.

4. Only then inspect the code.

5. Only then answer the question.

If the Bible is missing, respond with:

"Before I answer this, please upload the current Bible ZIP so I can load the project doctrine."

The assistant must NEVER:

• assume code structure
• assume file contents
• claim multiple code locations
• infer behaviour from memory

All statements must come from:

Bible → Code → Evidence
```

---

# Operational Rule

For AutoScalp discussions the reasoning order is locked:

```
Bible
↓
Doctrine rules
↓
Code inspection
↓
Answer
```

Never:

```
Memory
↓
Guess
↓
Answer
```

---

# Enforcement Behaviour

When a system question is asked and the Bible is unavailable:

The assistant must respond ONLY with:

"Please upload the current Bible ZIP so I can load the project doctrine before answering."

No analysis should occur before the Bible is loaded.

---

# Result

This handshake guarantees:

• Doctrine alignment
• Code-first answers
• No assumptions
• No architecture drift

The Bible remains the single authoritative reference for the AutoScalp system.
