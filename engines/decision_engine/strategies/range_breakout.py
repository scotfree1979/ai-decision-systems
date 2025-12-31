from __future__ import annotations
from .common import StrategyCtx, Instruction
from engines.config_paths import open_auto_db

# 📍 TARGET: engines/decision_engine/strategies/range_breakout.py
# 🔎 SEARCH: def decide(ctx: StrategyCtx) -> Instruction | None:
# 📆 PATCHED: 2025-12-31 — OC-accumulated envelope breakout (event-based)
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    S5_BREAKOUT — PRE, lay-first bias.

    Fires ONLY when price breaks beyond the OC-accumulated envelope
    (anchor + all OC odds so far). Event-based, DB-backed, OC-aware.
    """
    try:
        # --- basic guards (unchanged intent) ---
        if getattr(ctx, "phase", None) != "PRE":
            return None

        tto_s = int(getattr(ctx, "tto_s", 0) or 0)
        if not (60 <= tto_s <= 3600):
            return None

        price = float(getattr(ctx, "price", 0.0) or 0.0)
        if price <= 0.0:
            return None

        market_id = getattr(ctx, "marketId", None)
        selection_id = getattr(ctx, "selectionId", None)
        if not market_id or not selection_id:
            return None

        # --- build OC-accumulated envelope from DB ---
        con = open_auto_db(rw=False)
        cur = con.cursor()

        # anchor odd
        row = cur.execute(
            """
            SELECT anchor_odd
            FROM inbound_oc_cache
            WHERE marketId=? AND selectionId=?
            """,
            (market_id, selection_id),
        ).fetchone()

        if not row or not row[0]:
            con.close()
            return None

        anchor = float(row[0])

        # all OC odds so far
        rows = cur.execute(
            """
            SELECT odd
            FROM oc_series
            WHERE marketId=? AND selectionId=?
            """,
            (market_id, selection_id),
        ).fetchall()

        con.close()

        odds_seen = [anchor]
        for r in rows:
            try:
                o = float(r[0])
                if o > 0.0:
                    odds_seen.append(o)
            except Exception:
                pass

        if not odds_seen:
            return None

        range_low = min(odds_seen)
        range_high = max(odds_seen)

        # --- event-based breakout ---
        # lay-first: only top-side (drift) breakouts
        if price <= range_high:
            return None

        # --- return instruction (unchanged intent) ---
        side = "LAY"
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.6)

        return Instruction(
            side=side,
            hedge_ticks=2,
            stake=stake,
            source="R",
        )

    except Exception:
        return None
