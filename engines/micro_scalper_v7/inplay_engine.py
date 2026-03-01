# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: ^from typing import Dict, Any, Optional
# 🛠 ACTION: Replace entire file
# 📆 PATCHED: 2026-03-XX — First-Class MSC In-Play Engine (Arming → Trigger → Batch Emit)
# ==============================================================================

from typing import Dict, Any, List, Optional
import time
from datetime import datetime, timezone
from engines.micro_scalper_v7.direction_engine import compute_msc_decision
from engines.mastery.event_sink import emit




class InPlayEngine:
    """
    MSC_INPLAY — first-class in-play ladder engine.

    MODEL:
    - Arm runners continuously (pre-off + in-play)
    - Detect race start via volatility (NOT time)
    - Trigger batch ladder emission when conditions met
    - Emit ALL ladder parents at once
    - No sequencing, no sizing, no matching logic

    BUS + Router own execution.
    """

    # ----------------------------
    # Configuration
    # ----------------------------

    SWEETSPOT = 7.0

    LAY_LADDER  = [7.0, 8.0, 9.0, 10.0, 11.0, 12.0]
    BACK_LADDER = [5.0, 4.0, 3.0]

    VOLATILITY_TICKS_TRIGGER = 0.8     # race start detection
    ARM_CONFIDENCE_MIN       = 0.15    # minimum drift confidence

    # ----------------------------
    # Lifecycle
    # ----------------------------

    def __init__(self):
        # per-runner memory
        self.last_px: Dict[tuple, float] = {}

        # per-runner arming state
        self.armed_lay:  Dict[tuple, bool] = {}
        self.armed_back: Dict[tuple, bool] = {}

        # per-runner trigger state (one-shot)
        self.triggered: Dict[tuple, bool] = {}

        # per-market in-play detection
        self.market_inplay: Dict[str, float] = {}

    # ----------------------------
    # Main entrypoint
    # ----------------------------

    def tick(self, ctx: Dict[str, Any]) -> Dict[str, Any]:


# ============================================================================
# 📍 TARGET: <engine_file_here>
# 🔎 SEARCH: def tick(self, ctx):
# 🧩 ACTION: INSERT — Band Guard (IGNORED filter)
# 📆 PATCHED: 2026-04-14 — Enforce IGNORED runner exclusion
#
# PURPOSE:
# - Engines must never process IGNORED band runners
# - BusRoute supplies full-day surface
# - Engine owns eligibility decision
#
# INVARIANT:
# - If ctx["band"] == "IGNORED", engine returns None
# - No execution logic runs for ignored runners
# ============================================================================

      
        intel = {}
        mid = str(ctx.get("marketId"))
        sid = str(ctx.get("selectionId"))

        # --- AUTHORITATIVE BAND + PX FROM MARKET MONITOR ---
        from engines.market_monitor.monitor import classify

        mm = classify(mid, sid)

        if mm:
            # px fallback only if missing
            if ctx.get("px") is None and mm.get("px") is not None:
                ctx["px"] = mm.get("px")

            # authoritative band
            ctx["band"] = mm.get("band")

        px_val = ctx.get("px")
        if px_val is None:
            return self._no_signal("missing_px")

        px = float(px_val)
        if px <= 0:
            return self._no_signal("invalid_px")


        if ctx.get("band") == "IGNORED":
            return self._no_signal("ignored_band")



        if not mid or not sid or not px or px <= 0:
            return self._no_signal("missing_identity_or_px")

        key = (mid, sid)

        # --------------------------------------------------
        # Market Monitor Volatility (Authoritative)
        # --------------------------------------------------

        from engines.market_monitor.monitor import _STATE
        from engines.math.tick_utils import price_to_ticks  # if you already have it

        last_map = _STATE.get("last_px", {}).get(mid, {})
        prev_px  = last_map.get(sid)

        ticks_moved = 0.0
        direction = "FLAT"

        if prev_px is not None and px is not None:

            prev_ticks = price_to_ticks(float(prev_px))
            now_ticks  = price_to_ticks(float(px))

            ticks_moved = abs(now_ticks - prev_ticks)

            if now_ticks > prev_ticks:
                direction = "LAY->BACK"
            elif now_ticks < prev_ticks:
                direction = "BACK->LAY"

        # 🔒 update memory AFTER calculation
        _STATE.setdefault("last_px", {}).setdefault(mid, {})[sid] = px

        confidence = min(1.0, ticks_moved / 3.0)
        to_price = px

        # --------------------------------------------------
        # Race Start Authority (Non-Blocking)
        # --------------------------------------------------
# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: # Race Start Authority (Non-Blocking)
# 🛠 ACTION: Replace race start detection with scheduled+behaviour model
# 📆 PATCHED: 2026-04-28 — Dual Authority Race Start Detection
#
# PURPOSE:
# - Prevent pre-off false triggers
# - Prevent delayed-race misclassification
# - Remove reliance on trend cache timing
# - Anchor volatility to scheduled off time
#
# INVARIANT:
# - Race cannot start before scheduled off
# - Race must exhibit volatility to be confirmed
# ==============================================================================

        race_started = False
        race_quartile = None
        race_confidence = 0.0

        try:
            from engines.config_paths import open_bets_db
            from datetime import datetime, timezone
            import sqlite3

            con = open_bets_db(rw=False)
            row = con.execute("""
                SELECT marketStartTime
                FROM bets
                WHERE marketId = ?
                LIMIT 1
            """, (mid,)).fetchone()
            con.close()

            if row and row[0]:

                scheduled_off = datetime.fromisoformat(
                    row[0].replace("Z","")
                ).replace(tzinfo=timezone.utc).timestamp()

                now_ts = datetime.now(timezone.utc).timestamp()

                scheduled_started = now_ts >= scheduled_off

                # Behaviour confirmation
                behaviour_started = abs(ticks_moved) >= self.VOLATILITY_TICKS_TRIGGER

                race_started = scheduled_started and behaviour_started

                # Narrative clock (separate from execution)
                if scheduled_started:
                    elapsed = now_ts - scheduled_off

                    if elapsed <= 60:
                        race_quartile = "Q1"
                    elif elapsed <= 120:
                        race_quartile = "Q2"
                    elif elapsed <= 180:
                        race_quartile = "Q3"
                    else:
                        race_quartile = "Q4"

                    race_confidence = min(1.0, abs(ticks_moved) / 3.0)

        except Exception:
            race_started = False

# === PATCH END ==============================================================        # --------------------------------------------------
        # Authority Influence Layer (Ranking, not veto)
        # --------------------------------------------------
# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: # Authority Influence Layer (Ranking, not veto)
# 🛠 ACTION: Refine authority gating (LEADING + PROMINENT protection)
# 📆 PATCHED: 2026-02-24 — Balanced authority layer
#
# PURPOSE:
# - Authority ranks, not vetoes
# - Protect LEADING strongly
# - Protect PROMINENT unless collapse strong
# - Allow multiple runners per market
# ==============================================================================

        authoritative_runners = intel.get("runners", [])
        authority_available = bool(authoritative_runners)

        collapse_score = 0.0
        role = None

        if authority_available:

            collapse_map = {
                str(r.get("selectionId")): r.get("collapse_score", 0)
                for r in authoritative_runners
            }

            role_map = {
                str(r.get("selectionId")): r.get("role")
                for r in authoritative_runners
            }

            collapse_score = collapse_map.get(sid, 0.0)
            role = role_map.get(sid)

            # Block very weak collapse entirely
            if collapse_score < 1.0:
                return self._no_signal("collapse_score_too_low")

            # Protect LEADING unless extreme collapse
            if role == "LEADING" and collapse_score < 2.5:
                return self._no_signal("leader_protected")

            # Protect PROMINENT unless solid collapse
            if role == "PROMINENT" and collapse_score < 2.0:
                return self._no_signal("prominent_protected")

# === PATCH END ==============================================================


        # --------------------------------------------------
        # Arming logic (always on)
        # --------------------------------------------------

        if confidence >= self.ARM_CONFIDENCE_MIN:

            # DRIFT → potential loser → LAY ladder
            if direction == "LAY->BACK":
                self.armed_lay[key] = True

            # STEAM → exposure risk → BACK ladder
            elif direction == "BACK->LAY":
                self.armed_back[key] = True

        # --------------------------------------------------
        # Atomic V7 Report
        # --------------------------------------------------

        self._print_v7_inplay_report(
            market_id=mid,
            sid=sid,
            px=px,
            prev_px=prev_px,
            direction=direction,
            ticks_moved=ticks_moved,
            race_started=race_started,
            race_quartile=race_quartile,
            race_confidence=race_confidence,
        )

        # --------------------------------------------------
        # Triggering (in-play only, one-shot)
        # --------------------------------------------------

        if not race_started:
            return self._no_signal("armed_pre_inplay")

        if self.triggered.get(key):
            return self._no_signal("already_triggered")

        plans: List[Dict[str, Any]] = []

# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 INSERT BEFORE: # ---- LAY ladder trigger ----
# 📆 PATCHED: 2026-02-20 — Secondary Harvest Boundary (15–20 zone)
#
# PURPOSE:
# - Harvest money from clearly losing runners
# - Flat lay £10
# - Mutually exclusive with primary ladder
# - One-shot per runner
# ==============================================================================

        HARVEST_MIN = 15.0
        HARVEST_MAX = 20.0
        HARVEST_STAKE = 10.0

        # Secondary harvest condition
        if (
            HARVEST_MIN <= px <= HARVEST_MAX
            and not self.triggered.get(key)
            and not getattr(self, "harvested", {}).get(key)
        ):

            # Avoid harvesting leaders
  
            if role not in ("LEADING", "PROMINENT"):

                # Mark as harvested
                if not hasattr(self, "harvested"):
                    self.harvested = {}

                self.harvested[key] = True
                self.triggered[key] = True  # prevent primary ladder later

                return {
                    "enter": True,
                    "engine": "MSC_INPLAY",
                    "role": "PARENT",
                    "direction": "LAY->BACK",
                    "px": px,
                    "stake": HARVEST_STAKE,
                    "target_ticks": 100,  # deeper scalp
                    "why": "inplay_secondary_harvest",
                }

# === PATCH END ==============================================================


# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: # ---- LAY ladder trigger ----
# 🛠 ACTION: Add authority ranking bias
# 📆 PATCHED: 2026-02-24 — Allow multiple runners but prioritise higher collapse
#
# PURPOSE:
# - Permit several runners per market
# - Prefer higher collapse_score
# - Maintain sweetspot discipline
# ==============================================================================

        # ---- LAY ladder trigger ----
        if self.armed_lay.get(key):

            # If authority available, prefer higher collapse runners
            if authority_available and collapse_score < 1.0:
                return self._no_signal("collapse_below_trade_threshold")

            if to_price >= self.SWEETSPOT:
                plans.extend(self._emit_lay_ladder(ctx, to_price))
                self.triggered[key] = True            

# === PATCH END ==============================================================

        # ---- BACK ladder trigger ----
        elif self.armed_back.get(key):
            if to_price <= self.SWEETSPOT:
                plans.extend(self._emit_back_ladder(ctx, to_price))
                self.triggered[key] = True

# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py
# 🔎 SEARCH: if not plans:
# 🛠 ACTION: Restore batch emission contract for MSC_INPLAY
# 📆 PATCHED: 2026-04-28 — Reinstate full ladder batch emission
#
# PURPOSE:
# - Emit ALL ladder parents at trigger
# - Align with BUS batch expansion logic
# - Preserve 24-slot design (4 runners × 6 levels)
#
# INVARIANT:
# - Engine returns {"batch": True, "plans": [...]}
# - BUS expands into individual parents
# - Router promotes sequentially
# ==============================================================================

        if not plans:
            return self._no_signal("armed_not_triggered")

        _write_inplay_runtime_snapshot(ctx, self)

        return {
            "enter": True,
            "engine": "MSC_INPLAY",
            "batch": True,
            "plans": plans,
        }

# === PATCH END ==============================================================

    # ----------------------------
    # Ladder emitters (BATCH)
    # ----------------------------

    def _emit_lay_ladder(self, ctx: Dict[str, Any], px: float) -> List[Dict[str, Any]]:
        return [
            {
                "enter": True,
                "engine": "MSC_INPLAY",
                "role": "PARENT",
                "direction": "LAY->BACK",
                "px": lvl,
                "target_ticks": 50,
                "why": f"inplay_lay_ladder_{lvl}",
            }
            for lvl in self.LAY_LADDER
            if lvl >= px
        ]

    def _emit_back_ladder(self, ctx: Dict[str, Any], px: float) -> List[Dict[str, Any]]:
        return [
            {
                "enter": True,
                "engine": "MSC_INPLAY",
                "role": "PARENT",
                "direction": "BACK->LAY",
                "px": lvl,
                "target_ticks": 50,
                "why": f"inplay_back_ladder_{lvl}",
            }
            for lvl in self.BACK_LADDER
            if lvl <= px
        ]

    # --------------------------------------------------
    # V7 Atomic Report
    # --------------------------------------------------

    def _print_v7_inplay_report(
        self,
        market_id: str,
        sid: str,
        px: float,
        direction: str,
        ticks_moved: float,
        race_started: bool,
        race_quartile: Optional[str],
        race_confidence: float,
        prev_px: Optional[float],
    ):

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%SZ")

        print("\n══════════════════════════════════════════════════════")
        print("V7 MSC_INPLAY REPORT")
        print(f"t={now_str}   mode=LIVE")
        print("══════════════════════════════════════════════════════")

        print("\nMARKET")
        print("------------------------------------------------------")
        print(f"marketId                : {market_id}")
        print(f"race_started            : {'YES' if race_started else 'NO'}")
        print(f"race_quartile           : {race_quartile}")
        print(f"race_confidence         : {round(race_confidence,2)}")
        print("------------------------------------------------------")

        print("\nRUNNER")
        print("------------------------------------------------------")
        print(f"selectionId             : {sid}")
        print(f"price                   : {px}")
        print(f"direction               : {direction}")
        print(f"prev_price              : {prev_px}")
        print(f"price_delta             : {None if prev_px is None else round(px - prev_px,4)}")
        print(f"ticks_moved             : {ticks_moved}")
        print(f"armed_lay               : {self.armed_lay.get((market_id, sid), False)}")
        print(f"armed_back              : {self.armed_back.get((market_id, sid), False)}")
        print(f"triggered               : {self.triggered.get((market_id, sid), False)}")
        print("------------------------------------------------------")
        print("══════════════════════════════════════════════════════")

    # ----------------------------
    # No-signal helper
    # ----------------------------

    def _no_signal(self, reason: str) -> Dict[str, Any]:
        payload = {
            "enter": False,
            "engine": "MSC_INPLAY",
            "reason": reason,
            "re_eval": True,
        }
        try:
            emit("msc_inplay.no_signal", payload)
        except Exception:
            pass
        return payload

# === PATCH END ==============================================================

# === PATCH START ==============================================================
# 📍 TARGET: engines/micro_scalper_v7/inplay_engine.py (append at end)
# 📆 PATCHED: 2026-02-27 — Structured runtime snapshot (INPLAY)
# ==============================================================================

def _ensure_inplay_runtime_schema():
    import sqlite3
    from engines.config_paths import autoscalp_db
    con = sqlite3.connect(autoscalp_db(), timeout=6, isolation_level=None)
    con.execute("""
        CREATE TABLE IF NOT EXISTS inplay_runtime_snapshot(
            ts TEXT,
            marketId TEXT,
            selectionId TEXT,
            px REAL,
            direction TEXT,
            armed_lay INTEGER,
            armed_back INTEGER,
            triggered INTEGER
        )
    """)
    con.close()


# ==================================================
# 🟥 INPLAY RUNTIME SNAPSHOT (ENGINE-OWNED)
# ==================================================
def _write_inplay_runtime_snapshot(ctx, self):
    try:
        import sqlite3
        from datetime import datetime, timezone
        from engines.config_paths import autoscalp_db

        ts = datetime.now(timezone.utc).isoformat()

        con = sqlite3.connect(autoscalp_db(), timeout=6, isolation_level=None)

        for (mid, sid), _ in self.armed_lay.items():

            con.execute("""
                INSERT INTO inplay_runtime_snapshot
                VALUES (?,?,?,?,?,?,?,?)
            """, (
                ts,
                str(mid),
                str(sid),
                None,  # px handled via ctx snapshot, not engine memory
                None,
                int(self.armed_lay.get((mid, sid), False)),
                int(self.armed_back.get((mid, sid), False)),
                int(self.triggered.get((mid, sid), False)),
            ))

        con.close()

    except Exception:
        pass
