# engines/live/stoploss_engine.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Tuple, Any

# Runtime dependencies
from engines.price_math import calculate_tick_distance, odds_plus_ticks
from engines.mastery import event_sink


# ==========================================================================================
# CONFIGURATION CONSTANTS
# ==========================================================================================

# Absolute kill boundaries (hard fail-safes for runaway markets)
ODDS_MIN = 1.50
ODDS_MAX = 12.00

# Minimum trailing stop width in ticks for PRE-OFF
BASE_MIN_TRAIL_TICKS = 10   # ensures wide enough to avoid chop-out

# Stop Loss Equity (SLEQ) multiplier bounds
SLEQ_MIN = 0.00
SLEQ_MAX = 0.25             # max +25% widening

# ==========================================================================================
# DATA STRUCTURES
# ==========================================================================================

@dataclass
class SLEQState:
    sleq: float = 0.0  # grows with good stops, shrinks with negative stops

    def widen(self, ticks: int) -> int:
        """Return ticks widened by SLEQ (dynamic trailing stop scaling)."""
        mult = max(SLEQ_MIN, min(self.sleq, SLEQ_MAX))
        widened = int(ticks * (1.0 + mult))
        return max(widened, 1)  # never zero


@dataclass
class ParentState:
    parent_id: int
    entry_side: str
    entry_odds: float
    entry_stake: float


# ==========================================================================================
# CORE STOP-LOSS ENGINE
# ==========================================================================================

class StopLossEngine:
    """
    Unified dynamic trailing stop-loss engine for both Legacy and MicroScalper.

    Responsibilities:
      - Boundary stop-loss (ODDS_MIN / ODDS_MAX)
      - Dynamic trailing stop (tick-based)
      - Positive vs negative classification
      - Stop-loss equity scaling
      - Emits decision events for Overwatcher
      - Predictable, non-duplicate DB writes (router handles DB)
    """

    def __init__(self):
        # SLEQ per (marketId, selectionId)
        self.sleq_map: Dict[Tuple[str, str], SLEQState] = {}

    # ----------------------------------------------------------------------
    # ███ PUBLIC API (called from Overwatcher each tick)
    # ----------------------------------------------------------------------
    def evaluate(
        self,
        parent: ParentState,
        mid: str,
        sid: str,
        current_odds: float,
        oc_phase: int
    ) -> Optional[Dict[str, Any]]:
        """
        Return a STOPLOSS plan dict if a stop-loss should fire, else None.
        This does not place any orders — Overwatcher/live_router handle that part.
        """

        key = (mid, sid)
        sleq = self.sleq_map.setdefault(key, SLEQState())

        entry_side = parent.entry_side.upper()
        entry_odds = float(parent.entry_odds)
        stake = float(parent.entry_stake)
        px = float(current_odds)

        # ------------------------------------------------------------------
        # 1. ABSOLUTE BOUNDARY STOP (hard fail-safe)
        # ------------------------------------------------------------------
        if px <= ODDS_MIN or px >= ODDS_MAX:
            return self._emit_stoploss(
                parent, mid, sid, px,
                reason="BOUNDARY",
                is_positive=(px < entry_odds if entry_side == "LAY" else px > entry_odds),
                sleq=sleq
            )

        # ------------------------------------------------------------------
        # 2. TRAILING STOP (PRE-OFF & IN-PLAY behave the same except positive classification)
        # ------------------------------------------------------------------

        ticks = abs(calculate_tick_distance(entry_odds, px))

        # No trailing until we have moved at least *base minimum ticks*
        if ticks < BASE_MIN_TRAIL_TICKS:
            return None

        # base trail = 10% of movement, minimum BASE_MIN_TRAIL_TICKS
        trail = max(int(0.10 * ticks), BASE_MIN_TRAIL_TICKS)

        # widen with SLEQ
        final_trail = sleq.widen(trail)

        # Stop price = current ± final_trail ticks
        stop_odds = self._calc_stop_price(entry_side, px, final_trail)

        # Determine if we crossed the trailing threshold
        crossed = self._stop_crossed(entry_side, px, stop_odds, entry_odds)

        if not crossed:
            return None

        # ------------------------------------------------------------------
        # 3. CLASSIFICATION POSITIVE / NEGATIVE
        # ------------------------------------------------------------------
        is_positive = (px < entry_odds) if entry_side == "LAY" else (px > entry_odds)

        return self._emit_stoploss(
            parent, mid, sid, px,
            reason="TRAILING",
            is_positive=is_positive,
            sleq=sleq
        )

    # ======================================================================================
    # INTERNAL UTILITY FUNCTIONS
    # ======================================================================================

    def _calc_stop_price(self, entry_side: str, px: float, ticks: int) -> float:
        """Compute stop price relative to current px."""
        if entry_side == "LAY":
            # LAY entry → BACK to stop out → stop is BELOW current price
            return odds_plus_ticks(px, -ticks)
        else:
            # BACK entry → LAY to stop out → stop is ABOVE current price
            return odds_plus_ticks(px, ticks)

    def _stop_crossed(self, entry_side: str, px: float, stop_odds: float, entry_odds: float) -> bool:
        """Check whether price has moved past stop trigger."""
        if entry_side == "LAY":
            return px <= stop_odds     # price must fall below trailing line
        else:
            return px >= stop_odds     # price must rise above trailing line

    # ------------------------------------------------------------------
    # EMIT STOPLOSS EVENT TO OVERWATCHER + MASTERY
    # ------------------------------------------------------------------
    def _emit_stoploss(
        self,
        parent: ParentState,
        mid: str,
        sid: str,
        px: float,
        reason: str,
        is_positive: bool,
        sleq: SLEQState
    ) -> Dict[str, Any]:

        # Update SLEQ (simple reinforcement):
        # Positive stop → increase SLEQ
        # Negative stop → reduce SLEQ
        if is_positive:
            sleq.sleq = min(SLEQ_MAX, sleq.sleq + 0.02)
        else:
            sleq.sleq = max(SLEQ_MIN, sleq.sleq - 0.02)

        classification = "TS-POS" if is_positive else "TS-NEG"

        event = {
            "type": "stop_loss_triggered",
            "parent_id": parent.parent_id,
            "marketId": mid,
            "selectionId": sid,
            "entry_side": parent.entry_side,
            "entry_odds": parent.entry_odds,
            "current_odds": px,
            "reason": reason,
            "classification": classification,
            "sleq": sleq.sleq,
        }

        # Push learning event
        try:
            event_sink.on_decision(event)
        except Exception:
            pass

        return event
