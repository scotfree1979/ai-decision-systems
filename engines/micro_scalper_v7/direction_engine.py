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
# 6. TREND SIGNAL (STRUCTURAL)
# ─────────────────────────────────────────────────────────────
from tools.betfair_runner_trend_surface import get_runner_trend
from tools.betfair_match_surface import get_direction_confidence
from engines.market_monitor.monitor import get_market_state, get_crossover_signal
from engines.price_math import walk_ticks


def get_trend_signal(ctx: Dict[str, Any]) -> str | None:
    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")
    if not mid or not sid:
        return None

    trend = get_runner_trend(mid, sid)
    return trend.get("direction")


# ─────────────────────────────────────────────────────────────
# 7. CROSSOVER SIGNAL (HIGHEST AUTHORITY)
# ─────────────────────────────────────────────────────────────
def get_crossover_signal_direction(ctx: Dict[str, Any]) -> str | None:
    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")
    if not mid or not sid:
        return None

    sig = get_crossover_signal(mid, sid)
    if not sig.get("crossed_over_recent"):
        return None

    # Rank improvement = steam, rank drop = drift
    delta = sig.get("rank_delta", 0)
    if delta > 0:
        return "BACK->LAY"
    if delta < 0:
        return "LAY->BACK"

    return None


# ─────────────────────────────────────────────────────────────
# 8. BIAS SIGNAL
# ─────────────────────────────────────────────────────────────
def get_bias_signal(ctx: Dict[str, Any]) -> str | None:
    bias = _f(ctx.get("bias_value"), 0.0)
    if bias > 0.05:
        return "BACK->LAY"
    if bias < -0.05:
        return "LAY->BACK"
    return None


# ─────────────────────────────────────────────────────────────
# 9. FAVOURITE STRUCTURE SIGNAL
# ─────────────────────────────────────────────────────────────
def get_favourite_signal(ctx: Dict[str, Any]) -> str | None:
    fav_rank = ctx.get("fav_rank")
    trend_dir = get_trend_signal(ctx)

    if fav_rank == 1 and trend_dir == "LAY->BACK":
        return "LAY->BACK"

    return None


# ─────────────────────────────────────────────────────────────
# 10. VOLATILITY SIGNAL
# ─────────────────────────────────────────────────────────────
def get_volatility_signal(ctx: Dict[str, Any]) -> str | None:
    vol = _f(ctx.get("tick_volatility"), 0.0)
    trend_dir = get_trend_signal(ctx)

    if vol > 0.4:
        return trend_dir

    return None


# ─────────────────────────────────────────────────────────────
# 11. MATCH SURFACE CONFIRMATION
# ─────────────────────────────────────────────────────────────
def get_match_surface_signal(ctx: Dict[str, Any]) -> str | None:
    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")
    if not mid or not sid:
        return None

    conf = get_direction_confidence(mid, sid)
    if conf >= 0.6:
        return get_trend_signal(ctx)

    return None


# ─────────────────────────────────────────────────────────────
# 12. VOTE RESOLUTION
# ─────────────────────────────────────────────────────────────
def resolve_vote_direction(signals: list[str | None]) -> str | None:
    votes = [s for s in signals if s is not None]
    if not votes:
        return None

    drift_votes = votes.count("LAY->BACK")
    steam_votes = votes.count("BACK->LAY")

    if drift_votes > steam_votes:
        return "LAY->BACK"
    if steam_votes > drift_votes:
        return "BACK->LAY"

    return None


# ─────────────────────────────────────────────────────────────
# 13. BOUNDARY BUFFER (2 TICKS EACH SIDE)
# ─────────────────────────────────────────────────────────────
def apply_boundary_buffer(ctx: Dict[str, Any], direction: str | None) -> str | None:

    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")
    px  = ctx.get("px")

    if not mid or not sid or px is None:
        return direction

    market_state = get_market_state(mid) or {}
    runners = market_state.get("runners") or {}

    ladder = sorted(
        [(s, r.get("px")) for s, r in runners.items() if r.get("px") is not None],
        key=lambda x: x[1]
    )

    index = None
    for i, (runner_id, _) in enumerate(ladder):
        if str(runner_id) == str(sid):
            index = i
            break

    if index is None:
        return direction

    lower_px = ladder[index - 1][1] if index > 0 else None
    upper_px = ladder[index + 1][1] if index < len(ladder) - 1 else None

    if lower_px:
        boundary_low = walk_ticks(lower_px, 2, direction="up")
        if px <= boundary_low:
            return None

    if upper_px:
        boundary_high = walk_ticks(upper_px, 2, direction="down")
        if px >= boundary_high:
            return None

    return direction

# ─────────────────────────────────────────────────────────────
# 14. OPPORTUNITY SIGNAL (PERSISTENCE / REPEATABILITY)
# ─────────────────────────────────────────────────────────────
from engines.config_paths import auto_conn

def get_opportunity_signal(ctx: Dict[str, Any]) -> str | None:
    """
    Uses indicators_opportunities table to detect
    persistent directional pressure.
    """

    mid = ctx.get("marketId")
    sid = ctx.get("selectionId")

    if not mid or not sid:
        return None

    con = auto_conn(rw=False)
    try:
        row = con.execute("""
            SELECT opportunities, taken
            FROM indicators_opportunities
            WHERE day = date('now','utc')
              AND marketId = ?
              AND selectionId = ?
        """, (mid, sid)).fetchone()
    finally:
        con.close()

    if not row:
        return None

    opps = _f(row[0])
    taken = _f(row[1])

    if opps == 0:
        return None

    persistence = taken / opps

    # Persistent drift pressure
    if persistence > 0.6:
        return get_trend_signal(ctx)

    return None

# ─────────────────────────────────────────────────────────────
# 15. MODE REFINEMENT (POST-DIRECTION STRUCTURAL CONFIRMATION)
# ─────────────────────────────────────────────────────────────
def refine_mode_with_structure(ctx: Dict[str, Any], direction: str | None, base_mode: str) -> str:
    """
    Adjusts aggressiveness based on structural alignment.
    """

    if direction is None:
        return base_mode

    trend = get_trend_signal(ctx)
    match = get_match_surface_signal(ctx)
    vol   = get_volatility_signal(ctx)

    confirmations = 0

    if trend == direction:
        confirmations += 1
    if match == direction:
        confirmations += 1
    if vol == direction:
        confirmations += 1

    if confirmations >= 2:
        return "AGGRESSIVE"

    if confirmations == 0:
        return "CONSERVATIVE"

    return base_mode

# ─────────────────────────────────────────────────────────────
# 16. MASTER ENTRY POINT (FULL AUTHORITY)
# ─────────────────────────────────────────────────────────────
def compute_msc_decision(ctx: Dict[str, Any]) -> Dict[str, Any]:

    # Layer 1 — Original MSC
    win_prob = compute_win_probability(ctx)
    base_direction = compute_direction(win_prob, ctx)
    base_mode = compute_mode(win_prob, ctx)

    # Layer 2 — Structural Signals
    crossover = get_crossover_signal_direction(ctx)
    trend     = get_trend_signal(ctx)
    bias      = get_bias_signal(ctx)
    fav       = get_favourite_signal(ctx)
    vol       = get_volatility_signal(ctx)
    match     = get_match_surface_signal(ctx)
    opp       = get_opportunity_signal(ctx)

    # --------------------------------------------------
    # STRUCTURAL AUTHORITY LAYER (ANCHOR FIRST)
    # --------------------------------------------------

    anchor_px  = _f(ctx.get("anchor_odd"))
    current_px = _f(ctx.get("px"))

    direction = None

    # 1️⃣ Crossover overrides everything
    if crossover:
        direction = crossover

    else:

        # 2️⃣ Anchor displacement backbone
        if anchor_px > 0 and current_px > 0:

            delta = (current_px - anchor_px) / anchor_px

            if delta > 0.02:
                direction = "LAY->BACK"
            elif delta < -0.02:
                direction = "BACK->LAY"

        # 3️⃣ Structural vote fallback
        if not direction:
            structural_vote = resolve_vote_direction([
                trend,
                bias,
                fav,
                vol,
                match,
                opp,
            ])
            direction = structural_vote or base_direction

    direction = apply_boundary_buffer(ctx, direction)

    mode = refine_mode_with_structure(ctx, direction, base_mode)

    entry_ticks = compute_entry_ticks(mode)
    stop_ticks  = compute_stop_ticks(mode, ctx)

    return {
        "win_prob": win_prob,
        "direction": direction,
        "mode": mode,
        "entry_ticks": entry_ticks,
        "stop_ticks": stop_ticks,
    }

