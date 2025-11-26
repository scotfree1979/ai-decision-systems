from __future__ import annotations
import csv, time
from typing import Dict, List, Optional
from .types import MarketTick, MarketView, RunnerView
from . import direction_for, load_config

"""
Standalone demo ticker.

Input: a CSV with columns:
 ts,market_id,selection_id,oc_index,best_back_odds,anchor_odds,liquidity_flag
Additionally, for market view (optional XO/BIF), provide a second CSV listing
 per-tick market snapshot rows:
 ts,market_id,selection_id,best_back_odds

Usage (example):
  python -m trend_analyzer.ticker ticks.csv market_view.csv

This prints one line per input tick with direction/confidence/basis.
"""

import sys

def _load_ticks(path: str) -> List[MarketTick]:
    out: List[MarketTick] = []
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            out.append(MarketTick(
                ts=float(row["ts"]),
                market_id=row["market_id"],
                selection_id=int(row["selection_id"]),
                oc_index=int(row["oc_index"]),
                best_back_odds=(float(row["best_back_odds"]) if row.get("best_back_odds") else None),
                anchor_odds=(float(row["anchor_odds"]) if row.get("anchor_odds") else None),
                liquidity_flag=(row.get("liquidity_flag") or "NORMAL")
            ))
    return out


def _load_market_views(path: Optional[str]) -> Dict[tuple[float,str], MarketView]:
    views: Dict[tuple[float,str], MarketView] = {}
    if not path:
        return views
    rows_by_key: Dict[tuple[float,str], List[RunnerView]] = {}
    with open(path, newline="") as f:
        r = csv.DictReader(f)
        for row in r:
            ts = float(row["ts"])
            mid = row["market_id"]
            sid = int(row["selection_id"])
            o = float(row["best_back_odds"]) if row.get("best_back_odds") else None
            rows_by_key.setdefault((ts, mid), []).append(RunnerView(sid, o))
    for (ts, mid), runners in rows_by_key.items():
        # anchor favorite unknown here; set to the favorite at first snapshot time per market
        runners_sorted = sorted([r for r in runners if r.best_back_odds is not None], key=lambda r: (r.best_back_odds, r.selection_id))
        anchor_fav = runners_sorted[0].selection_id if runners_sorted else None
        views[(ts, mid)] = MarketView(ts, mid, runners, anchor_fav)
    return views


def main():
    if len(sys.argv) < 2:
        print("Usage: python -m trend_analyzer.ticker ticks.csv [market_view.csv]")
        sys.exit(1)
    ticks_path = sys.argv[1]
    views_path = sys.argv[2] if len(sys.argv) > 2 else None
    ticks = _load_ticks(ticks_path)
    views = _load_market_views(views_path)

    for t in ticks:
        view = views.get((t.ts, t.market_id))
        d = direction_for(t, view)
        b = d.basis
        print(f"[{t.ts:.3f}] MKT={t.market_id} SEL={t.selection_id} oc={t.oc_index} dir={d.direction} conf={d.confidence:.2f}")
        print(f"  basis: band={b.band_used} slope={b.slope:.3f} vol={b.vol_regime} range=[{b.range_low},{b.range_high}] pos={b.pos_in_range} d2b={b.distance_to_boundary}")
        print(f"         phase={b.phase} suff={d.sufficiency} events={'|'.join(b.events) or 'NONE'} health={b.health} oc_miss={b.oc_missing_streak}")
        if b.confidence_breakdown:
            parts = ' '.join([f"{k}={v:.2f}" for k,v in b.confidence_breakdown.items()])
            print(f"         conf_parts: {parts}")

if __name__ == "__main__":
    main()
