# engines/micro_scalper_v7/direction_engine.py
"""
direction_engine.py
───────────────────────────────────────────────
Unified direction & mode selector for MicroScalper.

Consumes:
    • win_probability (from narratives, form, oc movement)
    • bias_conf / bias_value
    • drift_speed / momentum_class
    • volatility_state
    • SLEQ (stop-loss equity)
    • opportunity_map micro-zone signals

Produces:
    • direction: "LAY->BACK" or "BACK->LAY"
    • mode: "CONSERVATIVE" | "MODERATE" | "AGGRESSIVE"
    • entry_ticks (distance)
    • stop_ticks (SL width)
"""

from typing import Dict, Any
import math

# ─────────────────────────────────────────────────────────────
# SAFE HELPERS
# ─────────────────────────────────────────────────────────────
def _f(x, d=0.0):
    try: return float(x)
    except: return float(d)

def _clamp(x, lo, hi):
    return max(lo, min(hi, x))

# ─────────────────────────────────────────────────────────────
# 1. WIN PROBABILITY ESTIMATOR
# ─────────────────────────────────────────────────────────────
def compute_win_probability(ctx: Dict[str, Any]) -> float:
    """
    Synthetic win-prob estimator.
    Inputs from ctx:
        oc_movement, drift_speed, momentum_class,
        form_win_rate, bias_conf, blueprint_conf
        inplay_progress (0→7)
    Output: 0.0 → 1.0 probability of winning
    """

    # OC movement (pre-off and in-play)
    oc = _f(ctx.get("oc_movement"), 0.0)       # +ve = coming in, -ve = drifting
    drift = _f(ctx.get("drift_speed"), 0.0)    # long-run drift

    # V7 intelligence fields
    mom = _f(ctx.get("momentum_class"), 0.0)
    form = _f(ctx.get("form_win_rate"), 0.0)
    biasc = _f(ctx.get("bias_conf"), 0.0)
    bconf = _f(ctx.get("blueprint_conf"), 0.0)

    # In-play progress — late race increases certainty
    prog = _clamp(_f(ctx.get("inplay_progress"), 0.0) / 7.0, 0.0, 1.0)

    # Weighted blend (early prototype)
    raw = (
        (0.25 * (oc * -1)) +       # if OC shorten → higher win_prob
        (0.20 * (1 - drift)) +     # if drifting out → lower win_prob
        (0.20 * mom) +
        (0.20 * form) +
        (0.10 * biasc) +
        (0.10 * bconf) +
        (0.10 * prog)
    )

    # Normalise to 0-1 range
    return _clamp(raw, 0.0, 1.0)

# ─────────────────────────────────────────────────────────────
# 2. DIRECTION DECISION (WIN_PROB → SIDE)
# ─────────────────────────────────────────────────────────────
def compute_direction(win_prob: float, ctx: Dict[str, Any]) -> str:
    """
    Direction selection:
        win_prob > 0.55 → BACK->LAY
        win_prob < 0.45 → LAY->BACK
        Middle zone uses bias to break tie.
    """

    if win_prob >= 0.55:
        return "BACK->LAY"       # strong chance of winning
    if win_prob <= 0.45:
        return "LAY->BACK"       # weak chance of winning

    # Tie-break region → use bias & form
    biasv = _f(ctx.get("bias_value"), 0.0)
    form = _f(ctx.get("form_win_rate"), 0.0)

    if biasv > 0.05 or form > 0.55:
        return "BACK->LAY"
    return "LAY->BACK"

# ─────────────────────────────────────────────────────────────
# 3. MODE SELECTOR (AGG, MOD, CON)
# ─────────────────────────────────────────────────────────────
def compute_mode(win_prob: float, ctx: Dict[str, Any]) -> str:
    """
    Determines how confident MSC should be:
        • Aggressive → wide stops, close entry
        • Conservative → tight stops, far entry
    """

    sleq = _f(ctx.get("sleq"), 1.0)
    vol = _f(ctx.get("tick_volatility"), 0.0)
    biasc = _f(ctx.get("bias_conf"), 0.0)

    score = 0.0
    score += (win_prob - 0.5) * 1.0
    score += (sleq - 1.0) * 0.6
    score += (biasc * 0.5)
    score -= (vol * 0.5)

    if score >= 0.25:
        return "AGGRESSIVE"
    if score <= -0.25:
        return "CONSERVATIVE"
    return "MODERATE"

# ─────────────────────────────────────────────────────────────
# 4. ENTRY DISTANCE (TICKS)
# ─────────────────────────────────────────────────────────────
def compute_entry_ticks(mode: str) -> int:
    if mode == "CONSERVATIVE":
        return 3
    if mode == "AGGRESSIVE":
        return 1
    return 2

# ─────────────────────────────────────────────────────────────
# 5. STOP-LOSS WIDTH (TICKS)
# ─────────────────────────────────────────────────────────────
def compute_stop_ticks(mode: str, ctx: Dict[str, Any]) -> int:
    sleq = _f(ctx.get("sleq"), 1.0)
    vol = _f(ctx.get("tick_volatility"), 0.0)

    if mode == "CONSERVATIVE":
        base = 2
    elif mode == "AGGRESSIVE":
        base = 6
    else:
        base = 4

    if sleq >= 1.3:
        base += 2
    elif sleq <= 0.9:
        base -= 1

    if vol > 0.4:
        base += 1
    elif vol < 0.1:
        base -= 1

    return max(1, int(base))

# ─────────────────────────────────────────────────────────────
# 6. MASTER ENTRY POINT (ALL IN ONE)
# ─────────────────────────────────────────────────────────────
def compute_msc_decision(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns:
        {
            "win_prob": float,
            "direction": "LAY->BACK" | "BACK->LAY",
            "mode": "AGGRESSIVE" | "MODERATE" | "CONSERVATIVE",
            "entry_ticks": int,
            "stop_ticks": int
        }
    """
    win_prob = compute_win_probability(ctx)
    direction = compute_direction(win_prob, ctx)
    mode = compute_mode(win_prob, ctx)
    entry_ticks = compute_entry_ticks(mode)
    stop_ticks = compute_stop_ticks(mode, ctx)

    return {
        "win_prob": win_prob,
        "direction": direction,
        "mode": mode,
        "entry_ticks": entry_ticks,
        "stop_ticks": stop_ticks,
    }
