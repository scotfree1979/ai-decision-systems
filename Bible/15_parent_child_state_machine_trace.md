# 15 — PARENT / CHILD STATE MACHINE TRACE

## Parent States
QUEUED → PLACED → MATCHED → CLOSED

## Child States
QUEUED → PLACED → MATCHED

## Transition Authority
• Placement Worker: QUEUED → PLACED
• Router Worker: PLACED → MATCHED
• _finalize_parent_on_hedge: MATCHED → CLOSED
• Settlement Loop: reconciliation validation

## DB Columns Mutated
• entry_status
• exit_status
• closed_at
• realized_pnl
• net_pl

## Exposure
• Reserved on parent placement (BankState)
• Released on hedge match

## Invariants
• Each parent may have hedge_of child
• Child MATCHED triggers parent finalization
• net_pl updated once per parent

## Observed Gap
• No explicit global absolute liability trigger in router or overwatcher code

