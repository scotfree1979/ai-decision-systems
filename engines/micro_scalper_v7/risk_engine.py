
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

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def __init__(self):
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-03 — per-parent RISC lifecycle
# ======================================================================================================

    def __init__(self):
        # Active legacy parent this RISC instance is shadowing
        self.parent_id = None

        # Parent anchor
        self.entry_px = None
        self.entry_side = None

        # Tick tracking
        self.last_px = None

        # One-price-once PER PARENT
        # per-parent ladder memory
        self.used_prices_by_parent: dict[int, set[float]] = {}


        # Lifecycle flags
        self.attached = False
        self.active_plan = None


# === PATCH END ==============================================================
        # --------------------------------------------------
        # RISC lifecycle tracking (compatibility only)
        # --------------------------------------------------
        # NOTE:
        # These are retained for backward compatibility and logging only.
        # They MUST NOT block execution.
        # RISC is per-parent, not global.
        self.risc_parent_id = None
        self.risc_cycle_active = False

    # --------------------------------------------------
    # TELEMETRY — price usage memory (NON-BLOCKING)
    # --------------------------------------------------
    def _record_px_use(self, ctx, px: float, direction: str):
        """
        Best-effort telemetry.
        MUST NEVER affect execution.
        """
        try:
            from engines.config_paths import connect_mastery_v7_cache

            con = connect_mastery_v7_cache()
            cur = con.cursor()

            cur.execute("""
                CREATE TABLE IF NOT EXISTS risc_px_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    day TEXT NOT NULL,
                    parent_id INTEGER NOT NULL,
                    marketId TEXT NOT NULL,
                    selectionId TEXT NOT NULL,
                    anchor_px REAL NOT NULL,
                    traded_px REAL NOT NULL,
                    direction TEXT NOT NULL,
                    side TEXT NOT NULL,
                    engine TEXT DEFAULT 'MSC_RISK',
                    run_id TEXT,
                    created_at TEXT DEFAULT (datetime('now','utc'))
                )
            """)

            cur.execute("""
                INSERT INTO risc_px_memory(
                    day, parent_id, marketId, selectionId,
                    anchor_px, traded_px, direction, side,
                    run_id
                )
                VALUES(date('now','utc'),?,?,?,?,?,?,?,?)
            """, (
                self.parent_id,
                ctx.get("marketId"),
                ctx.get("selectionId"),
                float(self.entry_px),
                float(px),
                direction,
                ctx.get("legacy_entry_side"),
                ctx.get("run_id"),
            ))

            con.commit()
            con.close()
        except Exception:
            # Telemetry must never break trading
            pass



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
            o.get("engine") == "LEGACY"
            and o.get("role") == "PARENT"
            and o.get("entry_status") == "MATCHED"
            for o in self._orders(ctx)
        )

    def _legacy_child_matched(self, ctx):
        return any(
            o.get("engine") == "LEGACY"
            and o.get("role") == "CHILD"
            and o.get("exit_status") == "MATCHED"
            for o in self._orders(ctx)
        )

    def _risc_parent_and_child_matched(self, ctx):
        if not self.risc_parent_id:
            return False

        parent_matched = any(
            o.get("engine") == "MSC_RISK"
            and o.get("role") == "PARENT"
            and o.get("id") == self.risc_parent_id
            and o.get("entry_status") == "MATCHED"
            for o in self._orders(ctx)
        )

        child_matched = any(
            o.get("engine") == "MSC_RISK"
            and o.get("role") == "CHILD"
            and o.get("hedge_of") == self.risc_parent_id
            and o.get("exit_status") == "MATCHED"
            for o in self._orders(ctx)
        )

        return parent_matched and child_matched

# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def _no_signal(self, ctx: Dict[str, Any], *, reason: str) -> Dict[str, Any]:
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-01-07 — RISC no_signal is telemetry-only (non-blocking)
#
# RATIONALE:
# - RISC is a mechanical engine, not a signal engine
# - "no_signal" means "no direction at this PX", NOT "blocked"
# - Returning a payload causes BUS to treat this as an explicit decision
# - RISC must be allowed to re-emit on the next tick unconditionally
#
# INVARIANT:
# - Telemetry is preserved
# - Control flow is NOT affected
# - Budget/exposure blocking remains owned by Placement/BankState
# ======================================================================================================

    def _no_signal(self, ctx: Dict[str, Any], *, reason: str):
        """
        Telemetry-only helper.

        Indicates that RISC could not infer a trade direction at this PX
        (e.g. anchor price or already-traded price).

        IMPORTANT:
        - This function MUST NOT block future emissions
        - It MUST NOT return a plan-like payload
        - Returning None preserves correct BUS semantics
        """
        try:
            from engines.mastery.event_sink import emit

            emit("msc_risk.no_signal", {
                "engine": "MSC_RISK",
                "reason": reason,
                "marketId": ctx.get("marketId"),
                "selectionId": ctx.get("selectionId"),
                "parent_id": ctx.get("legacy_parent_id"),
                "px": ctx.get("current_price") or ctx.get("px"),
                "ts": ctx.get("ts"),
            })
        except Exception:
            # Telemetry must never affect execution
            pass

        # CRITICAL:
        # Returning None means:
        # - RISC did not emit a plan this tick
        # - BUS is free to re-evaluate RISC on the next tick
        return None

# === PATCH END ========================================================================================


# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalaper_v7/risk_engine.py (inside class RiskEngine)
# 📆 PATCHED: 2025-12-06 — OC6 flatten/exit logic
# ============================================================================

    def _terminate_oc6(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        OC6 termination — STOPLOSS intent only.

        RISC does NOT execute children.
        It emits a STOPLOSS intent that BUS forwards to Overwatcher.
        Overwatcher owns stop-loss execution.
        """

        px = float(ctx.get("current_price") or ctx.get("px") or 0.0)
        if px <= 0:
            return None

        # --------------------------------------------------
        # Parent metadata (authoritative from BUS ctx)
        # --------------------------------------------------
        parent_side = (ctx.get("legacy_entry_side") or "").upper()
        anchor_px   = float(ctx.get("legacy_entry_odds") or px)
        parent_stk  = float(ctx.get("legacy_entry_stake") or 0.0)

        if parent_stk <= 0:
            return None

        # --------------------------------------------------
        # Determine STOPLOSS direction + kind
        # --------------------------------------------------
        if parent_side == "LAY":
            exit_side = "BACK"
            exit_kind = "H" if px > anchor_px else "S"
        else:
            exit_side = "LAY"
            exit_kind = "H" if px < anchor_px else "S"

        # --------------------------------------------------
        # Emit STOPLOSS INTENT (no execution here)
        # --------------------------------------------------
        plan = {
            "enter": True,
            "engine": "OVERWATCHER",          # 🔑 execution owner
            "type": "STOPLOSS",
            "parent_id": self.parent_id,

            "marketId": ctx.get("marketId"),
            "selectionId": ctx.get("selectionId"),

            "exit_side": exit_side,
            "px": float(px),
            "size": float(parent_stk),

            "exit_kind": exit_kind,
            "why": f"risc_oc6_stoploss_{exit_kind}",
        }

        # --------------------------------------------------
        # Detach RISC lifecycle (terminal)
        # --------------------------------------------------
        self.active_plan = None
        self.attached = False
        self.last_px = None
        self.used_prices_by_parent.pop(self.parent_id, None)
        self.risc_parent_id = None
        self.risc_cycle_active = False

        print(
            f"[MSC-RISK][OC6] pid={self.parent_id} "
            f"px={px} anchor={anchor_px} "
            f"→ STOPLOSS intent ({exit_kind})"
        )

        return plan

# === PATCH END ==============================================================


    # ----------------------------------------------------------------------
    # ENTRYPOINT: called each tick by MSC engine
    # ----------------------------------------------------------------------
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:

        # ----------------------------------------------
        # STOP CONDITION — legacy lifecycle complete
        # ----------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: STOP CONDITION — legacy lifecycle complete
# 🧩 ACTION: REPLACE ENTIRE BLOCK
# 📆 PATCHED: 2026-03-03 — RISC stoploss is terminal, no legacy coupling
#
# RATIONALE:
# - RISC does NOT manage children
# - STOPLOSS is owned by Overwatcher
# - Once stoploss fires, RISC lifecycle is complete
# ======================================================================================================

        if ctx.get("risc_stoploss_executed"):
            self.parent_id = None
            self.attached = False
            self.active_plan = None
            self.last_px = None
            self.used_prices_by_parent.pop(self.parent_id, None)
            self.risc_parent_id = None
            self.risc_cycle_active = False
            return self._no_signal(ctx, reason="risc_stoploss_executed")


        # --------------------------------------------------
        # RISC lifecycle completion (PER-PARENT, NOT GLOBAL)
        # --------------------------------------------------
        if self.risc_cycle_active:
            # Only terminate the cycle if *this parent* has completed
            if self._risc_parent_and_child_matched(ctx):
                # End lifecycle for THIS parent only
                self.risc_parent_id = None
                self.risc_cycle_active = False
                self.attached = False
                self.active_plan = None
                self.last_px = None

            # IMPORTANT:
            # Never block execution for other parents
            # RISC continues evaluating per-parent


        # direction-engine decision (already built)
        # --------------------------------------------------
        # LIVE PRICE (AUTHORITATIVE)
        # --------------------------------------------------
        px = float(ctx.get("current_price") or ctx.get("px") or 0.0)
        if px <= 0:
            return None

        # --------------------------------------------------
        # MECHANICAL RISK PARAMETERS (PARENT-DRIVEN)
        # --------------------------------------------------
        entry_ticks = int(ctx.get("risk_entry_ticks", 2))
        stop_ticks  = int(ctx.get("risk_stop_ticks", 4))
        self.mode   = ctx.get("risk_mode", "MODERATE")

        pid = ctx.get("legacy_parent_id")
        if pid is None:
            return None

        # NEW PARENT → reset per-parent state
        if self.parent_id != pid:
            self.parent_id = pid
            self.used_prices_by_parent.setdefault(pid, set())
            self.last_px = None

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
        tick_fn = ctx.get("tick_size_fn")
        if callable(tick_fn):
            tick = tick_fn(px)
        else:
            from engines.price_math import get_tick_size
            tick = get_tick_size(px)


        # Shadow in same DIRECTION as parent
        # Compute initial execution price
        if self.entry_side == "LAY":
            direction = "LAY"
            entry_px = self.entry_px + tick
        else:
            direction = "BACK"
            entry_px = self.entry_px - tick

        # 🔒 GUARD: never allow execution at anchor price
        if entry_px == self.entry_px:
            if direction == "LAY":
                entry_px = self.entry_px + tick   # force next rung up
            else:
                entry_px = self.entry_px - tick   # force next rung down

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
            "engine": "MSC_RISK",
            "source": "J",
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


# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def _scalp_tick(
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-03 — hybrid continuation + one-price-once invariant
# ======================================================================================================

    def _scalp_tick(self, px, entry_ticks, stop_ticks, stake_mult, ctx):
        tick_fn = ctx.get("tick_size_fn")
        if callable(tick_fn):
            tick = tick_fn(px)
        else:
            from engines.price_math import get_tick_size
            tick = get_tick_size(px)

        # --------------------------------------------------
        # HARD INVARIANTS
        # --------------------------------------------------

        # 1) Anchor price is NEVER tradable
        if px == self.entry_px:
            rreturn self._no_signal(ctx, reason="anchor_price")

        # 2) One price once per parent (no repeat scalps)
        used = self.used_prices_by_parent.setdefault(self.parent_id, set())

        # Anchor is never tradable
        if px == self.entry_px:
            return self._no_signal(ctx, reason="invalid_px")

        # One price once per parent
        if px in used:
           return self._no_signal(ctx, reason="price_already_traded")

        used.add(px)

        # --------------------------------------------------
        # EXISTING RISK SEMANTICS (UNCHANGED)
        # --------------------------------------------------

        moving_favour = (
            self.entry_side == "LAY"  and px < self.entry_px
        ) or (
            self.entry_side == "BACK" and px > self.entry_px
        )

        moving_against = not moving_favour

        # --------------------------------------------------
        # FLIP direction if crossing anchor
        # --------------------------------------------------
        if self._crossed_parent(px):
            direction = "LAY" if px > self.entry_px else "BACK"
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def _open_new(
# 🧩 ACTION: ADD guard to prevent same-price re-entry when target_ticks == 1
# 📆 PATCHED: 2026-03-04 — enforce ladder separation invariant
# ======================================================================================================

            # 🔒 GUARD: never trade at anchor price
            if px == self.entry_px:
                if direction == "LAY":
                    px = walk_ticks(self.entry_px, 1, "up")
                else:
                    px = walk_ticks(self.entry_px, 1, "down")

            size = self._stake(ctx, stake_mult, entry_ticks)
            self._record_px_use(ctx, px, "UP" if px > self.entry_px else "DOWN")
            return self._open_new(
                direction,
                px,
                entry_ticks,
                stop_ticks,
                size,
                reason="risk_cross"
            )

        # --------------------------------------------------
        # STACK (move with trend)
        # --------------------------------------------------
        if moving_favour:
            direction = "LAY" if self.entry_side == "LAY" else "BACK"
            # 🔒 GUARD: never trade at anchor price
            if px == self.entry_px:
                if direction == "LAY":
                    px = walk_ticks(self.entry_px, 1, "up")
                else:
                    px = walk_ticks(self.entry_px, 1, "down")
            size = self._stake(ctx, stake_mult, entry_ticks)
            self._record_px_use(ctx, px, "UP" if px > self.entry_px else "DOWN")
            return self._open_new(
                direction,
                px,
                entry_ticks,
                stop_ticks,
                size,
                reason="risk_stack"
            )

        # --------------------------------------------------
        # HEDGE (move against legacy)
        # --------------------------------------------------
        direction = "BACK" if self.entry_side == "LAY" else "LAY"
        # 🔒 GUARD: never trade at anchor price
        if px == self.entry_px:
            if direction == "LAY":
                px = walk_ticks(self.entry_px, 1, "up")
            else:
                px = walk_ticks(self.entry_px, 1, "down")
        size = self._stake(ctx, stake_mult, entry_ticks)
        self._record_px_use(ctx, px, "UP" if px > self.entry_px else "DOWN")
        return self._open_new(
            direction,
            px,
            entry_ticks,
            stop_ticks,
            size,
            reason="risk_hedge"
        )



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
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: 
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-03 — remove duplicate engine key
# ======================================================================================================

    def _open_new(self, direction, px, entry_ticks, stop_ticks, size, reason):
        from engines.price_math import walk_ticks

        baseline_sl_ticks = 3
        sl_dir = "up" if direction == "LAY" else "down"
        stop_loss_px = walk_ticks(float(px), baseline_sl_ticks, sl_dir)

        plan = {
            "enter": True,
            "role": "PARENT",
            "engine": "MSC_RISK",
            "source": "J",
            "parent_id": self.parent_id,
            "direction": "BACK->LAY" if direction == "BACK" else "LAY->BACK",
            "target_ticks": entry_ticks,
            "stop_ticks": stop_ticks,
            "size": size,
            "px": float(px),
            "why": reason,
            "stop_loss_ticks": baseline_sl_ticks,
            "stop_loss_px": float(stop_loss_px),
        }

        self.risc_parent_id = self.parent_id
        self.risc_cycle_active = True
        self.active_plan = plan
        self.last_px = px
        return plan



    # ----------------------------------------------------------------------
    # EXIT PLAN
    # ----------------------------------------------------------------------
# ======================================================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def _exit(self, reason, H):
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-03 — fix syntax error, no behaviour change
# ======================================================================================================

    def _exit(self, reason, H):
        plan = {
            "enter": False,
            "close": True,
            "role": "CHILD",
            "engine": "MSC_RISK",
            "source": "J",
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
        RISC stake logic (final):
        - Variant of parent stake (never fractional hedge)
        - Scales naturally as parent stake grows
        - Odds-aware taper (high odds need less size)
        - Hard capped and safe
        """

        parent_stake = float(ctx.get("legacy_entry_stake") or 0.0)
        if parent_stake <= 0:
            return 0.0

        # ----------------------------------------
        # Mode-based multiplier (parent-relative)
        # ----------------------------------------
        if self.mode == "AGGRESSIVE":
            mode_mult = 1.25
        elif self.mode == "CONSERVATIVE":
            mode_mult = 0.60
        else:  # MODERATE
            mode_mult = 1.00

        base = parent_stake * mode_mult

        # ----------------------------------------
        # Odds-aware taper (entry odds of legacy)
        # ----------------------------------------
        entry_odds = float(ctx.get("legacy_entry_odds") or 0.0)

        if entry_odds >= 12.0:
            odds_mult = 0.40
        elif entry_odds >= 10.0:
            odds_mult = 0.55
        elif entry_odds >= 7.0:
            odds_mult = 0.85
        else:
            odds_mult = 1.00

        base *= odds_mult

        # ----------------------------------------
        # Optional tick scaling (mild symmetry)
        # ----------------------------------------
        base *= max(1.0, float(entry_ticks))

        size = float(base) * float(stake_mult)

        # ----------------------------------------
        # Absolute RISC safety caps
        # ----------------------------------------
        size = min(size, 7.00)

        from engines.daily_config import MIN_STAKE
        size = max(size, MIN_STAKE)

        return round(size, 2)


# === PATCH END ================================================================
