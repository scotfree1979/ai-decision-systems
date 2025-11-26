from __future__ import annotations
from dataclasses import dataclass
from typing import List, Optional
from .types import MarketView

@dataclass
class XODetector:
    xo_epsilon: float
    xo_hold_ms: int

    def detect(self, sel_id: int, view_prev: Optional[MarketView], view_now: MarketView) -> List[str]:
        events: List[str] = []
        try:
            def odds_of(view: MarketView, sid: int) -> Optional[float]:
                for r in view.runners:
                    if r.selection_id == sid:
                        return r.best_back_odds
                return None
            # rank mapping
            def rank(view: MarketView, sid: int) -> Optional[int]:
                sorted_r = sorted([r for r in view.runners if r.best_back_odds is not None], key=lambda r: (r.best_back_odds, r.selection_id))
                for i, r in enumerate(sorted_r, 1):
                    if r.selection_id == sid:
                        return i
                return None

            if view_prev is None:
                return events
            # nearest neighbor by rank
            r_prev = rank(view_prev, sel_id)
            r_now = rank(view_now, sel_id)
            if r_prev is None or r_now is None:
                return events
            # compare odds with current favorite too
            fav_now = view_now.runners[0].selection_id if view_now.runners and view_now.runners[0].best_back_odds is not None else view_now.anchor_favorite_id
            if fav_now is not None and fav_now != sel_id:
                oR_prev = odds_of(view_prev, sel_id)
                oF_prev = odds_of(view_prev, fav_now)
                oR_now = odds_of(view_now, sel_id)
                oF_now = odds_of(view_now, fav_now)
                if None not in (oR_prev, oF_prev, oR_now, oF_now):
                    prev_diff = (oR_prev - oF_prev)
                    now_diff = (oR_now - oF_now)
                    if prev_diff * now_diff < 0 and abs(now_diff) > self.xo_epsilon:
                        events.append("XO_FAV_DOWN" if now_diff < 0 else "XO_FAV_UP")
            # BIF/FL/FR
            of = view_now.anchor_favorite_id
            cur_fav = min([r for r in view_now.runners if r.best_back_odds is not None], key=lambda r: (r.best_back_odds, r.selection_id), default=None)
            if cur_fav:
                if of is not None and cur_fav.selection_id != of:
                    events.append("FL")  # favorite lost
                    if cur_fav.selection_id == sel_id:
                        events.append("BIF_SELF")
                elif of is not None and cur_fav.selection_id == of:
                    events.append("FR")
            # Favorite cluster
            if len([r for r in view_now.runners if r.best_back_odds is not None]) >= 2:
                odds_sorted = sorted([r.best_back_odds for r in view_now.runners if r.best_back_odds is not None])
                if odds_sorted[1] - odds_sorted[0] <= 0.10:  # eps default; refined in config at integration
                    events.append("FC")
        except Exception:
            pass
        return events
