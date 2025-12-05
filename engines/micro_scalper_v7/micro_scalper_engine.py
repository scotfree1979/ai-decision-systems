# /engines/micro_scalper_v7/micro_scalper_engine.py

from typing import Dict, Any, Optional
from .exploratory_engine import ExploratoryEngine
from .risk_engine import RiskEngine
from .inplay_engine import InPlayEngine
from .intel_adapter import build_micro_state
from .state_machine import MSCGlobalState
from .utils import classify_direction_from_legacy

# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalaper_v7/micro_scalper_engine.py (module scope)
# 📆 PATCHED: 2025-12-01 — Consume Brain Pulse from Overwatcher
# ----------------------------------------------------------------------------
# We safely import the pulse container from Overwatcher. It always exists because
# Overwatcher defines `_last_brain_pulse = [None]` in the previous patch.
try:
    from engines.live.overwatcher import _last_brain_pulse
except Exception:
    _last_brain_pulse = [None]

def _consume_brain_pulse(ctx: dict) -> None:
    """
    Inject the latest Overwatcher Brain Pulse into the MicroScalper context.
    Non-intrusive:
        - If no pulse exists, does nothing.
        - If pulse is for a different runner, does nothing.
        - If present, adds:
              ctx["brain_coherence"]
              ctx["brain_adjustment"]
              ctx["brain_side"]
              ctx["brain_ts"]
              ctx["brain_reason"]
              ctx["brain_stake"]
    MSC engines can then incorporate this into directional logic or multipliers.
    """
    try:
        pulse = _last_brain_pulse[0]
        if not pulse:
            return

        # Only apply if pulse matches THIS runner
        if str(pulse.get("marketId")) != str(ctx.get("marketId")):
            return
        if str(pulse.get("selectionId")) != str(ctx.get("selectionId")):
            return

        ctx["brain_coherence"]  = float(pulse.get("confidence", 0.0))
        ctx["brain_adjustment"] = float(pulse.get("adjustment", 0.0))
        ctx["brain_side"]       = pulse.get("side")
        ctx["brain_ts"]         = pulse.get("ts")
        ctx["brain_reason"]     = pulse.get("reason")
        ctx["brain_stake"]      = float(pulse.get("stake", 2.0))
    except Exception:
        pass
# === PATCH END ===============================================================


# === PATCH START ============================================================
# 📍 TARGET: engines/micro_scalper_v7/micro_scalper_engine.py
# 🔎 SEARCH: class MicroScalperEngine:
# 📆 PATCHED: 2025-11-28 — Add SLEQ Multiplier + stake scaling
# ============================================================================

def calculate_msc_multiplier(ctx: Dict[str, Any]) -> float:
    """
    MSC Multiplier Model (v1)
    -------------------------
    Calculates a soft multiplier ∈ [0.8 → 1.4] based on:
      • SLEQ (stop-loss equity relative strength)
      • tick volatility (microstructure)
      • bias_conf
      • good_rate vs bad_rate (trend reliability)
    """

    # ----------- 1) Pull inputs safely -------------------
    sleq = float(ctx.get("sleq", 1.0))
    sleq = max(0.0, min(sleq, 2.0))        # clamp to sane band

    vol = float(ctx.get("tick_volatility", 0.0))
    vol = max(0.0, min(vol, 1.0))

    bias_conf = float(ctx.get("bias_conf", 0.0))
    bias_conf = max(0.0, min(bias_conf, 1.0))

    good = float(ctx.get("good_rate", 0.0))
    bad = float(ctx.get("bad_rate", 0.0))

    # ----------- 2) Component multipliers -----------------
    m_sleq = 1.00
    if sleq >= 1.30:
        m_sleq = 1.15
    elif sleq >= 1.10:
        m_sleq = 1.08
    elif sleq >= 0.90:
        m_sleq = 1.00
    else:
        m_sleq = 0.92

    m_vol = (1.00 - 0.20 * vol)            # more vol → smaller stake
    m_bias = (1.00 + 0.15 * bias_conf)     # high-confidence bias → larger stake

    m_trend = 1.00
    if good + bad > 5:
        edge = (good - bad) / (good + bad)
        m_trend = 1.00 + 0.10 * edge       # ±10%

    # ----------- 3) Final blended multiplier --------------
    mult = m_sleq * m_vol * m_bias * m_trend

    # global clamp
    return max(0.80, min(1.40, mult))



def _apply_multiplier(plan: Dict[str, Any], ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Apply MSC multiplier to any plan emitted by any MSC sub-engine.
    Leaves Legacy/DecideOnce completely untouched.
    """
    if not plan or "size" not in plan:
        return plan

    base_size = float(plan.get("size", 0.0))
    if base_size <= 0:
        return plan

    mult = calculate_msc_multiplier(ctx)
    plan["size"] = round(base_size * mult, 2)
    plan["msc_multiplier"] = mult   # optional for telemetry
    return plan

# === PATCH END ==============================================================
# ======================================================================
# 📍 TARGET: engines/micro_scalper_v7/micro_scalper_engine.py  (module scope)
# 🔎 SEARCH: class MicroScalperEngine:
# 📆 PATCHED: 2025-12-02 — MSC LIVE matcher background thread
# ======================================================================

import threading, time

def _msc_matcher_loop():
    """
    MSC background matcher loop.
    Ensures MSC parents get MATCHED exactly like Legacy parents.
    Calls the same LiveRouter functions:
        - _sync_parent_matches
        - _sync_all_matches
        - _sync_hedge_matches
    """
    try:
        from engines.live.live_router import (
            _sync_parent_matches,
            _sync_all_matches,
            _sync_hedge_matches,
        )
    except Exception:
        return  # live_router not available yet

    while True:
        try:
            _sync_parent_matches(limit=50)
        except Exception:
            pass


        try:
            _sync_all_matches(limit=100)
        except Exception:
            pass

        try:
            _sync_hedge_matches(limit=50)
        except Exception:
            pass

        time.sleep(1.0)

def _start_msc_matcher_loop_once():
    """Ensure the MSC matcher thread runs only once per process."""
    if getattr(_start_msc_matcher_loop_once, "_started", False):
        return
    _start_msc_matcher_loop_once._started = True

    t = threading.Thread(target=_msc_matcher_loop,
                         name="MSCMatcherLoop",
                         daemon=True)
    t.start()


# Hook into MSC engine startup
# Insert this call at the beginning of MicroScalperEngine.__init__()
# (see second patch block below)
# ======================================================================


class MicroScalperEngine:
    """
    MicroScalperEngine v7
    ---------------------
    This is the central orchestrator for all three micro-scalper systems:

      Engine A: Exploratory PRE-OFF MicroScalper
      Engine B: Risk-Reactive PRE-OFF MicroScalper (per Legacy parent)
      Engine C: Intelligent IN-PLAY Laying Engine

    It runs inside orchestrator.start_live_loop() and receives
    tick-level updates for every runner.
    """

    def __init__(self):
        # global PRE-OFF / IN-PLAY state
        self.state = MSCGlobalState.IDLE

        # === PATCH START (MSC LIVE matcher loop) ===
        try:
            _start_msc_matcher_loop_once()
        except Exception:
            pass
        # === PATCH END ===

        # sub-engine instances
        self.exploratory = ExploratoryEngine()
        self.inplay = InPlayEngine()

        # risk-engine pool, scoped per Legacy parent
        #   key = parent_order_id
        #   value = RiskEngine instance
        self.risk_engines: Dict[int, RiskEngine] = {}

    # ======================================================================
    # PUBLIC API — Called every tick by orchestrator (full market context)
    # ======================================================================
    def tick(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Main entry point called from orchestrator.

        ctx MUST include at minimum:
            - oc_phase: int (0–7 PRE-OFF, >=7 IN-PLAY)
            - marketId
            - selectionId
            - current_price
            - dynamic_stake_fn
            - legacy signals: 
                * ctx["legacy_parent_id"] (optional)
                * ctx["legacy_entry_side"] (optional)
                * ctx["legacy_expected_direction"] (optional)
            - stoploss_triggered_for_parent (optional)
            - v7-intel enriched fields
        """
        oc_phase = ctx.get("oc_phase", 0)

        # === PATCH START (BrainPulse → MSC injection) =========================
        try:
            _consume_brain_pulse(ctx)
        except Exception:
            pass
        # === PATCH END ========================================================


        # 1) PHASE SWITCHING
        if oc_phase < 7:
            self.state = MSCGlobalState.PRE_OFF
        else:
            self.state = MSCGlobalState.IN_PLAY

        # 2) PRE-OFF MODE → run exploratory + risk engines
        if self.state == MSCGlobalState.PRE_OFF:
            return self._tick_preoff(ctx)

        # 3) IN-PLAY MODE → run in-play laying engine
        if self.state == MSCGlobalState.IN_PLAY:
            return self._tick_inplay(ctx)

        return None

    # ======================================================================
    # PRE-OFF LOGIC (Exploratory + Risk Reactive)
    # ======================================================================
    def _tick_preoff(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Run Exploratory Engine and all active Risk Engines.
        Precedence rules:
          1. StopLoss events handled first.
          2. Risk-MSC for specific parents may generate plans.
          3. Exploratory MSC generates opportunities unaffected by risk engines.
        """
        # ---------------------------------------------------------
        # STOPLOSS CLEANUP FOR THIS PARENT
        # ---------------------------------------------------------
        # === PATCH START ===
        # 📍 TARGET: engines/micro_scalper_v7/micro_scalper_engine.py:_tick_preoff
        # 📆 PATCHED: 2025-11-29 — new dynamic SL detachment hook

        trig_parent = ctx.get("stoploss_triggered_for_parent")
        if trig_parent and trig_parent in self.risk_engines:
            # trailing SL hit → detach Risk Engine for this parent
            self.risk_engines[trig_parent].detach()
            del self.risk_engines[trig_parent]
            return {
                "type": "MSC_STOPLOSS_CLOSE",
                "marketId": ctx["marketId"],
                "selectionId": ctx["selectionId"],
                "parent_id": trig_parent
            }
        # === PATCH END ===

            # === PATCH START ============================================================
            # 📍 TARGET: micro_scalper_engine._tick_preoff
            # 🛠 ACTION: Inject SLEQ from StopLoss engine into MSC ctx
            # 📆 PATCHED: 2025-12-06
            from engines.live.stoploss_engine import StopLossEngine

            try:
                # Singleton TSL engine used by Overwatcher
                sleq_engine = StopLossEngine()
                key = (str(ctx["marketId"]), str(ctx["selectionId"]))
                sleq_state = sleq_engine.sleq_map.get(key)

                if sleq_state:
                    ctx["sleq"] = float(sleq_state.sleq)
                else:
                    ctx["sleq"] = 1.0  # default multiplier
            except Exception:
                ctx["sleq"] = 1.0
            # === PATCH END ============================================================



        # ---------------------------------------------------------
        # ATTACH RISK ENGINE (per Legacy parent)
        # ---------------------------------------------------------
        legacy_parent_id = ctx.get("legacy_parent_id")
        if legacy_parent_id and legacy_parent_id not in self.risk_engines:
            self.risk_engines[legacy_parent_id] = RiskEngine(legacy_parent_id)
            self.risk_engines[legacy_parent_id].attach(ctx)

        # ---------------------------------------------------------
        # RUN RISK ENGINES (tick-shadow)
        # ---------------------------------------------------------
        # Only one risk engine may emit a plan per tick for this runner.
        for parent_id, engine in list(self.risk_engines.items()):
            plan = engine.tick(ctx)
            if plan:
                plan["source"] = "J"
                return _apply_multiplier(plan, ctx)

        # ---------------------------------------------------------
        # RUN EXPLORATORY ENGINE (intelligent micro scalps)
        # ---------------------------------------------------------
        # Exploratory uses the same ctx but expects ctx["legacy_entry_side"]
        # for directional alignment.
        # If no Legacy parent exists for this runner, we derive direction from ctx.
        if ctx.get("legacy_entry_side"):
            ctx["legacy_expected_direction"] = classify_direction_from_legacy(
                ctx["legacy_entry_side"]
            )
        # === PATCH START ============================================================
        # 📍 TARGET: micro_scalper_engine._tick_preoff
        # 📆 PATCHED: 2025-12-01 — enrich ctx with MSC direction/mode
        from .direction_engine import compute_msc_decision

        msc_dec = compute_msc_decision(ctx)
        ctx["msc_direction"]    = msc_dec["direction"]
        ctx["msc_mode"]         = msc_dec["mode"]
        ctx["msc_entry_ticks"]  = msc_dec["entry_ticks"]
        ctx["msc_stop_ticks"]   = msc_dec["stop_ticks"]
        # === PATCH END ==============================================================


        plan = self.exploratory.tick(ctx)
        if plan:
            plan["source"] = "D"
            return _apply_multiplier(plan, ctx)

        return None

    # ======================================================================
    # IN-PLAY LOGIC (Intelligent Lay Engine)
    # ======================================================================
    def _tick_inplay(self, ctx: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Run the in-play intelligent laying engine (Engine C).
        """
        plan = self.inplay.tick(ctx)
        if plan:
            plan["source"] = "V"
            return _apply_multiplier(plan, ctx)
        return None

    # ======================================================================
    # SUPPORT: CLEANUP WHEN MARKET ENDS
    # ======================================================================
    def reset(self):
        """
        Reset MicroScalperEngine between markets (race ended).
        """
        self.state = MSCGlobalState.IDLE
        self.exploratory.state = None
        self.inplay.state = None
        self.risk_engines.clear()
