#!/usr/bin/env python3
"""
Decision Engine — Story helpers (pure functions).

A "story" tracks a single market over its OC timeline.
This module is DB-agnostic: it just computes keys/labels/metadata.
Adapters handle persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class StoryState:
    """Lightweight in-memory representation of a story (one market)."""
    run_id: int
    marketId: str
    started_at: str                  # ISO8601 UTC when story first seen
    story_id: Optional[int] = None   # filled by adapters.ensure_story(...)
    meta: Dict[str, Any] = None      # venue/meeting/start time/race info


def bootstrap_story(run_id: int, market: Dict[str, Any]) -> StoryState:
    """
    Build the initial story state for a market. The caller (engine) should pass
    this to adapters.ensure_story(...) which will return/attach story_id.
    `market` is whatever came from your get_markets() call.
    """
    meta = {
        "marketStartTime": market.get("marketStartTime"),
        "marketName": market.get("marketName"),
        "venue": market.get("venue") or market.get("courseName"),
        "totalRunners": len(market.get("runners") or []),
    }
    return StoryState(
        run_id=run_id,
        marketId=market.get("marketId", ""),
        started_at=now_utc_iso(),
        story_id=None,
        meta=meta,
    )


def oc_label_from_minutes(mto_minutes: float, schedule: Dict[int, int]) -> str:
    """
    Given minutes-to-off (mto_minutes) and an {OCn: threshold_minutes} schedule,
    return the current label ("OC0" pre-window, "OC1".."OC20" otherwise).
    """
    if mto_minutes is None:
        return "OC0"
    # Find the largest n whose threshold >= mto (remember schedule stores minutes BEFORE off, with negatives after)
    due = [n for n, thr in schedule.items() if n >= 1 and mto_minutes <= thr]
    return f"OC{max(due)}" if due else "OC0"


def story_key(run_id: int, marketId: str) -> str:
    """Stable key to identify a story in logs/metrics (not the DB PK)."""
    return f"{run_id}:{marketId}"


__all__ = [
    "StoryState",
    "bootstrap_story",
    "oc_label_from_minutes",
    "story_key",
]
