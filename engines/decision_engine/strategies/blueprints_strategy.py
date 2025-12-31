from __future__ import annotations

from .common import StrategyCtx, Instruction
from engines.config_paths import open_auto_db

# 📍 TARGET: engines/decision_engine/strategies/blueprints_strategy.py
# 📆 REWRITE: 2025-12-31 — DB-only Blueprint gate (engine owns intelligence)
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    BLUEPRINTS (P) — PRE, predictive execution gate.

    This strategy does NOT compute blueprints.
    It ONLY:
      • reads the persisted blueprint state written by the Blueprint engine
      • checks confidence
      • enforces once-per-OC firing
      • maps blueprint key → trade direction

    DB is the memory. StrategyCtx is only an ID carrier.
    """
    try:
        # -------------------- basic guards --------------------
        if getattr(ctx, "phase", None) != "PRE":
            return None

        tto_s = int(getattr(ctx, "tto_s", 0) or 0)
        if not (120 <= tto_s <= 3600):
            return None

        market_id = getattr(ctx, "marketId", None)
        selection_id = getattr(ctx, "selectionId", None)
        if not market_id or not selection_id:
            return None

        oc_index = getattr(ctx, "oc_index", None)
        try:
            oc_index = int(oc_index)
        except Exception:
            return None

        # -------------------- read blueprint state --------------------
        con = open_auto_db(rw=False)
        cur = con.cursor()

        row = cur.execute(
            """
            SELECT blueprint_key, score
            FROM blueprint_state
            WHERE marketId = ?
              AND selectionId = ?
            """,
            (market_id, selection_id),
        ).fetchone()

        if not row:
            con.close()
            return None

        blueprint_key, score = row
        try:
            score = float(score)
        except Exception:
            con.close()
            return None

        # -------------------- confidence gate --------------------
        BP_CONF_THRESHOLD = 0.62
        if not blueprint_key or score < BP_CONF_THRESHOLD:
            con.close()
            return None

        # -------------------- once-per-OC enforcement --------------------
        fired = cur.execute(
            """
            SELECT 1
            FROM orders
            WHERE role = 'PARENT'
              AND source = 'P'
              AND marketId = ?
              AND selectionId = ?
              AND oc_index = ?
            LIMIT 1
            """,
            (market_id, selection_id, oc_index),
        ).fetchone()

        con.close()

        if fired:
            return None

        # -------------------- direction mapping --------------------
        key = str(blueprint_key).upper()

        # Drift-dominant → LAY
        if "DRIFT" in key:
            side = "LAY"

        # Steam-dominant → BACK
        elif "STEAM" in key:
            side = "BACK"

        # Oscillation / neutral → skip
        else:
            return None

        # -------------------- construct instruction --------------------
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.7)

        return Instruction(
            side=side,
            hedge_ticks=3,
            stake=stake,
            source="P",
        )

    except Exception:
        return None
