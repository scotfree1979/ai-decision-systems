# bias/engine.py
from __future__ import annotations
from typing import Optional, Mapping
from .model import BiasOut

# Map any synonyms to canonical direction words
_CANON = {
    "LAY->BACK": "L2B", "LB": "L2B", "L2B": "L2B", "shorten": "L2B",
    "BACK->LAY": "B2L", "BL": "B2L", "B2L": "B2L", "drift":   "B2L",
    None: "FLAT", "": "FLAT", "FLAT": "FLAT",
}

def _canon_dir(x: Optional[str]) -> str:
    return _CANON.get(str(x).upper() if isinstance(x, str) else x, "FLAT")

def compute_bias(ctx: Mapping, plan: Optional[Mapping] = None) -> BiasOut:
    """
    Compute a robust, always-available bias summary.
    Signals used (if present):
      - fav rank movement (fav_rank -> fav_rank_now)
      - ltp vs quoted odds/price
      - seed from existing direction (ctx or plan)
      - minutes_to_off to scale confidence
    """
    why = []
    score = 0.0
    conf_parts = []

    # 1) Seed from any declared direction (ctx or plan)
    seed = _canon_dir(
        (ctx or {}).get("direction") or (plan or {}).get("direction")
    )
    if seed != "FLAT":
        seed_sign = 1.0 if seed == "L2B" else -1.0
        score += 0.35 * seed_sign
        conf_parts.append(0.35)
        why.append(f"seed={seed}")

    # 2) Fav rank change (smaller rank => stronger favourite => tends to shorten)
    fr = (ctx or {}).get("fav_rank")
    frn = (ctx or {}).get("fav_rank_now")
    if isinstance(fr, (int, float)) and isinstance(frn, (int, float)) and fr != 99 and frn != 99:
        delta = float(fr) - float(frn)  # + if improving to favourite
        if delta != 0.0:
            # scale: ~5 ranks move saturates the contribution
            contrib = max(-1.0, min(1.0, delta / 5.0))
            score += 0.30 * contrib
            conf_parts.append(0.25 * abs(contrib))
            why.append(f"fav_rank Δ={delta:+.2f} -> {'L2B' if delta>0 else 'B2L'}")

    # 3) LTP vs current odds
    ltp = (ctx or {}).get("ltp") or 0.0
    odds = (ctx or {}).get("odds") or (ctx or {}).get("price") or 0.0
    if isinstance(ltp, (int, float)) and isinstance(odds, (int, float)) and ltp > 0 and odds > 0:
        if ltp < odds:  # traded below current → tendency to shorten
            gap = (odds - ltp) / max(odds, 1e-9)
            contrib = max(0.0, min(1.0, gap * 2.0))
            score += 0.25 * contrib
            conf_parts.append(0.20 * contrib)
            why.append(f"ltp({ltp})<odds({odds}) -> L2B")
        elif ltp > odds:  # traded above current → tendency to drift
            gap = (ltp - odds) / max(ltp, 1e-9)
            contrib = max(0.0, min(1.0, gap * 2.0))
            score -= 0.25 * contrib
            conf_parts.append(0.20 * contrib)
            why.append(f"ltp({ltp})>odds({odds}) -> B2L")

    # 4) Time scaling: closer to off → sharper bias confidence
    mto = (ctx or {}).get("minutes_to_off")
    if isinstance(mto, (int, float)):
        urgency = 1.0 - max(0.0, min(mto / 60.0, 1.0))  # 0..1
        conf_parts.append(0.20 * urgency)
        why.append(f"mto={mto:.2f} -> conf+{0.20*urgency:.2f}")

    # Finalize
    score = max(-1.0, min(1.0, score))
    if abs(score) < 0.10:
        dir_ = "FLAT"
    else:
        dir_ = "L2B" if score > 0 else "B2L"

    # Base confidence 0.40 plus accumulated effects, clipped
    conf = max(0.0, min(1.0, 0.40 + sum(conf_parts)))
    return BiasOut(value=score, dir=dir_, conf=conf, why=" | ".join(why))
