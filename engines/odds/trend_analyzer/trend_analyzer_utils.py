from __future__ import annotations
from typing import Iterable, Optional


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def safe_delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    try:
        return float(b) - float(a)
    except Exception:
        return None


def norm_slope(delta: Optional[float], odds: Optional[float], spread: Optional[float]) -> float:
    if delta is None or odds is None:
        return 0.0
    scale = max(odds, 1.0) * max(spread or 1.0, 1.0)
    if scale <= 0:
        scale = 1.0
    return float(delta) / float(scale)


def pct_consensus(signs: Iterable[int]) -> float:
    s = list(signs)
    if not s:
        return 0.0
    pos = sum(1 for v in s if v > 0)
    neg = sum(1 for v in s if v < 0)
    return (pos - neg) / max(len(s), 1)
