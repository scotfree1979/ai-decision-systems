from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import math, random
from engines.config_paths import connect_db, autoscalp_db

@dataclass
class RunnerPlan:
    selectionId: str
    runnerName: str
    base_odds: float  # realistic pre-off anchor

@dataclass
class MarketPlan:
    marketId: str
    off_at_utc: datetime
    runners: list[RunnerPlan]

# --- helpers -------------------------------------------------------------

def _starting_book(n: int, rng: random.Random) -> list[float]:
    """
    Produce a realistic pre-off book of decimal odds for n runners:
    - Dirichlet strengths -> probabilities
    - Convert to odds and clamp to [1.8, 80]
    - Re-scale so favourite ~ 3–5, longshots 30–80
    """
    # Dirichlet strengths (bigger head for favs)
    alpha = [2.0] + [1.0] * (n - 1)
    strengths = [rng.gammavariate(a, 1.0) for a in alpha]
    total = sum(strengths)
    probs = [s / total for s in strengths]

    # Overround ~8–12%
    over = 1.08 + 0.04 * rng.random()
    probs = [p * over for p in probs]

    # Convert to odds and clamp
    odds = [max(1.8, min(80.0, 1.0 / max(1e-6, p))) for p in probs]

    # Normalise favourite and tails mildly
    fav = odds[0]
    scale = (4.2 + 1.2 * rng.random()) / fav  # push fav ~[4,5.4]
    odds = [max(1.8, min(80.0, o * scale)) for o in odds]
    return odds

# --- public --------------------------------------------------------------

def build_compressed_day(
    now: datetime,
    real_duration_sec: int,
    virtual_span_minutes: int = 300,
    n_markets: int = 27,
    seed: int = 42,
) -> tuple[list[MarketPlan], float]:
    """
    Build a "day" compressed into `real_duration_sec` seconds.

    Returns (markets, speed_min_per_sec), where `speed` converts real seconds
    into virtual minutes.
    """
    assert n_markets > 0
    rng = random.Random(seed)

    # virtual clock speed: minutes per real second
    speed_min_per_sec = float(virtual_span_minutes) / max(1, real_duration_sec)

    # equally spaced off-times across the virtual span
    markets: list[MarketPlan] = []
    for i in range(n_markets):
        off_vmin = int(i * (virtual_span_minutes / n_markets))   # virtual minute in [0, span)
        off_at = (now + timedelta(seconds=off_vmin / speed_min_per_sec)).replace(tzinfo=timezone.utc)

        # 6–14 runners
        n = rng.randint(6, 12 + rng.randint(0,2))
        base_odds = _starting_book(n, rng)
        runners = [
            RunnerPlan(
                selectionId=str(200000 + i*100 + j),
                runnerName=f"R{j+1:02}",
                base_odds=base_odds[j],
            )
            for j in range(n)
        ]
        markets.append(MarketPlan(marketId=f"MKT-{off_at:%Y%m%d}-{i+1:03}", off_at_utc=off_at, runners=runners))

    return markets, speed_min_per_sec
