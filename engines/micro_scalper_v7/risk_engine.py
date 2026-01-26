from engines.micro_scalper_v7.event_receiver import get_engine_outcomes
from typing import Dict, Any, Optional


class RiskEngine:
    """
    Risk-MicroScalper (Engine B)
    """

    # ======================================================
    # INIT — per-parent lifecycle
    # ======================================================
    def __init__(self):
        # parent_id -> state dict
        self._parents: dict[int, dict] = {}
        self.mode = "MODERATE"

    # ======================================================
    # Per-parent state accessor
    # ======================================================
    def _state(self, pid: int) -> dict:
        st = self._parents.get(pid)
        if not st:
            st = {
                "entry_px": None,
                "entry_side": None,
                "last_px": None,
                "attached": False,
                "active_plan": None,
                "used_prices": set(),
                "cycle_active": False,
            }
            self._parents[pid] = st
        return st

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

        pid = ctx.get("legacy_parent_id")
        if pid is None:
            return None

        px = float(ctx.get("px") or 0.0)
        if px <= 0:
            return None

        state = self._state(pid)

        entry_px = float(ctx.get("legacy_entry_odds") or 0.0)
        entry_side = (ctx.get("legacy_entry_side") or "").upper()
        if entry_px <= 0 or entry_side not in ("LAY", "BACK"):
            return None

        state["entry_px"] = entry_px
        state["entry_side"] = entry_side

        entry_ticks = int(ctx.get("risk_entry_ticks", 2))
        stop_ticks = int(ctx.get("risk_stop_ticks", 4))
        stake_mult = float(ctx.get("msc_multiplier") or 1.0)
        self.mode = ctx.get("risk_mode", "MODERATE")

        oc_phase = ctx.get("oc_phase")
        if oc_phase is not None and int(oc_phase) >= 6:
            return self._terminate_oc6(ctx)

        # --------------------------------------------------
        # FIRST ATTACH — START CYCLE ONLY AFTER PRICE MOVES
        # --------------------------------------------------
        if not state["cycle_active"]:

            state["cycle_active"] = True
            state["attached"] = True
            state["last_px"] = px

            # EntryPX is NOT tradable, but cycle is now alive
            if px == state["entry_px"]:
                return None

            return self._initial_shadow(
                state,
                px,
                entry_ticks,
                stop_ticks,
                stake_mult,
                ctx,
            )

        # --------------------------------------------------
        # ACTIVE PLAN DOES NOT BLOCK FURTHER SCALPING
        # --------------------------------------------------
        # RISC may emit multiple parent plans per legacy parent
        # (one per price level). Placement/BankState enforce exposure.
        # --------------------------------------------------
        if state["active_plan"]:
            self._monitor(state, px)
            # DO NOT return here


        return self._scalp_tick(state, px, entry_ticks, stop_ticks, stake_mult, ctx)

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

        if px in state["used_prices"]:
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
