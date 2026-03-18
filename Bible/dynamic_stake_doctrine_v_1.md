# DYNAMIC STAKE DOCTRINE — V7 (FINAL)

---

## PURPOSE

Dynamic Stake is the system that determines **how much to bet** based on:

- Market structure (what price is doing)
- Trade confirmation (what we have successfully executed)
- Risk positioning (distance + odds)

It does NOT:

- Manage capital
- Enforce budgets
- Check pots
- Control execution

Those are handled downstream by BUS / Router.

---

## SYSTEM FLOW

Snapshots → BUS Projection → 30-Point Score → Stake → BUS Gate → Router

---

## INPUT SOURCES (AUTHORITATIVE)

### MarketMonitor (Structure)
- px
- rank
- px_prev (derived)
- rank_prev (derived)

### Router Lifecycle (Execution Truth)
- orders_by_runner
- closed trades (parent + child matched)

### Direction
- ctx["direction"] → DRIFT / STEAM

### Position
- trend_start_px (anchor fallback)
- current px

---

## CORE MODEL

Score ∈ [1 → 30]

Score =
- Market Trend (0–10)
- Trade Confirmation (0–10)
- Risk & Position (0–10)

Stake = linear mapping from MIN → MAX using score

---

## 1. MARKET TREND (0–10)

Measures structure stability:

- Price moving consistently
- Rank improving or stable
- No reversals
- Direction aligned

Effect:
- Clean trend → increases score
- Noise / reversal → reduces score

---

## 2. TRADE CONFIRMATION (0–10)

Measures execution success:

- CLOSED trades only (parent + child matched)
- Directional alignment
- Closure speed

Effect:
- More aligned closures → increases score
- Faster closures → increases score
- Opposing trades → reduces score

Key Truth:

Price movement ≠ confidence

Confidence = successful completed trades along that movement

---

## 3. RISK & POSITION (0–10)

### Distance

|px - trend_start_px|

- Near → higher score
- Far → lower score

### Odds (Liability)

- 2–6 → optimal (max score)
- 1.5–2 / 6–10 → reduced
- >10 or <1.5 → heavily reduced

---

## SCORE BEHAVIOUR

1–5   → Minimum stake (weak signal)
10–20 → Scaling (developing signal)
25–30 → Maximum stake (full alignment)

---

## STAKE MAPPING

Linear 30-step scale:

stake = MIN + (score - 1) × step

No jumps, no cliffs, smooth progression.

---

## TREND LIFECYCLE

### Start
- Movement begins
- No confirmation
→ Low stake

### Build
- Trades begin closing
→ Stake increases

### Peak
- Multiple fast closures
- Strong structure
- Sweet spot odds
→ Max stake

### Extension
- Price far from anchor
→ Stake reduces

### Break
- Opposing trades / no closures
→ Stake collapses

---

## GREEN-UP RELATIONSHIP

Dynamic Stake:
- Determines position size

GreenUp:
- Locks profit equally across outcomes

Invariant:

WIN PnL ≈ LOSE PnL

---

## FINAL MODEL

Dynamic Stake =

Market Structure
× Trade Confirmation
× Risk Control

---

## CRITICAL INVARIANTS

- Deterministic
- Snapshot-driven
- No external dependencies
- No budget logic
- No pot logic
- No hidden state

---

## SYSTEM TRUTH

You are not sizing bets.

You are scaling conviction based on:

- Proven execution
- Within a structured market

---

## DONE STATE

✔ Market-driven
✔ Execution-confirmed
✔ Risk-aware
✔ Fully deterministic
✔ Smooth 30-step scaling

---

## PRE-LAUNCH CHECK (REQUIRED)

Before launch, verify:

1. BUS produces plans every tick
2. ctx contains:
   - px
   - rank
   - direction
   - orders_by_runner
3. dynamic_stake returns > 0
4. no NoneType or missing key errors
5. Router receives valid size

If any of the above fail → STOP

---

END OF DOCTRINE

