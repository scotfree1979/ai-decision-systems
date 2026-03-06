# AUTOSCALP DOCTRINE — BIBLE HANDSHAKE & ASSUMPTION FAILURE MODE

Branch Context: v7.9.14
Doctrine Version: v1
Purpose: Prevent assumption‑based reasoning when discussing or modifying AutoScalp.

---

# 1. The Failure Mode

The failure mode addressed by this doctrine is:

ASSUMPTION WITHOUT CODE VERIFICATION

This occurs when an assistant or developer:

• States facts about the code without verifying the file
• Claims multiple code locations without scanning the source
• Infers behaviour from memory
• Answers architectural questions without loading the project doctrine

Example class of failure:

"There are two places in the file"

when the file has only one.

This violates the core AutoScalp development principles:

• Code‑First
• Evidence‑First
• DB‑First
• No Guessing

The doctrine exists to eliminate this class of error entirely.

---

# 2. Root Cause

The root cause is **context drift**.

If the assistant answers before loading the project doctrine (Bible), reasoning can occur using:

• conversation memory
• partial context
• assumptions

This produces incorrect statements about the system.

Therefore doctrine must be loaded first.

---

# 3. The Bible Handshake Protocol

Before answering ANY AutoScalp system question the assistant must perform the following handshake.

BIBLE HANDSHAKE:

1. Confirm Bible.zip is accessible.

2. If Bible.zip is NOT accessible:

   Respond ONLY with:

   "Before I answer this, please upload the current Bible ZIP so I can load the project doctrine."

3. Once the Bible is available:

   • Read the doctrine files
   • Extract relevant rules

4. Only after doctrine is loaded:

   • Inspect the code

5. Only after code inspection:

   • Answer the question

---

# 4. Mandatory Reasoning Order

All system analysis must follow this sequence:

Bible
↓
Doctrine rules
↓
Code inspection
↓
Answer

The following sequence is forbidden:

Memory
↓
Assumption
↓
Answer

---

# 5. Hard Enforcement Rule

If the Bible is not available the assistant MUST STOP.

No technical analysis may occur.

No architectural statements may be made.

Only the handshake request may be returned.

---

# 6. What This Doctrine Guarantees

Following this doctrine ensures:

• No assumptions about the code
• No guessing about system structure
• No claims about files without verification
• Consistent architecture reasoning

The Bible becomes the authoritative starting point for every AutoScalp discussion.

---

# 7. Operational Outcome

When this doctrine is followed:

1. The assistant always reads the doctrine first
2. Then verifies the code
3. Then answers

This eliminates the assumption failure mode entirely.

