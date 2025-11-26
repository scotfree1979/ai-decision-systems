#!/usr/bin/env python3
"""
Decision Engine – Phase 1 (TEST/LEARNING/LIVE)
• Reads OC cache + bands to form range/trend/confidence.
• Writes chapters and decisions; emits SIM orders when TEST/LEARNING.
• LIVE routing (real bet placement) arrives in Phase 2.
"""
from __future__ import annotations

import threading, time, logging
from typing import Optional

from engines.decision_engine.constants import (
    ENGINE_TICK_SECS,
    TIER_ACTIVE_MAX,
    TIER_PASSIVE_MAX,
    MIN_CONF_TO_PROPOSE,
)
from engines.decision_engine.adapters import (
    get_or_create_run, inbound_candidates, oc_cache_row, current_oc_label, band_for_label,
    latest_price, anchor_from_cache, ensure_story, upsert_chapter, record_decision, record_order,
    get_market_meta, fetch_available_budget, add_ticks, tick_diff,
)
from engines.decision_engine.range_tracker import RangeTracker
from engines.decision_engine.confidence import compute_confidence
from engines.decision_engine.sizing import propose_stake

# UTC helpers (shared)
try:
    from engines.utils.time_utils import now_utc, minutes_to_off  # type: ignore
except Exception:
    from datetime import datetime, timezone
    def now_utc():
        return datetime.now(timezone.utc)
    def minutes_to_off(_start_iso):
        return None

# Mode source (GUI shim); fallback to LEARNING
try:
    from engines.upgrade_import_patch import get_mode  # type: ignore
except Exception:
    def get_mode() -> str:
        return "learning"



class DecisionEngine:
    def __init__(self):
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._range = RangeTracker()
        self._run_id: Optional[int] = None
        self._ui_mode: str = "learning"   # 'test'|'learning'|'live'
        self._orders_mode: str = "LEARNING"  # 'LEARNING' for TEST/LEARNING; 'LIVE' for live
        # Tunables for Phase‑1 (can be promoted to config later)
        self.min_band_len = 4           # need ≥4 samples in current OCn band
        self.min_ticks_exploratory = 1  # >=1 tick movement → exploratory
        self.min_ticks_partial = 2      # >=2 ticks → partial
        self.min_ticks_full = 3         # >=3 ticks → full
        self.conf_for_full = 0.8        # confidence threshold for FULL decision
        self.conf_for_partial = 0.5     # confidence threshold for PARTIAL
        self.window_min_to_post = (-9999.0, 120.0)  # minutes to off window (very loose in Phase‑1)
        self.stake_ratio = 0.01         # 1% of available budget by default
        self.last_state = {
            "evaluated": 0,
            "decisions": 0,
            "orders": 0,
            "last_decision": None,
            "last_order": None,
            "notes": [],
        }

    def start(self) -> None:
        self._ui_mode = (get_mode() or "learning").lower()
        self._orders_mode = "LIVE" if self._ui_mode == "live" else "LEARNING"
        run_mode = "TEST" if self._ui_mode == "test" else self._orders_mode  # runs.mode captures TEST explicitly
        self._run_id = get_or_create_run(run_mode, notes=f"phase1 engine ({self._ui_mode})")
        logging.info("🚀 DecisionEngine started ui_mode=%s run_mode=%s run_id=%s", self._ui_mode, run_mode, self._run_id)
        t = threading.Thread(target=self._loop, name="DecisionEngine", daemon=True)
        t.start()
        self._thread = t

    def stop(self) -> None:
        self._stop.set()
        if self._thread: self._thread.join(timeout=2)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                logging.exception("[DecisionEngine] tick error: %s", e)
            time.sleep(ENGINE_TICK_SECS)

    def _tick(self) -> None:
        cands = inbound_candidates()
        if not cands: return

        budget = float(fetch_available_budget())

        for c in cands:
            mid = c["marketId"]; sid = int(c["selectionId"]) if not isinstance(c["selectionId"], int) else c["selectionId"]
            cache = oc_cache_row(mid, sid)
            if cache is None: continue

            oc_label = current_oc_label(cache)
            band = band_for_label(cache, oc_label) or []
            cur = latest_price(cache); anchor = anchor_from_cache(cache)

            tier = 'active' if (cur is not None and cur < TIER_ACTIVE_MAX) else ('passive' if (cur is not None and cur <= TIER_PASSIVE_MAX) else 'ignored')
            if tier == 'ignored': continue

            meta_b = get_market_meta(mid)
            mto = minutes_to_off(meta_b.get('marketStartTime')) if meta_b.get('marketStartTime') else None

            story_id = ensure_story(mid, sid, start_label='OC0')
            entry_odds = band[0] if band else (cur or None)
            exit_odds  = band[-1] if band else (cur or None)

            trend_sign = 0
            if band and len(band) >= 2:
                diff = (band[-1] - band[0])
                trend_sign = 1 if diff > 0 else (-1 if diff < 0 else 0)

            upsert_chapter(
                story_id=story_id,
                oc_label=oc_label,
                entry_odds=entry_odds,
                exit_odds=exit_odds,
                band_json=band if band else None,
                tick_pattern=('volatile' if band and len(set(round(x,2) for x in band)) > 4 else ('flat' if trend_sign == 0 else ('drifted' if trend_sign > 0 else 'steamed'))),
                direction_bias=('drifted' if trend_sign > 0 else ('steamed' if trend_sign < 0 else 'flat')),
                position_ratio=None,
                volatility=None,
                minutes_to_post=mto,
                opened_at=None,
                closed_at=None,
            )

            rs = self._range.update(mid, sid, anchor, cur)
            near_edge = False
            if rs.low is not None and rs.high is not None and cur is not None:
                span = max(1, abs(tick_diff(rs.low, rs.high)))
                near_edge = abs(min(abs(tick_diff(cur, rs.low)), abs(tick_diff(rs.high, cur)))) <= max(1, int(round(span * 0.05)))

            conf, target_ticks, direction = compute_confidence(
                tier=tier,
                trend_sign=trend_sign,
                range_breakout=rs.breakout,
                range_breakout_confirmed=rs.breakout_confirmed,
                near_edge=near_edge,
            )
            if conf < MIN_CONF_TO_PROPOSE or cur is None:
                continue

            side = 'LAY' if direction == 'lay_to_back' else 'BACK'
            proposed_odds = add_ticks(cur, +1 if side == 'LAY' else -1)
            breakout = bool(rs.breakout_confirmed)
            stake = propose_stake(available_budget=budget, confidence=conf, breakout=breakout)

            dec_id = record_decision(
                self._run_id or 0, mid, sid,
                signal_type='exploratory', blueprint_match=None,
                confidence=conf, scalp_direction=direction,
                proposed_odds=proposed_odds, proposed_stake=stake,
                notes=f"{oc_label} trend={trend_sign} breakout={rs.breakout}:{rs.breakout_confirmed}",
            )

            suf = f"{int(now_utc().timestamp()) % 100000:05}"
            core = "LAY" if side == "LAY" else "BAC"
            ref = f"{core}_{mid[-4:]}_{sid}_{suf}"

            if self._orders_mode == 'LEARNING':
                record_order(
                    run_id=self._run_id or 0,
                    decision_id=dec_id,
                    customerOrderRef=ref,
                    marketId=mid,
                    selectionId=sid,
                    mode='LEARNING',
                    side=side,
                    entry_odds=proposed_odds,
                    entry_stake=stake,
                    entry_status='queued',
                    unrealized_pnl=0.0,
                )
            else:
                # LIVE routing reserved for Phase 2
                pass
# ─────────────────────────────────────────────────────────────────────────────
# Decision Engine (Phase 1): evaluate() + explain_state()
# Append this block at the end of engines/decision_engine/engine.py
# ─────────────────────────────────────────────────────────────────────────────


    # --- public API -----------------------------------------------------------

    def evaluate(self, mode: str = "TEST") -> dict:
        """
        Scan inbound candidates and make decisions. Returns a dict summary.
        """
        run_id = get_or_create_run(mode, notes="DecisionEngine.evaluate")
        decided = 0
        ordered = 0
        seen = 0
        notes = []

        for row in inbound_candidates():
            seen += 1
            mid = row["marketId"]
            sid = int(row["selectionId"])

            cache = oc_cache_row(mid, sid)
            label = current_oc_label(cache)
            band = band_for_label(cache, label)

            # Band requirements
            if label == "OC0" or not band or len(band) < self.min_band_len:
                continue

            # Compute slope and ticks
            start_v, end_v = float(band[0]), float(band[-1])
            slope = end_v - start_v
            ticks = abs(tick_diff(start_v, end_v))
            direction = "DRIFT" if slope > 0 else ("STEAM" if slope < 0 else "FLAT")

            # Time gate (best‑effort from bets meta)
            mmeta = get_market_meta(mid)
            mto = None
            try:
                # reuse now_utc() from your adapters; compute minutes to off
                start_iso = (mmeta.get("marketStartTime") or "")
                if start_iso:
                    from datetime import datetime, timezone
                    s = start_iso.replace("Z", "+00:00") if "Z" in start_iso else start_iso
                    start_dt = datetime.fromisoformat(s).astimezone(timezone.utc)
                    mto = (start_dt - now_utc()).total_seconds() / 60.0
            except Exception:
                mto = None

            if mto is not None:
                lo, hi = self.window_min_to_post
                if not (lo <= mto <= hi):
                    # out‑of‑window: still write chapter but skip order
                    self._write_story_chapter(mid, sid, label, band, slope, direction, None, mto, opened=False)
                    continue

            # Tiering
            tier = "exploratory"
            if ticks >= self.min_ticks_full:
                tier = "full"
            elif ticks >= self.min_ticks_partial:
                tier = "partial"

            # Confidence from ticks (simple ramp)
            # map [0..min_ticks_full] → [0..1]
            conf = min(1.0, ticks / max(1.0, float(self.min_ticks_full)))
            # sanity bump if monotonic band
            if self._is_monotonic(band):
                conf = min(1.0, conf + 0.1)

            # Record story+chapter snapshot (opened)
            self._write_story_chapter(mid, sid, label, band, slope, direction, conf, mto, opened=True)

            # Persist decision
            decided += 1
            signal_type = tier
            anchor = anchor_from_cache(cache) or get_anchor_from_bets(mid, sid) or start_v
            last_px = latest_price(cache) or end_v
            scalp_side = "LAY" if direction == "DRIFT" else ("BACK" if direction == "STEAM" else "NONE")
            # synthesize proposed odds toward momentum a touch (1 tick)
            proposed_odds = add_ticks(last_px, 1 if scalp_side == "LAY" else (-1 if scalp_side == "BACK" else 0))
            stake = round(fetch_available_budget() * self.stake_ratio, 2)
            decision_id = record_decision(
                run_id=run_id,
                marketId=mid,
                selectionId=sid,
                signal_type=signal_type,
                blueprint_match=None,
                confidence=float(conf),
                scalp_direction=scalp_side,
                proposed_odds=float(proposed_odds),
                proposed_stake=float(stake),
                notes=f"{direction} ticks={ticks} slope={slope:.2f} oc={label}",
            )

            # Decide whether to place an order
            place = False
            if tier == "full" and conf >= self.conf_for_full and scalp_side != "NONE":
                place = True
            elif tier == "partial" and conf >= self.conf_for_partial and scalp_side != "NONE":
                place = True
            # Exploratory: record decision only (no order)

            if place:
                ordered += 1
                order_id = record_order(
                    run_id=run_id,
                    decision_id=decision_id,
                    customerOrderRef=f"DE-{mid}-{sid}-{int(abs(hash(now_utc().isoformat())) % 1_000_000)}",
                    marketId=mid,
                    selectionId=sid,
                    mode=("SIM" if mode.upper() == "TEST" else mode.upper()),
                    side=scalp_side,
                    entry_odds=float(proposed_odds),
                    entry_stake=float(stake),
                    entry_status="queued",
                    unrealized_pnl=0.0,
                )
                self.last_state.update(
                    last_order={"id": order_id, "marketId": mid, "selectionId": sid, "side": scalp_side, "odds": proposed_odds}
                )

            # update last decision snapshot
            self.last_state.update(
                last_decision={
                    "marketId": mid,
                    "selectionId": sid,
                    "signal_type": signal_type,
                    "confidence": round(float(conf), 2),
                    "direction": direction,
                    "oc_label": label,
                    "ticks": ticks,
                }
            )

        # finalize summary
        self.last_state["evaluated"] += seen
        self.last_state["decisions"] += decided
        self.last_state["orders"] += ordered
        return {
            "evaluated": seen,
            "decisions": decided,
            "orders": ordered,
            "notes": notes,
        }

    def explain_state(self) -> str:
        """Return a compact narrative of the most recent engine activity."""
        ls = self.last_state
        last_dec = ls.get("last_decision")
        last_ord = ls.get("last_order")
        parts = [
            f"eval={ls.get('evaluated',0)}",
            f"dec={ls.get('decisions',0)}",
            f"ord={ls.get('orders',0)}",
        ]
        if last_dec:
            parts.append(
                f"last_dec[{last_dec['signal_type']} conf={last_dec['confidence']} dir={last_dec['direction']} "
                f"oc={last_dec['oc_label']} ticks={last_dec['ticks']}]"
            )
        if last_ord:
            parts.append(
                f"last_ord[id={last_ord['id']} side={last_ord['side']} @ {last_ord['odds']}]"
            )
        return " | ".join(parts) or "DecisionEngine: idle (no activity yet)"

    # --- internal helpers -----------------------------------------------------

    def _is_monotonic(self, seq: list[float]) -> bool:
        """Return True if band is strictly monotonic."""
        if len(seq) < 3:
            return False
        up = all(b >= a for a, b in zip(seq, seq[1:]))
        down = all(b <= a for a, b in zip(seq, seq[1:]))
        return up or down

    def _write_story_chapter(
        self,
        marketId: str,
        selectionId: int,
        oc_label: str,
        band: list[float],
        slope: float,
        direction: str,
        confidence: Optional[float],
        minutes_to_post: Optional[float],
        opened: bool,
    ) -> None:
        story_id = ensure_story(marketId, selectionId, start_label=oc_label)
        upsert_chapter(
            story_id=story_id,
            oc_label=oc_label,
            entry_odds=float(band[0]) if opened else None,
            exit_odds=float(band[-1]) if not opened else None,
            band_json=band,
            tick_pattern=("mono" if self._is_monotonic(band) else "mixed"),
            direction_bias=direction.lower(),
            position_ratio=min(1.0, (abs(tick_diff(band[0], band[-1])) / float(self.min_ticks_full or 1))),
            volatility=abs(slope),
            minutes_to_post=minutes_to_post,
            opened_at=now_utc().isoformat() if opened else None,
            closed_at=None if opened else now_utc().isoformat(),
        )


# CLI convenience
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    eng = DecisionEngine()
    eng.start()
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        eng.stop()
        print("\nDecisionEngine stopped.")
