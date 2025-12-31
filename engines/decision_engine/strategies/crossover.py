from __future__ import annotations

from .common import StrategyCtx, Instruction
from engines.config_paths import open_auto_db

# 📍 TARGET: engines/decision_engine/strategies/crossover.py
# 📆 REWRITE: 2025-12-31 — Rank crossover as pure event (runner order change)
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    S4_CROSSOVER — PRE, structural runner-order crossover.

    Fires ONLY when this runner's favourite rank JUST changes
    relative to the previous snapshot.
    Pure event-based, DB-backed, no thresholds.
    """
    try:
        # --- phase / time guards ---
        if getattr(ctx, "phase", None) != "PRE":
            return None

        tto_s = int(getattr(ctx, "tto_s", 0) or 0)
        if not (60 <= tto_s <= 3600):
            return None

        market_id = getattr(ctx, "marketId", None)
        selection_id = getattr(ctx, "selectionId", None)
        if not market_id or not selection_id:
            return None

        # current rank (already computed upstream)
        rank_now = getattr(ctx, "fav_rank", None)
        try:
            rank_now = int(rank_now)
        except Exception:
            return None

        # --- fetch previous rank from DB ---
        con = open_auto_db(rw=False)
        cur = con.cursor()

        row = cur.execute(
            """
            SELECT fav_rank
            FROM odds_current
            WHERE marketId = ?
              AND selectionId = ?
              AND fav_rank IS NOT NULL
            ORDER BY updated_ts DESC
            LIMIT 2
            """,
            (market_id, selection_id),
        ).fetchall()

        con.close()

        # Need at least two observations to detect a crossover
        if not row or len(row) < 2:
            return None

        try:
            rank_prev = int(row[1][0])
        except Exception:
            return None

        # --- event semantics ---
        # crossover = rank just changed
        if rank_now == rank_prev:
            return None

        # --- construct instruction ---
        # crossover is structural, medium confidence
        side = "LAY"
        size_cap = float(getattr(ctx, "size_cap", 2.0) or 2.0)
        stake = max(2.0, size_cap * 0.6)

        return Instruction(
            side=side,
            hedge_ticks=2,
            stake=stake,
            source="X",
        )

    except Exception:
        return None
