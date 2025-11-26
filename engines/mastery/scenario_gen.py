from __future__ import annotations
import random
from dataclasses import dataclass

@dataclass
class Segment:
    type: str      # "drift" | "pullback" | "chop" | "breakout" | "shock"
    ticks: int
    slope: float
    sigma: float
    depth_levels: list[int]  # levels at [0,+1,+2] ticks

def expand_script(script: dict, seed: int) -> list[dict]:
    rnd = random.Random(seed)
    mid = 0.0
    path: list[dict] = []
    for seg in script["segments"]:
        s = Segment(
            type=seg["type"],
            ticks=int(seg["ticks"]),
            slope=float(seg["slope"]),
            sigma=float(seg["sigma"]),
            depth_levels=list(seg["depth"]["levels"])
        )
        step_n = max(1, abs(s.ticks))
        step_sign = 1 if s.ticks >= 0 else -1
        for _ in range(step_n):
            delta = step_sign * abs(s.slope) + rnd.gauss(0, s.sigma)
            mid += delta
            levels = [max(0, int(l + rnd.gauss(0, max(1.0, l*0.05)))) for l in s.depth_levels]
            imbalance = rnd.uniform(-0.2, 0.2)
            path.append({"mid": mid, "levels": levels, "imbalance": imbalance})
    return path
