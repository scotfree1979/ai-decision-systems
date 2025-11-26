# engines/decision_engine/strategies/mlm.py
from __future__ import annotations
from .common import StrategyCtx, Instruction

import sqlite3, json
from datetime import datetime, timezone

# ── Local imports (consistent with your strategy architecture) ───────────────
from engines.config_paths import autoscalp_db, q_retry as _q
from engines.live import bank_state
from engines.mastery import event_sink

# ─────────────────────────────────────────────────────────────────────────────
def _now_utc_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

# === PATCH START ===
# 📍 TARGET: engines/decision_engine/strategies/mlm.py:_runner_liabilities
# 📆 PATCHED: 2025-10-28Z — use cashout_calc (single-runner worst loss per market)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def _runner_liabilities() -> dict[str, float]:
    """
    Unified live liabilities per marketId, taken from cashout_calc()
    (worst single-runner loss per market).
    """
    try:
        import sqlite3
        from engines.cashout_calc import cashout_calc
        from engines.config_paths import autoscalp_db
        uri = f"file:{autoscalp_db()}?mode=ro&cache=shared"
        con = sqlite3.connect(uri, uri=True, timeout=5)
        con.row_factory = sqlite3.Row
        live = cashout_calc(con, write_to_db=False)
        con.close()
        return {mid: float(info.get("liability", 0.0)) for mid, info in (live or {}).items()}
    except Exception:
        return {}
# === PATCH END ===


def _cap_per_market() -> float:
    """Return 12.5% of the current available balance as the per-market liability limit."""
    bal = float(bank_state.get_balance() or 0.0)
    return round(bal * 0.125, 2)

# ─────────────────────────────────────────────────────────────────────────────
def decide(ctx: StrategyCtx) -> Instruction | None:
    """
    MLM — Market Liability Manager (3-minute cooldown strategy).
    Evaluates total market liability vs per-market cap.
    If exceeded, emits an Instruction of type 'NO_TRADE' and notifies Mastery.
    Otherwise, does nothing.
    """
    try:
        # Only trigger during the cool-off window (≈3 min pre-off)
        phase = getattr(ctx, "phase", "UNKNOWN")
        mto = getattr(ctx, "minutes_to_off", None)
        try:
            mto = float(mto) if mto is not None else None
        except Exception:
            mto = None
        if mto is None or not (0.0 < mto <= 3.0):
            return None  # only relevant in the cool-off window

        liabs = _runner_liabilities()
        limit = _cap_per_market()
        if not liabs:
            return None

        breaches = {m: v for m, v in liabs.items() if abs(v) >= limit}
        if not breaches:
            return None

        worst_mid, worst_val = max(breaches.items(), key=lambda kv: abs(kv[1]))
        reason = f"mlm_cap_hit market={worst_mid} liability={worst_val:.2f} limit={limit:.2f}"

        # Emit event to Mastery so it can rebalance
        event_sink.on_decision({
            "type": "mlm_cap_hit",
            "marketId": worst_mid,
            "liability": worst_val,
            "limit": limit,
            "ts": _now_utc_str(),
        })

        # Return a no-trade Instruction (consistent with engine’s pattern)
        return Instruction(
            side=None,
            hedge_ticks=0,
            stake=0.0,
            source="MLM",
            meta={"why": reason, "cap": limit, "liability": worst_val}
        )

    except Exception as e:
        return Instruction(
            side=None,
            hedge_ticks=0,
            stake=0.0,
            source="MLM",
            meta={"error": type(e).__name__}
        )
