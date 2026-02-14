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
                ctx.get("anchor_parent_id"),
                ctx.get("marketId"),
                ctx.get("selectionId"),
                float(ctx.get("anchor_entry_odds")),
                float(px),
                direction,
                ctx.get("anchor_engine"),
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

        parent_stk = float(ctx.get("anchor_entry_stake") or 0.0)
        if parent_stk <= 0:
            return None

        return {
            "enter": True,
            "engine": "OVERWATCHER",
            "type": "STOPLOSS",
            "parent_id": ctx.get("anchor_parent_id"),
            "marketId": ctx.get("marketId"),
            "selectionId": ctx.get("selectionId"),
            "px": px,
            "size": parent_stk,
            "why": "risc_oc6_stoploss",
        }

    # ======================================================
    # ENTRYPOINT — CALLED EACH TICK
    # ======================================================
    def tick(self, ctx):

        mid = str(ctx.get("marketId"))
        sid = str(ctx.get("selectionId"))

        px = ctx.get("px")
        if px is None:
            return None

        px = float(px)
        if px <= 0:
            return None


        # --------------------------------------------------
        # 🔒 Engine-owned exclusions
        # --------------------------------------------------


        pid = ctx.get("anchor_parent_id")
        anchor = float(ctx.get("anchor_entry_odds") or 0.0)
        parent_stake = float(ctx.get("anchor_entry_stake") or 0.0)

        if not pid or anchor <= 0:
            return None

        state = self._parents.setdefault(pid, self._state(pid))



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

        # --------------------------------------------------
        # ⛔ USED PRICE (microcycle scoped)
        # --------------------------------------------------
        if px in state["used_prices"]:
            return None

        state["used_prices"].add(px)
        state["last_px"] = px

        # --------------------------------------------------
        # Emit parent plan (anchor-relative)
        # --------------------------------------------------
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
            "parent_id": pid,
            "marketId": mid,
            "selectionId": sid,
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
            "parent_id": ctx.get("anchor_parent_id"),
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

        anchor = state.get("anchor_px")
        if anchor is None:
            return None

        # --------------------------------------------------
        # BLOCK ANCHOR PRICE
        # --------------------------------------------------
        if px == anchor:
            return None

        # --------------------------------------------------
        # BLOCK IF PRICE ALREADY USED IN THIS MICRO-CYCLE
        # --------------------------------------------------
        if px in state["used_prices"]:
            return None

        # --------------------------------------------------
        # ENSURE PRICE IS STILL ON SAME SIDE OF ANCHOR
        # (Do NOT require anchor cross to re-arm)
        # --------------------------------------------------
        direction_side = None

        if px > anchor:
            direction_side = "UP"
        elif px < anchor:
            direction_side = "DOWN"
        else:
            return None

        # If microcycle direction exists, enforce consistency
        if state["microcycle_dir"] and state["microcycle_dir"] != direction_side:
            return None

        # If no microcycle yet, arm it
        if state["microcycle_dir"] is None:
            state["microcycle_dir"] = direction_side
            state["used_prices"].clear()

        # --------------------------------------------------
        # ACCEPT NEW UNUSED PRICE (NO ANCHOR CROSS REQUIRED)
        # --------------------------------------------------
        state["used_prices"].add(px)
        state["last_px"] = px

        size = self._stake(ctx, stake_mult, entry_ticks)

        # --------------------------------------------------
        # DIRECTION (ANCHOR RELATIVE)
        # --------------------------------------------------
        if px > anchor:
            direction = "LAY->BACK"
            reason = "risk_shadow_drift"
        else:
            direction = "BACK->LAY"
            reason = "risk_shadow_steam"

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

        pid = ctx.get("anchor_parent_id")

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
        parent_stake = float(ctx.get("anchor_entry_stake") or 0.0)
        if parent_stake <= 0:
            return 0.0

        mode_mult = 1.25 if self.mode == "AGGRESSIVE" else 0.6 if self.mode == "CONSERVATIVE" else 1.0
        base = parent_stake * mode_mult

        entry_odds = float(ctx.get("anchor_entry_odds") or 0.0)
        odds_mult = 0.4 if entry_odds >= 12 else 0.55 if entry_odds >= 10 else 0.85 if entry_odds >= 7 else 1.0
        base *= odds_mult
        base *= max(1.0, float(entry_ticks))

        from engines.daily_config import MIN_STAKE
        return round(max(MIN_STAKE, min(base * stake_mult, 7.0)), 2)
