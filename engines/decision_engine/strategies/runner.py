from __future__ import annotations
import sqlite3, time
from typing import List, Optional
from engines.config_paths import autoscalp_db
from engines.live.live_router import place_parent_and_hedge
from .registry import STRAT_CODE  # maps names to family letters (X,B,F,D,...) or “S” for legacy
from .common import build_ctx, Instruction, StrategyCtx, _cooldown_factory
from engines.decision_engine.strategies.common import SourceTagger
from engines.decision_engine.strategies.registry import STRAT_CODE




# Enable ALL by default (no env exports required)
ENABLED = {
    "BTL_AGGR": True,
    "BTL_SCOUT": True,
    "OG_STRATEGY": True,
    "LADDER_STRATEGY": True,
    "CROSSOVER_STRATEGY": True,
    "RANGE_BREAKOUT": True,
    "STEAM_FADE": True,
    "IP_SHOCK_DRIFT": True,
    "IP_TIRED_LEADER": True,
    "IP_CLOSE_FINISH": True,
    "IP_FENCE_ERROR": True,
    "IP_COLLAPSE_FADE": True,
}

# Import all deciders
from .btl_aggr import decide as S_BTL_AGGR
from .btl_scout import decide as S_BTL_SCOUT
from .og_bias import decide as S_OG
from .ladder_l2b import decide as S_LADDER
from .crossover import decide as S_XOVER
from .range_breakout import decide as S_RBO
from .steam_fade import decide as S_FADE
from .inplay import shock_drift_lay as IP_SHOCK
from .inplay import tired_leader as IP_TIRED
from .inplay import close_finish as IP_CLOSE
from .inplay import jump_mistake as IP_JUMP
from .inplay import collapse_fade as IP_COL

# Evaluation order (first match fires; ladder can return multiple)
ORDER = [
    ("BTL_SCOUT",   S_BTL_SCOUT),
    ("BTL_AGGR",    S_BTL_AGGR),
    ("OG_STRATEGY", S_OG),
    ("LADDER_STRATEGY", S_LADDER),
    ("CROSSOVER_STRATEGY", S_XOVER),
    ("RANGE_BREAKOUT", S_RBO),
    ("STEAM_FADE",  S_FADE),
    ("IP_SHOCK_DRIFT", IP_SHOCK),
    ("IP_TIRED_LEADER", IP_TIRED),
    ("IP_CLOSE_FINISH", IP_CLOSE),
    ("IP_FENCE_ERROR", IP_JUMP),
    ("IP_COLLAPSE_FADE", IP_COL),
]


def _place_live(run_id, strat_name, mid, sid, side, price, size, ticks):

    # Short, stable source tag so the strategy table can group correctly
    letter = STRAT_CODE.get(strat_name.upper(), strat_name.upper()[:1])
    src_tag = strat_name.upper() if letter in ("S","LEGACY") else letter  # e.g. X,B,F,D...

    place_parent_and_hedge(
        market_id=str(mid),
        selection_id=str(sid),
        side=str(side),
        entry_odds=float(price),
        stake=float(size),
        hedge_ticks=int(ticks),
        run_id=run_id,

        source=src_tag,
    )

def _adb() -> sqlite3.Connection:
    con = sqlite3.connect(autoscalp_db(), timeout=12, isolation_level=None, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    return con

def _day_and_next(con: sqlite3.Connection) -> tuple[str, Optional[str]]:
    r = con.execute("SELECT day FROM dashboard_runs ORDER BY datetime(last_heartbeat_ts) DESC LIMIT 1").fetchone()
    day = (r["day"] if r and r["day"] else None) or con.execute("SELECT date('now','utc')").fetchone()[0]
    m = con.execute("SELECT marketId FROM dashboard_markets WHERE day=? AND is_next=1 LIMIT 1", (day,)).fetchone()
    return day, (str(m["marketId"]) if m else None)

def safe_price(r: dict | None) -> float | None:
    """Return a float odds if available, else None."""
    if not r: 
        return None
    for key in ("ltp", "last_price_traded", "px", "anchor_odd", "entry_odds"):
        try:
            v = r.get(key)
            if v is not None:
                return float(v)
        except Exception:
            continue
    return None


def _top6(con: sqlite3.Connection, day: str, mid: str) -> List[str]:
    rows = con.execute("""
        SELECT selectionId FROM dashboard_runners
         WHERE day=? AND marketId=? AND in_top6=1 ORDER BY top6_rank ASC
    """, (day, mid)).fetchall()
    return [str(r["selectionId"]) for r in rows]

_cd_ok = _cooldown_factory()

# 📍 TARGET: engines/decision_engine/runner.py
# 🔎 SEARCH: ^def run_active_strategies\(run_id: str, logger=None\) -> int:
# ⬇️ REPLACE the entire function with this hardened version
def run_active_strategies(run_id: str, logger=None) -> int:
    """
    Scan the next market's top-6 runners and try strategies in registry.ORDER per runner.
    - Applies per-strategy gates (registry.STRAT_GATES)
    - Respects registry.ENABLED
    - First strategy that places for a runner wins (move to next runner)
    Returns the count of parents placed this tick.
    """
    import time
    import math
    from engines.decision_engine.strategies.registry import (
        ORDER as REG_ORDER,
        ENABLED as REG_ENABLED,
        STRAT_GATES,
        STRAT_CODE as REG_CODE,
    )
    from engines.decision_engine.strategies.common import gate_check, GateSpec

    if logger is None:
        logger = lambda *a, **k: None

    con = _adb()
    placed = 0
    try:
        day, mid = _day_and_next(con)
        if not mid:
            return 0
        sids = _top6(con, day, mid)
    finally:
        try: con.close()
        except Exception: pass

    # Throttled log memory
    _last = getattr(run_active_strategies, "_last", {})
    def _throttle(key: str, every: float = 60.0) -> bool:
        now = time.time()
        if now - _last.get(key, 0.0) >= every:
            _last[key] = now
            setattr(run_active_strategies, "_last", _last)
            return True
        return False

    # Helper: map a strategy display name to a GateSpec
    def _gate_for(name: str) -> GateSpec | None:
        k = name.upper()
        spec = STRAT_GATES.get(k)
        if spec:
            return spec
        letter = REG_CODE.get(k)
        if not letter:
            return None
        for _nm, _spec in STRAT_GATES.items():
            try:
                if getattr(_spec, "code_letter", "") == letter:
                    return _spec
            except Exception:
                pass
        return None

    # local float guard (pairs with _safe_int you added)
    def _safe_float(x, default=None):
        try:
            if x is None: return default
            v = float(x)
            return default if (isinstance(v, float) and math.isnan(v)) else v
        except Exception:
            return default

    for sid in sids:
        ctx = build_ctx(run_id=run_id, market_id=mid, selection_id=sid)
        ctx.cooldown_ok = lambda key, cd=60: _cd_ok(f"{ctx.market_id}:{ctx.selection_id}:{key}", cd)
        ctx.parent_open_for_runner = ctx.parent_open_for_runner
        ctx.parent_info = ctx.parent_info

        # NEW: use a shared dict view of ctx for gates + strategies
        ctx_dict = ctx.__dict__ if hasattr(ctx, "__dict__") else dict(ctx)

        for name, fn in REG_ORDER:
            if not REG_ENABLED.get(name, True):
                continue

            # per-strategy gate (dict)
            spec = _gate_for(name)
            if spec:
                try:
                    ok, why = gate_check(ctx_dict, spec)
                except Exception as e:
                    key = f"gateerr:{name}:{mid}:{sid}:{type(e).__name__}"
                    if _throttle(key, 120.0):
                        logger(f"[GATE {name}] check warn: {e} mid={mid} sid={sid}")
                    continue

                if not ok:
                    def _mto_txt():
                        v = ctx_dict.get("minutes_to_off")
                        try:
                            return f"{float(v):.1f}m" if v is not None else "?"
                        except Exception:
                            return "?"
                    key = f"gate:{name}:{mid}:{sid}:{why}"
                    if _throttle(key, 45.0):
                        p = (ctx_dict.get("price") or ctx_dict.get("price_now") or 0.0)
                        logger(f"[GATE {name}] {why} odds={float(p):.2f} tto={_mto_txt()} mid={mid} sid={sid}")
                    continue

            # strategy decide (dict!)
            try:
                res = fn(ctx_dict)
            except Exception as e:
                if _throttle(f"decerr:{name}:{mid}:{sid}", 60.0):
                    logger(f"[STRAT {name}] decide warn: {e}")
                continue


            if not res:
                if _throttle(f"dec0:{name}:{mid}:{sid}", 60.0):
                    logger(f"[STRAT {name}] decide=None mid={mid} sid={sid}")
                continue

            instructions = res if isinstance(res, list) else [res]
            # place first viable instruction per runner
            for ins in instructions:
                try:
                    # source tag like X3/B1/F7; fallback first letter
                    letter = REG_CODE.get(name.upper(), name[:1].upper())
                    src_tag = SourceTagger.next_tag(letter)

                    # sanitize price/stake/hedge_ticks (works for dataclass or dict)
                    side = getattr(ins, "side", None) or (ins.get("side") if isinstance(ins, dict) else None)

                    # prefer explicit price on ins, else ctx.price/price_now
                    price = _safe_float(
                        getattr(ins, "price", None) if hasattr(ins, "price") else (ins.get("price") if isinstance(ins, dict) else None),
                        default=None,
                    )
                    if price is None:
                        price = _safe_float(getattr(ctx, "price", None), default=None)
                    if price is None:
                        price = _safe_float(getattr(ctx, "price_now", None), default=None)

                    stake = _safe_float(
                        getattr(ins, "stake", None) if hasattr(ins, "stake") else (ins.get("stake") if isinstance(ins, dict) else None),
                        default=0.0,
                    )

                    hedge_ticks=_safe_int(getattr(ins, "hedge_ticks", None) if hasattr(ins, "hedge_ticks") else (ins.get("hedge_ticks") if isinstance(ins, dict) else None), default=1)

                    # guard: require minimal fields
                    if side is None or price is None or stake is None:
                        if _throttle(f"place:missing:{name}:{mid}:{sid}", 45.0):
                            logger(f"[STRAT {name}] missing side/price/stake (side={side}, price={price}, stake={stake})")
                        continue

                    bet_id, cor = place_parent_and_hedge(
                        market_id=ctx.market_id,
                        selection_id=ctx.selection_id,
                        side=side,
                        entry_odds=price,
                        stake=stake,
                        hedge_ticks=hedge_ticks,
                        run_id=run_id,
                        source=src_tag,
                        parent_persistence=cfg["parent_persistence"],
                        child_persistence=cfg["child_persistence"],
                    )
                    if bet_id:
                        placed += 1
                        logger(f"[STRAT] {name}→{src_tag} placed mid={ctx.market_id} sid={ctx.selection_id} side={side} p={price:.2f} st={stake:.2f} ht={hedge_ticks}")
                except Exception as e:
                    if _throttle(f"place:{name}:{mid}:{sid}", 30.0):
                        logger(f"[STRAT {name}] place warn: {e}")

                # once one strategy fires for this runner, go to next runner
                if placed:
                    break

            if placed:
                break

    return placed
