from engines.micro_scalper_v7.event_receiver import get_engine_outcomes
from typing import Dict, Any, Optional


class RiskEngine:
    """
    Risk-MicroScalper (Engine B)
    """

    # ======================================================
    # INIT — per-parent lifecycle
    # ======================================================
# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def __init__(self):
# 🧩 ACTION: ADD engine-scoped exclusion state
# 📆 PATCHED: 2026-03-29 — move exclusions from BusRoute to RiskEngine
#
# PURPOSE:
# - BusRoute becomes pure matched-parent surface
# - RiskEngine owns ALL eligibility gating
# ======================================================================

    def __init__(self):
        # parent_id -> state dict
        self._parents: dict[int, dict] = {}

        # 🔒 Engine-owned exclusions
        self._legacy_cycle_blocked: set[int] = set()
        self._exploratory_active: set[tuple[str, str]] = set()

        self.mode = "MODERATE"


    # ======================================================
    # Per-parent state accessor
    # ======================================================
# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def _state(self, pid: int) -> dict:
# 🧩 ACTION: REPLACE ENTIRE FUNCTION
# 📆 PATCHED: 2026-03-29 — Introduce microcycle-scoped state
#
# WHY:
# - Shadow cycle = time window
# - Microcycle = monotonic excursion away from anchor
# - used_prices MUST reset on anchor cross
# ======================================================================

    def _state(self, pid: int) -> dict:
        """
        Per-legacy-parent state.
        Microcycle-scoped.
        """
        return {
            "anchor_px": None,          # legacy entry odds
            "microcycle_dir": None,     # None | "UP" | "DOWN"
            "used_prices": set(),       # RESET per microcycle
            "last_px": None,
        }

# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🧩 ADD: exclusion refresh (engine-owned)
# 📆 PATCHED: 2026-03-29
# ======================================================================

    def _refresh_exclusions(self):
        """
        Pull exclusion surfaces directly from DB helpers.

        These are ENGINE decisions.
        BusRoute must not exclude.
        """
        try:
            from engines.bus_route import (
                get_risk_cycle_exclusions,
                get_exploratory_active_parent_pairs,
            )

            self._legacy_cycle_blocked = set(get_risk_cycle_exclusions())

            self._exploratory_active = {
                (str(mid), str(sid))
                for (mid, sid) in get_exploratory_active_parent_pairs()
            }

        except Exception:
            # Fail-open — risk must never crash
            self._legacy_cycle_blocked = set()
            self._exploratory_active = set()



    # ======================================================
    # Telemetry (non-blocking)
    # ======================================================
    def _record_px_use(self, ctx, px: float, direction: str):
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
                    anchor_px, traded_px, direction, side, run_id
                )
                VALUES(date('now','utc'),?,?,?,?,?,?,?,?)
            """, (
                ctx.get("legacy_parent_id"),
                ctx.get("marketId"),
                ctx.get("selectionId"),
                float(ctx.get("legacy_entry_odds")),
                float(px),
                direction,
                ctx.get("legacy_entry_side"),
                ctx.get("run_id"),
            ))
            con.commit()
            con.close()
        except Exception:
            pass

    # ======================================================
    # No-signal (telemetry only)
    # ======================================================
    def _no_signal(self, ctx, *, reason: str):
        return None

    # ======================================================
    # OC6 termination
    # ======================================================
    def _terminate_oc6(self, ctx):
        px = float(ctx.get("px") or 0.0)
        if px <= 0:
            return None

        parent_stk = float(ctx.get("legacy_entry_stake") or 0.0)
        if parent_stk <= 0:
            return None

        return {
            "enter": True,
            "engine": "OVERWATCHER",
            "type": "STOPLOSS",
            "parent_id": ctx.get("legacy_parent_id"),
            "marketId": ctx.get("marketId"),
            "selectionId": ctx.get("selectionId"),
            "px": px,
            "size": parent_stk,
            "why": "risc_oc6_stoploss",
        }

    # ======================================================
    # ENTRYPOINT — CALLED EACH TICK
    # ======================================================
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:

        px = float(ctx.get("px") or 0.0)

        if px <= 0:
            return None


        # 🔑 NORMALISE last_px
        ctx["last_px"] = px

        # --------------------------------------------------
        # 🔒 ENGINE-OWNED EXCLUSIONS
        # --------------------------------------------------

        self._refresh_exclusions()

        pid = ctx.get("legacy_parent_id")
        mid = str(ctx.get("marketId"))
        sid = str(ctx.get("selectionId"))
        engine = ctx.get("engine")

        # ------------------------------------
        # LEGACY: block if cycle blocked
        # ------------------------------------
        if engine == "LEGACY":
            if pid in self._legacy_cycle_blocked:
                return None

        # ------------------------------------
        # EXPLORATORY: block if already active
        # ------------------------------------
        if engine == "MSC_EXPLORATORY":
            if (mid, sid) in self._exploratory_active:
                return None


# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
# 🧩 ACTION: INSERT AFTER px VALIDATION
# 📆 PATCHED: 2026-03-29 — Anchor-cross microcycle control
#
# RULES:
# - Anchor cross RESETS microcycle
# - Reversal WITHOUT anchor cross FREEZES trading
# - used_prices is microcycle-scoped
# ======================================================================

        pid = ctx.get("legacy_parent_id")
        state = self._parents.setdefault(pid, self._state(pid))

        px = float(ctx.get("last_px") or 0.0)
        if px <= 0:
            return None

        anchor = float(ctx.get("entry_odds") or 0.0)
        if anchor <= 0:
            return None

        # --------------------------------------------------
        # Anchor binding (one-time)
        # --------------------------------------------------
        if state["anchor_px"] is None:
            state["anchor_px"] = anchor

        # --------------------------------------------------
        # 🔁 MICRO-CYCLE RESET — anchor cross
        # --------------------------------------------------
        if state["microcycle_dir"] == "DOWN" and px >= anchor:
            state["microcycle_dir"] = None
         

        elif state["microcycle_dir"] == "UP" and px <= anchor:
            state["microcycle_dir"] = None
           

        # --------------------------------------------------
        # 🟢 ARM NEW MICRO-CYCLE
        # --------------------------------------------------
        if state["microcycle_dir"] is None and px != anchor:
            state["microcycle_dir"] = "DOWN" if px < anchor else "UP"
            state["used_prices"].clear()
            state["last_px"] = px
            # allow first trade in new microcycle

# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: direction = ctx.get("risk_direction")
# 🧩 ACTION: REPLACE PRICE ELIGIBILITY LOGIC
# 📆 PATCHED: 2026-03-29 — Monotonic microcycle enforcement
#
# INVARIANT:
# - Trade ONLY while moving monotonically away from anchor
# - Freeze on retrace until anchor cross
# ======================================================================

        # --------------------------------------------------
        # ⛔ FREEZE ON RETRACE (no anchor cross)
        # --------------------------------------------------
        if state["last_px"] is not None:
            if state["microcycle_dir"] == "DOWN" and px >= state["last_px"]:
                return None
            if state["microcycle_dir"] == "UP" and px <= state["last_px"]:
                return None

        # --------------------------------------------------
        # ⛔ USED PRICE (microcycle scoped)
        # --------------------------------------------------
        if px in state["used_prices"]:
            return None

        # --------------------------------------------------
        # Record price use
        # --------------------------------------------------
        state["used_prices"].add(px)
        state["last_px"] = px

# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/risk_engine.py
# 🔎 SEARCH: return {
# 🧩 ACTION: REPLACE FINAL EMISSION
# 📆 PATCHED: 2026-03-29 — Anchor-relative risk emission
# ======================================================================

        if px > anchor:
            direction = "LAY->BACK"
        elif px < anchor:
            direction = "BACK->LAY"
        else:
            return None

        return {
            "enter": True,
            "role": "PARENT",
            "engine": "MSC_RISK",
            "parent_id": ctx["legacy_parent_id"],
            "marketId": ctx["marketId"],
            "selectionId": ctx["selectionId"],
            "direction": direction,
            "px": px,
            "why": "risk_shadow_microcycle",
        }


    # ======================================================
    # INITIAL SHADOW
    # ======================================================
    def _initial_shadow(self, state, px, entry_ticks, stop_ticks, stake_mult, ctx):
        from engines.price_math import walk_ticks

        entry_px   = state["entry_px"]
        entry_side = state["entry_side"]

        # --------------------------------------------------
        # AUTHORITATIVE INITIAL SHADOW RULE
        # --------------------------------------------------
        # Trade at CURRENT price, not parent ± tick
        # Parent price itself is never tradable
        # --------------------------------------------------
        if px == entry_px:
            return None

        # Direction rule:
        # price ABOVE parent → BACK->LAY
        # price BELOW parent → LAY->BACK
        if px > entry_px:
            direction = "BACK"
            plan_dir  = "BACK->LAY"
            sl_dir    = "up"
        else:
            direction = "LAY"
            plan_dir  = "LAY->BACK"
            sl_dir    = "down"

        size = self._stake(ctx, stake_mult, entry_ticks)

        stop_loss_px = walk_ticks(px, 3, sl_dir)

        plan = {
            "enter": True,
            "role": "PARENT",
            "engine": "MSC_RISK",
            "parent_id": ctx.get("legacy_parent_id"),
            "direction": plan_dir,
            "target_ticks": entry_ticks,
            "stop_ticks": stop_ticks,
            "size": size,
            "px": px,
            "stop_loss_px": float(stop_loss_px),
            "why": "risk_initial_shadow",
        }

        # --------------------------------------------------
        # CYCLE STATE (CRITICAL)
        # --------------------------------------------------
        state["active_plan"] = plan
        state["last_px"] = px
        state["cycle_active"] = True

        # Mark ACTUAL traded price
        state["used_prices"].add(px)

        return plan


    # ======================================================
    # SCALP TICK
    # ======================================================
    def _scalp_tick(self, state, px, entry_ticks, stop_ticks, stake_mult, ctx):

        entry_px = state["entry_px"]
        entry_side = state["entry_side"]

        # --------------------------------------------------
        # PRICE ELIGIBILITY (AUTHORITATIVE)
        # --------------------------------------------------
        if px == entry_px:
            return None

        if px in state["used_prices"] and state.get("active_plan"):
            return None

        state["used_prices"].add(px)


        if px > entry_px:
            # price ABOVE parent
            direction = "BACK"
        elif px < entry_px:
            # price BELOW parent
            direction = "LAY"
        else:
            return None  # exactly parent PX


        size = self._stake(ctx, stake_mult, entry_ticks)
        self._record_px_use(ctx, px, "UP" if px > entry_px else "DOWN")

        # --------------------------------------------------
        # DIRECTION + REASON (PARENT-RELATIVE, AUTHORITATIVE)
        # --------------------------------------------------
        entry_px   = state["entry_px"]
        entry_side = state["entry_side"]

        if entry_side == "LAY":
            if px < entry_px:
                direction = "BACK"   # favourable
                reason = "risk_stack"
            else:
                direction = "LAY"    # adverse
                reason = "risk_hedge"
        else:  # BACK parent
            if px > entry_px:
                direction = "LAY"    # favourable
                reason = "risk_stack"
            else:
                direction = "BACK"   # adverse
                reason = "risk_hedge"

        return self._open_new(
            direction,
            px,
            entry_ticks,
            stop_ticks,
            size,
            reason,
            ctx,
        )



    # ======================================================
    # MONITOR
    # ======================================================
    def _monitor(self, state, px):
        state["last_px"] = px


    # ======================================================
    # OPEN NEW RISK PARENT (per-legacy-parent)
    # ======================================================
    def _open_new(self, direction, px, entry_ticks, stop_ticks, size, reason, ctx):
        from engines.price_math import walk_ticks

        pid = ctx.get("legacy_parent_id")
        if pid is None:
            return None

        stop_loss_px = walk_ticks(
            float(px),
            3,
            "up" if direction == "LAY" else "down"
        )

        return {
            "enter": True,
            "role": "PARENT",          # ✅ parent-only, as requested
            "engine": "MSC_RISK",
            "parent_id": pid,
            "direction": "BACK->LAY" if direction == "BACK" else "LAY->BACK",
            "target_ticks": entry_ticks,
            "stop_ticks": stop_ticks,
            "size": size,
            "px": float(px),
            "stop_loss_px": float(stop_loss_px),
            "why": reason,
        }

    # ======================================================
    # STAKE LOGIC (unchanged)
    # ======================================================
    def _stake(self, ctx, stake_mult, entry_ticks):
        parent_stake = float(ctx.get("legacy_entry_stake") or 0.0)
        if parent_stake <= 0:
            return 0.0

        mode_mult = 1.25 if self.mode == "AGGRESSIVE" else 0.6 if self.mode == "CONSERVATIVE" else 1.0
        base = parent_stake * mode_mult

        entry_odds = float(ctx.get("legacy_entry_odds") or 0.0)
        odds_mult = 0.4 if entry_odds >= 12 else 0.55 if entry_odds >= 10 else 0.85 if entry_odds >= 7 else 1.0
        base *= odds_mult
        base *= max(1.0, float(entry_ticks))

        from engines.daily_config import MIN_STAKE
        return round(max(MIN_STAKE, min(base * stake_mult, 7.0)), 2)
