# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py
# 🔎 SEARCH: class RiskEngine
# 📆 PATCHED: 2025-12-02 — Full v7 Risk-MicroScalper rewrite
# ==============================================================================
from engines.micro_scalper_v7.event_receiver import get_engine_outcomes
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

# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def __init__(self, parent_id: int):
# 📆 PATCHED: 2026-02-14 — BUS-Compatible RiskEngine Constructor
# ============================================================================

    def __init__(self):
        # parent_id now resolved dynamically from ctx["legacy_parent_id"]
        self.parent_id = None
        self.attached = False
        self.entry_px = None
        self.entry_side = None
        self.last_px = None
        self.active_plan = None
        self.mode = "MODERATE"

# === PATCH END ==============================================================
        # --------------------------------------------------
        # RISC lifecycle tracking (one-at-a-time)
        # --------------------------------------------------
        self.risc_parent_id = None
        self.risc_cycle_active = False


# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: class RiskEngine(
# 🆕 ADD: enable toggle + legacy guard
# 📆 PATCHED: 2026-02-12
# ============================================================================

    # Bus-level toggles
    ENABLE_FOR_LEGACY = True
    ENABLE_FOR_EXPLORATORY = False  # phase 1 constraint

    # --------------------------------------------------
    # ORDER SNAPSHOT HELPERS (DB-truth via ctx)
    # --------------------------------------------------
    def _orders(self, ctx):
        return ctx.get("orders_by_runner") or []

    def _legacy_parent_matched(self, ctx):
        return any(
            o.get("family") == "LEGACY"
            and o.get("role") == "PARENT"
            and o.get("entry_status") == "MATCHED"
            for o in self._orders(ctx)
        )

    def _legacy_child_matched(self, ctx):
        return any(
            o.get("family") == "LEGACY"
            and o.get("role") == "CHILD"
            and o.get("exit_status") == "MATCHED"
            for o in self._orders(ctx)
        )

    def _risc_parent_and_child_matched(self, ctx):
        if not self.risc_parent_id:
            return False

        parent_matched = any(
            o.get("family") == "MSC_RISK"
            and o.get("role") == "PARENT"
            and o.get("id") == self.risc_parent_id
            and o.get("entry_status") == "MATCHED"
            for o in self._orders(ctx)
        )

        child_matched = any(
            o.get("family") == "MSC_RISK"
            and o.get("role") == "CHILD"
            and o.get("hedge_of") == self.risc_parent_id
            and o.get("exit_status") == "MATCHED"
            for o in self._orders(ctx)
        )

        return parent_matched and child_matched



    def _no_signal(self, ctx: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
        from engines.mastery.event_sink import emit

        payload = {
            "enter": False,
            "blocked": True,
            "engine": "MSC_RISK",
            "reason": reason,
            "re_eval": True,
        }

        try:
            emit("msc_risk.no_signal", payload)
        except Exception:
            pass

        return payload



# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py (inside class RiskEngine)
# 📆 PATCHED: 2025-12-06 — OC6 flatten/exit logic
# ============================================================================

    def _terminate_oc6(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """
        OC6 termination:
          • Immediately flatten legacy parent at market px
          • exit_kind = H (right side) or S (wrong side)
          • stake = exposure-neutralising
          • direction = opposite legacy entry
        """
        px = float(ctx.get("current_price") or ctx.get("px") or 0.0)
        if px <= 0:
            return None

        # -----------------------------
        # 1) Parent metadata
        # -----------------------------
        parent_side = (ctx.get("legacy_entry_side") or "").upper()  # LAY/BACK
        anchor      = float(ctx.get("legacy_entry_odds") or px)
        parent_stk  = float(ctx.get("legacy_entry_stake") or 0.0)

        if parent_stk <= 0:
            return None

        # -----------------------------
        # 2) Determine flatten direction
        # -----------------------------
        if parent_side == "LAY":
            # Legacy opened with LAY → flatten with BACK
            flatten_side  = "BACK"
            flatten_stake = parent_stk   # exposure-neutralising
            # Anchor logic:
            # px > anchor → right side → H
            exit_kind = "H" if px > anchor else "S"

        else:
            # Legacy opened with BACK → flatten with LAY
            flatten_side  = "LAY"
            flatten_stake = parent_stk
            # Anchor logic:
            # px < anchor → right side → H
            exit_kind = "H" if px < anchor else "S"

        # -----------------------------
        # 3) Build flatten child plan
        # -----------------------------
        plan = {
            "enter": True,
            "close": True,               # explicit terminal close
            "role": "CHILD",
            "family": "MSC_RISK",
            "parent_id": self.parent_id,
            "direction": flatten_side,
            "size": float(flatten_stake),
            "px": float(px),             # EXACT px – option A
            "exit_kind": exit_kind,
            "why": f"oc6_flatten_{exit_kind}",
        }

        # -----------------------------
        # 4) Detach and end micro-cycle
        # -----------------------------
        self.active_plan = None
        self.attached    = False
        self.last_px     = None

        print(f"[MSC-RISK][OC6] pid={self.parent_id} side={parent_side} "
              f"px={px} anchor={anchor} → flatten={flatten_side} "
              f"stk={flatten_stake} kind={exit_kind}")

        return plan

# === PATCH END ==============================================================


    # ----------------------------------------------------------------------
    # ENTRYPOINT: called each tick by MSC engine
    # ----------------------------------------------------------------------
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:

        # ----------------------------------------------
        # STOP CONDITION — legacy lifecycle complete
        # ----------------------------------------------
        if self._legacy_child_matched(ctx):
            self.risc_parent_id = None
            self.risc_cycle_active = False
            self.attached = False
            self.active_plan = None
            self.last_px = None
            return self._no_signal(ctx, reason="legacy_child_matched")

        # ----------------------------------------------
        # START CONDITION — legacy parent must be matched
        # ----------------------------------------------
        if not self._legacy_parent_matched(ctx):
            return self._no_signal(ctx, reason="legacy_parent_not_matched")

        # ----------------------------------------------
        # ONE-AT-A-TIME — wait for own cycle to finish
        # ----------------------------------------------
        if self.risc_cycle_active:
            if not self._risc_parent_and_child_matched(ctx):
                return self._no_signal(ctx, reason="risc_cycle_active")
            else:
                # previous RISC cycle completed
                self.risc_parent_id = None
                self.risc_cycle_active = False
                self.attached = False
                self.active_plan = None
                self.last_px = None

        # ----------------------------------------------
        # EXISTING LOGIC CONTINUES BELOW
        # ----------------------------------------------


        px = float(ctx.get("current_price") or 0)
        if px <= 0:
            return None

        # direction-engine decision (already built)
        # --------------------------------------------------
        # MECHANICAL RISK PARAMETERS (PARENT-DRIVEN)
        # --------------------------------------------------
        entry_ticks = int(ctx.get("risk_entry_ticks", 2))
        stop_ticks  = int(ctx.get("risk_stop_ticks", 4))
        self.mode   = ctx.get("risk_mode", "MODERATE")



# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def tick(self, ctx: Dict[str, Any]):
# 📆 PATCHED: 2026-02-14 — Assign parent_id dynamically
# ============================================================================

        pid = ctx.get("legacy_parent_id")
        if pid is None:
            return None

        # assign active parent_id
        self.parent_id = pid

# === PATCH END ==============================================================


        # parent metadata
        self.entry_side = (ctx.get("legacy_entry_side") or "").upper()
        self.entry_px   = float(ctx.get("legacy_entry_odds") or px)

        # SLEQ multiplier
        stake_mult = float(ctx.get("msc_multiplier") or 1.0)

        # ---------------------------------------------------------
        # OC6 TERMINATION (PRE-OFF → IN-PLAY boundary)
        # ---------------------------------------------------------
        oc_phase = ctx.get("oc_phase")
        if oc_phase is not None and int(oc_phase) >= 6:
            return self._terminate_oc6(ctx)

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
    # ============================================================
    # 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py
    # 🔎 SEARCH: def _initial_shadow(
    # 🛠 ACTION: Replace entire function with parent-plan version
    # 📆 PATCHED: 2025-12-06
    # ============================================================
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

        # 🔥 Emit MSC_RISK PARENT plan (router will hedge it)
        # === PATCH START: baseline 3-tick stop-loss ==========================
        from engines.price_math import walk_ticks

        baseline_sl_ticks = 3
        sl_dir = "up" if direction == "LAY" else "down"
        stop_loss_px = walk_ticks(float(entry_px), baseline_sl_ticks, sl_dir)

        plan = {
            "enter": True,
            "role": "PARENT",
            "family": "MSC_RISK",
            "parent_id": self.parent_id,
            # expose existing parent execution direction
            "direction": "BACK->LAY" if direction == "BACK" else "LAY->BACK",
            "target_ticks": entry_ticks,
            "stop_ticks": stop_ticks,
            "size": size,
            "px": entry_px,
            "why": "risk_initial_shadow",

            # NEW STOP-LOSS FIELDS
            "stop_loss_ticks": baseline_sl_ticks,
            "stop_loss_px": float(stop_loss_px),
        }
        # === PATCH END ========================================================

        # mark RISC cycle active
        self.risc_parent_id = self.parent_id
        self.risc_cycle_active = True


        self.last_px = px
        self.active_plan = plan
        return plan

    # ============================================================


    # ----------------------------------------------------------------------
    # TICK-BASED CONTINUOUS SCALPING
    # ----------------------------------------------------------------------
    # ============================================================
    # 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py
    # 🔎 SEARCH: def _scalp_tick(
    # 🛠 ACTION: Replace entire function
    # 📆 PATCHED: 2025-12-06
    # ============================================================
    def _scalp_tick(self, px, entry_ticks, stop_ticks, stake_mult, ctx):
        tick = ctx.get("tick_size_fn")(px)

        moving_favour = (
            self.entry_side == "LAY"  and px < self.entry_px
        ) or (
            self.entry_side == "BACK" and px > self.entry_px
        )

        moving_against = not moving_favour

        # FLIP direction if crossing anchor
        if self._crossed_parent(px):
            direction = "LAY" if px > self.entry_px else "BACK"
            size = self._stake(ctx, stake_mult, entry_ticks)
            return self._open_new(
                direction, px, entry_ticks, stop_ticks, size, reason="risk_cross"
            )

        # STACK (move with trend)
        if moving_favour:
            direction = "LAY" if self.entry_side == "LAY" else "BACK"
            size = self._stake(ctx, stake_mult, entry_ticks)
            return self._open_new(
                direction, px, entry_ticks, stop_ticks, size, reason="risk_stack"
            )

        # HEDGE (move against legacy)
        direction = "BACK" if self.entry_side == "LAY" else "LAY"
        size = self._stake(ctx, stake_mult, entry_ticks)
        return self._open_new(
            direction, px, entry_ticks, stop_ticks, size, reason="risk_hedge"
        )
    # ============================================================


    # ----------------------------------------------------------------------
    # MONITOR ACTIVE MICRO CHILD
    # ----------------------------------------------------------------------
    # ============================================================
    # 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py
    # 🔎 SEARCH: def _monitor(
    # 🛠 ACTION: Replace entire function
    # 📆 PATCHED: 2025-12-06
    # ============================================================
    def _monitor(self, px, entry_ticks, stop_ticks):
        # No internal exits. Router handles hedging and closure.
        self.last_px = px
        return None
    # ============================================================


    # ----------------------------------------------------------------------
    # OPEN A NEW MICRO SCALP
    # ----------------------------------------------------------------------
    # ============================================================
    # 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py
    # 🔎 SEARCH: def _open_new(
    # 🛠 ACTION: Replace with parent-version
    # 📆 PATCHED: 2025-12-06
    # ============================================================
    def _open_new(self, direction, px, entry_ticks, stop_ticks, size, reason):
        # === PATCH START: baseline 3-tick stop-loss ==========================
        from engines.price_math import walk_ticks

        baseline_sl_ticks = 3
        sl_dir = "up" if direction == "LAY" else "down"
        stop_loss_px = walk_ticks(float(px), baseline_sl_ticks, sl_dir)

        plan = {
            "enter": True,
            "role": "PARENT",
            "family": "MSC_RISK",
            "source": "J",                     # ← HARD-CODED FOR MSC-RISK
            "engine": "MSC_RISK",              # ← DB bucket
            "parent_id": self.parent_id,
            "direction": "BACK->LAY" if direction == "BACK" else "LAY->BACK",
            "target_ticks": entry_ticks,
            "stop_ticks": stop_ticks,
            "size": size,
            "px": float(px),
            "why": reason,

            # NEW STOP-LOSS FIELDS
            "stop_loss_ticks": baseline_sl_ticks,
            "stop_loss_px": float(stop_loss_px),
        }
        # === PATCH END ========================================================
        # mark RISC cycle active
        self.risc_parent_id = self.parent_id
        self.risc_cycle_active = True


        self.active_plan = plan
        self.last_px = px
        return plan

    # ============================================================


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
        """
        Mechanical risk stake:
        - Anchored to legacy parent exposure
        - Independent of intelligence / confidence
        """

        parent_stake = float(ctx.get("legacy_entry_stake") or 0.0)
        if parent_stake <= 0:
            return 0.0

        # Mode-based attenuation
        if self.mode == "AGGRESSIVE":
            frac = 0.5
        elif self.mode == "CONSERVATIVE":
            frac = 0.15
        else:  # MODERATE
            frac = 0.25

        base = parent_stake * frac

        # Optional tick scaling (keeps behaviour symmetric)
        base *= max(1.0, float(entry_ticks))

        return round(float(base) * float(stake_mult), 2)


# === PATCH END ================================================================
