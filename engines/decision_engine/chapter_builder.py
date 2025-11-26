#!/usr/bin/env python3
"""
Decision Engine — Chapter helpers (pure functions).

A "chapter" is a specific OC window (OC1..OC20) within a story.
We summarise band samples into stable features used for decisions and logs.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from statistics import median, pstdev
from typing import Dict, List, Any


@dataclass
class ChapterStats:
    oc_label: str
    n_samples: int
    last: float
    med: float
    stdev: float
    drift_from_anchor: float     # last - anchor
    drift_ticks: float | None    # optional (requires price_math)
    bias: float                  # sign(med - anchor) in [-1, 1]


def band_is_ready(samples: List[float], min_len: int = 4) -> bool:
    """Guard: bands should be a list with at least `min_len` samples."""
    try:
        return isinstance(samples, list) and len(samples) >= min_len
    except Exception:
        return False


def _safe_stdev(values: List[float]) -> float:
    if not values or len(values) == 1:
        return 0.0
    try:
        return float(pstdev(values))
    except Exception:
        return 0.0


def _tick_diff_safe(a: float, b: float) -> float | None:
    """
    Optional tick distance (requires engines.price_math). We keep it optional
    so chapter stats can be computed without that import in tests.
    """
    try:
        from engines.price_math import tick_diff  # lazy import
        return float(tick_diff(a, b))
    except Exception:
        return None


def compute_chapter_stats(anchor: float, samples: List[float], oc_label: str) -> ChapterStats:
    """
    Summarise samples for a chapter window against the anchor.
    - last/med/stdev
    - drift vs anchor (raw and ticks if available)
    - bias sign in [-1, 1]
    """
    if not samples:
        samples = []
    last = float(samples[-1]) if samples else float(anchor)
    medv = float(median(samples)) if samples else float(anchor)
    sd = _safe_stdev(samples)
    drift = last - float(anchor)
    bias = 0.0
    if medv != anchor:
        bias = 1.0 if medv > anchor else -1.0

    return ChapterStats(
        oc_label=oc_label,
        n_samples=len(samples),
        last=last,
        med=medv,
        stdev=sd,
        drift_from_anchor=drift,
        drift_ticks=_tick_diff_safe(last, float(anchor)),
        bias=bias,
    )


def as_dict(stats: ChapterStats) -> Dict[str, Any]:
    """Convenience: convert ChapterStats dataclass to a plain dict."""
    return asdict(stats)


__all__ = [
    "ChapterStats",
    "band_is_ready",
    "compute_chapter_stats",
    "as_dict",
]
