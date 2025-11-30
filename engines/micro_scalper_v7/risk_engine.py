# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py
# 🔎 SEARCH: class RiskEngine
# 📆 PATCHED: 2025-12-02 — Full v7 Risk-MicroScalper rewrite
# ==============================================================================

from typing import Dict, Any, Optional

class RiskEngine:
    """
    Risk-MicroScalper (Engine B)
    ----------------------------------
    Activated ONLY when:
        • a Legacy parent exists for this runner
        • parent_id is passed into ctx["legacy_parent_id"]

    Responsibilities:
        1. Immediately shadow the parent with a micro scalp
        2. Continue scalping on each tick while price moves
           • against → hedge_tick (direction=BACK->LAY for LAY parent, etc.)
           • favour  → stack_tick
        3. Flip direction whenever price crosses the parent entry
        4. Respect mode (AGGRESSIVE / MODERATE / CONSERVATIVE)
        5. Apply stop_ticks and entry_ticks from direction-engine
        6. Boundary exits (1.5 / 12.0)
        7. Use SLEQ (from ctx) to widen/narrow child stake
    """

    def __init__(self, parent_id: int):
        self.parent_id = parent_id
        self.attached = False
        self.entry_px = None          # parent entry odds
        self.entry_side = None        # "LAY" or "BACK"
        self.last_px = None
        self.active_plan = None       # the current child
        self.mode = "MODERATE"

    # ----------------------------------------------------------------------
    # ENTRYPOINT: called each tick by MSC engine
    # ----------------------------------------------------------------------
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:

        # If context is for a different parent → ignore
        if ctx.get("legacy_parent_id") != self.parent_id:
            return None

        px = float(ctx.get("current_price") or 0)
        if px <= 0:
            return None

        # direction-engine decision (already built)
        de = ctx.get("msc_decision") or {}
        entry_ticks = int(de.get("entry_ticks", 2))
        stop_ticks = int(de.get("stop_ticks", 4))
        self.mode  = de.get("mode", "MODERATE")

        # parent metadata
        self.entry_side = (ctx.get("legacy_entry_side") or "").upper()
        self.entry_px   = float(ctx.get("legacy_entry_odds") or px)

        # SLEQ multiplier
        stake_mult = float(ctx.get("msc_multiplier") or 1.0)

        # FIRST ATTACH → open micro scalp immediately
        if not self.attached:
            self.attached = True
            return self._initial_shadow(px, entry_ticks, stop_ticks, stake_mult, ctx)

        # MONITOR active plan
        if self.active_plan:
            out = self._monitor(px, entry_ticks, stop_ticks)
            if out:
                return out

        # CONTINUOUS SCALPING
        return self._scalp_tick(px, entry_ticks, stop_ticks, stake_mult, ctx)

    # ----------------------------------------------------------------------
    # INITIAL SHADOW-TRADE
    # ----------------------------------------------------------------------
    def _initial_shadow(self, px, entry_ticks, stop_ticks, stake_mult, ctx):
        tick = ctx.get("tick_size_fn")(px)

        # Shadow in same DIRECTION as parent
        if self.entry_side == "LAY":
            direction = "LAY"
            entry_px = self.entry_px + tick
        else:
            direction = "BACK"
            entry_px = self.entry_px - tick

        size = self._stake(ctx, stake_mult, entry_ticks)

        plan = {
            "enter": True,
            "role": "CHILD",
            "family": "MSC_RISK",
            "direction": direction,
            "parent_id": self.parent_id,
            "target_ticks": entry_ticks,
            "stop_ticks": stop_ticks,
            "size": size,
            "px": entry_px,
            "why": "risk_initial_shadow"
        }

        self.last_px = px
        self.active_plan = plan
        return plan

    # ----------------------------------------------------------------------
    # TICK-BASED CONTINUOUS SCALPING
    # ----------------------------------------------------------------------
    def _scalp_tick(self, px, entry_ticks, stop_ticks, stake_mult, ctx):
        """
        When price moves:
            • in favour of parent → stack_tick
            • against parent     → hedge_tick
        """
        tick = ctx.get("tick_size_fn")(px)

        moving_favour = (
            self.entry_side == "LAY"  and px < self.entry_px
        ) or (
            self.entry_side == "BACK" and px > self.entry_px
        )

        moving_against = not moving_favour

        # If crossing → flip direction
        if self._crossed_parent(px):
            direction = "LAY" if px > self.entry_px else "BACK"
            size = self._stake(ctx, stake_mult, entry_ticks)
            return self._open_new(direction, px, entry_ticks, stop_ticks, size,
                                  reason="risk_cross")

        if moving_favour:
            direction = "LAY" if self.entry_side == "LAY" else "BACK"
            size = self._stake(ctx, stake_mult, entry_ticks)
            return self._open_new(direction, px, entry_ticks, stop_ticks, size,
                                  reason="risk_stack")
        else:
            # hedging tick
            direction = "BACK" if self.entry_side == "LAY" else "LAY"
            size = self._stake(ctx, stake_mult, entry_ticks)
            return self._open_new(direction, px, entry_ticks, stop_ticks, size,
                                  reason="risk_hedge")

    # ----------------------------------------------------------------------
    # MONITOR ACTIVE MICRO CHILD
    # ----------------------------------------------------------------------
    def _monitor(self, px, entry_ticks, stop_ticks):
        entry_px = self.active_plan["px"]
        direction = self.active_plan["direction"]

        tick = abs(px - entry_px)
        if tick <= 0:
            return None

        # PROFIT target
        if (direction == "LAY" and px >= entry_px + tick * entry_ticks) or \
           (direction == "BACK" and px <= entry_px - tick * entry_ticks):
            return self._exit("risk_profit", H=True)

        # STOP LOSS
        if (direction == "LAY" and px <= entry_px - tick * stop_ticks) or \
           (direction == "BACK" and px >= entry_px + tick * stop_ticks):
            return self._exit("risk_stop", H=False)

        # BOUNDARIES 1.5 / 12.0
        if px >= 12.0 or px <= 1.5:
            parent = self.entry_side
            H = True if (px >= 12 and parent == "LAY") or \
                       (px <= 1.5 and parent == "BACK") else False
            return self._exit("risk_boundary", H=H)

        return None

    # ----------------------------------------------------------------------
    # OPEN A NEW MICRO SCALP
    # ----------------------------------------------------------------------
    def _open_new(self, direction, px, entry_ticks, stop_ticks, size, reason):
        plan = {
            "enter": True,
            "role": "CHILD",
            "family": "MSC_RISK",
            "parent_id": self.parent_id,
            "direction": direction,
            "target_ticks": entry_ticks,
            "stop_ticks": stop_ticks,
            "size": size,
            "px": px,
            "why": reason,
        }
        self.active_plan = plan
        self.last_px = px
        return plan

    # ----------------------------------------------------------------------
    # EXIT PLAN
    # ----------------------------------------------------------------------
    def _exit(self, reason, H):
        plan = {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "family": "MSC_RISK",
            "parent_id": self.parent_id,
            "direction": self.active_plan["direction"],
            "why": reason,
            "exit_kind": "H" if H else "S"
        }
        self.active_plan = None
        self.last_px = None
        return plan

    # ----------------------------------------------------------------------
    # HELPERS
    # ----------------------------------------------------------------------
    def _crossed_parent(self, px):
        if self.last_px is None:
            self.last_px = px
            return False
        crossed = (self.last_px <= self.entry_px < px) or \
                  (self.last_px >= self.entry_px > px)
        self.last_px = px
        return crossed

    def _stake(self, ctx, stake_mult, entry_ticks):
        base = ctx["dynamic_stake_fn"](
            family="MSC_RISK",
            confidence=ctx["msc_decision"].get("win_prob"),
            vol_state=self.mode,
            expected_ticks=entry_ticks,
        )
        return round(float(base) * float(stake_mult), 2)

# === PATCH END ================================================================
