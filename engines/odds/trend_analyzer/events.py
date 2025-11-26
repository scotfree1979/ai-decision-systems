# =========================================
# 📍 TARGET: engines/odds/trend_analyzer/events.py
# 🔎 SEARCH: @dataclass
# =========================================
from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from .types import MarketView, RunnerView

@dataclass
class XODetector:
    xo_epsilon: float
    xo_hold_ms: int
    fav_cluster_eps: float
    bif_hold_ms: int
    fav_flip_hold_ms: int

    # per-market state
    def __post_init__(self):
        self._last_fav: dict[str, Optional[int]] = {}
        self._fav_stable_since: dict[str, float] = {}
        self._of_lost: dict[str, bool] = {}

    def _ranked(self, view: MarketView) -> List[RunnerView]:
        return sorted(
            [r for r in view.runners if r.best_back_odds is not None],
            key=lambda r: (r.best_back_odds, r.selection_id),
        )

    def _odds_of(self, view: MarketView, sid: int) -> Optional[float]:
        for r in view.runners:
            if r.selection_id == sid:
                return r.best_back_odds
        return None

    def detect(self, sel_id: int, view_prev: Optional[MarketView], view_now: MarketView) -> List[str]:
        events: List[str] = []
        mid = view_now.market_id
        ts = view_now.ts
        ranked_now = self._ranked(view_now)
        cur_fav = ranked_now[0].selection_id if ranked_now else None
        of = view_now.anchor_favorite_id

        # track favorite stability per market
        last = self._last_fav.get(mid)
        if cur_fav != last:
            self._last_fav[mid] = cur_fav
            self._fav_stable_since[mid] = ts
        stable_ms = int((ts - self._fav_stable_since.get(mid, ts)) * 1000)

        # --- XO vs favorite (use ranked favorites, not insertion order) ---
        if view_prev is not None and cur_fav is not None:
            ranked_prev = self._ranked(view_prev)
            fav_prev = ranked_prev[0].selection_id if ranked_prev else cur_fav
            oR_prev = self._odds_of(view_prev, sel_id)
            oF_prev = self._odds_of(view_prev, fav_prev)
            oR_now  = self._odds_of(view_now, sel_id)
            oF_now  = self._odds_of(view_now, cur_fav)
            if None not in (oR_prev, oF_prev, oR_now, oF_now):
                prev_diff = (oR_prev - oF_prev)
                now_diff  = (oR_now  - oF_now)
                if prev_diff * now_diff < 0 and abs(now_diff) > self.xo_epsilon:
                    events.append("XO_FAV_DOWN" if now_diff < 0 else "XO_FAV_UP")

        # --- Favorite transitions (debounced) + BIF ---
        if cur_fav is not None and of is not None:
            if cur_fav != of:
                # Original favorite lost (only after it holds for fav_flip_hold_ms)
                if stable_ms >= self.fav_flip_hold_ms and not self._of_lost.get(mid, False):
                    events.append("FL")
                    self._of_lost[mid] = True
                # Backed-in favorite for this runner (if self becomes fav and holds)
                if sel_id == cur_fav and stable_ms >= self.bif_hold_ms:
                    events.append("BIF_SELF")
            else:
                # Original favorite regained (only if it had been lost and now holds)
                if self._of_lost.get(mid, False) and stable_ms >= self.fav_flip_hold_ms:
                    events.append("FR")
                    self._of_lost[mid] = False

        # --- Favorite cluster tightness (FC) ---
        if len(ranked_now) >= 2:
            o0 = ranked_now[0].best_back_odds
            o1 = ranked_now[1].best_back_odds
            if o0 is not None and o1 is not None and (o1 - o0) <= self.fav_cluster_eps:
                events.append("FC")

        return events
